"""
Skill-threshold sensitivity sweep.
==================================
Re-run the global alert pipeline at several skill cutoffs to see (a) how much
coverage the gate removes and (b) which alerts are robust across thresholds vs
only appear when the gate is relaxed. Leans to the low side per request, since
r>=0.35 already gates ~69% of in-season rows.

Imports the pipeline module and overrides R_THRESH + output paths per run.
Outputs land in artefacts/sweep/ (gitignored scratch).

Run: .venv/bin/python exploratory/skill_sweep.py
"""
import importlib.util
import os

THRESHOLDS = [0.20, 0.25, 0.30, 0.35]
OUTDIR = 'data/sweep'   # tracked, so the sensitivity chapter can read it without a re-run
os.makedirs(OUTDIR, exist_ok=True)

spec = importlib.util.spec_from_file_location(
    'alerts', 'exploratory/seas5_admin_drought_alerts.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)  # runs ee.Initialize + builds collections once

for r in THRESHOLDS:
    tag = 'r%02d' % int(round(r * 100))
    mod.R_THRESH = r                       # season_image reads this module global at call time
    mod.OUT_PATH = '%s/adm0_%s.parquet' % (OUTDIR, tag)
    mod.OUT_PATH_ADM1 = '%s/adm1_%s.parquet' % (OUTDIR, tag)
    print('\n==================  R_THRESH = %.2f  ==================' % r)
    mod.main()

print('\nsweep complete:', THRESHOLDS)
