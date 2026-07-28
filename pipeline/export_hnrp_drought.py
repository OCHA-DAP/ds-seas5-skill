"""Forecast × HNRP overlay: where a bad drought forecast meets a large PiN.

Joins the latest ADM1 drought forecast (skill_stats_detrended_adm1, dev blob) against
the HNRP mirror's intersectoral People-in-Need / targeted figures (hpc.needs_admin,
dev DB, from ds-hnrp-mirror) per admin-1 unit, and writes one JSON for the static
site's "Forecast × HNRP" tab.

Selection per ADM1 unit (latest issuance):
  - valid trimesters (lead −2..4), rainy-season only, skill r ≥ r_mod (0.30)
  - drought side only (forecast percentile < 50); severity = directional drought RP
  - the unit's row reports its WORST qualifying slot (max drought RP)

Humanitarian weight: population in JIAF severity 4+ (hpc.severity_admin, latest
analysis year per country), with intersectoral PiN (INN) and targeted (TGT) from
hpc.needs_admin alongside. Figures published at admin-2/3 are summed to admin-1
(both are additive across geography).

Run:  uv run python pipeline/export_hnrp_drought.py
"""

import argparse
import calendar
import json
import re
import sys
import unicodedata
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
import topojson as tp

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from src.skill import trimester_lead  # noqa: E402
from export_static_site import (  # noqa: E402
    THRESHOLDS, _min_signed, _tri_label, _tri_valid, compute_rainy_set,
    issued_year_for_season,
)

SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm1.parquet"
OUT = HERE.parent / "docs" / "data" / "hnrp_drought.json"
GEO_OUT = HERE.parent / "docs" / "data" / "hnrp_adm1.geojson"
SIMPLIFY_TOLERANCE = 0.02  # per-country topology-preserving simplify (adm1 scale)

# Admin level of the whole export (--level). Level 2 swaps the grouping key, the
# skill stats blob, output filenames, and a coarser simplify (5k+ polygons); the
# scope self-restricts to countries with adm2 polygons/zonal stats (22 of 45 —
# the rest have no adm2 in public.polygon and are excluded on that basis).
LEVEL = 1
ACODE, ANAME = "admin1_code", "admin1_name"


def _set_level(level: int) -> None:
    global LEVEL, ACODE, ANAME, SKILL_BLOB, OUT, GEO_OUT, SIMPLIFY_TOLERANCE
    LEVEL = level
    if level == 2:
        ACODE, ANAME = "admin2_code", "admin2_name"
        SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm2.parquet"
        OUT = HERE.parent / "docs" / "data" / "hnrp_drought_adm2.json"
        GEO_OUT = HERE.parent / "docs" / "data" / "hnrp_adm2.geojson"
        SIMPLIFY_TOLERANCE = 0.03


def export_geometry(isos: list[str], names: dict[str, str]) -> None:
    """ADM1 boundaries for the HNRP countries -> one simplified geojson.

    Source: the COD shapefiles the zonal-stats pipeline rasterized ({iso3}_shp.zip in
    the PROD `polygon` container), so pcodes match the skill data by construction.
    Simplified per country with topology preserved (shared borders stay gap-free);
    cross-country borders come from different files and may not align exactly.
    """
    parts = []
    for iso3 in isos:
        try:
            g = stratus.load_shp_from_blob(
                f"{iso3.lower()}_shp.zip", shapefile=f"{iso3.lower()}_adm{LEVEL}.shp",
                stage="prod", container_name="polygon",
            )
        except Exception as e:  # noqa: BLE001 — a missing country shouldn't kill the export
            print(f"  {iso3}: boundary load failed ({type(e).__name__}), skipped")
            continue
        pcol = next((c for c in g.columns
                     if c.lower() in (f"adm{LEVEL}_pcode", "pcode")), None)
        if pcol is None:
            print(f"  {iso3}: no pcode column, skipped")
            continue
        g = g[[pcol, "geometry"]].rename(columns={pcol: "pcode"})
        topo = tp.Topology(g, prequantize=True, shared_coords=True)
        g = topo.toposimplify(SIMPLIFY_TOLERANCE).to_gdf()
        g["geometry"] = g["geometry"].make_valid()
        # make_valid can emit GeometryCollections/points; keep polygonal parts only
        # (a stray Point renders as a Leaflet marker and wrecks the map fit).
        g = g.explode(index_parts=False)
        g = g[g.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        g = g.dissolve("pcode", as_index=False)
        parts.append(g.assign(iso3=iso3))
        print(f"  {iso3}: {len(g)} adm1 polygons")
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), crs=parts[0].crs)
    gdf["name"] = gdf["pcode"].map(names)
    GEO_OUT.write_text(gdf.to_json())
    print(f"Wrote {GEO_OUT}  ({GEO_OUT.stat().st_size / 1e6:.1f} MB, {len(gdf)} polygons)")


def _fold(s: str) -> str:
    """Casefold + strip accents for name comparison."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(s))
                   if not unicodedata.combining(c)).casefold().strip()


# Admin reforms newer than our COD vintage: map reformed/new codes back to the old
# unit that CONTAINS them, so population is neither dropped nor mis-attributed.
# (iso3, source pcode) -> (our pcode, {folded names that must match} | None).
# The name guard disambiguates code reuse across vintages (Mali's 2026 system reuses
# ML09 for Taoudenni where the old system means Bamako).
REFORM_XWALK = {
    # Mali 2023 reorganisation (19 regions + Bamako district):
    ("MLI", "ML09"): ("ML06", {"taoudenni", "taoudeni", "taoudenit"}),  # ⊂ old Tombouctou
    ("MLI", "MLI-XXX"): ("ML06", {"taoudenni", "taoudeni", "taoudenit"}),
    ("MLI", "ML20"): ("ML09", {"bamako"}),
    ("MLI", "ML11"): ("ML01", None), ("MLI", "ML12"): ("ML01", None),  # Nioro, Kita ⊂ Kayes
    ("MLI", "ML13"): ("ML02", None), ("MLI", "ML14"): ("ML02", None),  # Dioïla, Nara ⊂ Koulikoro
    ("MLI", "ML15"): ("ML03", None), ("MLI", "ML16"): ("ML03", None),  # Bougouni, Koutiala ⊂ Sikasso
    ("MLI", "ML17"): ("ML04", None),                                   # San ⊂ Ségou
    ("MLI", "ML18"): ("ML05", None), ("MLI", "ML19"): ("ML05", None),  # Douentza, Bandiagara ⊂ Mopti
    # Burkina Faso 2024 reorganisation (13 -> 17 regions; BF46/BF52/BF56 dissolved into
    # splits, the other codes were kept — renamed, boundaries approximately the old ones):
    ("BFA", "BF61"): ("BF46", None), ("BFA", "BF62"): ("BF46", None),  # Sourou, Bankui ⊂ Boucle du Mouhoun
    ("BFA", "BF58"): ("BF52", None), ("BFA", "BF60"): ("BF52", None),  # Sirba, Tapoa ⊂ Est
    ("BFA", "BF63"): ("BF52", None),                                   # Goulmou ⊂ Est
    ("BFA", "BF59"): ("BF56", None), ("BFA", "BF64"): ("BF56", None),  # Soum, Liptako ⊂ Sahel
    # CAR: prefectures created 2020, after our COD vintage:
    ("CAF", "CF33"): ("CF32", None),  # Ouham-Fafa ⊂ Ouham
    ("CAF", "CF34"): ("CF31", None),  # Lim-Pendé ⊂ Ouham-Pendé
}


def normalize_pcodes(
    df: pd.DataFrame, poly: pd.DataFrame, label: str, xxx_name_match: bool = False
) -> pd.DataFrame:
    """Reconcile humanitarian admin-1 pcodes to the COD vintage the skill data uses.

    Two mechanical mismatch classes are fixed by re-deriving each country's code style
    from our polygon table instead of hardcoding: prefix style (HPC/HAPI often use an
    ISO3 prefix where the COD uses ISO2 — NER001→NE001, TCD01→TD01) and zero-padding
    (CO5→CO05). A cautious name fallback (unique accent-folded exact match within the
    country) catches renumberings like GT23 'Quiché'. Placeholder codes (*-XXX) are
    dropped by default (plan-wide 'UNSPECIFIED' buckets, country-aggregate rows whose
    names collide with capital regions — 'Djibouti'); xxx_name_match=True name-matches
    them instead, for sources like the population baseline where HAPI ships whole
    un-p-coded countries that way (all of Madagascar's regions).
    Genuinely new admin units (e.g. Mali's 2023
    regions, Burkina's new provinces) have no COD polygon or forecast in our vintage —
    they stay unmatched and are reported so drift is visible on every run.
    """
    ref_by_iso = poly.groupby("iso3")["pcode"].apply(set).to_dict()
    name_ix = {
        iso3: {_fold(n): p for p, n in zip(g["pcode"], g["name"])}
        for iso3, g in poly.groupby("iso3")
    }
    out = df.copy()
    fixed, dropped, unmatched = [], [], []
    for i, row in out.iterrows():
        code, iso3 = row["pcode"], row["iso3"]
        ref = ref_by_iso.get(iso3, set())
        # Reform crosswalk first — it outranks a raw code hit, because reformed
        # vintages reuse codes with different meanings (Mali ML09).
        xw = REFORM_XWALK.get((iso3, code))
        if xw and (xw[1] is None or _fold(row.get("name") or "") in xw[1]):
            fixed.append(f"{code}→{xw[0]} (reform)")
            out.loc[i, "pcode"] = xw[0]
            continue
        if code in ref:
            continue
        # Placeholder codes: real units HAPI could not p-code (name-matchable, only
        # where the caller opts in) or plan-wide caseloads not attributed to any
        # admin unit ("PDI land", "UNSPECIFIED") — droppable, but loudly.
        if code.upper().endswith("-XXX") or _fold(row.get("name") or "") in {"pdi land", "unspecified"}:
            cand = (name_ix.get(iso3, {}).get(_fold(row.get("name") or ""))
                    if xxx_name_match else None)
            if cand is not None:
                fixed.append(f"{code}→{cand} (name)")
                out.loc[i, "pcode"] = cand
            else:
                dropped.append(f"{code} ({row.get('name')})")
                out.loc[i, "pcode"] = None
            continue
        new = None
        m = re.fullmatch(r"([A-Za-z]+)[-_]?(\d+)", code)
        if m and ref:
            r0 = next(iter(ref))
            rm = re.fullmatch(r"([A-Za-z]+)(\d+)", r0)
            if rm:
                cand = rm.group(1) + str(int(m.group(2))).zfill(len(rm.group(2)))
                if cand in ref:
                    new = cand
        if new is None and "name" in row and pd.notna(row.get("name")):
            cand = name_ix.get(iso3, {}).get(_fold(row["name"]))
            if cand is not None:
                new = cand
        if new is not None:
            fixed.append(f"{code}→{new}")
            out.loc[i, "pcode"] = new
        else:
            unmatched.append(f"{iso3}:{code}")
    out = out[out["pcode"].notna()]
    if fixed:
        print(f"  {label}: normalized {len(fixed)} pcodes ({', '.join(fixed[:8])}"
              f"{'…' if len(fixed) > 8 else ''})")
    if dropped:
        print(f"  {label}: dropped {len(dropped)} unattributable placeholder(s): {dropped}")
    if unmatched:
        print(f"  {label}: {len(unmatched)} unit(s) have no polygon in our COD vintage "
              f"(new admin divisions?): {unmatched}")
    return out


def load_pin_adm1() -> tuple[pd.DataFrame, dict[str, str]]:
    """PiN + targeted per ADM1 pcode, EVERY sector the plans publish.

    Per (country, sector): the latest reference period; rows published at admin-1
    preferred, else admin-2 summed per admin1_code. Intersectoral keeps the
    unprefixed pin/targeted columns; every other sector lands in wide
    pin__{code}/tgt__{code} columns (numeric, so pcode-merge aggregation still
    works) that serialization folds into a per-row "sec" dict. Also returns
    {sector_code: sector_name} for the site's caseload selector.
    """
    engine = stratus.get_engine("dev")
    # Admin level 3 included: BFA/COD/ETH/MMR/SYR publish subnational needs at
    # health-zone/township level ONLY — every row still carries admin1_code, so
    # they roll up like admin-2 rows do. Blank sector codes are indicator noise
    # ("Max value of indicators…"), not caseloads.
    q = f"""
    SELECT location_code, sector_code, sector_name, admin1_code, admin1_name,
           admin2_code, admin2_name,
           admin_level, population_status, population, reference_period_start
    FROM hpc.needs_admin
    WHERE category = 'total' AND population_status IN ('INN', 'TGT')
      AND admin_level IN ({LEVEL}, 2, 3) AND COALESCE(sector_code, '') <> ''
      AND {ACODE} IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start"])
    df = df[df["admin_level"] >= LEVEL]  # coarser rows can't be downscaled
    # A country only belongs here while it HAS a current plan: newest cycle must be
    # this year or last. Per-unit fallback to the previous cycle (flagged) is fine
    # WITHIN such a country, but a country whose newest subnational needs are older
    # (Ethiopia and Syria: 2024, no newer plan) must not appear with stale caseloads.
    cutoff = pd.Timestamp(year=pd.Timestamp.now().year - 1, month=1, day=1)
    newest = df.groupby("location_code")["reference_period_start"].transform("max")
    stale_locs = sorted(df.loc[newest < cutoff, "location_code"].unique())
    if stale_locs:
        print(f"  PiN: dropped {stale_locs} — newest plan cycle predates {cutoff.year}")
    df = df[newest >= cutoff]

    # 'ALL' ("Final HRP caseload") is the intersectoral-equivalent of the admin-3
    # publishers: promote it where the country has no subnational Intersectoral
    # rows (COD, SYR); drop it where it would duplicate them (BFA, MMR).
    has_is = set(df.loc[df["sector_code"] == "Intersectoral", "location_code"])
    is_all = df["sector_code"] == "ALL"
    df.loc[is_all & ~df["location_code"].isin(has_is), "sector_code"] = "Intersectoral"
    df = df[df["sector_code"] != "ALL"]

    def _fix_mojibake(s: str) -> str:
        try:  # some admin-3 sector names arrive UTF-8-as-latin1 ("SÃ©curitÃ©")
            return s.encode("latin1").decode("utf-8") if "Ã" in s else s
        except (UnicodeDecodeError, UnicodeEncodeError):
            return s
    sector_names = {
        code: _fix_mojibake(name)
        for code, name in (df.sort_values("admin_level")  # prefer the clean L1/L2 names
                           .drop_duplicates("sector_code")
                           .set_index("sector_code")["sector_name"].items())
    }

    rows: dict[str, dict] = {}
    n_stale_tgt = 0
    for (loc, sector), g in df.groupby(["location_code", "sector_code"]):
        # Walk reference periods newest -> oldest: each unit takes the NEWEST
        # available PiN and targeted independently. Units (or targeted figures)
        # missing from the current cycle fall back to the previous one — flagged:
        # a stale targeted carries its cycle year; a whole-unit fallback shows in
        # the row's plan year.
        refs = sorted(g["reference_period_start"].unique(), reverse=True)
        latest_year = int(pd.Timestamp(refs[0]).year)
        ent: dict[str, dict] = {}
        for ref in refs:
            gr = g[g["reference_period_start"] == ref]
            lvl = min(gr["admin_level"].unique())  # prefer the coarsest at/below LEVEL
            gr = gr[gr["admin_level"] == lvl]
            a1names = gr.drop_duplicates(ACODE).set_index(ACODE)[ANAME]
            agg = gr.groupby([ACODE, "population_status"])["population"].sum().unstack()
            yr = int(pd.Timestamp(ref).year)
            for pcode, r in agg.iterrows():
                e = ent.setdefault(pcode, {"name": a1names.get(pcode), "lvl": lvl})
                if "pin" not in e and pd.notna(r.get("INN")):
                    e["pin"], e["pin_yr"] = int(r["INN"]), yr
                if "tgt" not in e and pd.notna(r.get("TGT")):
                    e["tgt"], e["tgt_yr"] = int(r["TGT"]), yr
        k_inn, k_tgt = (("pin", "targeted") if sector == "Intersectoral"
                        else (f"pin__{sector}", f"tgt__{sector}"))
        for pcode, e in ent.items():
            row = rows.setdefault(pcode, {"pcode": pcode, "iso3": loc, "name": e["name"]})
            row[k_inn] = e.get("pin")
            row[k_tgt] = e.get("tgt")
            # Plan year of the figures actually used for this unit (not the plan's
            # newest cycle — a unit dropped from the current plan keeps its old year).
            used_yr = max(e.get("pin_yr", 0), e.get("tgt_yr", 0)) or latest_year
            row["ref_year"] = max(row.get("ref_year", 0), used_yr)
            row["pin_admin_level"] = e["lvl"]
            if e.get("tgt") is not None and e["tgt_yr"] < max(e.get("pin_yr", 0), latest_year):
                n_stale_tgt += 1
                if sector == "Intersectoral":
                    row["tgt_year"] = e["tgt_yr"]
                else:
                    row[f"tgtyr__{sector}"] = e["tgt_yr"]
    if n_stale_tgt:
        print(f"  PiN: {n_stale_tgt} targeted figure(s) fall back to an older plan cycle "
              f"(flagged per unit)")
    out = pd.DataFrame(rows.values())
    for col in ["pin", "targeted"]:
        if col not in out.columns:
            out[col] = None
    print(f"PiN: {out['iso3'].nunique()} countries, {len(out)} ADM1 units, "
          f"{len(sector_names)} sectors (intersectoral: {out['pin'].notna().sum()})")
    return out, sector_names


def load_severity_adm1() -> pd.DataFrame:
    """Population in JIAF severity 4+ per ADM1 pcode, each country's latest analysis year.

    Rows sit at the country's finest published level (admin-2; COD admin-3) and always
    carry admin1_code, so severity-4+ population sums straight to admin-1. Population
    groups overlap rather than partition: per country prefer the overall (blank) group,
    then 'Global_Population', then the largest named group (the most inclusive union,
    e.g. Cameroon's 'IDPs, Returnees, Refugees, Host communities').
    """
    engine = stratus.get_engine("dev")
    q = """
    SELECT iso3, year, admin1_code, admin1_name, admin2_code, admin2_name,
           population_group, final_severity, population
    FROM hpc.severity_admin
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    df = df[df[ACODE].notna()]
    df = df[df["year"] == df.groupby("iso3")["year"].transform("max")]
    df["population_group"] = df["population_group"].fillna("")

    rows = []
    for iso3, g in df.groupby("iso3"):
        # Population groups differ per plan: an overall group (blank/'Global_Population')
        # when it actually covers the country; union-style overlapping groups (commas in
        # the name, e.g. Cameroon's 'IDPs, Returnees, Refugees, Host communities') where
        # only the most inclusive one may be used; or disjoint displacement categories
        # (Mali: PDI / Rapatries / Communauté Hôte / …) which partition the analysed
        # population and must be summed.
        units = g.groupby("population_group")[ACODE].nunique()
        n_units = g[ACODE].nunique()
        if units.get("", 0) >= 0.5 * n_units:
            g = g[g["population_group"] == ""]
        elif "Global_Population" in units.index:
            g = g[g["population_group"] == "Global_Population"]
        elif any("," in grp for grp in units.index):
            top = g.groupby("population_group")["population"].sum().idxmax()
            g = g[g["population_group"] == top]
        # else: disjoint categories — keep all rows and let the sums add them up.
        a1names = g.drop_duplicates(ACODE).set_index(ACODE)[ANAME]
        sev4 = g[g["final_severity"] >= 4].groupby(ACODE)["population"].sum()
        total = g.groupby(ACODE)["population"].sum()
        # Full class breakdown (each source row carries ONE final_severity 1-5 for its
        # population; an admin-1's distribution comes from its sub-units' classes).
        by_class = g.pivot_table(index=ACODE, columns="final_severity",
                                 values="population", aggfunc="sum")
        for pcode, tot in total.items():
            row = {
                "pcode": pcode, "iso3": iso3, "name": a1names.get(pcode),
                "sev4": int(sev4.get(pcode, 0)),
                "sev_total": int(tot),
                "sev_year": int(g["year"].iloc[0]),
            }
            for c in range(1, 6):
                v = by_class.loc[pcode, c] if c in by_class.columns else None
                row[f"s{c}"] = int(v) if pd.notna(v) else 0
            rows.append(row)
    out = pd.DataFrame(rows)
    print(f"Severity: {out['iso3'].nunique()} countries, {len(out)} ADM1 units, "
          f"{out['sev4'].sum():,} people in severity 4+")
    return out


def load_ipc_adm1() -> pd.DataFrame:
    """IPC/CH acute food insecurity phases per ADM1 pcode, per analysis period.

    From ipc.population_admin (ds-ipc-mirror, HDX HAPI food-security): every
    (period type × validity window) is kept — current vs first/second projection —
    because rounds overlap and the right comparison window is a user choice.
    Admin-2 rows are summed to admin-1 where a country publishes deeper.
    """
    engine = stratus.get_engine("dev")
    # Recency: analyses valid in 2025 or later. Dead/stalled series (ETH 2021,
    # AGO/SLV 2022, BFA 2024-08, TLS 2024-09) are excluded as too old to act on.
    q = f"""
    SELECT location_code, admin1_code, admin1_name, admin2_code, admin2_name,
           admin_level, ipc_phase, ipc_type,
           population_in_phase, reference_period_start, reference_period_end
    FROM ipc.population_admin
    WHERE admin_level IN ({LEVEL}, 2) AND ipc_phase IN ('1', '2', '3', '4', '5', 'all')
      AND {ACODE} IS NOT NULL AND reference_period_end >= '2025-01-01'
    """
    # HAPI rows carry only the validity window; the exercise (analysis) date lives in
    # the per-country HDX table. (country, period type, window) joins it back 1:1.
    qd = """
    SELECT iso3 AS location_code, period_type AS ipc_type,
           reference_period_start, reference_period_end, MAX(analysis_date) AS analysis_date
    FROM ipc.population GROUP BY 1, 2, 3, 4
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start", "reference_period_end"])
        dates = pd.read_sql(qd, conn, parse_dates=[
            "reference_period_start", "reference_period_end", "analysis_date"])
    df = df.merge(dates, how="left", on=[
        "location_code", "ipc_type", "reference_period_start", "reference_period_end"])

    rows = []
    for (loc, t, s, e), g in df.groupby(
        ["location_code", "ipc_type", "reference_period_start", "reference_period_end"]
    ):
        lvl = min(g["admin_level"].unique())  # coarsest at/below LEVEL
        g = g[g["admin_level"] == lvl]
        piv = g.pivot_table(index=ACODE, columns="ipc_phase",
                            values="population_in_phase", aggfunc="sum")
        a1names = g.drop_duplicates(ACODE).set_index(ACODE)[ANAME]
        adate = g["analysis_date"].max()
        for pcode in piv.index:
            row = {"pcode": pcode, "iso3": loc, "name": a1names.get(pcode),
                   "t": t, "s": s, "e": e, "a": adate}
            for ph in ["1", "2", "3", "4", "5", "all"]:
                v = piv.loc[pcode, ph] if ph in piv.columns else None
                row[f"p{ph}"] = int(v) if pd.notna(v) else 0
            rows.append(row)
    out = pd.DataFrame(rows)
    print(f"IPC: {out['iso3'].nunique()} countries, {out['pcode'].nunique()} ADM1 units, "
          f"{out.groupby(['iso3', 't', 's']).ngroups} analysis periods")
    return out


def load_population_adm1() -> pd.DataFrame:
    """Total population per ADM1 unit from pop.population_admin
    (ds-population-mirror: HDX HAPI baseline population, UNFPA COD-PS derived).

    Latest reference period per unit, totals only. P-codes arrive raw from the
    mirror; normalize_pcodes reconciles them to our COD vintage (including
    name-matching units HAPI ships with *-XXX placeholder codes).
    """
    engine = stratus.get_engine("dev")
    q = f"""
    SELECT location_code AS iso3, {ACODE} AS pcode, {ANAME} AS name,
           population, reference_period_end
    FROM pop.population_admin
    WHERE admin_level = {LEVEL} AND {ACODE} IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_end"])
    dup = df.duplicated(["iso3", "pcode", "name", "reference_period_end"], keep=False)
    if dup.any():
        print(f"  population: {int(dup.sum())} duplicate unit row(s) upstream (kept "
              f"last): {sorted(set(df.loc[dup, 'iso3'] + ':' + df.loc[dup, 'pcode']))}")
    df = (df.sort_values("reference_period_end")
            .groupby(["iso3", "pcode", "name"], as_index=False).last())
    df["pop_year"] = df["reference_period_end"].dt.year
    print(f"Population baseline: {df['iso3'].nunique()} countries, "
          f"{len(df)} ADM1 units")
    return df.drop(columns=["reference_period_end"])


def load_hno_pop_adm1() -> pd.DataFrame:
    """Total population per ADM1 unit from the HNO/JIAF baseline (hpc.needs_admin,
    population_status='all'): each plan's own population base, self-consistent with
    the PiN/targeted figures on the axes. Second fallback layer for the scatter
    denominator — covers the countries the COD-PS baseline misses (YEM has no
    COD-PS in HAPI at all) or where its census vintage is distrusted (MLI, SDN…).
    """
    engine = stratus.get_engine("dev")
    q = f"""
    SELECT location_code AS iso3, admin1_code, {ANAME} AS name, admin2_code,
           admin3_code, admin_level, population, reference_period_start
    FROM hpc.needs_admin
    WHERE population_status = 'all' AND lower(COALESCE(category, '')) IN ('total', '')
      AND admin_level >= {LEVEL} AND {ACODE} IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start"])
    rows = []
    for iso3, g in df.groupby("iso3"):
        ref = g["reference_period_start"].max()
        g = g[g["reference_period_start"] == ref]
        lvl = min(g["admin_level"].unique())  # prefer the coarsest at/below LEVEL
        g = g[g["admin_level"] == lvl]
        # One row per finest unit, or the sum double-counts (dup check, not assumed).
        key = ["admin1_code", "admin2_code", "admin3_code"][:max(lvl, LEVEL)]
        dup = g.duplicated(key, keep=False)
        if dup.any():
            print(f"  hno-pop: {iso3} has {int(dup.sum())} duplicated unit row(s) "
                  f"at level {lvl} — kept first per unit")
            g = g.drop_duplicates(key)
        a1names = g.drop_duplicates(ACODE).set_index(ACODE)["name"]
        tot = g.groupby(ACODE)["population"].sum()
        rows += [{"pcode": p, "iso3": iso3, "name": a1names.get(p),
                  "hno_pop": int(v), "hno_year": int(ref.year)}
                 for p, v in tot.items() if pd.notna(v)]
    out = pd.DataFrame(rows)
    print(f"HNO baseline: {out['iso3'].nunique()} countries, {len(out)} ADM1 units")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-geometry", action="store_true",
                    help="Rewrite the geojson even if it already exists (it is "
                         "stable between runs; the default skips it when present).")
    ap.add_argument("--level", type=int, choices=(1, 2), default=1,
                    help="Admin level of the export (2 writes *_adm2 outputs)")
    args = ap.parse_args()
    _set_level(args.level)

    print(f"Loading ADM{LEVEL} skill stats: {SKILL_BLOB}")
    skill = stratus.load_parquet_from_blob(SKILL_BLOB, stage="dev")

    engine = stratus.get_engine("prod")
    with engine.connect() as conn:
        poly = pd.read_sql(
            f"SELECT pcode, iso3, name FROM public.polygon WHERE adm_level={LEVEL}", conn)
        country_names = pd.read_sql(
            "SELECT iso3, name FROM public.polygon WHERE adm_level=0", conn,
        ).set_index("iso3")["name"].to_dict()
        # Parent admin-1 names for level-2 rows (tooltips/table): no parent column
        # exists, but COD adm2 pcodes extend their adm1 parent's pcode.
        parent_names: dict[str, str] = {}
        if LEVEL == 2:
            p1 = pd.read_sql(
                "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=1", conn)
            a1_by_iso = {i: sorted(g_["pcode"], key=len, reverse=True)
                         for i, g_ in p1.groupby("iso3")}
            a1_names = p1.set_index("pcode")["name"].to_dict()
            for _, r in poly.iterrows():
                par = next((c for c in a1_by_iso.get(r["iso3"], [])
                            if str(r["pcode"]).startswith(c)), None)
                if par:
                    parent_names[r["pcode"]] = a1_names[par]
    names = poly.set_index("pcode")["name"].to_dict()

    print("Reconciling humanitarian pcodes to the COD vintage...")
    df_pin_raw, sector_names = load_pin_adm1()
    df_pin = normalize_pcodes(df_pin_raw, poly, "PiN")
    df_sev = normalize_pcodes(load_severity_adm1(), poly, "severity")
    df_ipc = normalize_pcodes(load_ipc_adm1(), poly, "IPC")
    df_pop = normalize_pcodes(load_population_adm1(), poly, "population",
                              xxx_name_match=True)
    # Normalization can merge units (reforms, renumberings) — sum to one per pcode.
    df_pop = (df_pop.groupby("pcode", as_index=False)
              .agg({"population": "sum", "pop_year": "max"})
              .rename(columns={"population": "pop"}))
    df_hno = normalize_pcodes(load_hno_pop_adm1(), poly, "hno-pop")
    df_hno = (df_hno.groupby("pcode", as_index=False)
              .agg({"hno_pop": "sum", "hno_year": "max"}))
    ipc_iso3 = df_ipc.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    df_ipc = (df_ipc.groupby(["pcode", "t", "s", "e"], as_index=False)
              .agg({**{f"p{ph}": "sum" for ph in ["1", "2", "3", "4", "5", "all"]},
                    "a": "max"}))
    # Per pcode: the list of available analysis periods, newest validity first —
    # capped at 6 (payload guard; the period logic only ever reaches recent ones
    # plus the most-recent-past fallback).
    ipc_lists: dict[str, list] = {}
    for pcode, g in df_ipc.groupby("pcode"):
        g = g.sort_values("e", ascending=False).head(6)
        ipc_lists[pcode] = [
            {
                "t": r["t"],
                "s": r["s"].strftime("%Y-%m"),
                "e": r["e"].strftime("%Y-%m"),
                "a": r["a"].strftime("%Y-%m") if pd.notna(r["a"]) else None,
                "label": f"{calendar.month_abbr[r['s'].month]}–"
                         f"{calendar.month_abbr[r['e'].month]} {r['e'].year}",
                "p": [int(r[f"p{ph}"]) for ph in ["1", "2", "3", "4", "5"]],
                "tot": int(r["pall"]),
            }
            for _, r in g.iterrows()
        ]
    # Normalization can merge codes (renumberings) — re-aggregate to one row per pcode.
    _sum = lambda s: s.sum(min_count=1)  # noqa: E731 — all-NaN stays None, not 0
    sec_cols = [c for c in df_pin.columns if c.startswith(("pin__", "tgt__", "tgtyr__"))]
    df_pin = (df_pin.groupby(["pcode", "iso3"], as_index=False)
              .agg({"name": "first", "pin": _sum, "targeted": _sum,
                    **{c: ("max" if c.startswith("tgtyr__") else _sum) for c in sec_cols},
                    **({"tgt_year": "max"} if "tgt_year" in df_pin.columns else {}),
                    "ref_year": "max", "pin_admin_level": "max"}))
    df_sev = (df_sev.groupby(["pcode", "iso3"], as_index=False)
              .agg({"name": "first", "sev4": "sum", "sev_total": "sum", "sev_year": "max",
                    **{f"s{c}": "sum" for c in range(1, 6)}}))
    # Union of PiN and severity units; iso3/name from whichever side has them.
    df_hum = df_pin.merge(df_sev, on="pcode", how="outer", suffixes=("", "_sev"))
    for col in ["iso3", "name"]:
        df_hum[col] = df_hum[col].combine_first(df_hum[f"{col}_sev"])
    df_hum = df_hum.drop(columns=["iso3_sev", "name_sev"]).rename(columns={"name": "name_hum"})

    # Scope: HNRP units PLUS IPC-covered units outside any plan — the tab's purpose
    # includes surfacing severe food insecurity the HNRP does not capture. IPC-only
    # rows carry no PiN/severity/targeted; the site shows them in IPC mode only.
    extra = sorted(set(ipc_iso3) - set(df_hum["pcode"]))
    if extra:
        df_hum = pd.concat(
            [df_hum, pd.DataFrame([{"pcode": p, "iso3": ipc_iso3[p]} for p in extra])],
            ignore_index=True,
        )
        print(f"Scope: +{len(extra)} IPC-covered ADM1 units outside HNRP plans "
              f"({len(set(ipc_iso3[p] for p in extra))} countries)")
    skill = skill[skill["pcode"].isin(set(df_hum["pcode"]))]
    if skill.empty:
        sys.exit("No overlap between ADM1 skill pcodes and HNRP PiN pcodes")

    # Latest issuance, same logic as the ADM0 static export.
    iy = skill[skill["current_forecast_year"].notna()].copy()
    iy["_iy"] = [
        issued_year_for_season(int(cy), im, t)
        for cy, im, t in zip(iy["current_forecast_year"], iy["issued_month"], iy["trimester"])
    ]
    max_iy_by_month = iy.groupby("issued_month")["_iy"].max().astype(int).to_dict()
    global_max_iy = max(max_iy_by_month.values())
    issued_month = max(m for m, y in max_iy_by_month.items() if y == global_max_iy)
    issued_label = f"{calendar.month_name[issued_month]} {global_max_iy}"
    print(f"Latest issuance: {issued_label}")

    # Rainy-season mask over the HNRP ADM1 units (same ERA5 climatology as other exports).
    pcodes = sorted(set(skill["pcode"].dropna()))
    ph = ",".join(["%s"] * len(pcodes))
    print(f"Querying ERA5 climatology for {len(pcodes)} ADM1 pcodes...")
    with engine.connect() as conn:
        era5 = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
            conn, params=tuple(pcodes), parse_dates=["valid_date"],
        )
    monthly_clim = (
        era5.assign(month=era5["valid_date"].dt.month)
        .groupby(["pcode", "month"])["mean"].mean()
        .reset_index().rename(columns={"mean": "mean_mm_day"})
    )
    rainy_set = compute_rainy_set(monthly_clim)

    # Per unit: the worst qualifying drought slot at the latest issuance.
    sub = skill[skill["issued_month"] == issued_month].copy()
    sub = sub[sub.apply(lambda r: _tri_valid(TRIMESTERS[r["trimester"]], issued_month), axis=1)]
    records = []
    for pcode, g in sub.groupby("pcode"):
        best = None
        for _, r in g.iterrows():
            pct, pr = r["forecast_percentile"], r["pearson_r"]
            if pd.isna(pct) or pd.isna(pr):
                continue
            if pr < THRESHOLDS["r_mod"] or pct >= 50:
                continue
            if (pcode, r["trimester"]) not in rainy_set:
                continue
            rp = r["forecast_rp"]  # directional drought RP (pct < 50)
            if pd.isna(rp):
                continue
            if best is None or rp > best["rp"]:
                best = {
                    "tri": r["trimester"],
                    "lead": trimester_lead(issued_month, TRIMESTERS[r["trimester"]]),
                    "rp": float(rp),
                    "pct": float(pct),
                    "r": float(pr),
                }
        row = {"pcode": pcode, "name": names.get(pcode)}
        if best:
            best["tri_label"] = _tri_label(TRIMESTERS[best["tri"]])
            best["rp"] = round(best["rp"], 1)
            best["pct"] = round(best["pct"], 1)
            best["r"] = round(best["r"], 3)
            row |= best
        # Every valid trimester's forecast, for the explicit valid-season selector.
        tris = {}
        for _, r in g.iterrows():
            pct, pr = r["forecast_percentile"], r["pearson_r"]
            if pd.isna(pct):
                continue
            drp = r["forecast_rp"] if pct < 50 else r["flood_rp"]
            tris[r["trimester"]] = {
                "lead": trimester_lead(issued_month, TRIMESTERS[r["trimester"]]),
                "pct": round(float(pct), 1),
                "r": round(float(pr), 3) if pd.notna(pr) else None,
                "rp": round(float(drp), 1) if pd.notna(drp) else None,
                "rainy": (pcode, r["trimester"]) in rainy_set,
            }
        if tris:
            row["tris"] = tris
        # Fallback display slot for units with no qualifying drought signal: the default
        # (lead-1) trimester, same as the Map tab's default selection. Lets the site show
        # every HNRP unit with its real forecast category (flood/normal/low-skill/
        # off-season) instead of a blank.
        fb = next((r for _, r in g.iterrows()
                   if trimester_lead(issued_month, TRIMESTERS[r["trimester"]]) == 1), None)
        if fb is not None:
            fpct, fr = fb["forecast_percentile"], fb["pearson_r"]
            frp = fb["forecast_rp"] if pd.notna(fpct) and fpct < 50 else fb["flood_rp"]
            row |= {
                "fb_tri": fb["trimester"],
                "fb_label": _tri_label(TRIMESTERS[fb["trimester"]]),
                "fb_pct": round(float(fpct), 1) if pd.notna(fpct) else None,
                "fb_r": round(float(fr), 3) if pd.notna(fr) else None,
                "fb_rp": round(float(frp), 1) if pd.notna(frp) else None,
                "fb_rainy": (pcode, fb["trimester"]) in rainy_set,
            }
        records.append(row)

    df_fc = pd.DataFrame(records)
    merged = df_hum.merge(df_fc, on="pcode", how="left")
    merged = merged.merge(df_pop, on="pcode", how="left")
    # Distrust the baseline where it can't be right. An analysed base slightly
    # above the total is expected (analyses use current population estimates,
    # the COD-PS baseline can be an old census — PAK 2017, MDG 2018), so allow
    # 1.3x headroom for vintage growth; beyond that one side is mis-assigned.
    # PAK is excluded outright: HAPI mis-p-codes its COD-PS rows (duplicate PK7,
    # figures shifted one unit over — verified against the 2017 census).
    ipc_max = {p: max((c["tot"] for c in lst), default=0) for p, lst in ipc_lists.items()}
    analysed = pd.concat(
        [merged["pcode"].map(ipc_max), merged["sev_total"]], axis=1
    ).max(axis=1).fillna(0)
    bad = merged["pop"].notna() & (
        merged["iso3"].isin({"PAK"}) | (analysed > 1.3 * merged["pop"])
    )
    if bad.any():
        print(f"Baseline distrusted for {int(bad.sum())} unit(s) (upstream "
              f"mis-coding or analysed > 1.3x total): "
              f"{sorted(set(merged.loc[bad, 'iso3'] + ':' + merged.loc[bad, 'pcode']))}")
        merged.loc[bad, ["pop", "pop_year"]] = float("nan")
    merged["pop_src"] = None
    merged.loc[merged["pop"].notna(), "pop_src"] = "COD-PS"
    # Layer 2: the HNO/JIAF baseline fills units the COD-PS layer left empty
    # (missing upstream or distrust-nulled). An HNO total clearly below the
    # analysed population can't be a valid denominator either — logged and
    # skipped. 5% headroom: the same plan's severity analysis often uses a
    # slightly newer population estimate (YEM 2026 JIAF runs 99–104% of the
    # 2025 HNO 'all' base) — vintage jitter, not a broken denominator.
    merged = merged.merge(df_hno, on="pcode", how="left")
    hno_low = (merged["pop"].isna() & merged["hno_pop"].notna()
               & (analysed > 1.05 * merged["hno_pop"]))
    if hno_low.any():
        print(f"  hno-pop: {int(hno_low.sum())} unit(s) have HNO total < analysed "
              f"population, skipped: "
              f"{sorted(set(merged.loc[hno_low, 'iso3'] + ':' + merged.loc[hno_low, 'pcode']))}")
    use_hno = merged["pop"].isna() & merged["hno_pop"].notna() & ~hno_low
    merged.loc[use_hno, "pop"] = merged.loc[use_hno, "hno_pop"]
    merged.loc[use_hno, "pop_year"] = merged.loc[use_hno, "hno_year"]
    merged.loc[use_hno, "pop_src"] = "HNO"
    merged = merged.drop(columns=["hno_pop", "hno_year"])
    n_pop = merged["pop"].notna().sum()
    print(f"Population baseline covers {n_pop}/{len(merged)} units "
          f"(COD-PS {int((merged['pop_src'] == 'COD-PS').sum())}, "
          f"HNO {int(use_hno.sum())}; the rest fall back to the analysed proxy)")
    # COD name where we have the polygon; the plan's own name for unmatched units.
    merged["name"] = merged["name"].combine_first(merged["name_hum"])
    merged = merged.drop(columns=["name_hum"])
    merged["country"] = merged["iso3"].map(country_names)
    if LEVEL == 2:
        merged["parent"] = merged["pcode"].map(parent_names)
    # A row whose pcode has no polygon at this level can never display — no map
    # geometry and no forecast series behind it (at level 2, mostly IPC codes from
    # countries outside the adm2 scope, plus unreconciled reform codes). Prune.
    in_poly = merged["pcode"].isin(set(poly["pcode"]))
    if (~in_poly).any():
        gone = sorted(set(merged.loc[~in_poly, "iso3"].dropna()))
        print(f"Pruned {int((~in_poly).sum())} unit(s) with no adm{LEVEL} polygon "
              f"({len(gone)} countries: {', '.join(gone[:14])}{'…' if len(gone) > 14 else ''})")
        merged = merged[in_poly]
    n_signal = merged["rp"].notna().sum()
    print(f"{len(merged)} HNRP ADM1 units; {n_signal} with a qualifying drought signal")

    # Coverage report: every humanitarian unit should have forecast data and a polygon.
    no_fc = merged.loc[~merged["pcode"].isin(set(skill["pcode"])), ["iso3", "pcode"]]
    if len(no_fc):
        print(f"NOTE: {len(no_fc)} humanitarian unit(s) have NO forecast data (absent from "
              f"the zonal stats): {sorted(no_fc['iso3'] + ':' + no_fc['pcode'])}")
    if GEO_OUT.exists():
        gp = {f["properties"]["pcode"] for f in json.loads(GEO_OUT.read_text())["features"]}
        no_geo = merged.loc[~merged["pcode"].isin(gp), ["iso3", "pcode"]]
        if len(no_geo):
            print(f"NOTE: {len(no_geo)} unit(s) missing from the geojson (not on the map): "
                  f"{sorted(no_geo['iso3'] + ':' + no_geo['pcode'])}")

    valid_tris = sorted(
        [t for t in TRIMESTERS if _tri_valid(TRIMESTERS[t], issued_month)],
        key=lambda t: _min_signed(TRIMESTERS[t], issued_month),
    )
    # Per-sector PiN/targeted travel the pipeline as wide numeric columns; fold them
    # into a compact per-row dict {sector: [pin, targeted]} for the payload.
    def _row(rec: dict) -> dict:
        sec, stale = {}, {}
        for c in sec_cols:
            v = rec.pop(c, None)
            if v is not None and pd.notna(v):
                pref, code = c.split("__", 1)
                if pref == "tgtyr":
                    stale[code] = int(v)
                else:
                    sec.setdefault(code, [None, None])[0 if pref == "pin" else 1] = int(v)
        for code, yr in stale.items():
            if code in sec and sec[code][1] is not None:
                sec[code].append(yr)  # [pin, targeted, staleTargetedYear]
        out = {k: (v if isinstance(v, (list, dict)) else None if pd.isna(v) else v)
               for k, v in rec.items()}
        if out.get("tgt_year") is not None:
            out["tgt_year"] = int(out["tgt_year"])
        for k in ("pop", "pop_year"):
            if out.get(k) is not None:
                out[k] = int(out[k])
        if sec:
            out["sec"] = sec
        if rec["pcode"] in ipc_lists:
            out["ipc"] = ipc_lists[rec["pcode"]]
        return out

    payload = {
        "adm_level": LEVEL,
        "issued_label": issued_label,
        "issued_month": int(issued_month),
        "issued_year": int(global_max_iy),
        "trimesters": valid_tris,
        "thresholds": THRESHOLDS,
        # Selector order: FSC first (the tab's theme is food security), then by name.
        "sectors": sorted(
            ([c, n] for c, n in sector_names.items() if c != "Intersectoral"),
            key=lambda cn: (cn[0] != "FSC", cn[1]),
        ),
        "weight_note": (
            "Humanitarian weight is population in JIAF inter-sectoral severity 4+ from the "
            "HNRP severity analysis (ds-hnrp-mirror, latest analysis year per country), with "
            "per-sector PiN / targeted alongside. Admin-2/3 figures are summed to admin-1."
        ),
        # Prune rows with no usable humanitarian data at all (e.g. Syria 2025:
        # severity classes published with NO population figures, needs pre-2025).
        "rows": [
            row for row in (
                _row(rec)
                for rec in merged.drop(columns=["pin_admin_level"]).to_dict("records"))
            if row.get("pin") is not None or row.get("targeted") is not None
            or row.get("sec") or row.get("ipc") or (row.get("sev_total") or 0) > 0
        ],
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB)")

    if args.rebuild_geometry or not GEO_OUT.exists():
        print("Building ADM1 geometry from COD shapefiles (polygon container)...")
        export_geometry(sorted(merged["iso3"].dropna().unique()), names)


if __name__ == "__main__":
    main()
