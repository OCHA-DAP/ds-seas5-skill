"""Compute SEAS5 forecast skill statistics at ADM2 level, scoped countries only.

Mirrors compute_skill_adm1.py (whose per-unit worker it reuses) but queries
adm_level=2 from the polygon table, restricted to the countries in the
Forecast × HNRP analysis (HNRP and/or recent IPC coverage) that HAVE adm2
polygons + zonal stats in the prod DB — 22 of the 45; the other 23 have no
adm2 in public.polygon and are excluded on that basis (a coverage gap on our
side, documented in the KB pcode audit).

Run:  uv run python pipeline/compute_skill_adm2.py                 # parallel (default 8 workers)
      uv run python pipeline/compute_skill_adm2.py --workers 4
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.constants import PROJECT_PREFIX  # noqa: E402
from compute_skill_adm1 import _compute_unit  # noqa: E402 — level-agnostic worker

# The Forecast × HNRP scope (HNRP or recent-IPC countries) ∩ adm2 availability.
ADM2_ISO3S = [
    "AFG", "BFA", "CAF", "CMR", "COD", "COL", "GTM", "HND", "HTI", "MLI", "MMR",
    "MOZ", "NER", "NGA", "SDN", "SLV", "SOM", "SSD", "TCD", "UKR", "VEN", "YEM",
]

SKILL_BLOB     = f"{PROJECT_PREFIX}/processed/skill_stats_adm2.parquet"
PAIRED_BLOB    = f"{PROJECT_PREFIX}/processed/paired_yearly_adm2.parquet"
SKILL_DT_BLOB  = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm2.parquet"
PAIRED_DT_BLOB = f"{PROJECT_PREFIX}/processed/paired_yearly_detrended_adm2.parquet"

CHECKPOINT_DIR = Path(__file__).parent / ".checkpoint_adm2"
COMPLETED_FILE = CHECKPOINT_DIR / "completed.json"
PARTIALS = {
    "skill":     CHECKPOINT_DIR / "skill_partial.parquet",
    "paired":    CHECKPOINT_DIR / "paired_partial.parquet",
    "skill_dt":  CHECKPOINT_DIR / "skill_dt_partial.parquet",
    "paired_dt": CHECKPOINT_DIR / "paired_dt_partial.parquet",
}
SAVE_EVERY = 100  # checkpoint after this many ADM2 units


def _load_checkpoint():
    if not COMPLETED_FILE.exists():
        return set(), {k: [] for k in PARTIALS}
    completed = set(json.loads(COMPLETED_FILE.read_text()))
    parts = {k: ([pd.read_parquet(p)] if p.exists() else []) for k, p in PARTIALS.items()}
    tqdm.write(f"Resuming from checkpoint: {len(completed)} ADM2 units already done")
    return completed, parts


def _save_checkpoint(completed, parts):
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    COMPLETED_FILE.write_text(json.dumps(sorted(completed)))
    for k, path in PARTIALS.items():
        if parts[k]:
            pd.concat(parts[k], ignore_index=True).to_parquet(path, index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    engine = stratus.get_engine("prod")
    ph = ",".join(["%s"] * len(ADM2_ISO3S))
    with engine.connect() as conn:
        df_adm2 = pd.read_sql(
            f"SELECT pcode, iso3, name FROM public.polygon "
            f"WHERE adm_level=2 AND iso3 IN ({ph})",
            conn, params=tuple(ADM2_ISO3S),
        )
    tqdm.write(f"{len(df_adm2):,} ADM2 units across {df_adm2['iso3'].nunique()} countries")

    completed, parts = _load_checkpoint()
    since_checkpoint = 0
    failed: list[tuple[str, str]] = []
    pending = [
        (r["pcode"], r["iso3"], r["name"])
        for _, r in df_adm2.iterrows()
        if r["pcode"] not in completed
    ]
    tqdm.write(f"{len(pending):,} units to compute with {args.workers} workers")

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_compute_unit, u): u[0] for u in pending}
        for fut in tqdm(as_completed(futures), total=len(futures), desc="adm2"):
            pcode, status, dfs = fut.result()
            if status == "ok":
                for k, df in zip(("skill", "paired", "skill_dt", "paired_dt"), dfs):
                    parts[k].append(df)
            elif status != "empty":
                failed.append((pcode, status))
                tqdm.write(f"  {pcode}: {status}")
            completed.add(pcode)
            since_checkpoint += 1
            if since_checkpoint >= SAVE_EVERY:
                _save_checkpoint(completed, parts)
                # Re-read consolidated partials so the lists don't grow unboundedly.
                completed, parts = _load_checkpoint()
                since_checkpoint = 0

    _save_checkpoint(completed, parts)
    if failed:
        tqdm.write(f"{len(failed)} unit(s) failed: {failed[:20]}")
    for k, blob in [("skill", SKILL_BLOB), ("paired", PAIRED_BLOB),
                    ("skill_dt", SKILL_DT_BLOB), ("paired_dt", PAIRED_DT_BLOB)]:
        df = pd.concat(parts[k], ignore_index=True)
        tqdm.write(f"Saving {k} ({len(df):,} rows) -> {blob}")
        stratus.upload_parquet_to_blob(df, blob, stage="dev")
    tqdm.write("Done.")


if __name__ == "__main__":
    main()
