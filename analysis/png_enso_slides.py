"""Two ENSO slides per country, for every country the alerts app covers,
published under pages/enso/ (one page, dropdown country selector).

Slide 1 — "what generally happens during El Niño": ERA5 rainfall x ENSO
teleconnection, pixelwise at the native 0.25 degree grid — the
ds-teleconnections page's PARTIAL pass (Nino3.4's unique signal holding
DMI/TNA/TSA/AMM constant, PDO excluded from controls, per-pixel best lags
frozen from the total sweep, p<0.05, the discrete blue/brown |r| bins) zoomed
to the country, with tiles for the four canonical trimesters (whole year).

Slide 2 — "what's predicted this year": the current SEAS5 issuance, one tile
per valid trimester (leads -2..4, in-season trimesters are the cube's mixed
obs+forecast aggregation), in the alerts app's own colours: drought/flood
3-yr and 10-yr return-period categories, skill-shaded exactly as the app.
Plus a bar chart of country-mean climatology vs this forecast (mm/day).

Data (per methods/reuse-published-stats.md — nothing recomputed that a
product already publishes):
  - ERA5 monthly mm/day COGs — sliced from the ds-teleconnections local cache
    where the country fits its viewport, else windowed reads from prod blob.
  - Six NOAA PSL climate indices (nino34, dmi, tna, tsa, amm, pdo; cached).
  - The detrended skill cube (dev blob) for all pixel fields + mm values, and
    docs/data/forecasts/<issued>.json for country pct/RP — vintage-checked.
  - ADM1 CODs from the prod polygon blob container (adm0 = dissolved adm1),
    falling back to the app's countries.geojson outline where no COD exists.

Run:  uv run python analysis/png_enso_slides.py --country all        # batch
      uv run python analysis/png_enso_slides.py --country NER [--force]
Writes pages/enso/slides/{ISO3}_slide{1,2}.png + {ISO3}.pdf and refreshes
pages/enso/countries.json (the dropdown manifest).
"""

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

import geopandas as gpd

try:  # island-rich adm1 features (e.g. PHL) exceed OGR's default GeoJSON cap
    import pyogrio

    pyogrio.set_gdal_config_options({"OGR_GEOJSON_MAX_OBJ_SIZE": "0"})
except Exception:  # noqa: BLE001
    pass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import requests  # noqa: E402
from scipy import stats  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "analysis" / "_png_enso_cache"
OUT_DIR = REPO / "pages" / "enso" / "slides"

# ds-teleconnections cache of the global ERA5 monthly stack (0.25deg, 1981-2025)
TELE_CACHE = Path.home() / "OCHA/repos/ds-teleconnections/cache/era5_pixel"
TELE_EXTENT = {"lon": (-100.0, 179.75), "lat": (-36.0, 56.0)}

START_YEAR, END_YEAR = 1981, 2025
MIN_YEARS = 25
MAX_LAG = 3            # l3, the teleconnections page default
ALPHA = 0.05
MIN_TRI_MM_DAY = 0.25  # hyper-arid cut (as the teleconnections pixel pass)

WINDOW_MARGIN = 0.6    # deg padding around the country bounds
MIN_W, MIN_H = 3.0, 2.25   # minimum window size (small island states)
PART_KEEP_DEG = 8.5    # keep territory parts within this of the main landmass

SLIDE1_TILES = ("JFM", "AMJ", "JAS", "OND")   # non-overlapping, whole year
DPI = 150

TRIMESTERS = {
    "NDJ": 1, "DJF": 2, "JFM": 3, "FMA": 4, "MAM": 5, "AMJ": 6,
    "MJJ": 7, "JJA": 8, "JAS": 9, "ASO": 10, "SON": 11, "OND": 12,
}
_TRI_MONTHS = {t: [((e - 3 + i) % 12) + 1 for i in range(3)]
               for t, e in ((t, TRIMESTERS[t]) for t in TRIMESTERS)}

# Discrete 2-bin correlation colours — identical to the teleconnections page
R_STRONG, R_MIN = 0.45, 0.30
C_POS_STRONG, C_POS_MOD = "#0D40B0", "#71B3E5"
C_NEG_MOD, C_NEG_STRONG = "#C8844A", "#7B3A1A"
C_NOSIG, C_OCEAN, C_OUTLINE = "#E8E8E8", "#FFFFFF", "#9AA3B0"

# Same index set + partial-control convention as the teleconnections page.
INDEX_SOURCES = {
    "nino34": "https://psl.noaa.gov/data/correlation/nina34.anom.data",
    "dmi":    "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data",
    "tna":    "https://psl.noaa.gov/data/correlation/tna.data",
    "tsa":    "https://psl.noaa.gov/data/correlation/tsa.data",
    "amm":    "https://psl.noaa.gov/data/correlation/amm.data",
    "pdo":    "https://psl.noaa.gov/data/correlation/pdo.data",
}
PARTIAL_EXCLUDE = {"pdo"}

# Names for app countries missing from countries.geojson
EXTRA_NAMES = {"BES": "Bonaire, Sint Eustatius and Saba", "GLP": "Guadeloupe",
               "GUF": "French Guiana", "MTQ": "Martinique"}


class SkipCountry(Exception):
    pass


# ── country registry & geometry ─────────────────────────────────────────────────

_STATE: dict = {}   # current country: iso3, name, lon, lat, adm1, adm0, clipped


def registry() -> dict[str, str]:
    """{iso3: display name} for every country in the app's current export."""
    issued = load_issued()
    data = json.loads(
        (REPO / "docs" / "data" / "forecasts" / f"{issued:%Y-%m}.json").read_text()
    )["data"]
    names = {f["properties"]["iso3"]: f["properties"]["name"]
             for f in json.loads(
                 (REPO / "docs" / "data" / "countries.geojson").read_text()
             )["features"]}
    names.update(EXTRA_NAMES)
    return {iso: names.get(iso, iso) for iso in sorted(data)}


def _load_adm_geoms(iso3: str):
    """(adm1, adm0) GeoDataFrames: COD adm1 from the polygon blob (adm0 =
    dissolve), else the app's countries.geojson outline with no adm1 lines."""
    iso = iso3.lower()
    f = CACHE / f"{iso}_adm1.geojson"
    if not f.exists():
        try:
            import ocha_stratus as stratus

            gdf = stratus.load_shp_from_blob(
                f"{iso}_shp.zip", shapefile=f"{iso}_adm1.shp",
                stage="prod", container_name="polygon")
            # simplify for display/caching; ~200 m tolerance is invisible at
            # the 0.25-0.4 deg grids these slides draw on
            gdf = gdf.assign(geometry=gdf.geometry.simplify(0.002))
            CACHE.mkdir(parents=True, exist_ok=True)
            gdf[["geometry"]].to_file(f, driver="GeoJSON")
        except Exception:
            f = None
    if f is not None and f.exists():
        adm1 = gpd.read_file(f)
        adm0 = gpd.GeoDataFrame(geometry=[adm1.union_all()], crs=adm1.crs)
        return adm1, adm0

    feats = [ft for ft in json.loads(
        (REPO / "docs" / "data" / "countries.geojson").read_text())["features"]
        if ft["properties"]["iso3"] == iso3]
    if not feats:
        raise SkipCountry(f"{iso3}: no adm1 COD and no countries.geojson outline")
    adm0 = gpd.GeoDataFrame.from_features(feats, crs=4326)
    return adm0.iloc[0:0].copy(), adm0   # empty adm1 -> no interior lines


def set_country(iso3: str, name: str) -> None:
    """Resolve geometry, trim far-flung territories, derive the map window."""
    from shapely.geometry import MultiPolygon

    adm1, adm0 = _load_adm_geoms(iso3)
    parts = []
    for geom in adm0.geometry:
        if geom is None:
            continue
        gs = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
        parts.extend(gs)
    if not parts:
        raise SkipCountry(f"{iso3}: empty geometry")

    main = max(parts, key=lambda g: g.area)
    mb = main.bounds

    def near(g):   # wrap-aware distance from the main landmass bbox
        b = g.bounds
        dlon = max(0.0, max(mb[0] - b[2], b[0] - mb[2]))
        dlon = min(dlon, 360 - dlon)
        dlat = max(0.0, max(mb[1] - b[3], b[1] - mb[3]))
        # drop antimeridian-crossing companions (naive frame only)
        seam = abs(g.centroid.x - main.centroid.x) > 180
        return (not seam) and max(dlon, dlat) <= PART_KEEP_DEG

    keep = [g for g in parts if near(g)]
    clipped = len(keep) < len(parts)
    xs = [b for g in keep for b in (g.bounds[0], g.bounds[2])]
    ys = [b for g in keep for b in (g.bounds[1], g.bounds[3])]
    x0, x1 = min(xs) - WINDOW_MARGIN, max(xs) + WINDOW_MARGIN
    y0, y1 = min(ys) - WINDOW_MARGIN, max(ys) + WINDOW_MARGIN
    if x1 - x0 < MIN_W:
        cx_ = (x0 + x1) / 2
        x0, x1 = cx_ - MIN_W / 2, cx_ + MIN_W / 2
    if y1 - y0 < MIN_H:
        cy_ = (y0 + y1) / 2
        y0, y1 = cy_ - MIN_H / 2, cy_ + MIN_H / 2

    _STATE.clear()
    _STATE.update(iso3=iso3, name=name, lon=(x0, x1), lat=(y1, y0),  # (N, S)
                  adm1=adm1, adm0=adm0, clipped=clipped)


def LON():
    return _STATE["lon"]


def LAT():
    return _STATE["lat"]


# ── shared helpers ──────────────────────────────────────────────────────────────


def _parse_psl(text: str) -> pd.Series:
    """NOAA PSL fixed-format monthly series -> Series indexed by month-end."""
    lines = text.splitlines()
    yr0, yr1 = (int(x) for x in lines[0].split()[:2])
    recs, missing = {}, None
    for ln in lines[1:]:
        parts = ln.split()
        if not parts:
            continue
        if len(parts) == 1:
            try:
                missing = float(parts[0])
            except ValueError:
                pass
            continue
        try:
            yr = int(parts[0])
        except ValueError:
            continue
        if not (yr0 <= yr <= yr1):
            continue
        for m, v in enumerate((float(v) for v in parts[1:13]), start=1):
            recs[pd.Timestamp(yr, m, 1) + pd.offsets.MonthEnd(0)] = v
    s = pd.Series(recs).sort_index()
    if missing is not None:
        s = s.where(s != missing)
    return s.where(s > -900)


def load_indices() -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in INDEX_SOURCES.items():
        f = CACHE / f"{name}.data"
        if not f.exists():
            f.write_text(requests.get(url, timeout=60).text)
        out[name] = _parse_psl(f.read_text())
    return pd.DataFrame(out).loc[f"{START_YEAR}":]


def load_nino34() -> pd.Series:
    return load_indices()["nino34"]


def index_trimester_mean(idx: pd.Series, end_month: int, lag: int) -> pd.Series:
    """Mean of the 3-month index window ending `lag` months before trimester end."""
    win = idx.shift(lag).rolling(3).mean()
    sub = win[win.index.month == end_month]
    off = -1 if end_month in (1, 2) else 0  # NDJ/DJF anchor on the earlier year
    return pd.Series(sub.values, index=sub.index.year + off)


def tri_month_pairs(tri: str, season_year: int) -> list[tuple[int, int]]:
    months = _TRI_MONTHS[tri]
    wrapping = 1 in months and 12 in months
    return [(season_year + (0 if (not wrapping or m > 6) else 1), m) for m in months]


# ── ERA5 monthly stack for the country window ───────────────────────────────────


def era5_stack() -> tuple[np.ndarray, list[tuple[int, int]], np.ndarray, np.ndarray]:
    """(n_months, ny, nx) mm/day for the country window + (year, month) + coords.

    Sliced live from the ds-teleconnections memmap when the window fits its
    viewport; else windowed COG reads from the prod raster blob (cached per
    country — only a handful of countries need this path)."""
    lon, lat = LON(), LAT()
    in_tele = (TELE_CACHE / "meta.json").exists() and (
        lon[0] >= TELE_EXTENT["lon"][0] - 0.125
        and lon[1] <= TELE_EXTENT["lon"][1] + 0.125
        and lat[1] >= TELE_EXTENT["lat"][0] - 0.125
        and lat[0] <= TELE_EXTENT["lat"][1] + 0.125)

    if in_tele:
        meta = json.loads((TELE_CACHE / "meta.json").read_text())
        x_all, y_all = np.asarray(meta["x"]), np.asarray(meta["y"])
        ym = [tuple(t) for t in meta["ym"]]
        xi = np.where((x_all >= lon[0]) & (x_all <= lon[1]))[0]
        yi = np.where((y_all <= lat[0]) & (y_all >= lat[1]))[0]
        big = np.load(TELE_CACHE / "monthly.npy", mmap_mode="r")
        stack = np.asarray(big[:, yi[0]:yi[-1] + 1, xi[0]:xi[-1] + 1],
                           dtype="float32")
        return stack, ym, x_all[xi], y_all[yi]

    f = CACHE / f"era5_{_STATE['iso3'].lower()}.npz"
    if f.exists():
        z = np.load(f)
        return z["stack"], [tuple(t) for t in z["ym"]], z["x"], z["y"]

    import ocha_stratus as stratus

    print(f"  {_STATE['iso3']}: outside the ERA5 cache viewport — "
          "fetching windowed COGs from blob (one-time)")
    container = stratus.get_container_client("raster", stage="prod")
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    avail = {date_re.search(b.name).group(0): b.name
             for b in container.list_blobs(name_starts_with="era5/monthly/processed/")
             if date_re.search(b.name)}
    ym = [(yr, m) for yr in range(START_YEAR, END_YEAR + 1) for m in range(1, 13)
          if f"{yr}-{m:02d}-01" in avail]
    probe = stratus.open_blob_cog(avail[f"{ym[0][0]}-{ym[0][1]:02d}-01"],
                                  container_name="raster",
                                  container_client=container)
    xs, ys = probe.x.values, probe.y.values
    xi = np.where((xs >= lon[0]) & (xs <= lon[1]))[0]
    yi = np.where((ys <= lat[0]) & (ys >= lat[1]))[0]
    x, y = xs[xi], ys[yi]
    stack = np.empty((len(ym), len(y), len(x)), dtype="float32")
    for i, (yr, m) in enumerate(ym):
        da = stratus.open_blob_cog(avail[f"{yr}-{m:02d}-01"],
                                   container_name="raster",
                                   container_client=container)
        stack[i] = da.isel(band=0, y=slice(yi[0], yi[-1] + 1),
                           x=slice(xi[0], xi[-1] + 1)).values
        if (i + 1) % 120 == 0:
            print(f"    {i + 1}/{len(ym)}")
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, stack=stack, ym=np.array(ym), x=x, y=y)
    return stack, ym, x, y


def land_mask(gdf: gpd.GeoDataFrame, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    from rasterio.features import rasterize
    from rasterio.transform import from_origin

    res = float(abs(x[1] - x[0]))
    transform = from_origin(x[0] - res / 2, y[0] + res / 2, res, res)
    return rasterize(((g, 1) for g in gdf.geometry if g is not None),
                     out_shape=(len(y), len(x)), transform=transform,
                     fill=0, all_touched=True, dtype="uint8").astype(bool)


# ── Slide 1: pixelwise ERA5 x Nino3.4 (partial) ─────────────────────────────────


def pearson_p(r: np.ndarray, n: int, k: int = 0) -> np.ndarray:
    """Two-tailed p for Pearson/partial r with `k` controlled variables."""
    dof = float(n) - k - 2
    with np.errstate(invalid="ignore", divide="ignore"):
        t = r * np.sqrt(dof / np.clip(1 - r**2, 1e-12, None))
        return 2 * stats.t.sf(np.abs(t), dof)


def _partial_vs_last(V: np.ndarray) -> np.ndarray:
    """Partial correlation of each leading column of V with its last column,
    controlling for the others — the precision-matrix identity, batched over
    cells exactly as in the teleconnections script. V: (c, n, q) -> (c, q-1)."""
    c, n, q = V.shape
    Vc = V - V.mean(axis=1, keepdims=True)
    sd = np.sqrt((Vc**2).sum(axis=1))
    ok = sd > 0
    Vs = Vc / np.where(ok, sd, 1.0)[:, None, :]
    C = np.einsum("cni,cnj->cij", Vs, Vs)
    C[:, np.arange(q), np.arange(q)] += 1e-9
    P = np.linalg.inv(C)
    d = np.sqrt(np.abs(np.diagonal(P, axis1=1, axis2=2)))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = -P[:, :-1, -1] / (d[:, :-1] * d[:, -1:])
    r = np.clip(r, -1.0, 1.0)
    r[~(ok[:, :-1] & ok[:, -1:])] = np.nan
    return r


def compute_enso_correlations():
    """Per-trimester PARTIAL r of trimester rainfall vs Nino3.4 — the unique
    ENSO signal, holding DMI, TNA, TSA and AMM constant (PDO excluded from the
    control set, as on the teleconnections page). Each index enters at its own
    per-pixel best lag frozen from the total sweep, mirroring pixel_partial_pass.

    Returns (results, analyzable, x, y, mask): results[tri] = dict(r, p) each an
    (ny, nx) array (NaN off-land), analyzable[tri] the per-trimester mask
    (land, non-hyper-arid, enough years — deliberately NO rainy-season filter,
    so dry-season ENSO signals stay visible)."""
    stack, ym, x, y = era5_stack()
    mask = land_mask(_STATE["adm0"], x, y)
    mpos = {p: i for i, p in enumerate(ym)}
    indices = load_indices()
    cols = list(indices.columns)
    base = [j for j, c in enumerate(cols) if c not in PARTIAL_EXCLUDE]
    j_nino = base.index(cols.index("nino34"))
    k_controls = len(base) - 1

    tri_years, tri_data = {}, {}
    for tri in TRIMESTERS:
        yrs = [sy for sy in range(START_YEAR, END_YEAR + 1)
               if all(p in mpos for p in tri_month_pairs(tri, sy))]
        tri_years[tri] = np.array(yrs)
        tri_data[tri] = np.stack([
            stack[[mpos[p] for p in tri_month_pairs(tri, sy)]].mean(axis=0)
            for sy in yrs
        ])  # (n_years, ny, nx)

    clim = {t: tri_data[t].mean(axis=0) for t in TRIMESTERS}
    analyzable = {}
    for tri in TRIMESTERS:
        analyzable[tri] = (mask & (clim[tri] >= MIN_TRI_MM_DAY)
                           & (len(tri_years[tri]) >= MIN_YEARS))

    results = {}
    for tri in TRIMESTERS:
        yrs = tri_years[tri]
        npix = mask.size
        shape = mask.shape

        series = {}
        common = yrs
        for j, name in enumerate(cols):
            for lag in range(MAX_LAG + 1):
                s = index_trimester_mean(indices[name], TRIMESTERS[tri], lag).dropna()
                series[(j, lag)] = s
                common = np.intersect1d(common, s.index.values)
        if len(common) < MIN_YEARS:
            nan = np.full(shape, np.nan)
            results[tri] = {"r": nan, "p": nan.copy()}
            continue
        sel = np.isin(yrs, common)
        Y = tri_data[tri].reshape(len(yrs), -1)[sel]        # (n_yr, npix)
        n_yr = len(common)

        # TOTAL sweep per index: per-pixel best lag (frozen for the partial)
        Yc = Y - Y.mean(axis=0)
        sy_norm = np.sqrt((Yc**2).sum(axis=0))
        lag_best = np.zeros((len(cols), npix), dtype=int)
        for j in range(len(cols)):
            r_best = np.full(npix, np.nan)
            for lag in range(MAX_LAG + 1):
                xc = series[(j, lag)].loc[common].values.astype("float64")
                xc -= xc.mean()
                sx = np.sqrt((xc**2).sum())
                with np.errstate(invalid="ignore", divide="ignore"):
                    r = (xc @ Yc) / (sx * sy_norm)
                upd = np.isfinite(r) & (np.isnan(r_best)
                                        | (np.abs(r) > np.abs(r_best)))
                r_best[upd], lag_best[j, upd] = r[upd], lag

        # PARTIAL pass on the frozen lags, base model only (PDO left out)
        X = np.stack([
            np.stack([series[(j, lag)].loc[common].values
                      for lag in range(MAX_LAG + 1)])
            for j in range(len(cols))
        ])                                                  # (n_idx, n_lags, n_yr)
        M = np.empty((npix, n_yr, len(base)))
        for bj, j in enumerate(base):
            M[:, :, bj] = X[j][lag_best[j]]
        V = np.concatenate([M, Y.T[:, :, None]], axis=2)
        rp = _partial_vs_last(V)[:, j_nino]                 # (npix,)
        results[tri] = {
            "r": np.where(mask.reshape(-1), rp, np.nan).reshape(shape),
            "p": pearson_p(rp, n_yr, k=k_controls).reshape(shape),
        }
    return results, analyzable, x, y, mask


def _r_rgb(img, r, sig):
    """Paint the discrete bins onto an (ny, nx, 3) uint8 image."""
    def hx(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    for cond, col in [
        (sig & (r >= R_STRONG), C_POS_STRONG),
        (sig & (r >= R_MIN) & (r < R_STRONG), C_POS_MOD),
        (sig & (r <= -R_MIN) & (r > -R_STRONG), C_NEG_MOD),
        (sig & (r <= -R_STRONG), C_NEG_STRONG),
    ]:
        img[np.where(np.isfinite(r), cond, False)] = hx(col)


def _extent(x, y):
    half = float(abs(x[1] - x[0])) / 2
    return (float(x[0]) - half, float(x[-1]) + half,
            float(y[-1]) - half, float(y[0]) + half)


def _map_panel(ax, r, sig, mask, x, y, title, title_size=11):
    def hx(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    img = np.empty((*mask.shape, 3), dtype="uint8")
    img[:] = hx(C_OCEAN)
    img[mask] = hx(C_NOSIG)
    _r_rgb(img, r, sig)
    ax.imshow(img, extent=_extent(x, y), origin="upper",
              interpolation="nearest", zorder=1)
    if len(_STATE["adm1"]):
        _STATE["adm1"].boundary.plot(ax=ax, color=C_OUTLINE, linewidth=0.5,
                                     zorder=3)
    _STATE["adm0"].boundary.plot(ax=ax, color="#5A6472", linewidth=0.7, zorder=4)
    ax.set_xlim(LON())
    ax.set_ylim(LAT()[1], LAT()[0])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title, fontsize=title_size, pad=4)


# Trimester-clump labels on the main map — a port of the hnrp view's
# renderTriLabels (docs/hnrp.js): leader clustering in RENDERED-pixel space,
# seeded by the densest pixel of each (trimester, direction) clump so the label
# always sits on real signal; threshold backs off while the map is soup; a
# declutter pass keeps the first label of every key so no season is dropped.
LABEL_MIN_PX = 95    # same-key labels closer than this merge (display px)
LABEL_CLEAR_PX = 42  # any-key labels closer than this: weaker one dropped
MAX_TRI_LABELS = 12  # past this the map is soup whatever the clustering
LABEL_DENSITY_R = 3.2  # grid-cell radius for the density seeding
MAX_LABEL_KEYS = 8   # only the largest (trimester, direction) clumps get labels
MIN_KEY_PIXELS = 4   # a key smaller than this is noise, not a clump


def _draw_tri_labels(fig, ax, order, best_t, r_best, has, x, y):
    from matplotlib import patheffects
    from scipy.spatial import cKDTree

    rows, cols = np.nonzero(has)
    if not len(rows):
        return
    tri_i = best_t[rows, cols]
    sign = (r_best[rows, cols] > 0).astype(int)
    keys = tri_i * 2 + sign

    # unlike the app (whose keys are just seasons), (trimester, direction)
    # yields up to 24 keys and the first-of-each-key rule then guarantees
    # soup — so only the biggest clumps get labelled at all
    uniq, counts = np.unique(keys, return_counts=True)
    big = uniq[counts >= MIN_KEY_PIXELS]
    big = big[np.argsort(-counts[np.isin(uniq, big)])][:MAX_LABEL_KEYS]
    keep = np.isin(keys, big)
    if not keep.any():
        return
    rows, cols, tri_i, sign, keys = (a[keep] for a in
                                     (rows, cols, tri_i, sign, keys))

    # density of same-key pixels within LABEL_DENSITY_R grid cells (seed rank)
    dens = np.zeros(len(rows))
    grid_pts = np.column_stack([rows, cols]).astype(float)
    for k in np.unique(keys):
        m = keys == k
        tree = cKDTree(grid_pts[m])
        dens[m] = np.array([len(v) for v in
                            tree.query_ball_point(grid_pts[m], LABEL_DENSITY_R)])

    # rendered display coordinates (needs a draw for final layout)
    fig.canvas.draw()
    disp = ax.transData.transform(
        np.column_stack([x[cols], y[rows]]))

    idx = np.argsort(-dens)
    pts = [{"key": int(keys[i]), "p": disp[i], "lon": float(x[cols[i]]),
            "lat": float(y[rows[i]]), "dens": float(dens[i]),
            "tri": order[int(tri_i[i])], "pos": bool(sign[i])}
           for i in idx]

    def cluster_at(min_px):
        out = []
        for pt in pts:
            best, best_d = None, np.inf
            for c in out:
                if c["key"] != pt["key"]:
                    continue
                d = float(np.hypot(*(c["seed"]["p"] - pt["p"])))
                if d < best_d:
                    best_d, best = d, c
            if best is not None and best_d <= min_px:
                best["n"] += 1
            else:
                out.append({"key": pt["key"], "seed": pt, "n": 1})
        return out

    clusters = cluster_at(LABEL_MIN_PX)
    px = LABEL_MIN_PX * 2
    while len(clusters) > MAX_TRI_LABELS and px <= 400:
        clusters = cluster_at(px)
        px *= 2

    # declutter, biggest clump first; first label of each key always survives
    clusters.sort(key=lambda c: -c["n"])
    placed, shown = [], set()
    for c in clusters:
        first = c["key"] not in shown
        floor = LABEL_CLEAR_PX * (0.55 if first else 1.0)
        if any(float(np.hypot(*(p["seed"]["p"] - c["seed"]["p"]))) < floor
               for p in placed):
            continue
        placed.append(c)
        shown.add(c["key"])

    for c in placed:
        s = c["seed"]
        ax.text(s["lon"], s["lat"], s["tri"],
                color="#0D40B0" if s["pos"] else "#5A2A0A",
                fontsize=8.5, fontweight="bold", ha="center", va="center",
                zorder=6, path_effects=[
                    patheffects.withStroke(linewidth=2.2, foreground="white")])


def make_slide1(path_png: Path):
    results, analyzable, x, y, mask = compute_enso_correlations()

    # Page-style reduce: strongest significant analysable trimester per pixel
    order = list(TRIMESTERS)
    R = np.stack([results[t]["r"] for t in order])
    P = np.stack([results[t]["p"] for t in order])
    AN = np.stack([analyzable[t] for t in order])
    sig = AN & np.isfinite(R) & (P < ALPHA)
    score = np.where(sig, np.abs(R), -1.0)
    best_t = score.argmax(axis=0)
    has = score.max(axis=0) > 0
    ii, jj = np.indices(mask.shape)
    r_best = np.where(has, R[best_t, ii, jj], np.nan)

    fig = plt.figure(figsize=(13.333, 7.5))
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 4, height_ratios=[1.75, 1.0],
                          left=0.04, right=0.72, top=0.85, bottom=0.05,
                          hspace=0.18, wspace=0.08)

    ax_main = fig.add_subplot(gs[0, :])
    _map_panel(ax_main, r_best, has, mask, x, y,
               "Unique ENSO signal — strongest significant trimester per pixel",
               title_size=12)
    _draw_tri_labels(fig, ax_main, order, best_t, r_best, has, x, y)

    for k, tri in enumerate(SLIDE1_TILES):
        ax = fig.add_subplot(gs[1, k])
        s = (analyzable[tri] & np.isfinite(results[tri]["r"])
             & (results[tri]["p"] < ALPHA))
        _map_panel(ax, results[tri]["r"], s, mask, x, y, tri, title_size=10)

    fig.text(0.04, 0.955,
             f"{_STATE['name']}: how does El Niño generally affect seasonal "
             "rainfall?",
             fontsize=17, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.905,
             "Pixelwise partial correlation of rainfall with Niño3.4 (ENSO), other "
             f"climate modes held constant — ERA5 0.25° grid, {START_YEAR}–{END_YEAR}",
             fontsize=11.5, color="#444")

    lx = 0.745
    fig.text(lx, 0.82, "Partial correlation with Niño3.4", fontsize=11,
             fontweight="bold")
    legend_rows = [
        (C_POS_STRONG, f"Positive, strong (r ≥ {R_STRONG})"),
        (C_POS_MOD, f"Positive, moderate ({R_MIN} ≤ r < {R_STRONG})"),
        (C_NEG_MOD, f"Negative, moderate (−{R_STRONG} < r ≤ −{R_MIN})"),
        (C_NEG_STRONG, f"Negative, strong (r ≤ −{R_STRONG})"),
        (C_NOSIG, "No significant signal (p ≥ 0.05)"),
    ]
    for i, (col, lab) in enumerate(legend_rows):
        yy = 0.775 - i * 0.048
        fig.add_artist(plt.Rectangle((lx, yy), 0.022, 0.032, facecolor=col,
                                     edgecolor="#999", linewidth=0.4,
                                     transform=fig.transFigure))
        fig.text(lx + 0.032, yy + 0.007, lab, fontsize=9.5, color="#333")

    fig.text(lx, 0.50,
             "Brown = El Niño years tend to be drier than\n"
             "normal in that season; blue = wetter.\n\n"
             "Method (the teleconnections page's partial pass):\n"
             "• Trimester rainfall means per pixel, all 12\n"
             "   overlapping 3-month windows, 1981–2025\n"
             "• Partial r of rainfall vs Niño3.4, holding DMI\n"
             "   (IOD), TNA, TSA and AMM constant\n"
             "• Each mode at its per-pixel best lag (0–3 mo)\n"
             "• Two-tailed p < 0.05, df adjusted for controls\n"
             "• All seasons kept (no rainy-season filter)",
             fontsize=9.5, color="#333", va="top", linespacing=1.5)
    foot = "Data: ERA5 monthly precip (0.25°), NOAA PSL\nNiño3.4. Bottom row: the four quarters of the year."
    if _STATE["clipped"]:
        foot += "\nDistant territories beyond the map edge not shown."
    fig.text(lx, 0.05, foot, fontsize=8.5, color="#777", va="bottom",
             linespacing=1.4)

    fig.savefig(path_png, dpi=DPI, facecolor="white")
    plt.close(fig)
    _quantize(path_png)


# ── Slide 2: current SEAS5 forecast, app categories + skill shading ─────────────

THRESH = {"sev_rp": 3, "vsev_rp": 10, "r_mod": 0.30, "r_high": 0.50}
RAINY_TRIMESTER_PCT = 0.15   # matches export_raster_site.py
C_DV, C_DS, C_FS, C_FV = "#7B3A1A", "#C8844A", "#71B3E5", "#0D40B0"
C_OFF, C_HATCH_GREY = "#D0D0D0", "#B4B4B4"
CUBE_PATH = Path("/tmp/skill_stats_grid_detrended.nc")
LEADS = (-2, 4)   # every valid trimester of the current issuance

T, OFF, LOW, HN, MN = 0, 1, 2, 3, 4
DVH, DVM, DSH, DSM, FSH, FSM, FVH, FVM = 5, 6, 7, 8, 9, 10, 11, 12
CODE_FILL = {OFF: C_OFF, DVH: C_DV, DVM: C_DV, DSH: C_DS, DSM: C_DS,
             FSH: C_FS, FSM: C_FS, FVH: C_FV, FVM: C_FV}

_CUBE_DS = None   # opened once per process (the batch reuses it)


def _classify_app(P, R, rainy):
    """Per-pixel category code — verbatim port of export_raster_site._classify
    (masked variant, the app default)."""
    vsev_m, sev_m = 100 / THRESH["vsev_rp"], 100 / THRESH["sev_rp"]
    valid = np.isfinite(P) & np.isfinite(R)
    off = valid & ~rainy
    skill = valid & ~off
    low = skill & (R < THRESH["r_mod"])
    ok = skill & (R >= THRESH["r_mod"])
    drought = P < 50
    high = R >= THRESH["r_high"]
    vsev = (P <= vsev_m) | (P >= 100 - vsev_m)
    sev = ((P > vsev_m) & (P <= sev_m)) | ((P >= 100 - sev_m) & (P < 100 - vsev_m))
    none = ok & ~vsev & ~sev

    code = np.zeros(P.shape, dtype=np.uint8)
    code[off] = OFF
    code[low] = LOW
    code[none & high] = HN
    code[none & ~high] = MN
    code[ok & vsev & drought & high] = DVH
    code[ok & vsev & drought & ~high] = DVM
    code[ok & sev & drought & high] = DSH
    code[ok & sev & drought & ~high] = DSM
    code[ok & sev & ~drought & high] = FSH
    code[ok & sev & ~drought & ~high] = FSM
    code[ok & vsev & ~drought & high] = FVH
    code[ok & vsev & ~drought & ~high] = FVM
    return code


def load_issued() -> pd.Timestamp:
    """The latest issuance, straight from the app's own export."""
    d = json.loads((REPO / "docs" / "data" / "forecast.json").read_text())
    return pd.Timestamp(int(d["issued_year"]), int(d["issued_month"]), 1)


def _cube():
    global _CUBE_DS
    if _CUBE_DS is None:
        import xarray as xr

        if not CUBE_PATH.exists():
            import ocha_stratus as stratus

            sys.path.insert(0, str(REPO))
            from src.constants import PROJECT_PREFIX

            print("Downloading detrended skill cube from DEV blob (one-time)...")
            CUBE_PATH.write_bytes(stratus.load_blob_data(
                f"{PROJECT_PREFIX}/processed/raster/skill_stats_grid_detrended.nc",
                stage="dev"))
        _CUBE_DS = xr.open_dataset(CUBE_PATH).load()
    return _CUBE_DS


def load_skill_cube_window(issued: pd.Timestamp):
    """Everything slide 2 draws, from the detrended skill cube (the same file
    the app's raster view is exported from), windowed to the country."""
    sys.path.insert(0, str(REPO))
    from src.constants import TRIMESTERS as TRI_MONTHS_MAP
    from src.skill import season_year_for, trimester_lead
    from src.skill_raster import rainy_from_cube

    im = int(issued.month)
    lo, hi = LEADS
    tris = sorted((t for t in TRI_MONTHS_MAP
                   if lo <= trimester_lead(im, TRI_MONTHS_MAP[t]) <= hi),
                  key=lambda t: trimester_lead(im, TRI_MONTHS_MAP[t]))

    ds = _cube().sel(x=slice(*LON()), y=slice(*LAT()))
    rainy_all = rainy_from_cube(ds, RAINY_TRIMESTER_PCT)
    out = {}
    for tri in tris:
        months = TRI_MONTHS_MAP[tri]
        sel = dict(issued_month=im, trimester=tri)
        cfy = int(ds["current_forecast_year"].sel(**sel))
        expect = season_year_for(im, int(issued.year), months)
        assert cfy == expect, (
            f"skill cube {tri} holds {cfy}, expected {expect} — "
            "rerun compute_skill_raster")
        out[tri] = {
            "P": ds["forecast_percentile"].sel(**sel).values,
            "R": ds["pearson_r"].sel(**sel).values,
            "rainy": rainy_all.sel(trimester=tri).values.astype(bool),
            "clim_mm": np.expm1(ds["era5_mean"].sel(**sel).values),
            "current_mm": np.expm1(ds["current_forecast_mean"].sel(**sel).values),
            "season_year": cfy,
            "lead": trimester_lead(im, months),
        }
    return out, ds["x"].values, ds["y"].values


def load_app_country_forecast(issued: pd.Timestamp) -> dict:
    """The alerts app's own country-level numbers for this issuance — the same
    pct / directional RP / rainy flags the main app page shows."""
    f = REPO / "docs" / "data" / "forecasts" / f"{issued:%Y-%m}.json"
    d = json.loads(f.read_text())
    assert (d["issued_year"], d["issued_month"]) == (issued.year, issued.month)
    return d["data"][_STATE["iso3"]]


def _rp_bin_color(p: float) -> str:
    """Country-mean percentile -> the app's RP category colour (bar chart)."""
    vsev_m, sev_m = 100 / THRESH["vsev_rp"], 100 / THRESH["sev_rp"]
    if p <= vsev_m:
        return C_DV
    if p <= sev_m:
        return C_DS
    if p >= 100 - vsev_m:
        return C_FV
    if p >= 100 - sev_m:
        return C_FS
    return "#F5F0EC"


def _ordinal(n: float) -> str:
    n = int(round(n))
    suf = "th" if 10 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def _season_label(tri: str, season_year: int) -> str:
    months = _TRI_MONTHS[tri]
    wraps = 1 in months and 12 in months
    return f"{tri} {season_year}" + (f"/{(season_year + 1) % 100:02d}" if wraps else "")


def make_slide2(path_png: Path):
    issued = load_issued()
    cube, cx, cy = load_skill_cube_window(issued)
    app_vals = load_app_country_forecast(issued)
    nino = load_nino34().dropna()
    nino_last, nino_when = float(nino.iloc[-1]), nino.index[-1]

    order = sorted(cube, key=lambda t: cube[t]["lead"])
    cmask = land_mask(_STATE["adm0"], cx, cy)

    # driest = the rainy, dry-side trimester with the highest app RP
    dry = [t for t in order
           if app_vals[t]["rainy"] and app_vals[t]["pct"] < 50
           and app_vals[t]["rp"] is not None and app_vals[t]["rp"] >= 1.5]
    worst = max(dry, key=lambda t: app_vals[t]["rp"]) if dry else None

    def hx(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    fig = plt.figure(figsize=(13.333, 7.5))
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 4, left=0.03, right=0.70, top=0.80, bottom=0.05,
                          hspace=0.26, wspace=0.06)

    for k, tri in enumerate(order):
        ax = fig.add_subplot(gs[k // 4, k % 4])
        d = cube[tri]
        code = _classify_app(d["P"], d["R"], d["rainy"])
        code[~cmask] = 0   # off-country -> white

        img = np.full((*code.shape, 3), 255, dtype="uint8")
        for c, col in CODE_FILL.items():
            img[code == c] = hx(col)
        ax.imshow(img, extent=_extent(cx, cy), origin="upper",
                  interpolation="nearest", zorder=1)
        # skill shading, as the app: white "\" = moderate skill on an alert,
        # grey "/" = no signal at moderate skill, grey "X" = low skill
        for codes, hatch, colr in (
            ({DVM, DSM, FSM, FVM}, "\\\\\\", "#FFFFFF"),
            ({MN}, "///", C_HATCH_GREY),
            ({LOW}, "xxx", C_HATCH_GREY),
        ):
            m = np.isin(code, list(codes)).astype("float32")
            if m.any() and m.shape[0] > 1 and m.shape[1] > 1:
                with plt.rc_context({"hatch.linewidth": 0.5,
                                     "hatch.color": colr}):
                    ax.contourf(cx, cy, m, levels=[0.5, 1.5], colors="none",
                                hatches=[hatch], zorder=2)
        if len(_STATE["adm1"]):
            _STATE["adm1"].boundary.plot(ax=ax, color="#6B7683",
                                         linewidth=0.5, zorder=3)
        _STATE["adm0"].boundary.plot(ax=ax, color="#3E4650", linewidth=0.7,
                                     zorder=4)
        ax.set_xlim(LON())
        ax.set_ylim(LAT()[1], LAT()[0])
        ax.set_aspect("equal")
        ax.set_axis_off()

        v = app_vals[tri]
        when = f"lead {d['lead']} mo" if d["lead"] >= 1 else "in season"
        if not v["rainy"]:
            sub, scol = "off-season", "#999"
        elif v["rp"] is None or v["rp"] < 1.5:
            sub, scol = "≈ normal", "#777"
        else:
            side = "dry" if v["pct"] < 50 else "wet"
            sub = f"1-in-{v['rp']:.0f} {side} · {_ordinal(v['pct'])} pct"
            scol = {"dry": "#7B3A1A", "wet": "#0D40B0"}[side]
        ax.set_title(
            f"{_season_label(tri, d['season_year'])} — {when}\n{sub}",
            fontsize=9.5, pad=3, linespacing=1.25,
            color=scol if tri == worst else ("#1a1a1a" if v["rainy"] else "#777"),
            fontweight="bold" if tri == worst else "normal")

    # bar chart in the last grid slot: country-mean climatology vs forecast
    C_CLIM_BAR = "#2B2B2B"
    bax = fig.add_subplot(gs[1, 3])
    xs_pos = np.arange(len(order))
    clim_means = [float(np.nanmean(np.where(cmask, cube[t]["clim_mm"], np.nan)))
                  for t in order]
    fc_means = [float(np.nanmean(np.where(cmask, cube[t]["current_mm"], np.nan)))
                for t in order]
    fc_colors = [_rp_bin_color(app_vals[t]["pct"]) if app_vals[t]["rainy"]
                 else C_OFF for t in order]
    bax.bar(xs_pos - 0.19, clim_means, width=0.36, color=C_CLIM_BAR, zorder=2)
    bax.bar(xs_pos + 0.19, fc_means, width=0.36, color=fc_colors,
            edgecolor="#888", linewidth=0.4, zorder=2)
    bax.set_xticks(xs_pos)
    bax.set_xticklabels([t for t in order], fontsize=7.5)
    for t, tick in zip(order, bax.get_xticklabels()):
        if t == worst:
            tick.set_color("#7B3A1A")
            tick.set_fontweight("bold")
    top = max([v for v in clim_means + fc_means if np.isfinite(v)] or [1.0])
    bax.set_ylim(0, top * 1.15)
    bax.tick_params(axis="y", labelsize=7.5, length=2)
    bax.tick_params(axis="x", length=0)
    for spine in ("top", "right"):
        bax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        bax.spines[spine].set_color("#CCC")
    bax.grid(axis="y", color="#EAEAEA", linewidth=0.6, zorder=0)
    bax.set_title("Country-mean rainfall (mm/day)\nblack = normal · coloured = forecast",
                  fontsize=9.5, pad=4, linespacing=1.25, color="#1a1a1a")

    fig.text(0.04, 0.945,
             f"{_STATE['name']}: what seasonal rainfall is predicted this year "
             f"({issued:%B}-issued forecast)?",
             fontsize=17, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.895,
             f"SEAS5 (issued {issued:%B %Y}) accounts for El Niño and every other "
             "driver · return-period categories vs the 1981–"
             f"{cube[order[0]]['season_year'] - 1} hindcast · skill-shaded · ADM1",
             fontsize=11, color="#444")

    # right column: legend + notes
    lx = 0.735

    def _swatch(xx, yy, fill, hatch=None, hatch_color=None, w=0.020, h=0.028):
        if fill is not None:
            fig.add_artist(plt.Rectangle((xx, yy), w, h, facecolor=fill,
                                         linewidth=0, transform=fig.transFigure))
        if hatch:
            with plt.rc_context({"hatch.linewidth": 0.5}):
                fig.add_artist(plt.Rectangle((xx, yy), w, h, facecolor="none",
                                             hatch=hatch, edgecolor=hatch_color,
                                             linewidth=0,
                                             transform=fig.transFigure))
        fig.add_artist(plt.Rectangle((xx, yy), w, h, facecolor="none",
                                     edgecolor="#999", linewidth=0.4,
                                     transform=fig.transFigure))

    legend_groups = [
        ("Forecast (high skill):", [
            (C_DV, None, None, "Drought — ≥ 10-yr return period"),
            (C_DS, None, None, "Drought — 3–10-yr return period"),
            ("#FFFFFF", None, None, "Roughly normal"),
            (C_FS, None, None, "Flood — 3–10-yr return period"),
            (C_FV, None, None, "Flood — ≥ 10-yr return period"),
        ]),
        ("Other:", [
            ("#FFFFFF", "///", C_HATCH_GREY, "Roughly normal (mod skill)"),
            ("#FFFFFF", "xxx", C_HATCH_GREY, "Low skill"),
            (C_OFF, None, None, "Outside rainy season"),
        ]),
    ]
    yy = 0.845
    for title, rows in legend_groups:
        fig.text(lx, yy, title, fontsize=10.5, fontweight="bold")
        yy -= 0.042
        for fill, hatch, hcol, lab in rows:
            _swatch(lx, yy, fill, hatch, hcol)
            fig.text(lx + 0.030, yy + 0.005, lab, fontsize=9, color="#333")
            yy -= 0.038
        yy -= 0.012

    notes = ("White hatch on a colour = moderate skill.\n"
             "Tile subtitles: the country-level return period\n"
             "and percentile from the alerts app.\n")
    if worst is not None:
        v = app_vals[worst]
        notes += (f"Driest outlook: {_season_label(worst, cube[worst]['season_year'])} "
                  f"(1-in-{v['rp']:.0f} dry).\n")
    if min(cube[t]["lead"] for t in order) < 1:
        notes += "In season = observed months + forecast.\n"
    notes += f"Niño3.4: {nino_last:+.1f} °C in {nino_when:%b %Y}."
    fig.text(lx, 0.40, notes, fontsize=9, color="#333", va="top",
             linespacing=1.45)

    foot = f"Data: ECMWF SEAS5 · Boundaries: {_STATE['iso3']} ADM1 (COD)"
    if _STATE["clipped"]:
        foot += "\nDistant territories beyond the map edge not shown."
    fig.text(lx, 0.015, foot, fontsize=8.5, color="#777", va="bottom",
             linespacing=1.4)

    fig.savefig(path_png, dpi=DPI, facecolor="white")
    plt.close(fig)
    _quantize(path_png)


# ── output helpers ──────────────────────────────────────────────────────────────


def _quantize(path_png: Path) -> None:
    """Palette-quantize the flat-colour slide PNG (60-70% smaller)."""
    from PIL import Image

    img = Image.open(path_png).convert("RGB")
    img.quantize(colors=256, method=Image.MEDIANCUT,
                 dither=Image.NONE).save(path_png, optimize=True)


def make_pdf(iso3: str) -> None:
    """Two-page PDF from the slide PNGs (JPEG-in-PDF keeps it small)."""
    from PIL import Image

    pages = [Image.open(OUT_DIR / f"{iso3}_slide{i}.png").convert("RGB")
             for i in (1, 2)]
    pages[0].save(OUT_DIR / f"{iso3}.pdf", save_all=True,
                  append_images=pages[1:], resolution=DPI)


def write_manifest(reg: dict[str, str]) -> None:
    """countries.json for the dropdown: only countries whose slides exist."""
    entries = [{"iso3": iso, "name": name}
               for iso, name in sorted(reg.items(), key=lambda kv: kv[1])
               if (OUT_DIR / f"{iso}_slide1.png").exists()
               and (OUT_DIR / f"{iso}_slide2.png").exists()]
    (OUT_DIR.parent / "countries.json").write_text(
        json.dumps(entries, indent=0) + "\n")
    print(f"countries.json: {len(entries)} countries")


def build_country(iso3: str, name: str, force: bool = False,
                  skip_era5: bool = False, skip_seas5: bool = False) -> None:
    s1, s2 = OUT_DIR / f"{iso3}_slide1.png", OUT_DIR / f"{iso3}_slide2.png"
    pdf = OUT_DIR / f"{iso3}.pdf"
    if not force and s1.exists() and s2.exists() and pdf.exists():
        print(f"{iso3}: exists, skipping (use --force to rebuild)")
        return
    set_country(iso3, name)
    if not skip_era5 and (force or not s1.exists()):
        make_slide1(s1)
    if not skip_seas5 and (force or not s2.exists()):
        make_slide2(s2)
    if s1.exists() and s2.exists():
        make_pdf(iso3)
    print(f"{iso3} ({name}): done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="all",
                    help="ISO3 code, or 'all' for every app country")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-era5", action="store_true")
    ap.add_argument("--skip-seas5", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    reg = registry()
    targets = (list(reg) if args.country.lower() == "all"
               else [args.country.upper()])
    failures = []
    for iso in targets:
        if iso not in reg:
            raise SystemExit(f"{iso}: not in the app's country list")
        try:
            build_country(iso, reg[iso], force=args.force,
                          skip_era5=args.skip_era5, skip_seas5=args.skip_seas5)
        except SkipCountry as e:
            print(f"SKIP {e}")
            failures.append((iso, str(e)))
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {iso}: {e}")
            traceback.print_exc()
            failures.append((iso, str(e)))
    write_manifest(reg)
    if failures:
        print(f"\n{len(failures)} countries failed/skipped:")
        for iso, msg in failures:
            print(f"  {iso}: {msg.splitlines()[0][:100]}")
