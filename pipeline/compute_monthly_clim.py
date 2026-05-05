"""Compute per-pcode monthly ERA5 climatology and upload to blob.

Produces monthly_clim.parquet with columns:
    pcode, iso3, country_name, month, mean_mm_day

Run for all monitored countries (default) or a subset:
    uv run pipeline/compute_monthly_clim.py
    uv run pipeline/compute_monthly_clim.py --pcodes NE BF TD
"""

import argparse
import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX

MONTHLY_CLIM_BLOB = f"{PROJECT_PREFIX}/processed/monthly_clim.parquet"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pcodes", nargs="+", metavar="PCODE",
        help="Only compute for these pcodes and merge with existing blob data",
    )
    args = parser.parse_args()

    engine = stratus.get_engine("prod")

    with engine.connect() as conn:
        df_adm0 = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=0",
            conn,
        )
    pcode_to_info = df_adm0.set_index("pcode")[["iso3", "name"]].to_dict("index")

    if args.pcodes:
        print(f"Targeted run for: {args.pcodes}")
        try:
            df_base = stratus.load_parquet_from_blob(MONTHLY_CLIM_BLOB)
            df_base = df_base[~df_base["pcode"].isin(args.pcodes)]
        except Exception:
            df_base = pd.DataFrame()
        pcodes_to_run = args.pcodes
    else:
        df_skill = stratus.load_parquet_from_blob(f"{PROJECT_PREFIX}/processed/skill_stats.parquet")
        pcodes_to_run = df_skill["pcode"].dropna().unique().tolist()
        df_base = pd.DataFrame()
        print(f"Full run: {len(pcodes_to_run)} monitored pcodes")

    # Load all ERA5 in a single query to avoid per-pcode connection churn
    placeholders = ",".join(["%s"] * len(pcodes_to_run))
    print("Loading ERA5 data...")
    with engine.connect() as conn:
        df_era5 = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({placeholders})",
            conn,
            params=tuple(pcodes_to_run),
            parse_dates=["valid_date"],
        )
    print(f"  {len(df_era5):,} ERA5 rows loaded for {df_era5['pcode'].nunique()} pcodes")

    df_era5["month"] = df_era5["valid_date"].dt.month
    monthly_all = (
        df_era5.groupby(["pcode", "month"])["mean"]
        .mean()
        .reset_index()
        .rename(columns={"mean": "mean_mm_day"})
    )

    results = []
    for pcode in pcodes_to_run:
        info = pcode_to_info.get(pcode)
        if info is None:
            continue
        mc = monthly_all[monthly_all["pcode"] == pcode]
        if len(mc) < 12:
            print(f"  {pcode}: only {len(mc)} months of ERA5 data, skipping")
            continue
        mc = mc.copy()
        mc["iso3"] = info["iso3"]
        mc["country_name"] = info["name"]
        results.append(mc[["pcode", "iso3", "country_name", "month", "mean_mm_day"]])

    if not results:
        print("No results — nothing to upload.")
        return

    df_new = pd.concat(results, ignore_index=True)
    df_out = pd.concat([df_base, df_new], ignore_index=True) if not df_base.empty else df_new

    print(f"\nSaving monthly climatology ({len(df_out):,} rows) -> {MONTHLY_CLIM_BLOB}")
    stratus.upload_parquet_to_blob(df_out, MONTHLY_CLIM_BLOB, stage="dev")
    print("Done.")


if __name__ == "__main__":
    main()
