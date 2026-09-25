"""Databricks entrypoint for the monthly refresh (see databricks.yml).

Runs the repo's ordinary pipeline scripts, unchanged, one logical step per job
task — on a Job Compute cluster that reaches the Postgres servers over their
private endpoints. The scripts stay plain argparse Python; this file is the only
Databricks-specific glue (repo location, secrets, step composition).

    python databricks/dispatch.py <step> [issued] [cma] [workers] [enso]

Steps (each is one task in databricks.yml; the dependency order is there):
  skill_adm0    compute_skill.py                        prod DB -> dev blob parquets
  skill_adm1    compute_skill_adm1.py --workers N
  skill_adm2    compute_skill_adm2.py --workers N
  clim_signal   compute_monthly_clim.py + build_hdx_signal_inputs.py, levels 0/1/2
  raster        compute_skill_raster.py for the issuance month, merged into the blob cubes
  site          previous site bundle -> every exporter -> vintage gate -> new bundle
                (pipeline/sync_site_data.py; the Pages deploy downloads it)
  enso_slides   analysis/png_enso_slides.py + sync_enso_slides.py upload (opt-in: enso=run)
  publish       dispatch the "Deploy pages site" GitHub workflow (needs the
                dsci secret GH_SEAS5_PAGES_TOKEN; without it the deploy's own
                cron picks the bundle up later)

Positional contract (mirrors `parameters:` in databricks.yml):
  argv[1] step
  argv[2] issued   YYYY-MM of the SEAS5 issuance this run is for; "" = the
                   current UTC month (the job runs on the 7th, after the ~5th issuance)
  argv[3] cma      auto | force | skip — refresh the CMA CMME mirror inside `site`
                   (auto = only when a CMME file newer than the last aggregation
                   exists on the dev blob; needs the dsci secret CMA_SITE_PASSWORD)
  argv[4] workers  process count for the adm1/adm2 computes ("" = 6)
  argv[5] enso     run | skip — render + upload the ENSO country slides after `site`
"""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

GITHUB_REPO = "OCHA-DAP/ds-seas5-skill"
DEPLOY_WORKFLOW = "deploy-pages.yml"
SECRET_SCOPE = "dsci"


# --------------------------------------------------------------------------- repo

def _checkout_root() -> Path:
    """Find the repo checkout (spark_python_task does not reliably define __file__)."""
    hints = []
    try:
        hints.append(Path(__file__).resolve())  # noqa: F821
    except NameError:
        pass
    if sys.argv and sys.argv[0]:
        hints.append(Path(sys.argv[0]).resolve())
    hints.append(Path.cwd())
    for h in hints:
        for d in [h, *h.parents]:
            if (d / "src").is_dir() and (d / "pipeline").is_dir():
                return d
    raise SystemExit(f"cannot find the repo root from {[str(h) for h in hints]}")


# Under `source: GIT` the checkout lives on the workspace filesystem, whose import
# probing is flaky and which may not be writable (team pattern: ds-aa-tracking
# databricks/nightly.py). Copy the repo onto local disk and run from there. The
# copy target is stable for the whole job run (same driver for every task), so
# docs/ written by the `site` task is still there for `enso_slides`.
_SRC = _checkout_root()
ROOT = Path("/local_disk0" if Path("/local_disk0").is_dir() else tempfile.gettempdir()) / "ds-seas5-skill-run"
shutil.copytree(
    _SRC, ROOT, dirs_exist_ok=True,
    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".venv", "outputs",
                                  "slides", "_png_enso_cache", ".checkpoint*"),
)
MARKER_DIR = ROOT / ".dispatch"
MARKER_DIR.mkdir(exist_ok=True)

# Azure Postgres requires SSL and stratus does not set sslmode (KB database.md).
os.environ.setdefault("PGSSLMODE", "require")
os.environ.setdefault("MPLBACKEND", "Agg")
# The task's pip env holds the console scripts of the job libraries (node/npx from
# nodejs-wheel, for the HNRP edge-match); subprocesses must find them on PATH.
os.environ["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"


def run(*args, **env) -> None:
    cmd = [sys.executable, *map(str, args)]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True, env={**os.environ, **env})


def _secret(key: str) -> str:
    """A dsci-scope secret via dbutils (redacted in logs), else the env var, else ""."""
    try:
        from pyspark.dbutils import DBUtils  # type: ignore
        from pyspark.sql import SparkSession  # type: ignore
        return DBUtils(SparkSession.builder.getOrCreate()).secrets.get(SECRET_SCOPE, key)
    except Exception:
        return os.environ.get(key, "")


# --------------------------------------------------------------------------- args

def _arg(i: int, default: str = "") -> str:
    v = sys.argv[i] if len(sys.argv) > i else ""
    return v if v else default


STEP = _arg(1)
ISSUED = _arg(2, dt.datetime.now(dt.timezone.utc).strftime("%Y-%m"))
CMA = _arg(3, "auto")
WORKERS = _arg(4, "6")
ENSO = _arg(5, "skip")

if not re.fullmatch(r"\d{4}-\d{2}", ISSUED):
    raise SystemExit(f"issued must be YYYY-MM, got {ISSUED!r}")
ISSUED_MONTH = int(ISSUED[5:])


# --------------------------------------------------------------------------- steps

def step_skill_adm0() -> None:
    run("pipeline/compute_skill.py")


def step_skill_adm1() -> None:
    run("pipeline/compute_skill_adm1.py", "--workers", WORKERS)


def step_skill_adm2() -> None:
    run("pipeline/compute_skill_adm2.py", "--workers", WORKERS)


def step_clim_signal() -> None:
    for level in ("0", "1", "2"):
        run("pipeline/compute_monthly_clim.py", "--level", level)
        run("pipeline/build_hdx_signal_inputs.py", "--level", level)


def step_raster() -> None:
    # Only the issuance month is recomputed (~25 min); --merge writes it into the
    # full blob cubes so the site and the ENSO slides read one consistent cube.
    run("pipeline/compute_skill_raster.py",
        "--issued-months", str(ISSUED_MONTH), "--end-date", f"{ISSUED}-01", "--merge")
    (MARKER_DIR / "raster_ok").write_text(ISSUED)


def pd_max_month(col) -> str:
    import pandas as pd
    return pd.to_datetime(col).max().strftime("%Y%m")


def _cma_needs_refresh() -> bool:
    """True when a CMME realtime file newer than the last aggregation is on the blob."""
    import ocha_stratus as stratus
    sys.path.insert(0, str(ROOT / "pipeline"))
    from compute_skill_cma import OUT_PREFIX, RT_PREFIX, RT_RE  # noqa: E402

    names = stratus.list_container_blobs(name_starts_with=RT_PREFIX,
                                         container_name="projects", stage="dev")
    stamps = [m.group(1) for n in names if (m := RT_RE.search(n))]
    if not stamps:
        print("CMA: no CMME realtime files on the blob")
        return False
    newest = max(stamps)
    try:
        monthly = stratus.load_parquet_from_blob(f"{OUT_PREFIX}/cma_adm0_monthly.parquet",
                                                 stage="dev")
        have = pd_max_month(monthly["issued_date"])
    except Exception as e:  # first run, or the column moved: refresh and find out
        print(f"CMA: cannot read the last aggregation ({e}); refreshing")
        return True
    print(f"CMA: newest CMME file {newest}, last aggregated init {have}")
    return newest > have


def step_site() -> None:
    # Start from the published bundle: the HNRP geometry files are reused when
    # present, history issuances are frozen, and the CMA payloads carry over when
    # the mirror is not refreshed this run.
    run("pipeline/sync_site_data.py", "download", "--if-exists")
    run("pipeline/export_static_site.py")
    run("pipeline/export_history_site.py")
    for level in ("1", "2", "3", "low", "ipc", "fews"):
        run("pipeline/export_hnrp_drought.py", "--level", level)
    run("pipeline/export_plan_caseloads.py")
    run("pipeline/export_raster_site.py")

    if CMA != "skip" and (CMA == "force" or _cma_needs_refresh()):
        password = _secret("CMA_SITE_PASSWORD")
        if password:
            run("pipeline/compute_skill_cma.py", "--reaggregate")
            run("pipeline/export_cma_site.py", CMA_SITE_PASSWORD=password)
        else:
            print("WARN: CMA mirror needs a refresh but the dsci secret CMA_SITE_PASSWORD "
                  "is not set — keeping the previous /cma payloads", flush=True)

    # The vintage gate: everything must show THIS issuance (the run's `issued`),
    # the HNRP payloads included (their adm1/adm2 inputs are part of this job now).
    # The raster is held to the same standard when this run rebuilt it.
    strict_raster = (MARKER_DIR / "raster_ok").exists()
    run("pipeline/verify_site_data.py", "--expect", ISSUED, "--strict-hnrp",
        *(["--strict-raster"] if strict_raster else []))
    run("pipeline/sync_site_data.py", "upload", "--tag", ISSUED)


def step_enso_slides() -> None:
    if ENSO != "run":
        print("ENSO slides: skipped (job parameter enso != run)")
        return
    if not (ROOT / "docs" / "data" / "forecast.json").exists():
        run("pipeline/sync_site_data.py", "download")
    run("analysis/png_enso_slides.py", "--country", "all", "--force")
    run("pipeline/sync_enso_slides.py", "upload")


def step_publish() -> None:
    token = _secret("GH_SEAS5_PAGES_TOKEN")
    if not token:
        print("WARN: dsci secret GH_SEAS5_PAGES_TOKEN not set — not dispatching the Pages "
              "deploy; its scheduled run will pick up the new bundle", flush=True)
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{DEPLOY_WORKFLOW}/dispatches"
    req = urllib.request.Request(
        url, method="POST", data=json.dumps({"ref": "main"}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"dispatched {DEPLOY_WORKFLOW} on {GITHUB_REPO}: HTTP {r.status}")


STEPS = {
    "skill_adm0": step_skill_adm0,
    "skill_adm1": step_skill_adm1,
    "skill_adm2": step_skill_adm2,
    "clim_signal": step_clim_signal,
    "raster": step_raster,
    "site": step_site,
    "enso_slides": step_enso_slides,
    "publish": step_publish,
}


def main() -> None:
    if STEP not in STEPS:
        raise SystemExit(f"unknown step {STEP!r}; one of {sorted(STEPS)}")
    for v in ("DSCI_AZ_DB_PROD_HOST", "DSCI_AZ_DB_DEV_HOST", "DSCI_AZ_BLOB_DEV_SAS_WRITE"):
        if not os.environ.get(v):
            raise SystemExit(f"{v} is not set — the cluster policy should inject the dsci secrets")
    print(f"step={STEP} issued={ISSUED} cma={CMA} workers={WORKERS} enso={ENSO} root={ROOT}",
          flush=True)
    STEPS[STEP]()


if __name__ == "__main__":
    main()
