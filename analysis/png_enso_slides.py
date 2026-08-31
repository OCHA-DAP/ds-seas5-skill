"""Two slides for Papua New Guinea, published under pages/png-enso/.

Slide 1 — ERA5 rainfall x ENSO (Nino3.4) teleconnection, pixelwise at the native
0.25 degree ERA5 grid, using exactly the ds-teleconnections page methodology
(trimester means, rainy-season + arid filters, lag sweep, p<0.05, the discrete
blue/brown |r| bins) but zoomed to PNG instead of the global viewport.

Slide 2 — the current SEAS5 forecast, pixelwise with ADM1 boundaries overlaid,
one tile per upcoming trimester (all four trimesters fully covered by the latest
issuance), coloured by percent anomaly of the ensemble-mean forecast vs the
SEAS5 hindcast climatology for the same issued month + leads (so model bias
cancels). The tile with the most negative country-mean anomaly is tagged as the
worst trimester.

Data:
  - ERA5 monthly mm/day COGs (prod raster blob, era5/monthly/processed/) — read
    from the ds-teleconnections local cache when present, else fetched.
  - Nino3.4 anomaly index from NOAA PSL (cached next to this script's outputs).
  - SEAS5 monthly-by-leadtime COGs via stratus.stack_cogs("seas5", ...).
  - PNG ADM1 from public.polygon (prod DB).

Run:  uv run python analysis/png_enso_slides.py [--skip-era5] [--skip-seas5]
Writes pages/png-enso/slide{1,2}.png and pages/png-enso/png_enso_slides.pdf.
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
OUT_DIR = REPO / "pages" / "png-enso"
CACHE = REPO / "analysis" / "_png_enso_cache"

# PNG window (covers mainland + Bismarck Archipelago + Bougainville)
LON = (140.5, 156.75)
LAT = (0.25, -12.0)  # north, south

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

NINA34_URL = "https://psl.noaa.gov/data/correlation/nina34.anom.data"


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


def load_nino34() -> pd.Series:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / "nina34.anom.data"
    if not f.exists():
        f.write_text(requests.get(NINA34_URL, timeout=60).text)
    return _parse_psl(f.read_text())


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
    """PNG ADM1 polygons from the prod polygon blob container, cached as GeoJSON."""
    f = CACHE / "png_adm1.geojson"
    if f.exists():
        return gpd.read_file(f)
    import ocha_stratus as stratus

    gdf = stratus.load_shp_from_blob(
        "png_shp.zip", shapefile="png_adm1.shp",
        stage="prod", container_name="polygon",
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    gdf.to_file(f, driver="GeoJSON")
    return gdf


def load_png_adm0() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(REPO / "analysis" / "_ne_50m_countries.gpkg")
    col = next(c for c in ("iso3", "ISO_A3", "ADM0_A3", "SOV_A3") if c in gdf.columns)
    return gdf[gdf[col] == "PNG"]


# ── ERA5 monthly stack for the PNG window ───────────────────────────────────────


def era5_png_stack() -> tuple[np.ndarray, list[tuple[int, int]], np.ndarray, np.ndarray]:
    """(n_months, ny, nx) mm/day for the PNG window + (year, month) list + coords."""
    f = CACHE / "era5_png.npz"
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


def pearson_p(r: np.ndarray, n: int) -> np.ndarray:
    dof = float(n) - 2
    with np.errstate(invalid="ignore", divide="ignore"):
        t = r * np.sqrt(dof / np.clip(1 - r**2, 1e-12, None))
        return 2 * stats.t.sf(np.abs(t), dof)


def compute_enso_correlations():
    """Per-trimester best-lag Pearson r of trimester rainfall vs Nino3.4.

    Returns (results, rainy, x, y, mask) where results[tri] = dict(r, p, lag)
    each an (ny, nx) array (NaN off-land), rainy[tri] the analysable mask.
    """
    stack, ym, x, y = era5_png_stack()
    adm0 = load_png_adm0()
    mask = land_mask(adm0, x, y)
    mpos = {p: i for i, p in enumerate(ym)}
    nino = load_nino34()

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
        Y = tri_data[tri].reshape(len(tri_years[tri]), -1)
        yrs = tri_years[tri]
        r_best = np.full(Y.shape[1], np.nan)
        lag_best = np.full(Y.shape[1], -1, dtype=int)
        n_best = 0
        for lag in range(MAX_LAG + 1):
            xs = index_trimester_mean(nino, TRIMESTERS[tri], lag).dropna()
            common = np.intersect1d(yrs, xs.index.values)
            if len(common) < MIN_YEARS:
                continue
            sel = np.isin(yrs, common)
            Yc = Y[sel] - Y[sel].mean(axis=0)
            sy = np.sqrt((Yc**2).sum(axis=0))
            xc = xs.loc[common].values
            xc = xc - xc.mean()
            sx = np.sqrt((xc**2).sum())
            with np.errstate(invalid="ignore", divide="ignore"):
                r = (xc @ Yc) / (sx * sy)
            upd = np.isfinite(r) & (np.isnan(r_best) | (np.abs(r) > np.abs(r_best)))
            r_best[upd], lag_best[upd] = r[upd], lag
            n_best = len(common)
        shape = mask.shape
        results[tri] = {
            "r": np.where(mask.reshape(-1), r_best, np.nan).reshape(shape),
            "p": pearson_p(r_best, n_best).reshape(shape),
            "lag": lag_best.reshape(shape),
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
               "Peak ENSO signal — strongest significant trimester per pixel",
               title_size=12)

    for k, tri in enumerate(_ANNUAL):
        ax = fig.add_subplot(gs[1, k])
        s = rainy[tri] & np.isfinite(results[tri]["r"]) & (results[tri]["p"] < ALPHA)
        _map_panel(ax, results[tri]["r"], s, mask, x, y, adm1, adm0, tri,
                   title_size=10)

    fig.text(0.04, 0.955, "Papua New Guinea — ERA5 rainfall × ENSO teleconnection",
             fontsize=19, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.905,
             "Pixelwise Pearson correlation of trimester rainfall with Niño3.4, "
             f"ERA5 native 0.25° grid, {START_YEAR}–{END_YEAR}",
             fontsize=11.5, color="#444")

    # Legend + reading notes
    lx = 0.745
    fig.text(lx, 0.82, "Correlation with Niño3.4", fontsize=11, fontweight="bold")
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
             "Method (as the ERA5 teleconnections page):\n"
             "• Trimester rainfall means per pixel, all 12\n"
             "   overlapping 3-month windows\n"
             "• Niño3.4 window ending 0–3 months before the\n"
             "   trimester end; lag with max |r| kept\n"
             "• Two-tailed p < 0.05\n"
             "• Unlike the global page, all seasons are kept\n"
             "   (no rainy-season filter): PNG's El Niño drought\n"
             "   signal peaks in its drier JAS–OND months\n\n"
             "Negative (brown) = El Niño → drier than normal.",
             fontsize=9.5, color="#333", va="top", linespacing=1.5)
    fig.text(lx, 0.06, "Data: ERA5 monthly precip (0.25°), NOAA PSL\n"
                       "Niño3.4. Bottom row: canonical trimesters.",
             fontsize=8.5, color="#777", va="bottom", linespacing=1.4)

    fig.savefig(path_png, dpi=200, facecolor="white")
    plt.close(fig)
    return fig


# ── Slide 2: current SEAS5 forecast, pixelwise, tiled trimesters ────────────────

from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap  # noqa: E402

# Brown-neutral-blue drought/flood ramp shared with the teleconnections page
DROUGHT_FLOOD_CMAP = LinearSegmentedColormap.from_list(
    "drought_flood",
    [(0.00, "#7B3A1A"), (0.35, "#C8844A"), (0.50, "#F5F0EC"),
     (0.65, "#71B3E5"), (1.00, "#0D40B0")],
)
PCT_BOUNDS = [0, 10, 20, 33, 67, 80, 90, 100]


def load_current_seas5():
    """Latest-issuance SEAS5 trimester percentiles for PNG, cached to disk.

    Returns (per_tri, x, y, issued) where per_tri[tri] = dict(pct, current_mm,
    clim_mm) as (ny, nx) arrays on the SEAS5 native 0.4deg grid, and issued is
    the issuance Timestamp.
    """
    import sys

    sys.path.insert(0, str(REPO))
    import ocha_stratus as stratus
    import xarray as xr
    from shapely.geometry import box

    from src.skill_raster import aggregate_seas5_trimester_grid, trimester_lead
    from src.constants import TRIMESTERS as TRI_MONTHS_MAP

    f = CACHE / "seas5_png_current.npz"
    if f.exists():
        z = np.load(f, allow_pickle=True)
        return z["per_tri"].item(), z["x"], z["y"], pd.Timestamp(str(z["issued"]))

    clip = gpd.GeoDataFrame(geometry=[box(LON[0], LAT[1], LON[1], LAT[0])], crs=4326)
    today = pd.Timestamp.today().normalize().replace(day=1)

    # find the newest issuance actually on blob (monthly refresh can lag)
    da = None
    for issued in (today, today - pd.DateOffset(months=1)):
        dates = [d.strftime("%Y-%m-%d")
                 for d in pd.date_range("1981-01-01", issued, freq="MS")
                 if d.month == issued.month]
        try:
            raw = stratus.stack_cogs("seas5", dates, stage="prod",
                                     clip_gdf=clip, mode="pipeline")
            da = raw[list(raw.data_vars)[0]] if isinstance(raw, xr.Dataset) else raw
            if pd.to_datetime(da["date"].values).max() == issued:
                break
            da = None
        except Exception as e:  # noqa: BLE001
            print(f"  issuance {issued.date()} not loadable ({e}); trying previous")
            da = None
    if da is None:
        raise RuntimeError("no recent SEAS5 issuance found on blob")

    im = int(issued.month)
    per_tri = {}
    for tri, months in TRI_MONTHS_MAP.items():
        lead = trimester_lead(im, months)
        if not (1 <= lead <= 4):   # fully-forecast trimesters within the horizon
            continue
        fc = aggregate_seas5_trimester_grid(da, im, months)  # (season_year, y, x)
        if fc is None:
            continue
        cur_year = int(fc["season_year"].max())
        current = fc.sel(season_year=cur_year)
        hist = fc.sel(season_year=fc["season_year"] < cur_year)
        n = hist.sizes["season_year"]
        # repo convention (forecast_metrics_grid): share of hindcast years <= current
        pct = (100.0 * (hist <= current).sum("season_year") / n)
        per_tri[tri] = {
            "pct": pct.values.astype("float32"),
            "current_mm": current.values.astype("float32"),
            "clim_mm": hist.mean("season_year").values.astype("float32"),
            "lead": lead, "n": int(n), "season_year": cur_year,
        }

    x, y = da["x"].values, da["y"].values
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, per_tri=np.array(per_tri, dtype=object),
                        x=x, y=y, issued=str(issued.date()))
    return per_tri, x, y, issued


def _season_label(tri: str, season_year: int) -> str:
    months = _TRI_MONTHS[tri]
    wraps = 1 in months and 12 in months
    return f"{tri} {season_year}" + (f"/{(season_year + 1) % 100:02d}" if wraps else "")


def make_slide2(path_png: Path):
    per_tri, x, y, issued = load_current_seas5()
    nino = load_nino34().dropna()
    nino_last, nino_when = float(nino.iloc[-1]), nino.index[-1]
    adm1, adm0 = load_png_adm1(), load_png_adm0()
    mask = land_mask(adm0, x, y)

    order = sorted(per_tri, key=lambda t: per_tri[t]["lead"])
    means = {t: float(np.nanmean(np.where(mask, per_tri[t]["pct"], np.nan)))
             for t in order}
    worst = min(means, key=means.get)

    cmap = DROUGHT_FLOOD_CMAP
    norm = BoundaryNorm(PCT_BOUNDS, cmap.N)

    fig = plt.figure(figsize=(13.333, 7.5))
    fig.patch.set_facecolor("white")
    gs = fig.add_gridspec(2, 2, left=0.04, right=0.72, top=0.82, bottom=0.05,
                          hspace=0.24, wspace=0.08)

    for k, tri in enumerate(order):
        ax = fig.add_subplot(gs[k // 2, k % 2])
        d = per_tri[tri]
        pct = np.where(mask, d["pct"], np.nan)
        ax.imshow(pct, extent=_extent(x, y), origin="upper", cmap=cmap, norm=norm,
                  interpolation="nearest", zorder=1)
        adm1.boundary.plot(ax=ax, color="#6B7683", linewidth=0.55, zorder=3)
        adm0.boundary.plot(ax=ax, color="#3E4650", linewidth=0.8, zorder=4)
        ax.set_xlim(LON)
        ax.set_ylim(LAT[1], LAT[0])
        ax.set_aspect("equal")
        ax.set_axis_off()
        tag = "   ← driest overall" if tri == worst else ""
        ax.set_title(
            f"{_season_label(tri, d['season_year'])} — lead {d['lead']} mo — "
            f"country mean {means[tri]:.0f}th pct{tag}",
            fontsize=10.5, pad=4,
            color="#7B3A1A" if tri == worst else "#1a1a1a",
            fontweight="bold" if tri == worst else "normal")

    fig.text(0.04, 0.945, "Papua New Guinea — current SEAS5 seasonal forecast",
             fontsize=19, fontweight="bold", color="#1a1a1a")
    fig.text(0.04, 0.895,
             f"Issued {issued:%B %Y} · ensemble-mean trimester rainfall as a percentile of "
             f"the same-issuance SEAS5 hindcast (1981–{per_tri[order[0]]['season_year'] - 1}) "
             "· native 0.4° grid · ADM1 boundaries",
             fontsize=11, color="#444")

    # legend
    lx = 0.745
    fig.text(lx, 0.78, "Forecast percentile vs climatology", fontsize=11,
             fontweight="bold")
    labels = ["≤ 10th (severely dry)", "10–20th (very dry)", "20–33rd (dry tercile)",
              "33–67th (near normal)", "67–80th (wet)", "80–90th (very wet)",
              "> 90th (severely wet)"]
    for i, lab in enumerate(labels):
        c = cmap(norm((PCT_BOUNDS[i] + PCT_BOUNDS[i + 1]) / 2))
        yy = 0.735 - i * 0.048
        fig.add_artist(plt.Rectangle((lx, yy), 0.022, 0.032, facecolor=c,
                                     edgecolor="#999", linewidth=0.4,
                                     transform=fig.transFigure))
        fig.text(lx + 0.032, yy + 0.007, lab, fontsize=9.5, color="#333")

    fig.text(lx, 0.36,
             "One tile per upcoming trimester fully covered\n"
             "by this issuance (leads 1–4). Percentile is\n"
             "computed per pixel against the same issued\n"
             "month and leads in the SEAS5 hindcast, so\n"
             "model bias cancels.\n\n"
             f"Driest outlook overall: {_season_label(worst, per_tri[worst]['season_year'])} "
             f"(country mean\n{means[worst]:.0f}th percentile). Niño3.4 anomaly:\n"
             f"{nino_last:+.1f} °C in {nino_when:%b %Y}.",
             fontsize=9.5, color="#333", va="top", linespacing=1.5)
    fig.text(lx, 0.06, "Data: ECMWF SEAS5 (ensemble-mean monthly\n"
                       "precipitation). Boundaries: PNG ADM1 (COD).",
             fontsize=8.5, color="#777", va="bottom", linespacing=1.4)

    fig.savefig(path_png, dpi=200, facecolor="white")
    plt.close(fig)


def make_pdf():
    """Assemble the two slide PNGs into a single downloadable PDF."""
    from PIL import Image

    with PdfPages(OUT_DIR / "png_enso_slides.pdf") as pdf:
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
    ap.add_argument("--skip-era5", action="store_true")
    ap.add_argument("--skip-seas5", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_era5:
        make_slide1(OUT_DIR / "slide1.png")
        print("slide1.png written")
    if not args.skip_seas5:
        make_slide2(OUT_DIR / "slide2.png")
        print("slide2.png written")
    make_pdf()
    print("png_enso_slides.pdf written")
