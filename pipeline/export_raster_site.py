"""Render the pixel-level forecast (latest issuance) to PNG overlays for the Leaflet viewer.

Bakes the adm0 colour scheme + skill hatching into transparent PNGs (one per valid trimester,
masked / all-pixels variants) so a browser just swaps images over a plain world-outline basemap.
Reads the local detrended skill cube written by compute_skill_raster.py (matches the adm0 static
map's default). No data is shipped to the browser beyond the small PNGs + meta.json.

Run:  uv run python pipeline/export_raster_site.py            # latest issuance (auto)
      uv run python pipeline/export_raster_site.py -m 6       # force issued month
"""

import argparse
import calendar
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import xarray as xr
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_bounds

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # for src
sys.path.insert(0, str(HERE))         # for the sibling export_static_site module

from src.constants import TRIMESTERS  # noqa: E402
from src.skill import trimester_lead  # noqa: E402
from src.skill_raster import rainy_from_cube  # noqa: E402
from export_static_site import _default_tri, issued_year_for_season  # noqa: E402

RAINY_TRIMESTER_PCT = 0.15  # matches the country export's trimester_pct default

OUT = Path(__file__).resolve().parent.parent / "docs" / "raster" / "data"
GEO = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"
CUBE = Path("/tmp/skill_stats_grid_detrended.nc")  # falls back to dev blob if missing

THRESH = {"sev_rp": 3, "vsev_rp": 10, "r_mod": 0.30, "r_high": 0.50}

# Full adm0 category set (code per pixel). The browser derives colour + hatch from this and draws
# both on one canvas so they share the exact same grid (no fill-vs-hatch drift).
#   0 transparent  1 off_season  2 low_skill  3 high_none  4 mid_none
#   5/6 drought_vsev high/mod  7/8 drought_sev  9/10 flood_sev  11/12 flood_vsev
T, OFF, LOW, HN, MN = 0, 1, 2, 3, 4
DVH, DVM, DSH, DSM, FSH, FSM, FVH, FVM = 5, 6, 7, 8, 9, 10, 11, 12


def _ensure_cube():
    if CUBE.exists():
        return CUBE
    import ocha_stratus as stratus
    from src.constants import PROJECT_PREFIX
    print("Downloading detrended cube from DEV blob (one-time)...", flush=True)
    CUBE.write_bytes(stratus.load_blob_data(
        f"{PROJECT_PREFIX}/processed/raster/skill_stats_grid_detrended.nc", stage="dev"))
    return CUBE


def _classify(P, R, rainy, masked):
    """Per-pixel category code (full adm0 set). Mirrors the adm0 classify()."""
    vsev_m, sev_m = 100 / THRESH["vsev_rp"], 100 / THRESH["sev_rp"]
    valid = np.isfinite(P) & np.isfinite(R)
    off = masked & valid & (rainy != 1)
    skill = valid & ~off
    low = skill & (R < THRESH["r_mod"])
    ok = skill & (R >= THRESH["r_mod"])
    drought = P < 50
    high = R >= THRESH["r_high"]
    vsev = (P <= vsev_m) | (P >= 100 - vsev_m)
    sev = ((P > vsev_m) & (P <= sev_m)) | ((P >= 100 - sev_m) & (P < 100 - vsev_m))
    none = ok & ~vsev & ~sev

    code = np.zeros(P.shape, dtype=np.uint8)            # transparent
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


def _code_png(code, path):
    """Per-pixel category code (0-12) at native grid resolution; the JS layer renders it."""
    Image.fromarray(code, "L").save(path, optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--issued-month", type=int, default=None)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    ds = xr.open_dataset(_ensure_cube())

    # adm0-style helpers: trimester validity and season_year -> issue_year mapping.
    # Valid = lead −2 … 4, matching the country site: 0–4 are fully-forecast complete
    # trimesters; −1/−2 are in-season (mixed obs+forecast).
    def tri_valid(months, m):
        return -2 <= trimester_lead(m, months) <= 4

    def min_signed(months, m):
        return min(o if (o := (mm - m) % 12) <= 6 else o - 12 for mm in months)

    def issue_year(cfy, m, tri):
        return issued_year_for_season(int(cfy), m, tri)

    cfy = ds["current_forecast_year"]
    if args.issued_month:
        im = args.issued_month
    else:
        # Latest real issuance = most recent (issue_year, issued_month) over valid combos.
        iy_by_month = {}
        for m in range(1, 13):
            ys = [issue_year(float(cfy.sel(issued_month=m, trimester=t)), m, t)
                  for t in TRIMESTERS
                  if tri_valid(TRIMESTERS[t], m)
                  and np.isfinite(float(cfy.sel(issued_month=m, trimester=t)))]
            if ys:
                iy_by_month[m] = max(ys)
        ymax = max(iy_by_month.values())
        im = max(m for m, iy in iy_by_month.items() if iy == ymax)
    issued_year = max(issue_year(float(cfy.sel(issued_month=im, trimester=t)), im, t)
                      for t in TRIMESTERS if tri_valid(TRIMESTERS[t], im)
                      and np.isfinite(float(cfy.sel(issued_month=im, trimester=t))))
    issued_label = f"{calendar.month_name[im]} {issued_year}"

    sl = ds.sel(issued_month=im)
    # Only trimesters the cube actually has data for — an older cube without the
    # in-season combos would otherwise bake fully-transparent PNGs for them.
    tris = sorted([t for t in TRIMESTERS if tri_valid(TRIMESTERS[t], im)
                   and np.isfinite(float(cfy.sel(issued_month=im, trimester=t)))],
                  key=lambda t: min_signed(TRIMESTERS[t], im))
    print(f"Issuance: {issued_label} (month {im}); trimesters: {tris}", flush=True)

    x, y = ds["x"].values, ds["y"].values
    bounds = [[float(y.min()), float(x.min())], [float(y.max()), float(x.max())]]

    # Land mask (rasterise country polygons onto the grid) so only land is categorised —
    # otherwise ocean pixels (which still have precip/skill) blanket the map. adm0 is land-only.
    res = abs(float(x[1] - x[0]))
    transform = from_bounds(x.min() - res / 2, y.min() - res / 2,
                            x.max() + res / 2, y.max() + res / 2, len(x), len(y))
    land = rasterize([(g, 1) for g in gpd.read_file(GEO).geometry if g is not None],
                     out_shape=(len(y), len(x)), transform=transform,
                     fill=0, all_touched=True).astype(bool)

    meta = {"issued_label": issued_label, "issued_month": im, "issued_year": issued_year,
            "bounds": bounds, "thresholds": THRESH, "default_trimester": _default_tri(tris, im),
            "trimesters": [{"key": t, "label": "–".join(calendar.month_abbr[m] for m in TRIMESTERS[t])}
                           for t in tris]}

    rainy_da = rainy_from_cube(ds, RAINY_TRIMESTER_PCT)  # re-derive at current threshold
    for t in tris:
        s = sl.sel(trimester=t)
        P = s["forecast_percentile"].values
        R = s["pearson_r"].values
        RA = rainy_da.sel(trimester=t).values
        for masked, tag in [(True, "masked"), (False, "all")]:
            code = _classify(P, R, RA, masked)
            code[~land] = 0   # ocean / non-land -> transparent (show basemap)
            _code_png(code, OUT / f"{t}_{tag}.png")
        print(f"  {t}: wrote masked/all PNGs", flush=True)

    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"Wrote {OUT/'meta.json'} and {2*len(tris)} PNGs", flush=True)


if __name__ == "__main__":
    main()
