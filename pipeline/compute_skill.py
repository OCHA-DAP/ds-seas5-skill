import json
import sys
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX
from src.datasources.era5 import load_era5
from src.datasources.seas5 import load_seas5
from src.skill import run_all_combinations

SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats.parquet"
PAIRED_BLOB = f"{PROJECT_PREFIX}/processed/paired_yearly.parquet"
RAINY_BLOB = f"{PROJECT_PREFIX}/processed/rainy_pairs.parquet"

CHECKPOINT_DIR = Path(__file__).parent / ".checkpoint"
COMPLETED_FILE = CHECKPOINT_DIR / "completed.json"
SKILL_PARTIAL = CHECKPOINT_DIR / "skill_partial.parquet"
PAIRED_PARTIAL = CHECKPOINT_DIR / "paired_partial.parquet"
SAVE_EVERY = 10  # checkpoint after this many countries


def _compute_rainy_pairs(df_paired: pd.DataFrame) -> pd.DataFrame:
    """Classify each (pcode, trimester) as rainy (≥25% of annual rainfall)."""
    clim = (
        df_paired.dropna(subset=["obs_mean"])
        .drop_duplicates(["pcode", "trimester", "season_year"])
        .assign(obs_orig=lambda d: np.expm1(d["obs_mean"]))
        .groupby(["pcode", "trimester"])["obs_orig"]
        .mean()
        .reset_index(name="mean_mm_day")
    )
    annual = clim.groupby("pcode")["mean_mm_day"].sum().rename("annual")
    clim = clim.merge(annual.reset_index(), on="pcode")
    clim["is_rainy"] = 3 * clim["mean_mm_day"] / clim["annual"] >= 0.25
    return clim[["pcode", "trimester", "is_rainy"]]


def _load_checkpoint() -> tuple[set[str], list[pd.DataFrame], list[pd.DataFrame]]:
    if not COMPLETED_FILE.exists():
        return set(), [], []
    completed = set(json.loads(COMPLETED_FILE.read_text()))
    all_skill = [pd.read_parquet(SKILL_PARTIAL)] if SKILL_PARTIAL.exists() else []
    all_paired = [pd.read_parquet(PAIRED_PARTIAL)] if PAIRED_PARTIAL.exists() else []
    tqdm.write(f"Resuming from checkpoint: {len(completed)} countries already done")
    return completed, all_skill, all_paired


def _save_checkpoint(
    completed: set[str],
    all_skill: list[pd.DataFrame],
    all_paired: list[pd.DataFrame],
) -> None:
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    COMPLETED_FILE.write_text(json.dumps(sorted(completed)))
    if all_skill:
        pd.concat(all_skill, ignore_index=True).to_parquet(SKILL_PARTIAL, index=False)
    if all_paired:
        pd.concat(all_paired, ignore_index=True).to_parquet(PAIRED_PARTIAL, index=False)


def _clear_checkpoint() -> None:
    for f in [COMPLETED_FILE, SKILL_PARTIAL, PAIRED_PARTIAL]:
        if f.exists():
            f.unlink()
    if CHECKPOINT_DIR.exists():
        try:
            CHECKPOINT_DIR.rmdir()
        except OSError:
            pass


def main() -> None:
    engine = stratus.get_engine("prod")

    # Load all ADM0 entries from polygon table
    with engine.connect() as conn:
        df_adm0 = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=0",
            conn,
        )
    tqdm.write(f"Found {len(df_adm0):,} ADM0 entries in polygon table")

    completed, all_skill, all_paired = _load_checkpoint()
    since_checkpoint = 0

    for _, adm0_row in tqdm(df_adm0.iterrows(), total=len(df_adm0), desc="pcodes"):
        pcode = adm0_row["pcode"]
        iso3 = adm0_row["iso3"]
        country_name = adm0_row["name"]

        if pcode in completed:
            continue

        df_seas5 = load_seas5(pcode)
        df_era5 = load_era5(pcode)
        if df_seas5.empty and df_era5.empty:
            completed.add(pcode)
            continue

        tqdm.write(f"\n{country_name} ({pcode}): SEAS5 {len(df_seas5):,} rows | ERA5 {len(df_era5):,} rows")

        with tqdm(total=144, desc=pcode, leave=False) as pbar:
            df_skill, df_paired = run_all_combinations(
                pcode, iso3, country_name, df_seas5, df_era5, progress=pbar
            )

        all_skill.append(df_skill)
        all_paired.append(df_paired)
        completed.add(pcode)
        since_checkpoint += 1

        if since_checkpoint >= SAVE_EVERY:
            tqdm.write("  [checkpoint saved]")
            _save_checkpoint(completed, all_skill, all_paired)
            since_checkpoint = 0

    df_skill_all = pd.concat(all_skill, ignore_index=True)
    df_paired_all = pd.concat(all_paired, ignore_index=True)

    tqdm.write(f"\nSaving skill stats  ({len(df_skill_all):,} rows) -> {SKILL_BLOB}")
    stratus.upload_parquet_to_blob(df_skill_all, SKILL_BLOB, stage="dev")

    tqdm.write(f"Saving paired yearly ({len(df_paired_all):,} rows) -> {PAIRED_BLOB}")
    stratus.upload_parquet_to_blob(df_paired_all, PAIRED_BLOB, stage="dev")

    df_rainy = _compute_rainy_pairs(df_paired_all)
    tqdm.write(f"Saving rainy pairs   ({len(df_rainy):,} rows) -> {RAINY_BLOB}")
    stratus.upload_parquet_to_blob(df_rainy, RAINY_BLOB, stage="dev")

    _clear_checkpoint()
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
