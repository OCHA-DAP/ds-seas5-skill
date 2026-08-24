"""Uganda OND flood recurrence from FloodScan daily SFED COGs.

For every complete Oct-Dec season 1998-2025, reads the daily FloodScan SFED
band (blob raster/floodscan/daily/v5/processed/, 0.0833 deg Africa COGs,
windowed to Uganda) and takes the per-pixel seasonal maximum. Recurrence =
share of seasons whose max SFED exceeds a threshold; 0.05 is the team's noise
floor (ds-floodexposure-monitoring), 0.20 marks substantial flooding.

Writes (dev blob):
  {PROJECT_PREFIX}/processed/uga/flood_ond_recurrence.tif
      band 1: recurrence of SFED >= 0.05 (share of seasons, 0-1)
      band 2: recurrence of SFED >= 0.20
      band 3: mean seasonal-max SFED
  {PROJECT_PREFIX}/processed/uga/flood_ond_adm2.parquet
      per-district exactextract means of the three layers
  {PROJECT_PREFIX}/processed/uga/flood_ond_yearly_max.tif
      one band per season (band description = year): per-pixel OND max SFED

Run:  uv run python pipeline/compute_uga_flood_recurrence.py
"""

import io
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import rasterio
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
from ocha_stratus import codab
from rasterio.windows import bounds as window_bounds
from rasterio.windows import from_bounds
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

UGA_BOX = (29.0, -2.0, 35.5, 4.7)
SEASON_MONTHS = {10, 11, 12}
YEARS = range(1998, 2026)          # complete OND seasons only
THRESH_ANY, THRESH_SUB = 0.05, 0.20
DATE_RE = re.compile(r"aer_area_300s_v(\d{4})-(\d{2})-(\d{2})_v05r01\.tif$")

OUT_TIF = f"{PROJECT_PREFIX}/processed/uga/flood_ond_recurrence.tif"
OUT_YEARLY = f"{PROJECT_PREFIX}/processed/uga/flood_ond_yearly_max.tif"
OUT_PARQUET = f"{PROJECT_PREFIX}/processed/uga/flood_ond_adm2.parquet"


def _read_sfed(blob_name: str, attempts: int = 3):
    for i in range(attempts):
        try:
            data = stratus.load_blob_data(blob_name, container_name="raster", stage="prod")
            with rasterio.open(io.BytesIO(data)) as ds:
                w = (from_bounds(*UGA_BOX, ds.transform)
                     .round_offsets(op="floor").round_lengths(op="ceil"))
                arr = ds.read(1, window=w)  # band 1 = SFED
                return arr, window_bounds(w, ds.transform), ds.crs.to_wkt()
        except Exception:  # noqa: BLE001 — network retry; re-raised after last attempt
            if i == attempts - 1:
                raise
    raise RuntimeError("unreachable")


def main() -> None:
    names = [n for n in stratus.list_container_blobs(
        name_starts_with="floodscan/daily/v5/processed/aer_area_300s",
        container_name="raster", stage="prod") if (m := DATE_RE.search(n))]
    per_year: dict[int, list[str]] = {}
    for n in names:
        y, mo, _ = map(int, DATE_RE.search(n).groups())
        if y in YEARS and mo in SEASON_MONTHS:
            per_year.setdefault(y, []).append(n)
    missing = [y for y in YEARS if len(per_year.get(y, [])) < 85]
    if missing:
        raise RuntimeError(f"years with <85 of ~92 OND dailies: {missing} — refusing a biased recurrence")
    tqdm.write(f"{sum(len(v) for v in per_year.values()):,} daily rasters across {len(per_year)} seasons")

    season_max, bounds_ref, wkt = {}, None, None
    with ThreadPoolExecutor(max_workers=12) as pool:
        for y in tqdm(sorted(per_year), desc="seasons"):
            results = list(pool.map(_read_sfed, per_year[y]))
            arrs = [r[0] for r in results]
            if bounds_ref is None:
                bounds_ref, wkt = results[0][1], results[0][2]
            if any(a.shape != arrs[0].shape for a in arrs):
                raise RuntimeError(f"{y}: inconsistent window shapes")
            season_max[y] = np.nanmax(np.stack(arrs), axis=0)

    stack = np.stack([season_max[y] for y in sorted(season_max)])
    rec_any = np.nanmean(stack >= THRESH_ANY, axis=0).astype("float32")
    rec_sub = np.nanmean(stack >= THRESH_SUB, axis=0).astype("float32")
    mean_max = np.nanmean(stack, axis=0).astype("float32")

    xmin, ymin, xmax, ymax = bounds_ref
    transform = rasterio.transform.from_bounds(xmin, ymin, xmax, ymax,
                                               stack.shape[2], stack.shape[1])
    profile = dict(driver="GTiff", width=stack.shape[2], height=stack.shape[1],
                   count=3, dtype="float32", crs=wkt, transform=transform,
                   compress="deflate", nodata=np.nan)
    buf = io.BytesIO()
    with rasterio.open(buf, "w", **profile) as dst:
        for i, (band, desc) in enumerate([(rec_any, f"recurrence_sfed_ge_{THRESH_ANY}"),
                                          (rec_sub, f"recurrence_sfed_ge_{THRESH_SUB}"),
                                          (mean_max, "mean_seasonal_max_sfed")], 1):
            dst.write(band, i)
            dst.set_band_description(i, desc)
    stratus.upload_blob_data(buf.getvalue(), OUT_TIF, stage="dev")
    tqdm.write(f"Uploaded {OUT_TIF}")

    years = sorted(season_max)
    profile_y = dict(profile, count=len(years))
    buf_y = io.BytesIO()
    with rasterio.open(buf_y, "w", **profile_y) as dst:
        for i, y in enumerate(years, 1):
            dst.write(season_max[y].astype("float32"), i)
            dst.set_band_description(i, str(y))
    stratus.upload_blob_data(buf_y.getvalue(), OUT_YEARLY, stage="dev")
    tqdm.write(f"Uploaded {OUT_YEARLY} ({len(years)} bands)")

    cod2 = codab.load_codab_from_blob("uga", admin_level=2)
    srcs = [NumPyRasterSource(b, xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
                              name=nm, srs_wkt=wkt)
            for b, nm in [(rec_any, "rec_any"), (rec_sub, "rec_sub"), (mean_max, "mean_max")]]
    df = exact_extract(srcs, cod2, ["mean"], include_cols=["ADM2_PCODE"], output="pandas")
    df = df.rename(columns={"ADM2_PCODE": "pcode", "rec_any_mean": "recurrence_any",
                            "rec_sub_mean": "recurrence_substantial", "mean_max_mean": "mean_seasonal_max"})
    if len(df) != 135 or df["recurrence_any"].isna().any():
        raise RuntimeError("district extraction incomplete")
    stratus.upload_parquet_to_blob(df, OUT_PARQUET, stage="dev")
    tqdm.write(f"Uploaded {OUT_PARQUET} ({len(df)} districts). Done.")


if __name__ == "__main__":
    main()
