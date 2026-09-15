"""Compute SEAS5 forecast skill statistics at ADM2 level, scoped countries only.

Mirrors compute_skill_adm1.py (whose per-unit worker it reuses) but queries
adm_level=2 from the polygon table, restricted to ADM2_ISO3S: the countries in the
Forecast × HNRP analysis (HNRP and/or recent IPC coverage) that HAVE adm2
polygons + zonal stats in the prod DB — 22 of the 45; the other 23 have no
adm2 in public.polygon and are excluded on that basis (a coverage gap on our
side, documented in the KB pcode audit) — plus Ethiopia, added for the HDX
signal (complex bimodal climatology; admin-2 zones are the sensible unit).
Everything in public.polygon at adm_level=2 that is not in ADM2_ISO3S has no
skill stats; add the iso3 here and run --iso3 to extend.

Run:  uv run python pipeline/compute_skill_adm2.py                 # all of ADM2_ISO3S (default 8 workers)
      uv run python pipeline/compute_skill_adm2.py --workers 4
      uv run python pipeline/compute_skill_adm2.py --iso3 ETH        # only these countries, merged into the blobs
"""

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
from azure.core.exceptions import ResourceNotFoundError
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.constants import PROJECT_PREFIX  # noqa: E402
from compute_skill_adm1 import _compute_unit  # noqa: E402 — level-agnostic worker

# The Forecast × HNRP scope (HNRP or recent-IPC countries) ∩ adm2 availability.
ADM2_ISO3S = [
    "AFG", "BFA", "CAF", "CMR", "COD", "COL", "GTM", "HND", "HTI", "MLI", "MMR",
    "MOZ", "NER", "NGA", "SDN", "SLV", "SOM", "SSD", "SYR", "TCD", "UKR", "VEN", "YEM",
    "ETH",  # HDX signal — not an HNRP/IPC-scope country, added 2026-09
]

SKILL_BLOB     = f"{PROJECT_PREFIX}/processed/skill_stats_adm2.parquet"
PAIRED_BLOB    = f"{PROJECT_PREFIX}/processed/paired_yearly_adm2.parquet"
SKILL_DT_BLOB  = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm2.parquet"
PAIRED_DT_BLOB = f"{PROJECT_PREFIX}/processed/paired_yearly_detrended_adm2.parquet"
BLOBS = {"skill": SKILL_BLOB, "paired": PAIRED_BLOB,
         "skill_dt": SKILL_DT_BLOB, "paired_dt": PAIRED_DT_BLOB}

CHECKPOINT_DIR = Path(__file__).parent / ".checkpoint_adm2"
COMPLETED_FILE = CHECKPOINT_DIR / "completed.json"
PARTIALS = {
    "skill":     CHECKPOINT_DIR / "skill_partial.parquet",
    "paired":    CHECKPOINT_DIR / "paired_partial.parquet",
    "skill_dt":  CHECKPOINT_DIR / "skill_dt_partial.parquet",
    "paired_dt": CHECKPOINT_DIR / "paired_dt_partial.parquet",
}
SAVE_EVERY = 25  # checkpoint often — background runs keep getting killed externally


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
    parser.add_argument(
        "--iso3", nargs="+", metavar="ISO3",
        help="Only (re)compute these countries and MERGE them into the existing blobs "
             "(the default recomputes all of ADM2_ISO3S and overwrites)",
    )
    args = parser.parse_args()
    iso3s = [i.upper() for i in args.iso3] if args.iso3 else ADM2_ISO3S
    unknown = sorted(set(iso3s) - set(ADM2_ISO3S))
    if unknown:
        sys.exit(f"{unknown} not in ADM2_ISO3S — add them there first so the scope stays "
                 "declared in one place.")

    engine = stratus.get_engine("prod")
    ph = ",".join(["%s"] * len(iso3s))
    with engine.connect() as conn:
        df_adm2 = pd.read_sql(
            f"SELECT pcode, iso3, name FROM public.polygon "
            f"WHERE adm_level=2 AND iso3 IN ({ph})",
            conn, params=tuple(iso3s),
        )
    tqdm.write(f"{len(df_adm2):,} ADM2 units across {df_adm2['iso3'].nunique()} countries")

    if args.iso3:
        # Targeted runs checkpoint separately so a stale full-run checkpoint can't leak in.
        global CHECKPOINT_DIR, COMPLETED_FILE, PARTIALS
        CHECKPOINT_DIR = Path(__file__).parent / ".checkpoint_adm2_targeted"
        COMPLETED_FILE = CHECKPOINT_DIR / "completed.json"
        PARTIALS = {k: CHECKPOINT_DIR / p.name for k, p in PARTIALS.items()}

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
    for k, blob in BLOBS.items():
        df = pd.concat(parts[k], ignore_index=True)
        if args.iso3:
            _merge_upload(df, blob, iso3s)
        else:
            tqdm.write(f"Saving {k} ({len(df):,} rows, {df['iso3'].nunique()} countries) -> {blob}")
            stratus.upload_parquet_to_blob(df, blob, stage="dev")
    _clear_checkpoint()
    tqdm.write("Done.")


def _merge_upload(df_new: pd.DataFrame, blob: str, iso3s: list[str]) -> None:
    """Replace ``iso3s`` in the existing blob with ``df_new`` and upload the result.

    Done at the Arrow level: the adm2 paired_yearly files are 0.5 GB parquet / ~35 M
    rows, far too big to round-trip through pandas with object-dtype strings. Anything
    but a genuinely missing blob aborts — an empty baseline would silently drop every
    other country when written back.
    """
    import io

    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    cc = stratus.get_container_client(stage="dev", container_name="projects")
    try:
        existing = pq.read_table(io.BytesIO(cc.download_blob(blob).readall()))
    except ResourceNotFoundError:
        tqdm.write(f"  {blob}: not found, starting fresh")
        existing = None
    new = pa.Table.from_pandas(df_new, preserve_index=False)
    if existing is not None:
        keep = existing.filter(pc.invert(pc.is_in(existing["iso3"], pa.array(iso3s))))
        new = new.select(existing.column_names).cast(existing.schema)
        merged = pa.concat_tables([keep, new])
    else:
        merged = new
    n_iso = len(pc.unique(merged["iso3"]))
    tqdm.write(f"Saving {merged.num_rows:,} rows ({n_iso} countries; "
               f"{new.num_rows:,} new for {iso3s}) -> {blob}")
    buf = io.BytesIO()
    pq.write_table(merged, buf)
    buf.seek(0)
    stratus.get_container_client(stage="dev", container_name="projects", write=True) \
        .upload_blob(blob, buf, overwrite=True)


def _clear_checkpoint() -> None:
    for p in [COMPLETED_FILE, *PARTIALS.values()]:
        if p.exists():
            p.unlink()
    if CHECKPOINT_DIR.exists():
        try:
            CHECKPOINT_DIR.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    main()
