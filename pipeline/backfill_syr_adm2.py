"""One-off: compute SYR's adm2 skill units and merge into the adm2 blobs.

Syria entered the Forecast × HNRP scope via the PiN-by-Severity table after the
adm2 skill compute ran, so its 62 districts were never computed — leaving its
adm3 sub-districts nothing to inherit. Run once:
    uv run python pipeline/backfill_syr_adm2.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compute_skill_adm1 import _compute_unit  # noqa: E402
from src.constants import PROJECT_PREFIX  # noqa: E402


def main() -> None:
    with stratus.get_engine("prod").connect() as c:
        units = pd.read_sql(
            "SELECT pcode, iso3, name FROM public.polygon "
            "WHERE adm_level=2 AND iso3='SYR'", c)
    print(len(units), "SYR adm2 units")
    results = {"skill": [], "paired": [], "skill_dt": [], "paired_dt": []}
    with ProcessPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(_compute_unit, tuple(r)): r["pcode"]
                for _, r in units.iterrows()}
        for f in as_completed(futs):
            pcode, status, dfs = f.result()
            if status != "ok":
                print(pcode, status)
                continue
            for k, df in zip(results, dfs):
                results[k].append(df)
    print("computed", len(results["skill"]), "units; merging into blobs")
    for k, blob in [("skill", "skill_stats_adm2"),
                    ("paired", "paired_yearly_adm2"),
                    ("skill_dt", "skill_stats_detrended_adm2"),
                    ("paired_dt", "paired_yearly_detrended_adm2")]:
        path = f"{PROJECT_PREFIX}/processed/{blob}.parquet"
        base = stratus.load_parquet_from_blob(path, stage="dev")
        base = base[~base["pcode"].isin(set(units["pcode"]))]
        out = pd.concat([base, *results[k]], ignore_index=True)
        stratus.upload_parquet_to_blob(out, path, stage="dev")
        print(f"  {blob}: {len(base):,} -> {len(out):,} rows")


if __name__ == "__main__":
    main()
