"""Uganda country-team supplementary data (JIAF 2.0 Light + max reach workbooks).

Uganda has no HNRP (its only plan is the refugee RRP, which the plan pipeline
excludes as not the country's own plan), so PiN / severity / reach for the
Uganda analysis come from workbooks the country team shared, mirrored to the
dev blob under {PROJECT_PREFIX}/raw/country_team/uga/.

Loaders return tidy district-level frames keyed by CODAB ADM2 pcode. Two JIAF
"districts" are operational units, not CODAB units, and are aliased explicitly:

  - "Terego" (created 2020, absent from the 135-district CODAB vintage): its
    territory is the Terego county polygon INSIDE CODAB "Arua" (UG307203 has
    ADM2_PCODE UG3072), so it maps to UG3072.
  - "Madi-Okollo & Terego" (combined refugee-response unit): dominated by Madi
    Okollo -> UG3084.

  A shared pcode only attaches the same forecast to both rows — people counts
  stay per-row, so nothing is double-counted; only a per-pcode choropleth sum
  would merge them (flagged by the `pcode_shared` column).

  That safety argument covers FORECAST joins only. People-count joins across
  workbooks must match by NAME, never by pcode: the reach workbooks use
  post-split districts, so their "Arua" row lands on UG3072 — the same pcode
  as JIAF "Terego" — and a pcode-keyed merge silently credits Arua's
  targeted/reached to Terego (disjoint territories since the 2020 split).
  The consumer (analysis/uganda_hnrp.qmd) joins reach by normalized district
  name, with the single documented exception that the combined JIAF unit
  "Madi-Okollo & Terego" takes the workbook's "Madi Okollo" row.
"""

import io
from functools import lru_cache

import ocha_stratus as stratus
import pandas as pd
from ocha_stratus import codab

from src.constants import PROJECT_PREFIX

BLOB_DIR = f"{PROJECT_PREFIX}/raw/country_team/uga"
JIAF_BLOB = f"{BLOB_DIR}/Uganda JIAF 2.0 Light - Data Collection (v5 census baseline).xlsx"
REACH_BLOBS = {
    "achieved": f"{BLOB_DIR}/Gender-MAX-Achieved People-2026-08-19.xlsx",
    "targeted": f"{BLOB_DIR}/Gender-MAX-Targeted People-2026-08-19.xlsx",
}

DISTRICT_ALIASES = {
    "terego": "UG3072",
    "madi-okollo & terego": "UG3084",
}

SECTORS = ["Food Security", "Nutrition", "Health", "WASH", "Protection", "Shelter / NFI", "Education"]

REACH_COLS = {
    "#Projects": "n_projects", "Total Adj": "total", "Men": "men", "Women": "women",
    "Boys": "boys", "Girls": "girls", "Disability Total": "disability_total",
    "Disabled Men": "disabled_men", "Disabled Women": "disabled_women",
    "Disabled Boys": "disabled_boys", "Disabled Girls": "disabled_girls",
}


@lru_cache(maxsize=8)
def _xlsx(blob: str) -> bytes:
    return stratus.load_blob_data(blob, stage="dev")


@lru_cache(maxsize=1)
def _district_pcodes() -> dict[str, str]:
    cod = codab.load_codab_from_blob("uga", admin_level=2)
    return {n.strip().lower(): p for n, p in zip(cod["ADM2_EN"], cod["ADM2_PCODE"])}


def add_district_pcodes(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """Attach ADM2 pcodes by (normalized) district name; raise on any miss."""
    lut = {**_district_pcodes(), **DISTRICT_ALIASES}
    keys = df[col].astype(str).str.strip().str.lower()
    unmatched = sorted(set(keys[~keys.isin(lut)]))
    if unmatched:
        raise ValueError(
            f"district name(s) not in CODAB adm2 or DISTRICT_ALIASES: {unmatched} — "
            f"add an alias (with a documented geometry rationale) rather than dropping them"
        )
    out = df.copy()
    out["pcode"] = keys.map(lut)
    out["pcode_shared"] = out["pcode"].duplicated(keep=False) & (
        out.groupby("pcode")[col].transform("nunique") > 1
    )
    return out


def _jiaf_rows(sheet: str, header_map: dict[str, str]) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(_xlsx(JIAF_BLOB)), sheet_name=sheet, header=1)
    missing = [c for c in header_map if c not in df.columns]
    if missing:
        raise ValueError(f"JIAF sheet {sheet!r} is missing expected column(s) {missing} — layout changed?")
    df = df.rename(columns=header_map)
    # Keep only real district × population-group rows; the sheet also carries a
    # TOTAL row and sub-total rows (blank sub-region / population group).
    df = df[df["district"].notna() & df["pop_group"].notna() & (df["sub_region"] != "TOTAL") & df["sub_region"].notna()]
    bad_groups = set(df["pop_group"]) - {"Host community", "Refugees"}
    if bad_groups:
        raise ValueError(f"unexpected population group(s) in JIAF {sheet!r}: {bad_groups}")
    return df.reset_index(drop=True)


def load_jiaf_severity() -> pd.DataFrame:
    """District × population-group intersectoral + sectoral severity classes."""
    df = _jiaf_rows("1. Severity", {
        "Sub-region": "sub_region", "District": "district",
        "Population group": "pop_group", "Population baseline": "population",
        "Preliminary intersectoral severity (auto)": "intersectoral_severity",
        **{s: s for s in SECTORS},
    })
    keep = ["sub_region", "district", "pop_group", "population", "intersectoral_severity", *SECTORS]
    return add_district_pcodes(df[keep], "district")


def load_jiaf_pin() -> pd.DataFrame:
    """District × population-group PiN: sectoral, joint (= max sectoral), % of population."""
    df = _jiaf_rows("2. PiN", {
        "Sub-region (auto)": "sub_region", "District (auto)": "district",
        "Population group (auto)": "pop_group", "Population (auto)": "population",
        "JOINT PiN = highest sectoral PiN (auto)": "joint_pin",
        "% of population (auto)": "pct_population",
        **{s: s for s in SECTORS},
    })
    keep = ["sub_region", "district", "pop_group", "population", "joint_pin", "pct_population", *SECTORS]
    df = df[keep]
    if df["joint_pin"].isna().all():
        raise ValueError("JIAF PiN sheet: joint PiN column is empty — cached formula values missing?")
    return add_district_pcodes(df, "district")


def load_reach(kind: str, level: int = 2) -> pd.DataFrame:
    """Max targeted / achieved people (with gender + disability breakdowns).

    kind: 'targeted' or 'achieved'; level: 1 (region), 2 (district), 3 (county).
    District rows (level=2) get CODAB pcodes; other levels keep names only
    (region names here are the statistical regions = CODAB adm1 names).
    """
    if kind not in REACH_BLOBS:
        raise ValueError(f"kind must be one of {sorted(REACH_BLOBS)}, got {kind!r}")
    df = pd.read_excel(io.BytesIO(_xlsx(REACH_BLOBS[kind])), sheet_name=f"Admin{level}")
    admin_cols = {f"Admin {i}": f"adm{i}_name" for i in range(1, level + 1) if f"Admin {i}" in df.columns}
    missing = [c for c in ("Total Adj", f"Admin {level}") if c not in df.columns]
    if missing:
        raise ValueError(f"reach workbook {kind!r} Admin{level}: missing column(s) {missing} — layout changed?")
    df = df.rename(columns={**REACH_COLS, **admin_cols})
    df = df[df[f"adm{level}_name"].notna()].reset_index(drop=True)
    if df["total"].isna().any() or (df["total"] < 0).any():
        raise ValueError(f"reach workbook {kind!r} Admin{level}: null/negative totals")
    if level == 2:
        df = add_district_pcodes(df, "adm2_name")
    return df
