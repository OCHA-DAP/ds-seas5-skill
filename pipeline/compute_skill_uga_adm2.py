"""SEAS5 forecast skill for Uganda districts (CODAB adm2), from project parquets.

Uganda is outside the rasterstats DB's adm2 coverage (public.iso3 has
max_adm_level=1 for UGA), so the inputs here are the district zonal stats
computed by compute_uga_district_stats.py — same schema as public.seas5 /
public.era5 — read from the project blob instead of the DB. The skill / RP
methodology is src.skill.run_all_combinations, identical to the app's
adm1/adm2 pipelines.

Writes (dev blob):
  {PROJECT_PREFIX}/processed/uga/skill_stats_adm2.parquet
  {PROJECT_PREFIX}/processed/uga/paired_yearly_adm2.parquet
  {PROJECT_PREFIX}/processed/uga/skill_stats_detrended_adm2.parquet
  {PROJECT_PREFIX}/processed/uga/paired_yearly_detrended_adm2.parquet

Run:  uv run python pipeline/compute_skill_uga_adm2.py            # all 135 districts
      uv run python pipeline/compute_skill_uga_adm2.py --pcodes UG3072 UG3084
"""

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from ocha_stratus import codab
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402
from src.skill import run_all_combinations  # noqa: E402

UGA_DIR = f"{PROJECT_PREFIX}/processed/uga"
OUT_BLOBS = {
    "skill": f"{UGA_DIR}/skill_stats_adm2.parquet",
    "paired": f"{UGA_DIR}/paired_yearly_adm2.parquet",
    "skill_dt": f"{UGA_DIR}/skill_stats_detrended_adm2.parquet",
    "paired_dt": f"{UGA_DIR}/paired_yearly_detrended_adm2.parquet",
}

_seas5: pd.DataFrame | None = None
_era5: pd.DataFrame | None = None


def _init_worker() -> None:
    # Each worker loads the two input parquets once, then slices per pcode.
    global _seas5, _era5
    _seas5 = stratus.load_parquet_from_blob(f"{UGA_DIR}/seas5_adm2.parquet", stage="dev")
    _era5 = stratus.load_parquet_from_blob(f"{UGA_DIR}/era5_adm2.parquet", stage="dev")


def _compute_unit(unit: tuple[str, str]):
    pcode, name = unit
    try:
        df_s = _seas5[_seas5["pcode"] == pcode].reset_index(drop=True)
        df_e = _era5[_era5["pcode"] == pcode].reset_index(drop=True)
        if df_s.empty or df_e.empty:
            return pcode, "empty", None
        df_skill, df_paired = run_all_combinations(pcode, "UGA", name, df_s, df_e)
        df_skill_dt, df_paired_dt = run_all_combinations(pcode, "UGA", name, df_s, df_e, detrend=True)
        return pcode, "ok", (df_skill, df_paired, df_skill_dt, df_paired_dt)
    except Exception as e:  # noqa: BLE001 — fault isolation: report, don't crash the pool
        return pcode, f"error: {type(e).__name__}: {e}", None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--pcodes", nargs="+", help="restrict to specific district pcodes")
    args = parser.parse_args()

    cod = codab.load_codab_from_blob("uga", admin_level=2)
    units = [(p, n) for p, n in zip(cod["ADM2_PCODE"], cod["ADM2_EN"])]
    if args.pcodes:
        units = [u for u in units if u[0] in args.pcodes]
    tqdm.write(f"{len(units)} districts, {args.workers} workers")

    parts: dict[str, list[pd.DataFrame]] = {k: [] for k in OUT_BLOBS}
    failed: list[tuple[str, str]] = []
    empty: list[str] = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as pool:
        futures = {pool.submit(_compute_unit, u): u[0] for u in units}
        for fut in tqdm(as_completed(futures), total=len(futures), desc="districts"):
            pcode, status, dfs = fut.result()
            if status == "ok":
                for k, df in zip(OUT_BLOBS, dfs):
                    parts[k].append(df)
            elif status == "empty":
                empty.append(pcode)
            else:
                failed.append((pcode, status))
                tqdm.write(f"  {pcode}: {status}")

    if failed:
        raise RuntimeError(f"{len(failed)} district(s) failed — not uploading: {failed[:10]}")
    if empty:
        # Input parquets cover every CODAB district; a hole means the zonal-stats
        # run is incomplete, not that the district has no data.
        raise RuntimeError(f"{len(empty)} district(s) had no input rows: {empty[:10]} — "
                           f"is compute_uga_district_stats.py finished?")

    for k, blob in OUT_BLOBS.items():
        df = pd.concat(parts[k], ignore_index=True)
        tqdm.write(f"Saving {k} ({len(df):,} rows) -> {blob}")
        stratus.upload_parquet_to_blob(df, blob, stage="dev")
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
