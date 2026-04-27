import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX, TARGET_PCODES
from src.datasources.era5 import load_era5
from src.datasources.seas5 import load_seas5
from src.skill import run_all_combinations

SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats.parquet"
PAIRED_BLOB = f"{PROJECT_PREFIX}/processed/paired_yearly.parquet"


def main() -> None:
    all_skill: list[pd.DataFrame] = []
    all_paired: list[pd.DataFrame] = []

    for pcode in tqdm(TARGET_PCODES, desc="pcodes"):
        print(f"\nLoading data for {pcode}...")
        df_seas5 = load_seas5(pcode)
        df_era5 = load_era5(pcode)
        print(f"  SEAS5: {len(df_seas5):,} rows | ERA5: {len(df_era5):,} rows")

        with tqdm(total=144, desc=pcode, leave=False) as pbar:
            df_skill, df_paired = run_all_combinations(
                pcode, df_seas5, df_era5, progress=pbar
            )

        all_skill.append(df_skill)
        all_paired.append(df_paired)

    df_skill_all = pd.concat(all_skill, ignore_index=True)
    df_paired_all = pd.concat(all_paired, ignore_index=True)

    print(f"\nSaving skill stats  ({len(df_skill_all):,} rows) → {SKILL_BLOB}")
    stratus.upload_parquet_to_blob(df_skill_all, SKILL_BLOB, stage="prod")

    print(f"Saving paired yearly ({len(df_paired_all):,} rows) → {PAIRED_BLOB}")
    stratus.upload_parquet_to_blob(df_paired_all, PAIRED_BLOB, stage="prod")

    print("Done.")


if __name__ == "__main__":
    main()
