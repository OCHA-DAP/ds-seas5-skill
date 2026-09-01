"""Two slides per country (Papua New Guinea, Timor-Leste, Niger), published
under pages/{png,tls,ner}-enso/.

Slide 1 — ERA5 rainfall x ENSO (Nino3.4) teleconnection, pixelwise at the native
0.25 degree ERA5 grid: the ds-teleconnections page's PARTIAL pass (Nino3.4's
unique signal holding DMI/TNA/TSA/AMM constant, PDO excluded from controls,
per-pixel best lags frozen from the total sweep, p<0.05, the discrete blue/brown
|r| bins) zoomed to PNG instead of the global viewport.

Slide 2 — the current SEAS5 forecast, pixelwise with ADM1 boundaries overlaid,
one tile per upcoming trimester (all four fully covered by the latest issuance),
in the alerts app's own colours: drought/flood 3-yr and 10-yr return-period
categories from the detrended skill cube's forecast percentile, skill-shaded
exactly as the app (solid = high skill, white hatch = moderate, cross-hatch =
low, grey = off-season). Plus a bar chart of country-mean hindcast climatology
vs this forecast (mm/day). The tile with the lowest country-mean percentile is
tagged as the driest.

Data (per methods/reuse-published-stats.md, nothing recomputed that a product
already publishes):
  - ERA5 monthly mm/day COGs (prod raster blob) — read from the
    ds-teleconnections local cache when present, else fetched.
  - Six NOAA PSL climate indices (nino34, dmi, tna, tsa, amm, pdo; cached).
  - The detrended skill cube (dev blob) for all pixel fields + mm values, and
    docs/data/forecasts/<issued>.json for country pct/RP — both vintage-checked.
  - ADM1 from the prod polygon blob container.

Run:  uv run python analysis/png_enso_slides.py [--country png|tls|ner]
                                                [--skip-era5] [--skip-seas5]
Writes pages/<key>-enso/slide{1,2}.png and pages/<key>-enso/<key>_enso_slides.pdf.
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
from matplotlib.backends.backend_pdf import PdfPages
from scipy import stats

REPO = Path(__file__).resolve().parents[1]
CACHE = REPO / "analysis" / "_png_enso_cache"

COUNTRIES = {
    # `leads`: trimester-lead window shown on slide 2 (negative = in-season,
    # mixed obs+forecast — the cube aggregates those correctly).
    # `tiles`: the four per-trimester panels on slide 1.
    # PNG window covers mainland + Bismarck Archipelago + Bougainville
    "png": dict(iso3="PNG", name="Papua New Guinea",
                lon=(140.5, 156.75), lat=(0.25, -12.0),
                leads=(1, 4), tiles=("DJF", "MAM", "JAS", "OND")),
    # TLS window covers the mainland, the Oecusse enclave and Atauro/Jaco
    "tls": dict(iso3="TLS", name="Timor-Leste",
                lon=(123.6, 127.8), lat=(-7.6, -10.1),
                leads=(1, 4), tiles=("DJF", "MAM", "JAS", "OND")),
    # NER: single JJA-JAS rainy season, in-season from an Aug issuance —
    # show the season (leads -2..1) rather than the dry SON-DJF window
    "ner": dict(iso3="NER", name="Niger",
                lon=(0.0, 16.2), lat=(23.7, 11.4),
                leads=(-2, 1), tiles=("JJA", "JAS", "ASO", "SON")),
}
KEY = "png"
COUNTRY = COUNTRIES[KEY]
OUT_DIR = REPO / "pages" / f"{KEY}-enso"
LON, LAT = COUNTRY["lon"], COUNTRY["lat"]   # lat = (north, south)


def set_country(key: str) -> None:
    global KEY, COUNTRY, OUT_DIR, LON, LAT
    KEY = key
    COUNTRY = COUNTRIES[key]
    OUT_DIR = REPO / "pages" / f"{key}-enso"
    LON, LAT = COUNTRY["lon"], COUNTRY["lat"]

# ds-teleconnections cache of the global ERA5 monthly stack (0.25deg, 1981-2025)
TELE_CACHE = Path.home() / "OCHA/repos/ds-teleconnections/cache/era5_pixel"

START_YEAR, END_YEAR = 1981, 2025
MIN_YEARS = 25
MAX_LAG = 3            # l3, the teleconnections page default
ALPHA = 0.05
MIN_TRI_MM_DAY = 0.25  # hyper-arid cut (irrelevant for PNG but kept for parity)

TRIMESTERS = {
    "NDJ": 1, "DJF": 2, "JFM": 3, "FMA": 4, "MAM": 5, "AMJ": 6,
    "MJJ": 7, "JJA": 8, "JAS": 9, "ASO": 10, "SON": 11, "OND": 12,
}
_TRI_MONTHS = {t: [((e - 3 + i) % 12) + 1 for i in range(3)]
               for t, e in ((t, TRIMESTERS[t]) for t in TRIMESTERS)}
_ANNUAL = ("DJF", "MAM", "JAS", "OND")

# Discrete 2-bin correlation colours — identical to the teleconnections page
R_STRONG, R_MIN = 0.45, 0.30
C_POS_STRONG, C_POS_MOD = "#0D40B0", "#71B3E5"
C_NEG_MOD, C_NEG_STRONG = "#C8844A", "#7B3A1A"
C_NOSIG, C_OCEAN, C_OUTLINE = "#E8E8E8", "#FFFFFF", "#9AA3B0"

# Same index set + partial-control convention as the teleconnections page:
# every mode enters at its own per-pixel best lag; PDO is kept out of the
# control set (cfg["partial_exclude"] there) but nino34's partial is computed
# within the base model {nino34, dmi, tna, tsa, amm}.
INDEX_SOURCES = {
    "nino34": "https://psl.noaa.gov/data/correlation/nina34.anom.data",
    "dmi":    "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data",
    "tna":    "https://psl.noaa.gov/data/correlation/tna.data",
    "tsa":    "https://psl.noaa.gov/data/correlation/tsa.data",
    "amm":    "https://psl.noaa.gov/data/correlation/amm.data",
    "pdo":    "https://psl.noaa.gov/data/correlation/pdo.data",
}
PARTIAL_EXCLUDE = {"pdo"}


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


def load_png_adm1() -> gpd.GeoDataFrame:
    """ADM1 polygons from the prod polygon blob container, cached as GeoJSON."""
    iso = COUNTRY["iso3"].lower()
    f = CACHE / f"{iso}_adm1.geojson"
    if f.exists():
        return gpd.read_file(f)
    import ocha_stratus as stratus

    gdf = stratus.load_shp_from_blob(
        f"{iso}_shp.zip", shapefile=f"{iso}_adm1.shp",
        stage="prod", container_name="polygon",
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(f, driver="GeoJSON")
    return gdf


def load_png_adm0() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(REPO / "analysis" / "_ne_50m_countries.gpkg")
    col = next(c for c in ("iso3", "ISO_A3", "ADM0_A3", "SOV_A3") if c in gdf.columns)
    return gdf[gdf[col] == COUNTRY["iso3"]]


# ── ERA5 monthly stack for the PNG window ───────────────────────────────────────


def era5_png_stack() -> tuple[np.ndarray, list[tuple[int, int]], np.ndarray, np.ndarray]:
    """(n_months, ny, nx) mm/day for the PNG window + (year, month) list + coords."""
    f = CACHE / f"era5_{KEY}.npz"
    if f.exists():
        z = np.load(f)
        return z["stack"], [tuple(t) for t in z["ym"]], z["x"], z["y"]

    meta_path = TELE_CACHE / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        x_all, y_all = np.asarray(meta["x"]), np.asarray(meta["y"])
        ym = [tuple(t) for t in meta["ym"]]
        xi = np.where((x_all >= LON[0]) & (x_all <= LON[1]))[0]
        yi = np.where((y_all <= LAT[0]) & (y_all >= LAT[1]))[0]
        big = np.load(TELE_CACHE / "monthly.npy", mmap_mode="r")
        stack = np.asarray(big[:, yi[0]:yi[-1] + 1, xi[0]:xi[-1] + 1], dtype="float32")
        x, y = x_all[xi], y_all[yi]
    else:  # fall back to windowed reads from the prod raster blob
        import ocha_stratus as stratus

        container = stratus.get_container_client("raster", stage="prod")
        import re

        date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
        avail = {date_re.search(b.name).group(0): b.name
                 for b in container.list_blobs(name_starts_with="era5/monthly/processed/")
                 if date_re.search(b.name)}
        ym = [(yr, m) for yr in range(START_YEAR, END_YEAR + 1) for m in range(1, 13)
              if f"{yr}-{m:02d}-01" in avail]
        probe = stratus.open_blob_cog(avail[f"{ym[0][0]}-{ym[0][1]:02d}-01"],
                                      container_name="raster", container_client=container)
        xs, ys = probe.x.values, probe.y.values
        xi = np.where((xs >= LON[0]) & (xs <= LON[1]))[0]
        yi = np.where((ys <= LAT[0]) & (ys >= LAT[1]))[0]
        x, y = xs[xi], ys[yi]
        stack = np.empty((len(ym), len(y), len(x)), dtype="float32")
        for i, (yr, m) in enumerate(ym):
            da = stratus.open_blob_cog(avail[f"{yr}-{m:02d}-01"],
                                       container_name="raster", container_client=container)
            stack[i] = da.isel(band=0, y=slice(yi[0], yi[-1] + 1),
                               x=slice(xi[0], xi[-1] + 1)).values

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


# ── Slide 1: pixelwise ERA5 x Nino3.4 ───────────────────────────────────────────


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

    Returns (results, rainy, x, y, mask) where results[tri] = dict(r, p) each an
    (ny, nx) array (partial r / p; NaN off-land), rainy[tri] the analysable mask.
    """
    stack, ym, x, y = era5_png_stack()
    adm0 = load_png_adm0()
    mask = land_mask(adm0, x, y)
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
        cube = np.stack([
            stack[[mpos[p] for p in tri_month_pairs(tri, sy)]].mean(axis=0)
            for sy in yrs
        ])  # (n_years, ny, nx)
        tri_data[tri] = cube

    # NOTE: deliberately NOT applying the global page's rainy-season filter
    # (trimester >= 25% of annual): PNG's El Nino drought signal peaks in its
    # climatologically drier JAS-OND months, which that filter would mask.
    # The aridity cut and min-years requirement are kept.
    clim = {t: tri_data[t].mean(axis=0) for t in TRIMESTERS}
    rainy = {}
    for tri in TRIMESTERS:
        rainy[tri] = (mask & (clim[tri] >= MIN_TRI_MM_DAY)
                      & (len(tri_years[tri]) >= MIN_YEARS))

    results = {}
    for tri in TRIMESTERS:
        yrs = tri_years[tri]
        npix = mask.size
        shape = mask.shape

        # one shared year set per trimester: seasons where every index exists at
        # every candidate lag, so the frozen per-cell lag mix is always aligned
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
    return results, rainy, x, y, mask


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


def _map_panel(ax, r, sig, mask, x, y, adm1, adm0, title, title_size=11):
    def hx(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    img = np.empty((*mask.shape, 3), dtype="uint8")
    img[:] = hx(C_OCEAN)
    img[mask] = hx(C_NOSIG)
    _r_rgb(img, r, sig)
    ax.imshow(img, extent=_extent(x, y), origin="upper",
              interpolation="nearest", zorder=1)
    adm1.boundary.plot(ax=ax, color=C_OUTLINE, linewidth=0.5, zorder=3)
    adm0.boundary.plot(ax=ax, color="#5A6472", linewidth=0.7, zorder=4)
    ax.set_xlim(LON)
    ax.set_ylim(LAT[1], LAT[0])
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title, fontsize=title_size, pad=4)


def make_slide1(path_png: Path):
    results, rainy, x, y, mask = compute_enso_correlations()
    adm1, adm0 = load_png_adm1(), load_png_adm0()

    # Page-style reduce: strongest significant rainy trimester per pixel
    order = list(TRIMESTERS)
    R = np.stack([results[t]["r"] for t in order])
    P = np.stack([results[t]["p"] for t in order])
    RY = np.stack([rainy[t] for t in order])
    sig = RY & np.isfinite(R) & (P < ALPHA)
    score = np.where(sig, np.abs(R), -1.0)
    best_t = score.argmax(axis=0)
    has = score.max(axis=0) > 0
    ii, jj = np.indices(mask.shape)
    r_best = np.where(has, R[best_t, ii, jj], np.nan)

    fig = plt.figure(figsize=(13.333, 7.5))
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 4, height_ratios=[1.75, 1.0],
                          left=0.04, right=0.72, top=0.86, bottom=0.05,
                          hspace=0.18, wspace=0.08)

    ax_main = fig.add_subplot(gs[0, :])
    _map_panel(ax_main, r_best, has, mask, x, y, adm1, adm0,
               "Unique ENSO signal — strongest significant trimester per pixel",
               title_size=12)

    for k, tri in enumerate(COUNTRY["tiles"]):
        ax = fig.add_subplot(gs[1, k])
        s = rainy[tri] & np.isfinite(results[tri]["r"]) & (results[tri]["p"] < ALPHA)
        _map_panel(ax, results[tri]["r"], s, mask, x, y, adm1, adm0, tri,
                   title_size=10)

    fig.text(0.04, 0.955, f"{COUNTRY['name']} — ERA5 rainfall × ENSO teleconnection",
             fontsize=19, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.905,
             "Pixelwise partial correlation of trimester rainfall with Niño3.4 — the other "
             f"climate modes held constant — ERA5 0.25° grid, {START_YEAR}–{END_YEAR}",
             fontsize=11.5, color="#444")

    # Legend + reading notes
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
             "Method (the teleconnections page's partial pass):\n"
             "• Trimester rainfall means per pixel, all 12\n"
             "   overlapping 3-month windows\n"
             "• Partial r of rainfall vs Niño3.4, holding DMI\n"
             "   (IOD), TNA, TSA and AMM constant (PDO kept\n"
             "   out of the controls, as on the page)\n"
             "• Each mode enters at its per-pixel best lag\n"
             "   (0–3 mo) frozen from the total sweep\n"
             "• Two-tailed p < 0.05, df adjusted for controls\n"
             "• Unlike the global page, all seasons are kept\n"
             "   (no rainy-season filter), so dry-season ENSO\n"
             "   signals stay visible\n\n"
             "Negative (brown) = El Niño → drier than normal.",
             fontsize=9.5, color="#333", va="top", linespacing=1.5)
    fig.text(lx, 0.06, "Data: ERA5 monthly precip (0.25°), NOAA PSL\n"
                       "Niño3.4. Bottom row: selected trimesters.",
             fontsize=8.5, color="#777", va="bottom", linespacing=1.4)

    fig.savefig(path_png, dpi=200, facecolor="white")
    plt.close(fig)
    return fig


# ── Slide 2: current SEAS5 forecast, pixelwise, tiled trimesters ────────────────
#
# Colour scheme + skill shading identical to the alerts app raster view
# (pipeline/export_raster_site.py + docs/raster/app.js): drought/flood 3-yr and
# 10-yr return-period categories from the detrended skill cube's forecast
# percentile, solid where skill r >= 0.5, white-hatched where 0.30-0.50, grey
# cross-hatch where r < 0.30, light grey outside the rainy season.

THRESH = {"sev_rp": 3, "vsev_rp": 10, "r_mod": 0.30, "r_high": 0.50}
RAINY_TRIMESTER_PCT = 0.15   # matches export_raster_site.py
C_DV, C_DS, C_FS, C_FV = "#7B3A1A", "#C8844A", "#71B3E5", "#0D40B0"
C_OFF, C_HATCH_GREY = "#D0D0D0", "#B4B4B4"
CUBE_PATH = Path("/tmp/skill_stats_grid_detrended.nc")

T, OFF, LOW, HN, MN = 0, 1, 2, 3, 4
DVH, DVM, DSH, DSM, FSH, FSM, FVH, FVM = 5, 6, 7, 8, 9, 10, 11, 12
CODE_FILL = {OFF: C_OFF, DVH: C_DV, DVM: C_DV, DSH: C_DS, DSM: C_DS,
             FSH: C_FS, FSM: C_FS, FVH: C_FV, FVM: C_FV}


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


def load_skill_cube_png(issued: pd.Timestamp):
    """Everything slide 2 draws, from the detrended skill cube (the same file
    the app's raster view is exported from): per-trimester forecast percentile,
    skill r, rainy mask, plus mm-scale climatology and bias-adjusted current
    forecast (expm1 of the cube's log-space fields). Trimesters selected by the
    country's lead window; each combo's vintage is asserted against `issued`."""
    import sys

    sys.path.insert(0, str(REPO))
    import xarray as xr

    if not CUBE_PATH.exists():
        import ocha_stratus as stratus
        from src.constants import PROJECT_PREFIX

        print("Downloading detrended skill cube from DEV blob (one-time)...")
        CUBE_PATH.write_bytes(stratus.load_blob_data(
            f"{PROJECT_PREFIX}/processed/raster/skill_stats_grid_detrended.nc",
            stage="dev"))
    from src.constants import TRIMESTERS as TRI_MONTHS_MAP
    from src.skill import season_year_for, trimester_lead
    from src.skill_raster import rainy_from_cube

    im = int(issued.month)
    lo, hi = COUNTRY["leads"]
    tris = sorted((t for t in TRI_MONTHS_MAP
                   if lo <= trimester_lead(im, TRI_MONTHS_MAP[t]) <= hi),
                  key=lambda t: trimester_lead(im, TRI_MONTHS_MAP[t]))

    ds = xr.open_dataset(CUBE_PATH).sel(x=slice(*LON), y=slice(*LAT))
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
    pct / directional RP / rainy flags the main app page shows (adm0 zonal
    stats, detrended, exported by pipeline/export_static_site.py)."""
    f = REPO / "docs" / "data" / "forecasts" / f"{issued:%Y-%m}.json"
    d = json.loads(f.read_text())
    assert (d["issued_year"], d["issued_month"]) == (issued.year, issued.month)
    return d["data"][COUNTRY["iso3"]]


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
    cube, cx, cy = load_skill_cube_png(issued)
    nino = load_nino34().dropna()
    nino_last, nino_when = float(nino.iloc[-1]), nino.index[-1]
    adm1, adm0 = load_png_adm1(), load_png_adm0()

    order = sorted(cube, key=lambda t: cube[t]["lead"])
    # the cube extends over ocean and neighbouring countries; restrict to ours
    cmask = land_mask(adm0, cx, cy)
    means = {t: float(np.nanmean(np.where(cmask, cube[t]["P"], np.nan)))
             for t in order}
    worst = min(means, key=means.get)

    def hx(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    fig = plt.figure(figsize=(13.333, 7.5))
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 2, left=0.03, right=0.66, top=0.82, bottom=0.05,
                          hspace=0.24, wspace=0.06)

    for k, tri in enumerate(order):
        ax = fig.add_subplot(gs[k // 2, k % 2])
        d = cube[tri]
        code = _classify_app(d["P"], d["R"], d["rainy"])
        code[~cmask] = 0   # off-PNG -> transparent/white

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
            if not m.any():
                continue
            with plt.rc_context({"hatch.linewidth": 0.5, "hatch.color": colr}):
                ax.contourf(cx, cy, m, levels=[0.5, 1.5], colors="none",
                            hatches=[hatch], zorder=2)
        adm1.boundary.plot(ax=ax, color="#6B7683", linewidth=0.55, zorder=3)
        adm0.boundary.plot(ax=ax, color="#3E4650", linewidth=0.8, zorder=4)
        ax.set_xlim(LON)
        ax.set_ylim(LAT[1], LAT[0])
        ax.set_aspect("equal")
        ax.set_axis_off()
        tag = "  ← driest overall" if tri == worst else ""
        when = f"lead {d['lead']} mo" if d["lead"] >= 1 else "in season"
        ax.set_title(
            f"{_season_label(tri, d['season_year'])} — {when} — "
            f"{_ordinal(means[tri])} pct{tag}",
            fontsize=10.5, pad=4,
            color="#7B3A1A" if tri == worst else "#1a1a1a",
            fontweight="bold" if tri == worst else "normal")

    fig.text(0.04, 0.945, f"{COUNTRY['name']} — current SEAS5 seasonal forecast",
             fontsize=19, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.895,
             f"Issued {issued:%B %Y} · drought/flood return-period categories vs the "
             f"same-issuance SEAS5 hindcast (1981–{cube[order[0]]['season_year'] - 1}) "
             "· skill-shaded · native 0.4° grid · ADM1 boundaries",
             fontsize=11, color="#444")

    # right column: RP category legend (app colours), climatology bars, notes
    lx = 0.70

    def _swatch(xx, yy, fill, hatch=None, hatch_color=None, w=0.020, h=0.028):
        if fill is not None:
            fig.add_artist(plt.Rectangle((xx, yy), w, h, facecolor=fill,
                                         linewidth=0, transform=fig.transFigure))
        if hatch:
            with plt.rc_context({"hatch.linewidth": 0.5}):
                fig.add_artist(plt.Rectangle((xx, yy), w, h, facecolor="none",
                                             hatch=hatch, edgecolor=hatch_color,
                                             linewidth=0, transform=fig.transFigure))
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

    # climatology as a bar chart: country-mean hindcast vs this forecast, mm/day
    C_CLIM_BAR = "#AEB8C2"
    bax = fig.add_axes([lx + 0.012, 0.225, 0.265, 0.175])
    xs_pos = np.arange(len(order))
    clim_means = [float(np.nanmean(np.where(cmask, cube[t]["clim_mm"], np.nan)))
                  for t in order]
    fc_means = [float(np.nanmean(np.where(cmask, cube[t]["current_mm"], np.nan)))
                for t in order]
    app_vals = load_app_country_forecast(issued)
    fc_colors = [_rp_bin_color(app_vals[t]["pct"]) if app_vals[t]["rainy"]
                 else C_OFF for t in order]
    bax.bar(xs_pos - 0.19, clim_means, width=0.34, color=C_CLIM_BAR, zorder=2)
    bax.bar(xs_pos + 0.19, fc_means, width=0.34, color=fc_colors,
            edgecolor="#888", linewidth=0.4, zorder=2)
    for xp, v in list(zip(xs_pos - 0.19, clim_means)) + list(zip(xs_pos + 0.19, fc_means)):
        bax.text(xp, v + 0.12, f"{v:.1f}", ha="center", va="bottom",
                 fontsize=7.5, color="#555")
    bax.set_xticks(xs_pos)
    bax.set_xticklabels([t for t in order], fontsize=9)
    for t, tick in zip(order, bax.get_xticklabels()):
        if t == worst:
            tick.set_color("#7B3A1A")
            tick.set_fontweight("bold")
    # overall country-level RP per trimester — the app's own numbers
    for xp, tri in zip(xs_pos, order):
        v = app_vals[tri]
        side = "dry" if v["pct"] < 50 else "wet"
        if not v["rainy"]:
            lab, col = f"1-in-{v['rp']:.0f} {side}*", "#999"
        elif v["rp"] < 1.5:
            lab, col = "≈ normal", "#777"
        else:
            lab = f"1-in-{v['rp']:.0f} {side}"
            col = {"dry": "#7B3A1A", "wet": "#0D40B0"}[side]
        bax.text(xp, -0.21, lab, transform=bax.get_xaxis_transform(),
                 ha="center", va="top", fontsize=8, color=col)
    bax.set_ylim(0, max(clim_means + fc_means) * 1.22)
    bax.tick_params(axis="y", labelsize=8, length=2)
    bax.tick_params(axis="x", length=0)
    for spine in ("top", "right"):
        bax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        bax.spines[spine].set_color("#CCC")
    bax.grid(axis="y", color="#EAEAEA", linewidth=0.6, zorder=0)
    bax.set_title("Country-mean rainfall (mm/day)", fontsize=10.5,
                  fontweight="bold", loc="left", pad=10)
    fig.add_artist(plt.Rectangle((lx + 0.012, 0.143), 0.016, 0.022,
                                 facecolor=C_CLIM_BAR, transform=fig.transFigure))
    fig.text(lx + 0.036, 0.147, "climatology (ERA5)", fontsize=8.5, color="#333")
    fig.add_artist(plt.Rectangle((lx + 0.145, 0.143), 0.016, 0.022,
                                 facecolor=_rp_bin_color(means[worst]),
                                 edgecolor="#888", linewidth=0.4,
                                 transform=fig.transFigure))
    fig.text(lx + 0.169, 0.147, "this forecast", fontsize=8.5, color="#333")

    fig.text(lx, 0.112,
             "Categories from the detrended skill cube vs the\n"
             "same issued month + leads. Bar colours + RPs: the\n"
             "app's country-level forecast (* = off-season).\n"
             f"Niño3.4: {nino_last:+.1f} °C in {nino_when:%b %Y}."
             + ("" if min(cube[t]["lead"] for t in order) >= 1
                else " In season = obs + fcst."),
             fontsize=9, color="#333", va="top", linespacing=1.4)
    fig.text(lx, 0.015,
             f"Data: ECMWF SEAS5 · Boundaries: {COUNTRY['iso3']} ADM1 (COD)",
             fontsize=8.5, color="#777", va="bottom")

    fig.savefig(path_png, dpi=200, facecolor="white")
    plt.close(fig)


def make_pdf():
    """Assemble the two slide PNGs into a single downloadable PDF."""
    from PIL import Image

    with PdfPages(OUT_DIR / f"{KEY}_enso_slides.pdf") as pdf:
        for name in ("slide1.png", "slide2.png"):
            if not (OUT_DIR / name).exists():
                print(f"  {name} missing — skipped in PDF")
                continue
            img = Image.open(OUT_DIR / name)
            fig = plt.figure(figsize=(13.333, 7.5))
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(img)
            ax.set_axis_off()
            pdf.savefig(fig, dpi=200)
            plt.close(fig)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", choices=list(COUNTRIES), default="png")
    ap.add_argument("--skip-era5", action="store_true")
    ap.add_argument("--skip-seas5", action="store_true")
    args = ap.parse_args()

    set_country(args.country)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_era5:
        make_slide1(OUT_DIR / "slide1.png")
        print("slide1.png written")
    if not args.skip_seas5:
        make_slide2(OUT_DIR / "slide2.png")
        print("slide2.png written")
    make_pdf()
    print(f"{KEY}_enso_slides.pdf written")
