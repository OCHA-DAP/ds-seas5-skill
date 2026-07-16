"""Quick viewer for the pixel-level skill cubes (analysis only — not an app/notebook).

Opens the NetCDF skill cube lazily (only the requested slice is read) and renders one global
map to PNG. Uses the local file written by pipeline/compute_skill_raster.py if present, else
downloads it once from the DEV blob and caches it.

Examples:
  uv run python analysis/view_skill_raster.py                                  # r, Jun/JAS
  uv run python analysis/view_skill_raster.py --var forecast_percentile -m 6 -t JAS
  uv run python analysis/view_skill_raster.py --var pearson_r -m 6 -t JAS --rainy-only
  uv run python analysis/view_skill_raster.py --var forecast_rp -m 6 -t SON --detrended
"""

import argparse
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ocha_stratus as stratus  # noqa: E402
from src.constants import PROJECT_PREFIX  # noqa: E402

CACHE = Path(__file__).resolve().parent / "_skill_cache"
GEO = Path(__file__).resolve().parent / "_world_countries.gpkg"

# var -> (colormap, vmin, vmax, center)  center=None => sequential
STYLE = {
    "pearson_r":           ("RdBu_r", -0.7, 0.7, 0.0),
    "rmse":                ("viridis", None, None, None),
    "forecast_percentile": ("BrBG", 0, 100, 50),
    "forecast_rp":         ("YlOrBr", 1, 25, None),
    "flood_rp":            ("YlGnBu", 1, 25, None),
    "prob_lower_tercile":  ("BrBG_r", 0, 1, 1 / 3),
    "current_forecast_mean": ("viridis", None, None, None),
    "era5_mean":           ("viridis", None, None, None),
    "lower_tercile_mm":    ("YlGnBu", None, None, None),
    "rainy":               ("Greens", 0, 1, None),
}


def _local_path(suffix: str) -> Path:
    name = f"skill_stats_grid{suffix}.nc"
    tmp = Path("/tmp") / name
    if tmp.exists():
        return tmp
    cache = CACHE / name
    if not cache.exists():
        CACHE.mkdir(exist_ok=True)
        blob = f"{PROJECT_PREFIX}/processed/raster/{name}"
        print(f"Downloading {blob} from DEV blob (~1.2 GB, one-time)...", flush=True)
        cache.write_bytes(stratus.load_blob_data(blob, stage="dev"))
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="pearson_r", choices=list(STYLE))
    ap.add_argument("-m", "--issued-month", type=int, default=6)
    ap.add_argument("-t", "--trimester", default="JAS")
    ap.add_argument("--detrended", action="store_true")
    ap.add_argument("--rainy-only", action="store_true", help="Mask non-rainy-season pixels")
    ap.add_argument("--extent", type=float, nargs=4, metavar=("W", "E", "S", "N"),
                    help="Clip view to lon/lat box")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    suffix = "_detrended" if args.detrended else ""
    ds = xr.open_dataset(_local_path(suffix))
    sl = ds.sel(issued_month=args.issued_month, trimester=args.trimester)
    da = sl[args.var]
    if da.isnull().all():
        sys.exit(f"No data for {args.var} at issued_month={args.issued_month}, {args.trimester} "
                 f"(not a valid <=6-month-lead trimester?).")
    if args.rainy_only and args.var != "rainy":
        da = da.where(sl["rainy"] == 1)

    cmap, vmin, vmax, center = STYLE[args.var]
    norm = None
    if center is not None and vmin is not None and vmax is not None:
        from matplotlib.colors import TwoSlopeNorm
        norm = TwoSlopeNorm(vmin=vmin, vcenter=center, vmax=vmax)
        vmin = vmax = None

    x, y = da["x"].values, da["y"].values
    extent = [x.min(), x.max(), y.max(), y.min()]  # y descending -> origin upper
    fig, ax = plt.subplots(figsize=(13, 6.2), dpi=130)
    im = ax.imshow(da.values, extent=extent, origin="upper", cmap=cmap,
                   vmin=vmin, vmax=vmax, norm=norm, interpolation="nearest")
    try:
        gpd.read_file(GEO).boundary.plot(ax=ax, linewidth=0.25, color="#444", zorder=2)
    except Exception:
        pass
    if args.extent:
        ax.set_xlim(args.extent[0], args.extent[1])
        ax.set_ylim(args.extent[2], args.extent[3])
    else:
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[3], extent[2])
    ax.set_aspect("equal")
    yr = int(sl["current_forecast_year"]) if not np.isnan(float(sl["current_forecast_year"])) else "—"
    n = int(sl["n_years"]) if not np.isnan(float(sl["n_years"])) else "—"
    _rs = " [rainy-season pixels]" if args.rainy_only and args.var != "rainy" else ""
    ax.set_title(f"{args.var}{' [detrended]' if args.detrended else ''} — issued "
                 f"month {args.issued_month}, valid {args.trimester} {yr}  (n={n}){_rs}", fontsize=11)
    fig.colorbar(im, ax=ax, shrink=0.7, pad=0.01, label=args.var)
    plt.tight_layout()

    out = Path(args.out) if args.out else Path(
        f"/tmp/skill_{args.var}_{args.issued_month}_{args.trimester}{suffix}.png")
    fig.savefig(out, bbox_inches="tight")
    print(f"Wrote {out}")
    if sys.platform == "darwin" and not args.out:
        subprocess.run(["open", str(out)], check=False)


if __name__ == "__main__":
    main()
