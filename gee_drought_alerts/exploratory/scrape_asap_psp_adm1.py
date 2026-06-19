"""
ASAP Season Forecasts – batch scraper
======================================
Fetches season-warning data from the ASAP WFS and explorer endpoints for all
ADM1 units in a local GAUL1 shapefile, then saves the raw warnings to Parquet.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from tqdm.auto import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WFS_URL = "https://agricultural-production-hotspots.ec.europa.eu/ows"
EXPLORER_URL = "https://agricultural-production-hotspots.ec.europa.eu/wexplorer/getDataSeasonWarnings.php"

ADM1_FILEPATH = Path("temp/gaul1_asap_v05")
OUTPUT_PATH = Path("temp/scraped_asap1_sf_warnings.parquet")

# Months to probe per unit (stops at first hit that returns warnings)
PROBE_MONTHS = range(1, 8)
REFERENCE_YEAR = 2025

MONTH_DATE_COLS = [
    "frcst_period_s_mon",
    "frcst_period_e_mon",
    "usrp_sos_mon",
    "usrp_eos_mon",
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WFS helper (optional – kept for reference / spot checks)
# ---------------------------------------------------------------------------

def fetch_wfs_season_warnings(
    reference_date: str = "2025-10-11",
    count: int = 1_000,
) -> gpd.GeoDataFrame:
    """Return a GeoDataFrame of season warnings from the ASAP WFS endpoint."""
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": "asap8:season_warning_gaul1_crop_500k",
        "outputFormat": "application/json",
        "viewparams": f"reference_date:{reference_date}",
        "propertyName": "asap1_id,adm0_code,adm1_code,dekad,season_order,season_cnt",
        "count": count,
    }
    r = requests.get(WFS_URL, params=params)
    r.raise_for_status()
    return gpd.read_file(r.text)


# ---------------------------------------------------------------------------
# Explorer helper
# ---------------------------------------------------------------------------

def get_asap_sf_data(
    reference_date: str,
    asap_id: int,
    layertype: int = 1,
    admin_unit: str = "gaul1",
) -> dict:
    """
    Fetch season-forecast warning data for a single ASAP unit.

    Parameters
    ----------
    reference_date:
        ISO date string, e.g. ``"2025-08-01"``.
    asap_id:
        ASAP administrative unit identifier.
    layertype:
        Layer type flag (default 1).
    admin_unit:
        ``"gaul1"`` or ``"gaul2"``.
    """
    params = {
        "reference_date": reference_date,
        "asap_id": asap_id,
        "layertype": layertype,
        "admin_unit": admin_unit,
    }
    r = requests.get(EXPLORER_URL, params=params)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Batch scraper
# ---------------------------------------------------------------------------

def scrape_warnings(
    asap_ids: list[int],
    year: int = REFERENCE_YEAR,
    probe_months: range = PROBE_MONTHS,
    layertype: int = 1,
    admin_unit: str = "gaul1",
) -> pd.DataFrame:
    """
    For each ASAP ID, probe months in order and collect warnings from the
    first month that returns a non-empty result.

    Returns a DataFrame of raw warning records with an ``asap_id`` column
    appended.
    """
    records: list[dict] = []

    for asap_id in tqdm(asap_ids, desc="ASAP IDs"):
        for month in probe_months:
            date_str = f"{year}-{month:02d}-01"
            try:
                data = get_asap_sf_data(
                    reference_date=date_str,
                    asap_id=asap_id,
                    layertype=layertype,
                    admin_unit=admin_unit,
                )
            except Exception as exc:
                log.warning("Failed for asap_id=%s date=%s: %s", asap_id, date_str, exc)
                continue

            warnings = data.get("warnings") or []
            if warnings:
                for w in warnings:
                    w["asap_id"] = asap_id
                records.extend(warnings)
                break  # first hit wins; move to next unit

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def postprocess(df: pd.DataFrame) -> pd.DataFrame:
    """Parse month-date columns and return a clean DataFrame."""
    df = df.copy()
    for col in MONTH_DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%Y-%m")
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Load ADM1 units
    log.info("Loading ADM1 shapefile from %s", ADM1_FILEPATH)
    adm1 = gpd.read_file(ADM1_FILEPATH)
    asap_ids = pd.unique(adm1["asap1_id"]).tolist()
    log.info("Found %d unique ASAP1 IDs", len(asap_ids))

    # Scrape
    df_raw = scrape_warnings(asap_ids)
    log.info("Collected %d warning records", len(df_raw))

    # Post-process
    df = postprocess(df_raw)

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False)
    log.info("Saved to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()