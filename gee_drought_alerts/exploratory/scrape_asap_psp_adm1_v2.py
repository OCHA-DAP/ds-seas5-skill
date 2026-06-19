"""
ASAP Season Forecasts – batch scraper (v2)
==========================================
Fetches season-warning data from the ASAP explorer endpoint for all ADM1 units
in a local GAUL1 shapefile, then saves the raw warnings to Parquet.

Differences vs. v1 (``scrape_asap_psp_adm1.py``):

* Probes every month in ``PROBE_MONTHS`` and unions the results — v1 broke on
  the first non-empty probe, which silently dropped one season for bimodal
  units whose two forecast windows don't overlap in time.
* Dedupes warnings by their server-assigned ``id`` (the same warning appears
  in every probe month once its forecast window is open).
* Stamps each retained warning with the ``probe_month`` it was first seen in,
  for downstream auditing.
* Checkpoints per unit to ``CACHE_DIR/{asap1_id}.parquet`` so a crashed run
  resumes without re-fetching completed units.
* Adds a session + retry/backoff loop; v1 silently dropped a unit on any
  exception.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm.auto import tqdm
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

EXPLORER_URL = "https://agricultural-production-hotspots.ec.europa.eu/wexplorer/getDataSeasonWarnings.php"

ADM1_FILEPATH = Path("temp/gaul1_asap_v05/gaul1_asap_v05/gaul1_asap.shp")
CACHE_DIR = Path("temp/asap_warnings_cache_v2")
OUTPUT_PATH = Path("temp/asap1_psp_v2.parquet")

# Test mode: when non-empty, skip shapefile load and run only these IDs.
# Writes to separate cache/output paths so a test run doesn't poison the
# eventual full-run cache.
TEST_IDS: list[int] = []
TEST_CACHE_DIR = Path("temp/asap_warnings_cache_v2_test")
TEST_OUTPUT_PATH = Path("temp/asap1_psp_v2_test.parquet")

PROBE_MONTHS = range(1, 13)
REFERENCE_YEAR = 2025

# ASAP exposes a `layertype` flag on the explorer endpoint: 1=crop, 2=rangeland.
# Crop and rangeland warnings live in disjoint id/season_id namespaces, so we
# can scrape both and union safely (dedupe-by-id won't lose anything).
LAYERTYPES: list[int] = [1, 2]

REQUEST_TIMEOUT = 30  # seconds

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTTP session with retry/backoff
# ---------------------------------------------------------------------------

def make_session() -> requests.Session:
    """Session that retries on transient failures with exponential backoff."""
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.0,  # 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get_asap_sf_data(
    session: requests.Session,
    reference_date: str,
    asap_id: int,
    layertype: int = 1,
    admin_unit: str = "gaul1",
) -> dict:
    """Fetch season-forecast warnings for a single ASAP unit at a reference date."""
    params = {
        "reference_date": reference_date,
        "asap_id": asap_id,
        "layertype": layertype,
        "admin_unit": admin_unit,
    }
    r = session.get(EXPLORER_URL, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Per-unit scrape (all probe months, deduped)
# ---------------------------------------------------------------------------

def scrape_unit(
    session: requests.Session,
    asap_id: int,
    year: int,
    probe_months: range,
    layertype: int,
    admin_unit: str,
) -> list[dict]:
    """
    Probe every month for one (unit, layertype); return deduped warning records.

    Each warning's server-assigned ``id`` is the dedupe key. The first probe
    month that surfaces a given warning is recorded as ``probe_month``.
    """
    seen: dict[str, dict] = {}

    for month in probe_months:
        date_str = f"{year}-{month:02d}-01"
        try:
            data = get_asap_sf_data(
                session=session,
                reference_date=date_str,
                asap_id=asap_id,
                layertype=layertype,
                admin_unit=admin_unit,
            )
        except Exception as exc:
            log.warning("asap_id=%s lt=%s probe=%s failed: %s",
                        asap_id, layertype, date_str, exc)
            continue

        for w in data.get("warnings") or []:
            wid = w.get("id")
            if wid is None or wid in seen:
                continue
            w = dict(w)  # don't mutate the parsed json
            w["probe_month"] = month
            seen[wid] = w

    return list(seen.values())


# ---------------------------------------------------------------------------
# Batch driver with per-unit checkpointing
# ---------------------------------------------------------------------------

def scrape_warnings(
    asap_ids: list[int],
    cache_dir: Path,
    year: int = REFERENCE_YEAR,
    probe_months: range = PROBE_MONTHS,
    layertypes: list[int] = LAYERTYPES,
    admin_unit: str = "gaul1",
) -> None:
    """
    Scrape each (unit × layertype) and persist to
    ``cache_dir/{asap_id}_lt{layertype}.parquet``.

    Separate per-layertype cache files mean you can add a layertype later
    without re-scraping the ones you already have.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    session = make_session()

    for asap_id in tqdm(asap_ids, desc="ASAP IDs"):
        for layertype in layertypes:
            cache_file = cache_dir / f"{asap_id}_lt{layertype}.parquet"
            if cache_file.exists():
                continue

            records = scrape_unit(
                session=session,
                asap_id=int(asap_id),
                year=year,
                probe_months=probe_months,
                layertype=layertype,
                admin_unit=admin_unit,
            )

            # Always write a file — even when empty — so resumed runs can tell
            # "already probed, no warnings" apart from "not yet probed".
            df_unit = pd.DataFrame(records)
            df_unit.to_parquet(cache_file, index=False)


# ---------------------------------------------------------------------------
# Concat + post-process
# ---------------------------------------------------------------------------

def load_cache(cache_dir: Path) -> pd.DataFrame:
    """Concatenate every per-unit parquet in the cache into one DataFrame."""
    frames = []
    for f in sorted(cache_dir.glob("*.parquet")):
        df = pd.read_parquet(f)
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def build_psp_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the time-series scrape to one row per (asap1_id, season_id).

    PSP boundaries are month-floored (1-12 ints) because the source field
    ``usrp_sos_mon`` arrives as ``"YYYY-MM"`` and only the month component is
    meaningful — the year just reflects which cycle the probe happened to
    return. We've verified month-of-year is stable across cycles within each
    (asap1_id, season_id) group, so ``first()`` is safe.
    """
    if df.empty:
        return pd.DataFrame(
            columns=["asap1_id", "season_id", "season", "psp_sos_month",
                     "psp_eos_month", "psp_length", "land_use"]
        )

    df = df.copy()
    df["psp_sos_month"] = pd.to_datetime(
        df["usrp_sos_mon"], format="%Y-%m", errors="coerce"
    ).dt.month
    df["psp_eos_month"] = pd.to_datetime(
        df["usrp_eos_mon"], format="%Y-%m", errors="coerce"
    ).dt.month

    summary = (
        df.groupby(["asap1_id", "season_id"], as_index=False)
          .agg(
              season=("season", "first"),
              psp_sos_month=("psp_sos_month", "first"),
              psp_eos_month=("psp_eos_month", "first"),
              psp_length=("usrp_length", "first"),
              land_use=("land_use", "first"),
          )
    )
    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if TEST_IDS:
        log.info("TEST MODE: running %d hardcoded IDs", len(TEST_IDS))
        asap_ids = list(TEST_IDS)
        cache_dir = TEST_CACHE_DIR
        output_path = TEST_OUTPUT_PATH
    else:
        log.info("Loading ADM1 shapefile from %s", ADM1_FILEPATH)
        adm1 = gpd.read_file(ADM1_FILEPATH)
        # Skip units where ASAP itself says there's nothing agricultural to
        # forecast — guaranteed-empty probes otherwise.
        eligible = adm1[(adm1["an_crop"] == 1) | (adm1["an_range"] == 1)]
        asap_ids = pd.unique(eligible["asap1_id"]).tolist()
        log.info("Found %d eligible ASAP1 IDs (skipped %d with no crop/range)",
                 len(asap_ids), len(adm1) - len(eligible))
        cache_dir = CACHE_DIR
        output_path = OUTPUT_PATH

    t0 = time.time()
    scrape_warnings(asap_ids, cache_dir=cache_dir)
    log.info("Scrape phase done in %.1f min", (time.time() - t0) / 60)

    df_raw = load_cache(cache_dir)
    log.info("Loaded %d raw warning records across %d units",
             len(df_raw),
             df_raw["asap1_id"].nunique() if not df_raw.empty else 0)

    psp = build_psp_summary(df_raw)
    log.info("PSP summary: %d (unit × season) rows across %d units",
             len(psp),
             psp["asap1_id"].nunique() if not psp.empty else 0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    psp.to_parquet(output_path, index=False)
    log.info("Saved PSP summary to %s", output_path)


if __name__ == "__main__":
    main()
