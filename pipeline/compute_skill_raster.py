"""Pixel-level (raster) version of pipeline/compute_skill.py.

Loads SEAS5 + ERA5 rasters from the **PROD** blob (`stack_cogs`), regrids ERA5 down to the
SEAS5 native grid, then computes the same skill / forecast-position / rainy-season metrics as the
country pipeline — but **per pixel** — for every (issued_month × trimester) combination. Writes a
single NetCDF skill cube with dims (issued_month, trimester, y, x) to the **DEV** blob.

Memory note (16 GB box): ERA5 (regridded) + monthly climatology are loaded once; SEAS5 is loaded
one issued-month at a time; the output cube (~2.3 GB float32) is held and written at the end.

Computes the raw and detrended variants in a single pass (shared loads) and writes both.

Run:
  uv run python pipeline/compute_skill_raster.py                          # full global
  uv run python pipeline/compute_skill_raster.py --clip-iso3 ETH SOM KEN  # smoke test
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from rasterio.enums import Resampling
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ocha_stratus as stratus  # noqa: E402

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
import src.skill_raster as sr  # noqa: E402

SEAS5_START = "1981-01-01"
TRI_NAMES = list(TRIMESTERS.keys())
GEO_SRC = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"

# Per-pixel (issued_month, trimester, y, x) outputs
PIXEL_VARS = [
    "pearson_r", "rmse", "era5_mean", "era5_std", "lower_tercile_mm",
    "current_forecast_mean", "forecast_percentile", "forecast_rp", "flood_rp",
    "prob_lower_tercile", "sigma", "rainy",
]
# Per-combo (issued_month, trimester) scalars
COMBO_VARS = ["n_years", "current_forecast_year"]


def _to_da(x):
    return x[list(x.data_vars)[0]] if isinstance(x, xr.Dataset) else x


def _month_firsts(end_date):
    return [d.strftime("%Y-%m-%d") for d in pd.date_range(SEAS5_START, end_date, freq="MS")]


def load_seas5_issued_month(issued_month, end_date, stage, clip_gdf):
    dates = [d.strftime("%Y-%m-%d")
             for d in pd.date_range(SEAS5_START, end_date, freq="MS") if d.month == issued_month]
    da = _to_da(stratus.stack_cogs("seas5", dates, stage=stage, clip_gdf=clip_gdf, mode="pipeline"))
    return da.rio.write_crs(4326)


def load_era5_regridded(end_date, template, stage, clip_gdf):
    da = _to_da(stratus.stack_cogs("era5", _month_firsts(end_date),
                                   stage=stage, clip_gdf=clip_gdf, mode="pipeline"))
    return da.rio.write_crs(4326).rio.reproject_match(template, resampling=Resampling.average)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date", default=pd.Timestamp.today().strftime("%Y-%m-01"),
                    help="Latest issued month to include (YYYY-MM-01)")
    ap.add_argument("--issued-months", type=int, nargs="+", default=list(range(1, 13)))
    ap.add_argument("--clip-iso3", nargs="+", default=None, help="Clip to these ISO3s (smoke test)")
    ap.add_argument("--no-upload", action="store_true", help="Write local NetCDF only")
    args = ap.parse_args()

    clip_gdf = None
    if args.clip_iso3:
        world = gpd.read_file(GEO_SRC)
        clip_gdf = world[world["iso3"].isin(args.clip_iso3)]
        tqdm.write(f"Clipping to {args.clip_iso3}")

    # Template grid from the first issued month we need; load it up-front and reuse.
    first_im = args.issued_months[0]
    tqdm.write(f"Loading SEAS5 issued-month {first_im} (for grid template)...")
    seas5_cache = {first_im: load_seas5_issued_month(first_im, args.end_date, "prod", clip_gdf)}
    template = seas5_cache[first_im].isel(date=0, leadtime=0, drop=True)

    tqdm.write("Loading ERA5 (regridding to SEAS5 grid)...")
    era5 = load_era5_regridded(args.end_date, template, "prod", clip_gdf)
    clim = sr.monthly_clim_grid(era5)

    # Pre-allocate the output cubes (raw + detrended computed in a single pass).
    def _empty_cube():
        nan_px = np.full((12, 12, template.sizes["y"], template.sizes["x"]), np.nan, "float32")
        nan_combo = np.full((12, 12), np.nan, "float32")
        return xr.Dataset(
            {v: (("issued_month", "trimester", "y", "x"), nan_px.copy()) for v in PIXEL_VARS}
            | {v: (("issued_month", "trimester"), nan_combo.copy()) for v in COMBO_VARS},
            coords={"issued_month": list(range(1, 13)), "trimester": TRI_NAMES,
                    "y": template["y"].values, "x": template["x"].values},
        )

    out_raw, out_dt = _empty_cube(), _empty_cube()

    def _fill(out, im, ti, fc_n, obs_l, rainy):
        skill = sr.compute_skill_grid(fc_n, obs_l)
        if skill is None:
            return
        era5_mean = obs_l.mean("season_year")
        era5_std = obs_l.std("season_year", ddof=1)
        fm = sr.forecast_metrics_grid(fc_n, obs_l, skill["pearson_r"],
                                      era5_mean, era5_std, skill["overlap_years"])
        vals = {
            "pearson_r": skill["pearson_r"], "rmse": skill["rmse"],
            "era5_mean": era5_mean, "era5_std": era5_std,
            "lower_tercile_mm": fm["lower_tercile_mm"],
            "current_forecast_mean": fm["current_forecast_mean"],
            "forecast_percentile": fm["forecast_percentile"],
            "forecast_rp": fm["forecast_rp"], "flood_rp": fm["flood_rp"],
            "prob_lower_tercile": fm["prob_lower_tercile"], "sigma": fm["sigma"],
            "rainy": rainy.astype("float32"),
        }
        for v, da in vals.items():
            out[v][im - 1, ti, :, :] = da.transpose("y", "x").values
        out["n_years"][im - 1, ti] = skill["n_years"]
        out["current_forecast_year"][im - 1, ti] = fm["current_forecast_year"]

    for im in tqdm(args.issued_months, desc="issued_month"):
        seas5 = seas5_cache.pop(im, None)
        if seas5 is None:
            seas5 = load_seas5_issued_month(im, args.end_date, "prod", clip_gdf)
        for ti, tri in enumerate(TRI_NAMES):
            valid = TRIMESTERS[tri]
            # In-season (mixed) trimesters — issuance falls inside the trimester, so the
            # already-observed months come from ERA5 and only the rest is forecast.
            if sr.trimester_lead(im, valid) in (-1, -2):
                fc = sr.aggregate_mixed_trimester_grid(seas5, era5, im, valid)
            else:
                fc = sr.aggregate_seas5_trimester_grid(seas5, im, valid)
            obs = sr.aggregate_era5_trimester_grid(era5, valid)
            if fc is None or obs is None:
                continue
            fc_n = sr.normalize_seas5_grid(sr.log1p(fc), sr.log1p(obs))
            obs_l = sr.log1p(obs)
            rainy = sr.rainy_grid(clim, valid)
            _fill(out_raw, im, ti, fc_n, obs_l, rainy)
            # Detrended variant reuses the same normalised forecast + obs.
            hist = sr._overlap_years(fc_n, obs_l)
            if len(hist) >= sr.MIN_YEARS:
                _fill(out_dt, im, ti, sr.detrend_grid(fc_n, hist),
                      sr.detrend_grid(obs_l, hist), rainy)
        del seas5

    for out, suffix, label in [(out_raw, "", "raw"), (out_dt, "_detrended", "detrended")]:
        out.attrs["description"] = (
            f"Pixel-level SEAS5 seasonal precipitation skill on the SEAS5 native grid "
            f"({label}). Dims: issued_month × trimester × y × x."
        )
        local = Path(f"/tmp/skill_stats_grid{suffix}.nc")
        enc = {v: {"zlib": True, "complevel": 4} for v in out.data_vars}
        out.to_netcdf(local, engine="netcdf4", encoding=enc)
        tqdm.write(f"Wrote {local} ({local.stat().st_size / 1e6:.1f} MB)")
        if not args.no_upload:
            blob = f"{PROJECT_PREFIX}/processed/raster/skill_stats_grid{suffix}.nc"
            with open(local, "rb") as f:
                stratus.upload_blob_data(f.read(), blob, stage="dev")
            tqdm.write(f"Uploaded -> DEV blob: {blob}")
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
