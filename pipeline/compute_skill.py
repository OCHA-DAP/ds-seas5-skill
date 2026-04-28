import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX, TARGET_ISO3S
from src.datasources.era5 import load_era5
from src.datasources.seas5 import load_seas5
from src.skill import run_all_combinations

SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats.parquet"
PAIRED_BLOB = f"{PROJECT_PREFIX}/processed/paired_yearly.parquet"


def main() -> None:
    engine = stratus.get_engine("prod")

    # Look up ADM0 pcodes and country names from polygon table
    with engine.connect() as conn:
        df_adm0 = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=0 AND iso3 IN %s",
            conn,
            params=(tuple(TARGET_ISO3S),),
        )
    pcode_by_iso3 = dict(zip(df_adm0["iso3"], df_adm0["pcode"]))
    name_by_iso3 = dict(zip(df_adm0["iso3"], df_adm0["name"]))

    missing = [iso3 for iso3 in TARGET_ISO3S if iso3 not in pcode_by_iso3]
    if missing:
        raise ValueError(f"No ADM0 pcode found in polygon table for: {missing}")

    all_skill: list[pd.DataFrame] = []
    all_paired: list[pd.DataFrame] = []

    for iso3 in tqdm(TARGET_ISO3S, desc="pcodes"):
        pcode = pcode_by_iso3[iso3]
        country_name = name_by_iso3[iso3]
        tqdm.write(f"\nLoading {country_name} ({pcode})...")

        df_seas5 = load_seas5(pcode)
        df_era5 = load_era5(pcode)
        tqdm.write(f"  SEAS5: {len(df_seas5):,} rows | ERA5: {len(df_era5):,} rows")

        with tqdm(total=144, desc=pcode, leave=False) as pbar:
            df_skill, df_paired = run_all_combinations(
                pcode, country_name, df_seas5, df_era5, progress=pbar
            )

        all_skill.append(df_skill)
        all_paired.append(df_paired)

    df_skill_all = pd.concat(all_skill, ignore_index=True)
    df_paired_all = pd.concat(all_paired, ignore_index=True)

    tqdm.write(f"\nSaving skill stats  ({len(df_skill_all):,} rows) → {SKILL_BLOB}")
    stratus.upload_parquet_to_blob(df_skill_all, SKILL_BLOB, stage="dev")

    tqdm.write(f"Saving paired yearly ({len(df_paired_all):,} rows) → {PAIRED_BLOB}")
    stratus.upload_parquet_to_blob(df_paired_all, PAIRED_BLOB, stage="dev")

    tqdm.write("Done.")


if __name__ == "__main__":
    main()
