"""Uganda district (CODAB adm2) zonal stats for SEAS5 + ERA5 monthly rasters.

Uganda is not in the Forecast × HNRP country scope, so public.seas5/public.era5
hold it only at adm0/adm1 (the 4 statistical regions). The Uganda country-team
analysis needs district resolution, so this one-off computes the same zonal
stats over the 135 CODAB adm2 districts with exactextract (coverage-weighted;
matches the DB's upsampled means within ~1% — verified against UG1–UG4 for
issued 1991-01 lt0).

Output parquets mirror the public.seas5 / public.era5 schemas (skill code only
consumes `mean`, `issued_date`, `valid_date`; `count`/`sum` here are
coverage-fraction based, NOT upsampled-cell counts like the DB — do not compare
those columns across sources):

  {PROJECT_PREFIX}/processed/uga/seas5_adm2.parquet   (dev blob)
  {PROJECT_PREFIX}/processed/uga/era5_adm2.parquet    (dev blob)

Run:  uv run python pipeline/compute_uga_district_stats.py            # full (~4.4k rasters)
      uv run python pipeline/compute_uga_district_stats.py --limit 5  # smoke test, no upload
"""

import argparse
import io
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
import rasterio
from rasterio.windows import bounds as window_bounds
from rasterio.windows import from_bounds
from exactextract import exact_extract
from exactextract.raster import NumPyRasterSource
from ocha_stratus import codab
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

SEAS5_RE = re.compile(r"precip_em_i(\d{4}-\d{2}-\d{2})_lt(\d)\.tif$")
ERA5_RE = re.compile(r"precip_reanalysis_v(\d{4}-\d{2}-\d{2})\.tif$")
STATS = ["mean", "median", "min", "max", "count", "sum", "stdev"]

SEAS5_BLOB = f"{PROJECT_PREFIX}/processed/uga/seas5_adm2.parquet"
ERA5_BLOB = f"{PROJECT_PREFIX}/processed/uga/era5_adm2.parquet"

CHECKPOINT_DIR = Path(__file__).parent / ".checkpoint_uga"
DONE_FILE = CHECKPOINT_DIR / "done.json"
PARTIAL = CHECKPOINT_DIR / "partial.parquet"
SAVE_EVERY = 200

# Uganda bbox padded by one 0.4° cell so edge districts keep full coverage.
UGA_BOX = (29.0, -2.0, 35.5, 4.7)


# Per-call overhead in exact_extract (vector prep + coverage) is ~0.4s — batching
# many same-grid rasters into ONE call amortizes it; downloads run in threads.
CHUNK = 150


def _download_clip(blob_name: str, var: str) -> NumPyRasterSource:
    # Raw bytes + an explicit windowed read: open_blob_cog's fsspec layer caches
    # every file it touches for the process lifetime, which OOMs a ~4.4k-raster
    # sweep. This path holds nothing beyond the returned window.
    data = stratus.load_blob_data(blob_name, container_name="raster", stage="prod")
    with rasterio.open(io.BytesIO(data)) as ds:
        w = (from_bounds(*UGA_BOX, ds.transform)
             .round_offsets(op="floor").round_lengths(op="ceil"))
        arr = ds.read(1, window=w)
        xmin, ymin, xmax, ymax = window_bounds(w, ds.transform)
        return NumPyRasterSource(
            arr, xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax,
            nodata=ds.nodata, name=var, srs_wkt=ds.crs.to_wkt(),
        )


def _meta_for(blob_name: str) -> dict:
    if m := SEAS5_RE.search(blob_name):
        issued = pd.Timestamp(m.group(1))
        lt = int(m.group(2))
        return {"issued_date": issued, "leadtime": lt,
                "valid_date": issued + pd.DateOffset(months=lt)}
    if m := ERA5_RE.search(blob_name):
        return {"valid_date": pd.Timestamp(m.group(1))}
    raise ValueError(f"unrecognized raster name: {blob_name}")


def _extract_chunk(blob_names: list[str], gdf, pool) -> pd.DataFrame:
    """One exact_extract call over many same-grid rasters -> long-format rows.

    Sources get synthetic names v0..vN: exactextract prefixes result columns
    with the source name (multi-source) or omits it entirely (single source).
    """
    variables = [f"v{i}" for i in range(len(blob_names))]
    srcs = list(pool.map(_download_clip, blob_names, variables))
    wide = exact_extract(srcs, gdf, STATS, include_cols=["ADM2_PCODE"], output="pandas")
    out = []
    for name, var in zip(blob_names, variables):
        prefix = f"{var}_" if len(blob_names) > 1 else ""
        cols = {f"{prefix}{s}": s for s in STATS}
        df = wide[["ADM2_PCODE", *cols]].rename(columns={"ADM2_PCODE": "pcode", **cols})
        df = df.rename(columns={"stdev": "std"})
        df["iso3"] = "UGA"
        df["adm_level"] = 2
        for k, v in _meta_for(name).items():
            df[k] = v
        df["_blob"] = name
        out.append(df)
    return pd.concat(out, ignore_index=True)


def _load_checkpoint() -> tuple[set, list]:
    if not DONE_FILE.exists():
        return set(), []
    done = set(json.loads(DONE_FILE.read_text()))
    parts = [pd.read_parquet(PARTIAL)] if PARTIAL.exists() else []
    tqdm.write(f"Resuming: {len(done)} rasters already done")
    return done, parts


def _save_checkpoint(done: set, parts: list) -> None:
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    DONE_FILE.write_text(json.dumps(sorted(done)))
    if parts:
        pd.concat(parts, ignore_index=True).to_parquet(PARTIAL, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, help="process only N rasters per source (smoke test; skips upload)")
    args = parser.parse_args()

    gdf = codab.load_codab_from_blob("uga", admin_level=2)
    if len(gdf) != 135:
        raise RuntimeError(f"expected 135 UGA adm2 districts, got {len(gdf)}")

    seas5 = sorted(
        n for n in stratus.list_container_blobs(
            name_starts_with="seas5/monthly/processed/", container_name="raster", stage="prod")
        if SEAS5_RE.search(n)
    )
    era5 = sorted(
        n for n in stratus.list_container_blobs(
            name_starts_with="era5/monthly/processed/", container_name="raster", stage="prod")
        if ERA5_RE.search(n)
    )
    if args.limit:
        seas5, era5 = seas5[: args.limit], era5[: args.limit]
    tqdm.write(f"{len(seas5):,} SEAS5 + {len(era5):,} ERA5 rasters")

    done, parts = _load_checkpoint()
    # Chunks stay within one source type so every raster in an exact_extract
    # call shares the same grid.
    chunks = []
    for group in (seas5, era5):
        pend = [n for n in group if n not in done]
        chunks += [pend[i : i + CHUNK] for i in range(0, len(pend), CHUNK)]
    n_pend = sum(len(c) for c in chunks)
    tqdm.write(f"{n_pend:,} to compute in {len(chunks)} chunks, {args.workers} download threads")

    failed: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for chunk in tqdm(chunks, desc="chunks"):
            try:
                parts.append(_extract_chunk(chunk, gdf, pool))
                done.update(chunk)
            except Exception as e:  # noqa: BLE001 — collected and re-raised below
                failed.append((chunk[0], f"{type(e).__name__}: {e}"))
            _save_checkpoint(done, parts)
            done, parts = _load_checkpoint()
    _save_checkpoint(done, parts)

    if failed:
        for name, err in failed[:20]:
            tqdm.write(f"  FAILED {name}: {err}")
        raise RuntimeError(
            f"{len(failed)} raster(s) failed — NOT uploading partial output. "
            f"Re-run to retry (checkpoint keeps completed work)."
        )

    df = pd.concat(parts, ignore_index=True)
    df_s = df[df["_blob"].str.contains("seas5/")].drop(columns=["_blob"])
    df_e = df[df["_blob"].str.contains("era5/")].drop(columns=["_blob", "issued_date", "leadtime"], errors="ignore")

    n_exp_s = len(seas5) * 135
    if len(df_s) != n_exp_s:
        raise RuntimeError(f"SEAS5 row count {len(df_s):,} != expected {n_exp_s:,}")
    n_exp_e = len(era5) * 135
    if len(df_e) != n_exp_e:
        raise RuntimeError(f"ERA5 row count {len(df_e):,} != expected {n_exp_e:,}")

    if args.limit:
        tqdm.write(f"Smoke test OK: {len(df_s):,} SEAS5 rows, {len(df_e):,} ERA5 rows. No upload.")
        return

    for out, blob in [(df_s, SEAS5_BLOB), (df_e, ERA5_BLOB)]:
        tqdm.write(f"Uploading {len(out):,} rows -> {blob} (dev)")
        stratus.upload_parquet_to_blob(out, blob, stage="dev")
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
