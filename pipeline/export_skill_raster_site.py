"""Render per-pixel SEAS5 skill (Pearson r) to colour PNG overlays for the skill map.

Unlike the forecast overlays, skill has no hatching or rainy-season masking, so the
categorical colours are baked straight into RGBA PNGs (one per trimester × leadtime).
The browser just swaps a plain L.imageOverlay — no client-side canvas needed.

Skill is leadtime-based, not tied to one issuance: for each trimester and leadtime
(months from the issue month to the trimester's first month) we select the matching
issued month from the cube. Leads 0–6 span SEAS5's 7-month horizon.

Reads the local detrended skill cube written by compute_skill_raster.py (matches the
adm0 map's default), falling back to the DEV blob.

Run:  uv run python pipeline/export_skill_raster_site.py
"""

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import TRIMESTERS  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "raster" / "skill"
GEO = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"
CUBE = Path("/tmp/skill_stats_grid_detrended.nc")  # falls back to dev blob if missing

THRESH = {"r_mod": 0.30, "r_high": 0.50}
LEADS = [0, 1, 2, 3, 4, 5, 6]

# Category code per pixel and its RGBA colour (matches the Skill-tab heatmap palette).
#   0 transparent (no data / ocean)  1 negative  2 low  3 moderate  4 high
PALETTE = {
    0: (0, 0, 0, 0),
    1: (229, 115, 115, 255),  # negative  r < 0
    2: (254, 224, 139, 255),  # low       0 ≤ r < r_mod
    3: (166, 217, 106, 255),  # moderate  r_mod ≤ r < r_high
    4: (26, 152, 80, 255),    # high      r ≥ r_high
}


def _ensure_cube():
    if CUBE.exists():
        return CUBE
    import ocha_stratus as stratus
    from src.constants import PROJECT_PREFIX
    print("Downloading detrended cube from DEV blob (one-time)...", flush=True)
    CUBE.write_bytes(stratus.load_blob_data(
        f"{PROJECT_PREFIX}/processed/raster/skill_stats_grid_detrended.nc", stage="dev"))
    return CUBE


def _classify_skill(R):
    """Per-pixel skill category (0–4) from Pearson r."""
    code = np.zeros(R.shape, dtype=np.uint8)
    valid = np.isfinite(R)
    code[valid & (R < 0)] = 1
    code[valid & (R >= 0) & (R < THRESH["r_mod"])] = 2
    code[valid & (R >= THRESH["r_mod"]) & (R < THRESH["r_high"])] = 3
    code[valid & (R >= THRESH["r_high"])] = 4
    return code


def _rgba_png(code, path):
    rgba = np.zeros((*code.shape, 4), dtype=np.uint8)
    for c, col in PALETTE.items():
        rgba[code == c] = col
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ds = xr.open_dataset(_ensure_cube())

    x, y = ds["x"].values, ds["y"].values
    assert y[0] > y[-1], "expected north-up grid (y descending)"
    bounds = [[float(y.min()), float(x.min())], [float(y.max()), float(x.max())]]

    # Land mask (rasterise country polygons) so only land is coloured — adm0 is land-only.
    res = abs(float(x[1] - x[0]))
    transform = from_bounds(x.min() - res / 2, y.min() - res / 2,
                            x.max() + res / 2, y.max() + res / 2, len(x), len(y))
    land = rasterize([(g, 1) for g in gpd.read_file(GEO).geometry if g is not None],
                     out_shape=(len(y), len(x)), transform=transform,
                     fill=0, all_touched=True).astype(bool)

    # Off-season grey cover (#D0D0D0) per trimester — the rainy mask is leadtime-independent,
    # so one cover overlays all leads. Drawn over the skill image when the rainy mask is on.
    off_grey = (208, 208, 208, 255)

    n = 0
    for t in TRIMESTERS:
        start = TRIMESTERS[t][0]
        for lead in LEADS:
            im = ((start - lead - 1) % 12) + 1
            R = ds["pearson_r"].sel(issued_month=im, trimester=t).values
            code = _classify_skill(R)
            code[~land] = 0
            _rgba_png(code, OUT / f"{t}_L{lead}.png")
            n += 1

        rainy = (ds["rainy"].sel(trimester=t) == 1).any("issued_month").values
        cover = np.zeros((len(y), len(x), 4), dtype=np.uint8)
        cover[land & ~rainy] = off_grey
        Image.fromarray(cover, "RGBA").save(OUT / f"mask_{t}.png", optimize=True)
        print(f"  {t}: wrote {len(LEADS)} leadtime PNGs + off-season mask", flush=True)

    meta = {
        "bounds": bounds,
        "thresholds": THRESH,
        "leads": LEADS,
        "trimesters": [{"key": t, "label": "–".join(calendar.month_abbr[m] for m in TRIMESTERS[t])}
                       for t in TRIMESTERS],
        "default_trimester": "JAS",
        "default_lead": 1,
        "has_mask": True,
    }
    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"Wrote {OUT/'meta.json'} and {n} PNGs", flush=True)


if __name__ == "__main__":
    main()
