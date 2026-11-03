"""Check the Admin explorer's in-browser detrending against the pipeline's.

The explorer ships only the RAW yearly pairs (pipeline/export_explorer_site.py) and
re-derives the detrended series with `detrend` in docs/explorer_math.js. This runs that
exact JS under Node on the exported files and compares it with
paired_yearly_detrended{,_adm1,_adm2}.parquet for the same units, issue month and
trimesters. Run it after changing either side of the detrending (src/skill.py or
docs/explorer_math.js); it is a developer check, not part of the monthly job.

Run:  uv run python pipeline/export_explorer_site.py --iso3 ETH SOM NGA   # if not built
      uv run python pipeline/check_explorer_detrend.py                    # all built units
      uv run python pipeline/check_explorer_detrend.py --levels 0 1 --max-units 300

Needs `node` on PATH. Exit 1 if any value is off by more than --tol (log1p space; the
export rounds to 1e-4, so the default 5e-4 allows rounding only).
"""

import argparse
import gzip
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.constants import PROJECT_PREFIX  # noqa: E402

EXPLORER = HERE.parent / "docs" / "data" / "explorer"
MATH_JS = HERE.parent / "docs" / "explorer_math.js"
SUFFIX = {0: "", 1: "_adm1", 2: "_adm2"}

NODE_HARNESS = r"""
// node -e <script> a b c  ->  process.argv = [node, a, b, c]
const [mathJs, unitsJson, scaleArg] = process.argv.slice(1);
const M = require(mathJs);
const units = JSON.parse(require("fs").readFileSync(unitsJson, "utf8"));
const scale = Number(scaleArg);
const out = {};
for (const [pcode, u] of Object.entries(units)) {
  out[pcode] = {};
  for (const [tri, t] of Object.entries(u.tri)) {
    const d = M.detrend(t.y0, M.decode(t.fc, scale), M.decode(t.obs, scale));
    out[pcode][tri] = { y0: t.y0, fc: d.fc, obs: d.obs };
  }
}
process.stdout.write(JSON.stringify(out));
"""


def _units_by_level(max_units: int) -> dict[int, dict]:
    """{level: {pcode: unit}} from the built explorer files (admin 0 = the unit whose
    pcode is the country file's `pcode`)."""
    out: dict[int, dict] = {0: {}, 1: {}, 2: {}}
    for f in sorted(EXPLORER.glob("*.json.gz")):
        c = json.loads(gzip.decompress(f.read_bytes()))
        for pcode, u in c["units"].items():
            out[0 if pcode == c["pcode"] else 1][pcode] = u
    for f in sorted((EXPLORER / "adm2").glob("*.json.gz")):
        out[2].update(json.loads(gzip.decompress(f.read_bytes()))["units"])
    return {lv: dict(list(u.items())[:max_units]) for lv, u in out.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--levels", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--max-units", type=int, default=100_000)
    ap.add_argument("--tol", type=float, default=5e-4)
    args = ap.parse_args()

    index = json.loads((EXPLORER / "index.json").read_text())
    im, scale = index["issued_month"], index["scale"]
    units = _units_by_level(args.max_units)
    worst = 0.0
    bad = 0
    for level in args.levels:
        if not units[level]:
            print(f"level {level}: no exported units, skipped")
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(units[level], fh)
        js = subprocess.run(["node", "-e", NODE_HARNESS, str(MATH_JS), fh.name, str(scale)],
                            capture_output=True, text=True)
        Path(fh.name).unlink()
        if js.returncode:
            raise SystemExit(f"node failed:\n{js.stderr}")
        got = json.loads(js.stdout)
        blob = f"{PROJECT_PREFIX}/processed/paired_yearly_detrended{SUFFIX[level]}.parquet"
        tbl = pq.read_table(
            io.BytesIO(stratus.load_blob_data(blob, stage="dev")),
            columns=["pcode", "trimester", "season_year", "forecast_mean", "obs_mean"],
            filters=[("issued_month", "=", im), ("pcode", "in", list(units[level]))],
        ).to_pandas()
        exp = {k: g.set_index("season_year") for k, g in tbl.groupby(["pcode", "trimester"])}
        n = 0
        lvl_worst = 0.0
        for pcode, tris in got.items():
            for tri, d in tris.items():
                g = exp.get((pcode, tri))
                if g is None:
                    print(f"  FAIL {pcode} {tri}: not in the detrended parquet")
                    bad += 1
                    continue
                for key, col in (("fc", "forecast_mean"), ("obs", "obs_mean")):
                    for i, v in enumerate(d[key]):
                        yr = d["y0"] + i
                        e = g[col].get(yr, np.nan)
                        if v is None or np.isnan(e):
                            if (v is None) != bool(np.isnan(e)):
                                print(f"  FAIL {pcode} {tri} {key} {yr}: js {v} vs pipeline {e}")
                                bad += 1
                            continue
                        diff = abs(v - e)
                        lvl_worst = max(lvl_worst, diff)
                        n += 1
                        if diff > args.tol:
                            bad += 1
                            if bad <= 20:
                                print(f"  FAIL {pcode} {tri} {key} {yr}: js {v:.5f} vs "
                                      f"pipeline {e:.5f}")
        worst = max(worst, lvl_worst)
        print(f"level {level}: {len(got)} units, {n:,} values, max |diff| {lvl_worst:.2e}")
    print(f"{'FAILED' if bad else 'OK'}: {bad} mismatches, worst |diff| {worst:.2e} "
          f"(tol {args.tol})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
