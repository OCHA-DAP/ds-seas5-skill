"""Compute per-pcode monthly ERA5 climatology and upload to blob.

One file per admin level, all with the same schema
    pcode, iso3, name, adm_level, month, mean_mm_day
(mean of every ERA5 month of that calendar month over the whole 1981–present record;
the exports' rainy flag and the HDX signal's in-season rule both derive from it via
src/season.py).

    --level 0  ->  ds-seas5-skill/processed/monthly_clim.parquet        (countries)
    --level 1  ->  ds-seas5-skill/processed/monthly_clim_adm1.parquet   (admin-1)
    --level 2  ->  ds-seas5-skill/processed/monthly_clim_adm2.parquet   (admin-2, same
                   country scope as compute_skill_adm2.py)

Units = the pcodes present in the matching skill_stats*.parquet, so the climatology
covers exactly what the skill stats cover. Use --pcodes to refresh a subset (merged
into the existing blob).

Run:  uv run python pipeline/compute_monthly_clim.py --level 1
      uv run python pipeline/compute_monthly_clim.py --level 2 --pcodes ET0101 ET0102
"""

import argparse
import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX  # noqa: E402
from src.season import monthly_clim_from_era5  # noqa: E402

SUFFIX = {0: "", 1: "_adm1", 2: "_adm2"}
COLUMNS = ["pcode", "iso3", "name", "adm_level", "month", "mean_mm_day"]


def clim_blob(level: int) -> str:
    return f"{PROJECT_PREFIX}/processed/monthly_clim{SUFFIX[level]}.parquet"


def skill_blob(level: int) -> str:
    return f"{PROJECT_PREFIX}/processed/skill_stats{SUFFIX[level]}.parquet"


def load_era5_many(engine, pcodes: list[str], chunk: int = 500) -> pd.DataFrame:
    """ERA5 monthly means for many pcodes, in chunks (2,330 admin-1 units in one IN() is
    fine for Postgres but slow to stream; chunking keeps memory flat)."""
    parts = []
    with engine.connect() as conn:
        for i in range(0, len(pcodes), chunk):
            sub = pcodes[i:i + chunk]
            ph = ",".join(["%s"] * len(sub))
            parts.append(pd.read_sql(
                f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
                conn, params=tuple(sub), parse_dates=["valid_date"],
            ))
            print(f"  ERA5 {min(i + chunk, len(pcodes)):,}/{len(pcodes):,} pcodes")
    df = pd.concat(parts, ignore_index=True)
    df["mean"] = pd.to_numeric(df["mean"], errors="coerce")
    return df.dropna(subset=["mean"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--level", type=int, choices=(0, 1, 2), default=0)
    parser.add_argument(
        "--pcodes", nargs="+", metavar="PCODE",
        help="Only compute for these pcodes and merge with existing blob data",
    )
    args = parser.parse_args()
    level, out_blob = args.level, clim_blob(args.level)

    engine = stratus.get_engine("prod")
    with engine.connect() as conn:
        poly = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=%s",
            conn, params=(level,),
        )
    info = poly.set_index("pcode")[["iso3", "name"]]

    if args.pcodes:
        print(f"Targeted run (level {level}) for: {args.pcodes}")
        # Only a missing blob means "start fresh" — any other failure must abort, or
        # the upload below would overwrite the blob with just the targeted pcodes.
        try:
            df_base = stratus.load_parquet_from_blob(out_blob, stage="dev")
            df_base = df_base[~df_base["pcode"].isin(args.pcodes)]
        except ResourceNotFoundError:
            print(f"{out_blob}: not found, starting fresh")
            df_base = pd.DataFrame()
        pcodes = list(args.pcodes)
    else:
        df_skill = stratus.load_parquet_from_blob(skill_blob(level), stage="dev")
        pcodes = sorted(df_skill["pcode"].dropna().unique().tolist())
        df_base = pd.DataFrame()
        print(f"Full run (level {level}): {len(pcodes):,} pcodes from {skill_blob(level)}")

    print("Loading ERA5 data...")
    era5 = load_era5_many(engine, pcodes)
    print(f"  {len(era5):,} ERA5 rows for {era5['pcode'].nunique():,} pcodes")

    mc = monthly_clim_from_era5(era5)
    n_months = mc.groupby("pcode")["month"].nunique()
    partial = n_months[n_months < 12]
    if len(partial):
        print(f"  {len(partial)} pcode(s) with <12 climatology months, dropped: "
              f"{partial.index.tolist()[:10]}{'…' if len(partial) > 10 else ''}")
        mc = mc[~mc["pcode"].isin(partial.index)]
    missing = sorted(set(pcodes) - set(mc["pcode"]))
    if missing:
        print(f"  {len(missing)} requested pcode(s) have no ERA5 rows: {missing[:10]}"
              f"{'…' if len(missing) > 10 else ''}")

    mc = mc[mc["pcode"].isin(info.index)].merge(info, left_on="pcode", right_index=True)
    mc["adm_level"] = level
    df_new = mc[COLUMNS]
    df_out = pd.concat([df_base, df_new], ignore_index=True) if not df_base.empty else df_new
    df_out = df_out.sort_values(["pcode", "month"]).reset_index(drop=True)

    print(f"\nSaving monthly climatology ({len(df_out):,} rows, "
          f"{df_out['pcode'].nunique():,} pcodes) -> {out_blob}")
    stratus.upload_parquet_to_blob(df_out, out_blob, stage="dev")
    print("Done.")


if __name__ == "__main__":
    main()
