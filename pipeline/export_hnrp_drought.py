"""Forecast × HNRP overlay: where a bad drought forecast meets a large PiN.

Joins the latest ADM1/ADM2 drought forecast against the humanitarian mirrors
(hpc.*, ipc.*, pop.* in the dev DB) per admin unit and writes one JSON per level
for the static site's "Forecast × HNRP" tab.

Forecast selection per unit (latest issuance):
  - valid trimesters (lead −2..4), rainy-season only, skill r ≥ r_mod (0.30)
  - drought side only (forecast percentile < 50); severity = directional drought RP
  - the unit's row reports its WORST qualifying slot (max drought RP)

DATA SEMANTICS & AGGREGATION RULES (hard-won — read before editing a loader):

1. JIAF CLASSIFIES AREAS, IPC COUNTS PEOPLE. hpc.severity_admin gives each finest
   unit (× population group) ONE final_severity and that unit's population — there
   is NO people-per-class breakdown (verified: max one class per unit across all
   8,108 units). Our "sN" figures are therefore populations of areas classified at
   class N, and every label must say so. ipc.population_admin genuinely is
   population_in_phase — people-level. PiN (needs_admin INN) is also people-level
   and is NOT derivable from the severity table; treat it as the plan's
   authoritative caseload and severity classes as area context.
   HOW PiN IS MADE (JIAF 2.0 "Mosaic Method"): each sector estimates its own PiN
   per finest analysis unit; the intersectoral PiN takes the HIGHEST sectoral PiN
   per unit, sums those maxima upward, then validation workshops resolve flags by
   consensus — so sectoral arithmetic will NOT reproduce it exactly (TCD: 73% of
   admin-2 units equal max(sector); SDN 0% equal but 98% ≥ it, the signature of
   mosaic at a finer unit). From HPC 2026 overall PiN counts only areas in
   intersectoral severity 3+ (2025-cycle PiN can include class-1/2 areas — why
   PiN > pop(3+ areas) in COD/MOZ). The PiN-BY-SEVERITY distribution ("PiN par
   gravité" workbook sheets, reintroduced by the 2025 Humanitarian Reset) IS in
   the mirror, as hpc.pin_admin — final_pin per unit × population group, classed
   by COALESCE(final_severity, severity). load_pbs_adm1() below is its consumer and
   the site's default severity source; severity_admin is the area-classification
   fallback for units the PbS does not class. (This block said the opposite until
   2026-08; the PbS landed in the mirror after it was written.)

2. PREFER THE COARSEST PUBLISHED LEVEL. Where a country publishes the same series
   at admin-1 AND finer, the admin-1 figures equal the finer sums (ratio 1.000)
   EXCEPT where the finer level double-counts (MMR PRO 2024 sums to 2.18× its
   admin-1 total) — so we aggregate the coarsest level at/below the target and
   never mix levels.

3. ONE NAMED SERIES PER SECTOR CODE. A sector_code can carry several sector_names
   ("Protection (total)" AND "General Protection" both as PRO, category='total');
   summing across them double-counts. Keep the dominant series (most rows, then
   largest total) per (country, sector).

4. category='total' ONLY for caseloads (231 other values are sex/age/group
   breakdowns of the same people); population_status='all' + category total/blank
   for the HNO population baseline.

5. *-XXX PLACEHOLDER SUB-CODES may carry a WRONG stated admin-1 (MOZ 2024 filed
   Nampula districts under Sofala, Zambézia under Cidade de Maputo — a systematic
   shift). Their names are good: rollups re-attribute them by unique COD sub-unit
   name match (sub_parent lookup); at their own level they are unmappable and drop.

6. HAPI SHIPS EXACT DUPLICATE ROWS (same resource, same value, twice — COD 450
   keys) in ipc.population_admin: drop_duplicates before any pivot/sum.

7. hpc.severity_admin population GROUPS overlap rather than partition — per
   country prefer the overall (blank) group when it covers ≥50% of units, then
   'Global_Population', then the largest comma-named union; only sum when groups
   are disjoint displacement categories.

8. pop.population_admin is totals-only (gender='all', age_range='all') — safe to
   take rows as-is; its *-XXX rows are distinct areas (one row per name).

9. NO SUBNATIONAL TARGETED EXISTS FOR 2026, anywhere (checked 2026-08 four ways:
   needs_admin population_status='TGT', the HAPI humanitarian-needs endpoint, the
   HPC v2 API, and humanitarianaction.info). HPC's 2026 targeting is a national
   total only, because the 2026 subnational figures come from the JIAF needs
   analysis, which publishes PiN by severity and no targets. Do NOT go looking
   again, and do NOT carry 2025 targets forward: the 2025 subnational sums match
   the 2025 national totals exactly (ratio 1.00 for all 20 countries) and NOT
   2026's (0.63–1.88), so they are last year's targets, not mislabelled ones.

10. THE POPULATION DENOMINATOR IS THE WEAKEST FIGURE IN THE PAYLOAD. popBase takes
   the LARGEST of COD-PS / HNO / WorldPop, IPC analysed and JIAF analysed, and it
   is still exceeded by the caseload in ~3% of units. Worst where a country has no
   COD-PS and falls back to WorldPop 2020: SYR (270 admin-3 units, 91 over 100% —
   displacement into NW Idleb/Aleppo and returns to a Quneitra WorldPop counted at
   221 people) — though SYR's national PiN is still only 90% of its summed
   baseline, i.e. a distribution problem, not an inflated total. Treat >100% as
   informative about the baseline, not as a bug to clamp away.

11. ABSENT IS NOT ZERO, AND NOT "MINIMAL". An IPC projection covers FEWER areas than the
   countrywide current period while its 'all' row stays at full country scope, so an
   unassessed unit arrives with a population and no phase rows. Read literally that
   classifies it phase 1 — in Aug 2026 the site painted most of Sudan, mid-famine, as
   "Minimal" on exactly this. Anything consuming the sN/pN columns must test that the
   distribution SUMS ABOVE ZERO before classifying, and treat zero as "not assessed".
   The same trap is any `.get(k, 0)`, `fillna(0)` or `COALESCE(x, 0)` on a severity or
   caseload column: it turns missing into reassuring. See the site's ipcComboOf(), the
   KB's methods/absent-data.md, and NEVER verify this class of bug on national totals —
   ours matched IPC to 1% while the map was wrong.

12. IPC ROWS ARE DUPLICATED, AND drop_duplicates ON ALL COLUMNS DOES NOT CATCH IT. HAPI
   ships some units twice per period; the copies round the same published figure
   independently (SSD Rumbek North: 77,350 x 0.15 = 11,602.5 filed as both 11,603 and
   11,602), so an all-column dedup keeps both and the pivot SUMS them. Their 'all' rows
   round identically and do dedup, which is why phase sums land at a clean 2.00x their
   analysed population — that ratio is the tell. Dedup on the KEY, never the value.

Run:  uv run python pipeline/export_hnrp_drought.py [--level 2] [--rebuild-geometry]
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


# Admin-3: countries with adm3 humanitarian data AND boundaries we hold. BFA/MMR/
# SYR come from the COD shapefile zips' adm3 layers; COD (DRC) publishes against
# zones de santé — the SNIS/OCHA health-zone shapefile (RDC_Zone_de_sante_09092019,
# HDX dr-congo-health-0, mirrored to our dev blob) joins hpc.* 519/519 by ZSCode.
ADM3_ISO3S = ["BFA", "COD", "MMR", "SYR"]


def load_adm3_shp(iso3: str):
    """The adm3 boundary layer + (pcode, name, parent-adm2) column names."""
    if iso3 == "COD":
        g = stratus.load_shp_from_blob(
            f"{PROJECT_PREFIX}/raw/cod_zs_09092019.zip",
            shapefile="RDC_Zone_de_sante_09092019.shp",
            stage="dev", container_name="projects",
        )
        g["_parent"] = g["ZSCode"].str[:6]  # CD####ZS## -> CD####
        return g, "ZSCode", "Nom", "_parent"
    g = stratus.load_shp_from_blob(
        f"{iso3.lower()}_shp.zip", shapefile=f"{iso3.lower()}_adm3.shp",
        stage="prod", container_name="polygon",
    )
    ncol = next(c for c in g.columns if c.upper() in ("ADM3_EN", "ADM3_FR", "ADM3_ES"))
    return g, "ADM3_PCODE", ncol, "ADM2_PCODE"


def _set_level(level: int) -> None:
    global LEVEL, ACODE, ANAME, SKILL_BLOB, OUT, GEO_OUT, SIMPLIFY_TOLERANCE
    LEVEL = level
    if level == 2:
        ACODE, ANAME = "admin2_code", "admin2_name"
        SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm2.parquet"
        OUT = HERE.parent / "docs" / "data" / "hnrp_drought_adm2.json"
        GEO_OUT = HERE.parent / "docs" / "data" / "hnrp_adm2.geojson"
        SIMPLIFY_TOLERANCE = 0.03
    elif level == 3:
        # No adm3 zonal stats exist — the forecast (skill, rainy season) is
        # inherited from each unit's PARENT admin-2 (mapped via the shapefiles'
        # ADM2_PCODE column), so the adm2 skill blob is the source here too.
        ACODE, ANAME = "admin3_code", "admin3_name"
        SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm2.parquet"
        OUT = HERE.parent / "docs" / "data" / "hnrp_drought_adm3.json"
        GEO_OUT = HERE.parent / "docs" / "data" / "hnrp_adm3.geojson"
        SIMPLIFY_TOLERANCE = 0.035


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
            if LEVEL == 3:
                g, pcol, _, _ = load_adm3_shp(iso3)
            else:
                g = stratus.load_shp_from_blob(
                    f"{iso3.lower()}_shp.zip", shapefile=f"{iso3.lower()}_adm{LEVEL}.shp",
                    stage="prod", container_name="polygon",
                )
                pcol = next((c for c in g.columns
                             if c.lower() in (f"adm{LEVEL}_pcode", "pcode")), None)
        except Exception as e:  # noqa: BLE001 — a missing country shouldn't kill the export
            print(f"  {iso3}: boundary load failed ({type(e).__name__}), skipped")
            continue
        if pcol is None:
            print(f"  {iso3}: no pcode column, skipped")
            continue
        g = g[[pcol, "geometry"]].rename(columns={pcol: "pcode"})
        topo = tp.Topology(g, prequantize=True, shared_coords=True)
        g = topo.toposimplify(SIMPLIFY_TOLERANCE).to_gdf()
        try:
            g["geometry"] = g["geometry"].make_valid()
        except Exception:  # mixed-dimension collections (COD zones): rebuild by structure
            g["geometry"] = g["geometry"].make_valid(method="structure", keep_collapsed=False)
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

    A name-matched placeholder NEVER lands on a unit another row already holds:
    the frames reaching here carry one row per unit, so a collision would mean
    adding a second, and the merge downstream sums them. That guard is what makes
    xxx_name_match safe on caseloads — the hazard it was withheld from was exactly
    a plan-wide row whose name collides with a real unit ("Djibouti").
    """
    ref_by_iso = poly.groupby("iso3")["pcode"].apply(set).to_dict()
    name_ix = {
        iso3: {_fold(n): p for p, n in zip(g["pcode"], g["name"])}
        for iso3, g in poly.groupby("iso3")
    }
    out = df.copy()
    # Units already spoken for by a real (non-placeholder) row.
    _ph = out["pcode"].fillna("").str.upper().str.endswith("-XXX")
    taken = set(zip(out.loc[~_ph, "iso3"], out.loc[~_ph, "pcode"]))
    fixed, dropped, unmatched, blocked = [], [], [], []
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
            if cand is not None and (iso3, cand) in taken:
                blocked.append(f"{code} ({row.get('name')}) → {cand} already held")
                cand = None
            if cand is not None:
                fixed.append(f"{code}→{cand} (name)")
                out.loc[i, "pcode"] = cand
                taken.add((iso3, cand))
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
    if blocked:
        print(f"  {label}: {len(blocked)} placeholder(s) NOT name-matched — the unit "
              f"already has a row: {blocked}")
    if dropped:
        print(f"  {label}: dropped {len(dropped)} unattributable placeholder(s): {dropped}")
    if unmatched:
        print(f"  {label}: {len(unmatched)} unit(s) have no polygon in our COD vintage "
              f"(new admin divisions?): {unmatched}")
    return out


def _scrambled_cycles(engine) -> set[tuple[str, pd.Timestamp]]:
    """Plan cycles whose unit-level attribution is provably misaligned upstream.

    The signature, found in VEN 2025: a cycle's own admin population baseline
    (Intersectoral / population_status 'all') is EXACTLY the COD-PS population
    MULTISET — the plan is quoting COD-PS, unit for unit — yet a large share of
    units carry another unit's number. Same values, wrong owners: the rows were
    shuffled against their p-codes somewhere upstream, and everything sitting on
    those rows (PiN, targeted, every sector) belongs to a different unit.
    VEN 2025 puts 156,307 PiN on Cardenal Quintero (9,441 people) and 4,387 on
    Libertador (217,537) — and its baseline hands Cardenal Quintero the 600,351
    that belongs to Sucre in Miranda. 135 of 335 units are affected; the national
    total is untouched, which is why it survives every aggregate check.

    Only an EXACT multiset match licenses this call. Plans that publish their own
    population estimates (MLI, SDN, CAF — 50–100% of units differ from COD-PS in
    VALUE) are a different, legitimate thing and are never flagged. Across the 17
    country-cycles that carry a baseline, this fires on VEN 2025 alone: VEN 2024
    and HTI 2025 also quote COD-PS exactly, and assign every unit correctly.

    Un-scrambling is possible in principle — each row's baseline value identifies
    the unit it belongs to — but that would mean publishing an attribution the
    source never made, silently.

    What the drop actually does (checked against the 2026-08 payload, because the
    obvious guess is wrong): it removes the cycle from needs_admin ONLY. VEN 2025
    survives in the payload, seeded instead from that year's PbS (pin_admin,
    pbs_yr=2025), which carries the same units correctly attributed — Cardenal
    Quintero 3,493 against 9,441 people, no unit above 37% of its population.
    needs_admin is the only source of TARGETED figures, so the visible effect is
    that VEN 2025 has a PiN and no targets; targeting is available under 2024.
    It is not a whole-country fallback to the previous cycle.
    """
    q_base = """
    SELECT location_code, reference_period_start,
           COALESCE(admin2_code, admin1_code) AS pcode, population
    FROM hpc.needs_admin
    WHERE sector_code = 'Intersectoral' AND category = 'total'
      AND population_status = 'all' AND population IS NOT NULL
      AND COALESCE(admin2_code, admin1_code) IS NOT NULL
    """
    q_pop = """
    SELECT COALESCE(admin2_code, admin1_code) AS pcode, population, reference_period_start
    FROM pop.population_admin WHERE population IS NOT NULL
    """
    with engine.connect() as conn:
        base = pd.read_sql(q_base, conn, parse_dates=["reference_period_start"])
        pop = pd.read_sql(q_pop, conn, parse_dates=["reference_period_start"])
    ref = (pop.sort_values("reference_period_start").groupby("pcode")["population"].last())
    bad: set[tuple[str, pd.Timestamp]] = set()
    for (loc, period), g in base.groupby(["location_code", "reference_period_start"]):
        g = g.drop_duplicates("pcode")
        cod = g["pcode"].map(ref)
        ok = cod.notna()
        if ok.sum() < 10:
            continue
        plan_v, cod_v = g["population"][ok].astype("int64"), cod[ok].astype("int64")
        if sorted(plan_v) != sorted(cod_v):
            continue  # the plan is not quoting COD-PS — nothing to conclude
        mis = int((plan_v.values != cod_v.values).sum())
        if mis >= 5 and mis / len(plan_v) > 0.02:
            bad.add((loc, period))
            print(f"  PiN: {loc} {period:%Y} cycle DROPPED — its baseline is the COD-PS "
                  f"population but {mis} of {len(plan_v)} units carry another unit's "
                  f"value; every figure on those rows is misattributed upstream")
    return bad


def load_pin_adm1(sub_parent: dict | None = None) -> tuple[pd.DataFrame, dict[str, str]]:
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
           admin2_code, admin2_name, admin3_code, admin3_name,
           admin_level, population_status, population, reference_period_start
    FROM hpc.needs_admin
    WHERE category = 'total' AND population_status IN ('INN', 'TGT')
      AND admin_level IN ({LEVEL}, 2, 3) AND COALESCE(sector_code, '') <> ''
      AND {ACODE} IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start"])
    df = df[df["admin_level"] >= LEVEL]  # coarser rows can't be downscaled

    # One sector_code can carry SEVERAL named series WITHIN a reference period
    # ("Protection (total)" AND "General Protection" both under PRO,
    # category='total') — summing them double-counts (MMR PRO 2024 summed to 2.2x
    # its published admin-1 total). Keep the dominant series (most rows, then
    # largest total) per (country, sector, reference period) — per period, because
    # publishers rename series between cycles (COD: "Final HRP Caseload" 2024 vs
    # "…caseload" 2025) and a global pick would silently freeze an old cycle.
    grp = ["location_code", "sector_code", "reference_period_start"]
    picks = (df.groupby(grp + ["sector_name"])
             .agg(n=("population", "size"), tot=("population", "sum")).reset_index())
    multi = picks.groupby(grp).filter(lambda x: len(x) > 1)
    if len(multi):
        keep = picks.sort_values(["n", "tot"], ascending=False).drop_duplicates(grp)
        keep_keys = set(zip(*(keep[c] for c in grp + ["sector_name"])))
        before = len(df)
        df = df[[k in keep_keys for k in zip(*(df[c] for c in grp + ["sector_name"]))]]
        dropped_series = sorted({f"{l}/{s}" for l, s in
                                 zip(multi["location_code"], multi["sector_code"])})
        print(f"  PiN: secondary same-period series under shared sector codes dropped "
              f"({before - len(df)} rows; {', '.join(dropped_series[:8])}"
              f"{'…' if len(dropped_series) > 8 else ''})")

    if sub_parent:
        # Placeholder-coded sub-rows (*-XXX) can carry a WRONG admin-1 upstream
        # (MOZ 2024: Nampula districts filed under Sofala, Zambézia under Cidade de
        # Maputo — a systematic shift). Their names are good: re-attribute by unique
        # COD sub-unit name match; keep the stated parent when the name is unknown.
        is_xxx = (df["admin2_code"].fillna("").str.endswith("-XXX")
                  | df.get("admin3_code", pd.Series("", index=df.index)).fillna("")
                    .str.endswith("-XXX"))
        moved = 0
        for i in df.index[is_xxx]:
            nm = df.at[i, "admin2_name"]
            par = sub_parent.get((df.at[i, "location_code"], _fold(nm or "")))
            if par and par != df.at[i, "admin1_code"]:
                df.at[i, "admin1_code"] = par
                moved += 1
        if moved:
            print(f"  PiN: re-attributed {moved} placeholder-coded sub-row(s) to their "
                  f"name-matched admin-1 (upstream parent was wrong)")
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

    # Then drop individual cycles whose rows are misaligned against their p-codes.
    # AFTER the staleness cut, deliberately: a country with a corrupt current cycle
    # still HAS a current plan, so it stays in the tab on its previous cycle rather
    # than vanishing — the same fallback a unit missing from this cycle already gets.
    scrambled = _scrambled_cycles(engine)
    if scrambled:
        keep = [(l, p) not in scrambled for l, p in
                zip(df["location_code"], df["reference_period_start"])]
        df = df[keep]

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
    for (loc, sector), g in df.groupby(["location_code", "sector_code"]):
        # EVERY cycle is kept, each in its own pinY<year> / tgtY<year> column, and
        # they are never mixed: the site puts a plan-year selector in front of them
        # so a unit shows the figures of the cycle you asked for, or nothing. What
        # this must never become again is a per-unit walk newest→oldest, which
        # dressed units the current plan never covered in an older cycle's numbers.
        # (Non-intersectoral sectors keep newest-only — nothing reads them per year.)
        refs = sorted(g["reference_period_start"].unique(), reverse=True)
        inter = sector == "Intersectoral"
        for ref in (refs if inter else refs[:1]):
            gr = g[g["reference_period_start"] == ref]
            yr = int(pd.Timestamp(ref).year)
            lvl = min(gr["admin_level"].unique())  # prefer the coarsest at/below LEVEL
            gr = gr[gr["admin_level"] == lvl]
            a1names = gr.drop_duplicates(ACODE).set_index(ACODE)[ANAME]
            agg = gr.groupby([ACODE, "population_status"])["population"].sum().unstack()
            k_inn, k_tgt = ((f"pinY{yr}", f"tgtY{yr}") if inter
                            else (f"pin__{sector}", f"tgt__{sector}"))
            for pcode, r in agg.iterrows():
                row = rows.setdefault(pcode, {"pcode": pcode, "iso3": loc,
                                              "name": a1names.get(pcode)})
                row[k_inn] = int(r["INN"]) if pd.notna(r.get("INN")) else None
                row[k_tgt] = int(r["TGT"]) if pd.notna(r.get("TGT")) else None
                if ref == refs[0]:
                    row["ref_year"] = max(row.get("ref_year", 0), yr)
                    row["pin_admin_level"] = lvl
                if inter and ref == refs[0]:
                    # The newest needs-table cycle still fills pin/targeted, which
                    # everything downstream (pruning, adm1 rollups) keys on.
                    row["pin"] = row[k_inn]
                    row["targeted"] = row[k_tgt]
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
           admin3_code, admin3_name,
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


def load_ipc_adm1(key: str | None = None) -> pd.DataFrame:
    """IPC/CH acute food insecurity phases per unit, per analysis period.

    From ipc.population_admin (ds-ipc-mirror, HDX HAPI food-security): every
    (period type × validity window) is kept — current vs first/second projection —
    because rounds overlap and the right comparison window is a user choice.
    Admin-2 rows are summed to admin-1 where a country publishes deeper.
    key overrides the grouping column (parent-level lists for downscaling).
    """
    key = key or ACODE
    if key == "admin3_code":  # HAPI food-security has no admin-3 layer
        print("IPC: none at admin-3 (HAPI stops at admin-2)")
        return pd.DataFrame(columns=["pcode", "iso3", "name", "t", "s", "e", "a",
                                     *(f"p{p}" for p in ["1", "2", "3", "4", "5", "all"])])
    engine = stratus.get_engine("dev")
    lvls = "1, 2" if key == "admin1_code" else "2, 2"
    # Recency: analyses valid in 2025 or later. Dead/stalled series (ETH 2021,
    # AGO/SLV 2022, BFA 2024-08, TLS 2024-09) are excluded as too old to act on.
    q = f"""
    SELECT location_code, admin1_code, admin1_name, admin2_code, admin2_name,
           admin_level, ipc_phase, ipc_type,
           population_in_phase, reference_period_start, reference_period_end
    FROM ipc.population_admin
    WHERE admin_level IN ({lvls}) AND ipc_phase IN ('1', '2', '3', '4', '5', 'all')
      AND {key} IS NOT NULL AND reference_period_end >= '2025-01-01'
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
    # HAPI ships some rows TWICE (same resource file, same unit — COD 450 keys,
    # CAF 204, SSD 138…); the pivot below sums, so a survivor doubles that phase.
    # Dedup on the KEY, never on the value: the two copies are the same published
    # figure rounded independently, so they often differ by 1 and an all-column
    # drop_duplicates keeps both. SSD Rumbek North Apr–Jul 2026 is the type case —
    # 77,350 × 0.15 filed once as 11,603 and once as 11,602, summing to 23,205 and
    # putting the whole country 19% over IPC's published total. The 'all' rows of
    # the same pairs round identically, so they DID dedup, which is what made the
    # phase sums land at a clean 2.00× their analysed population.
    keys = [c for c in df.columns if c != "population_in_phase"]
    before = len(df)
    df = df.drop_duplicates(subset=keys)
    if len(df) < before:
        print(f"  IPC: dropped {before - len(df)} duplicate row(s) on "
              f"(unit × phase × type × period) (upstream)")

    rows = []
    for (loc, t, s, e), g in df.groupby(
        ["location_code", "ipc_type", "reference_period_start", "reference_period_end"]
    ):
        lvl = min(g["admin_level"].unique())  # coarsest available
        g = g[g["admin_level"] == lvl]
        piv = g.pivot_table(index=key, columns="ipc_phase",
                            values="population_in_phase", aggfunc="sum")
        a1names = g.drop_duplicates(key).set_index(key)[key.replace("code", "name")]
        adate = g["analysis_date"].max()
        for pcode in piv.index:
            row = {"pcode": pcode, "iso3": loc, "name": a1names.get(pcode),
                   "t": t, "s": s, "e": e, "a": adate}
            for ph in ["1", "2", "3", "4", "5", "all"]:
                v = piv.loc[pcode, ph] if ph in piv.columns else None
                row[f"p{ph}"] = int(v) if pd.notna(v) else 0
            rows.append(row)
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["pcode", "iso3", "name", "t", "s", "e", "a",
                                     *(f"p{p}" for p in ["1", "2", "3", "4", "5", "all"])])
    print(f"IPC ({key}): {out['iso3'].nunique()} countries, {out['pcode'].nunique()} units, "
          f"{out.groupby(['iso3', 't', 's']).ngroups} analysis periods")
    return out


def load_pbs_adm1() -> pd.DataFrame:
    """Intersectoral PiN BY SEVERITY CLASS per unit (hpc.pin_admin — the JIAF 2.0
    PiN-by-Severity workstream, mirrored from country workbooks), latest year per
    country. This is people-level: final_pin per (unit × group × severity class) —
    the per-class headcounts the area classification cannot give. Some plans
    publish PiN without classes (GTM/SLV/VEN: severity NULL) — total only.

    Group logic mirrors load_severity_adm1: a blank group covering ≥50% of units
    is the overall figure (BFA) — use it alone; otherwise sum the named disjoint
    groups (spelling variants like CAF's 'IDP_FA'/'IDP FA' cover disjoint units —
    verified — so a plain sum is safe).

    Where a plan's PbS classes are unusable (SSD: constant class 4 on every row;
    GTM/SLV/VEN: no classes), the unit's PiN is placed at the unit's AREA
    classification from hpc.severity_admin — the same one-class-per-unit
    semantic every other country's PbS encodes — flagged pbs_area for the site.
    """
    engine = stratus.get_engine("dev")
    q = f"""
    SELECT iso3, year, admin1_code, admin2_code, admin3_code,
           COALESCE(admin3_name, admin2_name, admin1_name) AS name, population_group,
           severity, final_pin
    FROM hpc.pin_admin WHERE {ACODE} IS NOT NULL AND final_pin IS NOT NULL
    """
    qa = f"""
    SELECT iso3, year, {ACODE} AS pcode, final_severity, population
    FROM hpc.severity_admin
    WHERE {ACODE} IS NOT NULL AND final_severity IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
        sev = pd.read_sql(qa, conn)
    # Per-unit dominant area class (population-weighted; row count where the
    # sheet publishes no populations — BFA/SYR adm3).
    # Area classifications, per YEAR — the fallback has to move with the cycle too.
    sev["_wt"] = sev["population"].fillna(1)
    cls_w = (sev.groupby(["iso3", "year", "pcode", "final_severity"])["_wt"].sum()
             .reset_index())
    cls_w = cls_w.sort_values("_wt", ascending=False).drop_duplicates(
        ["iso3", "year", "pcode"])
    area_cls = {(r["iso3"], int(r["year"]), r["pcode"]): int(r["final_severity"])
                for _, r in cls_w.iterrows()}
    # EVERY cycle is kept, not just the newest. The severity class has to follow the
    # plan-year selector: 851 of 2,961 units that carry both 2025 and 2026 (29%) are
    # classed differently between them, so pinning the colour to the newest cycle
    # painted 2025 caseloads with 2026 classes — the vintage blend we removed from
    # IPC mode. Each cycle travels as its own pbY<year>_<class> columns and is folded
    # into the payload's per-year "sevc" dict by _row().
    df["population_group"] = df["population_group"].fillna("")

    rows = []
    for (iso3, year), g in df.groupby(["iso3", "year"]):
        units = g.groupby("population_group")[ACODE].nunique()
        n_units = g[ACODE].nunique()
        if units.get("", 0) >= 0.5 * n_units:
            # Blank group is the overall figure (BFA) — named groups are subsets.
            g = g[g["population_group"] == ""]
        elif (units.index != "").any():
            # Named disjoint groups partition the caseload — sum them; a stray
            # sub-dominant blank row (MLI: 1 unit) would double-count, drop it.
            g = g[g["population_group"] != ""]
        classed = g[g["severity"].notna()]
        # Degenerate-distribution guard: SSD 2026 fills a constant severity 4 on
        # every PiN row while its severity sheet shows a real 3/4/5 spread (KB:
        # pipelines/hnrp-mirror). One distinct class across a whole country's
        # units is a template artifact, not analysis — degrade to total-only.
        if classed["severity"].nunique() == 1 and classed[ACODE].nunique() >= 10:
            print(f"  pbs: {iso3} {year} fills one constant class "
                  f"({int(classed['severity'].iloc[0])}) on every unit — "
                  f"degenerate, kept as total-only")
            classed = classed.iloc[0:0]
        by_class = classed.pivot_table(
            index=ACODE, columns="severity", values="final_pin", aggfunc="sum")
        total = g.groupby(ACODE)["final_pin"].sum()
        a1names = g.drop_duplicates(ACODE).set_index(ACODE)["name"]
        yr = int(year)
        n_area = 0
        for pcode, tot in total.items():
            row = {"pcode": pcode, "iso3": iso3, "name": a1names.get(pcode),
                   f"pbsTotY{yr}": int(tot), f"pbsAreaY{yr}": 0}
            split = [0] * 5
            if pcode in by_class.index:
                for cls in range(1, 6):
                    v = (by_class.loc[pcode, cls]
                         if cls in by_class.columns else None)
                    split[cls - 1] = int(v) if pd.notna(v) else 0
            # THE CLASS IS NOT THE SPLIT. A unit can be assessed and hold no PiN —
            # Colombia classifies all 1,122 units for 2026 while 672 of them come to
            # zero people in need. Reading the class off the argmax of the PiN split
            # left those 672 unclassified and grey, when the plan had in fact
            # assessed every one of them. So: take the class from the split where
            # there IS a caseload to place, and otherwise from the same year's AREA
            # classification, which covers all 672.
            cls_from_split = (max(range(1, 6), key=lambda c: split[c - 1])
                              if any(v > 0 for v in split) else None)
            unit_cls = cls_from_split
            if unit_cls is None:
                acls = area_cls.get((iso3, yr, pcode))
                if acls is not None:
                    unit_cls = acls
                    row[f"pbsAreaY{yr}"] = 1
                    n_area += 1
                    # Nothing to distribute, but keep the class visible: a zero
                    # caseload at class N is still class N on the map.
                    if tot:
                        split[acls - 1] = int(tot)
            for cls in range(1, 6):
                row[f"pbY{yr}_{cls}"] = split[cls - 1]
            row[f"pbsClsY{yr}"] = unit_cls
            row[f"pbsClassedY{yr}"] = int(unit_cls is not None)
            rows.append(row)
        if n_area:
            print(f"  pbs: {iso3} {yr}: {n_area} unit(s) classed from the AREA "
                  f"classification (no class carried by the PiN split)")
    # One row per unit again: the per-year columns are disjoint, so a groupby-first
    # over them recombines each unit's cycles without any of them overwriting another.
    out = pd.DataFrame(rows)
    if not out.empty:
        out = (out.groupby(["pcode", "iso3"], as_index=False)
                  .agg({c: "first" for c in out.columns if c not in ("pcode", "iso3")}))
    yrs = sorted({c.split("_")[0][3:] for c in out.columns if c.startswith("pbY")})
    print(f"PiN-by-severity: {out['iso3'].nunique()} countries, {len(out)} units, "
          f"cycles {', '.join(yrs)}")
    return out


def load_population_adm1() -> pd.DataFrame:
    """Total population per ADM1 unit from pop.population_admin
    (ds-population-mirror: HDX HAPI baseline population, UNFPA COD-PS derived).

    Latest reference period per unit, totals only. P-codes arrive raw from the
    mirror; normalize_pcodes reconciles them to our COD vintage (including
    name-matching units HAPI ships with *-XXX placeholder codes).
    """
    if LEVEL == 3:
        # COD-PS via HAPI has no admin-3 layer — WorldPop 1km UN-adjusted totals,
        # zonally summed over the same adm3 boundaries, stand in
        # (pipeline/backfill_adm3_population.py; spatial join, no pcodes involved).
        try:
            df = stratus.load_parquet_from_blob(
                f"{PROJECT_PREFIX}/processed/pop_adm3_worldpop.parquet", stage="dev")
            df = df[df["population"].notna()]
            print(f"Population baseline (WorldPop adm3): {df['iso3'].nunique()} "
                  f"countries, {len(df)} units")
            return df
        except Exception as e:  # noqa: BLE001
            print(f"Population baseline: WorldPop adm3 parquet unavailable "
                  f"({type(e).__name__}) — run backfill_adm3_population.py")
            return pd.DataFrame(columns=["iso3", "pcode", "name", "population", "pop_year"])
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


def load_hno_pop_adm1(sub_parent: dict | None = None) -> pd.DataFrame:
    """Total population per ADM1 unit from the HNO/JIAF baseline (hpc.needs_admin,
    population_status='all'): each plan's own population base, self-consistent with
    the PiN/targeted figures on the axes. Second fallback layer for the scatter
    denominator — covers the countries the COD-PS baseline misses (YEM has no
    COD-PS in HAPI at all) or where its census vintage is distrusted (MLI, SDN…).
    """
    engine = stratus.get_engine("dev")
    q = f"""
    SELECT location_code AS iso3, admin1_code, {ANAME} AS name, admin2_code,
           admin2_name, admin3_code, admin_level, population, reference_period_start
    FROM hpc.needs_admin
    WHERE population_status = 'all' AND lower(COALESCE(category, '')) IN ('total', '')
      AND admin_level >= {LEVEL} AND {ACODE} IS NOT NULL
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start"])
    if sub_parent:
        # Same upstream hazard as the PiN loader: *-XXX sub-rows with a wrong
        # stated admin-1 — re-attribute by unique COD sub-unit name.
        is_xxx = df["admin2_code"].fillna("").str.endswith("-XXX")
        moved = 0
        for i in df.index[is_xxx]:
            par = sub_parent.get((df.at[i, "iso3"], _fold(df.at[i, "admin2_name"] or "")))
            if par and par != df.at[i, "admin1_code"]:
                df.at[i, "admin1_code"] = par
                moved += 1
        if moved:
            print(f"  hno-pop: re-attributed {moved} placeholder-coded sub-row(s) by name")
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
    if not rows:
        print("HNO baseline: none at this level")
        return pd.DataFrame(columns=["pcode", "iso3", "name", "hno_pop", "hno_year"])
    out = pd.DataFrame(rows)
    print(f"HNO baseline: {out['iso3'].nunique()} countries, {len(out)} units")
    return out


def _ipc_lists(df_ipc: pd.DataFrame) -> dict[str, list]:
    """Per pcode: the analysis periods, newest validity first — capped at 6
    (payload guard; the period logic only ever reaches recent ones plus the
    most-recent-past fallback)."""
    if df_ipc.empty:
        return {}
    df_ipc = (df_ipc.groupby(["pcode", "t", "s", "e"], as_index=False)
              .agg({**{f"p{ph}": "sum" for ph in ["1", "2", "3", "4", "5", "all"]},
                    "a": "max"}))
    out: dict[str, list] = {}
    for pcode, g in df_ipc.groupby("pcode"):
        g = g.sort_values("e", ascending=False).head(6)
        out[pcode] = [
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
    return out


def build_lowest() -> None:
    """Combine the per-level exports into the 'lowest available level' view.

    Per country: admin-3 where we have it (BFA/MMR/SYR), else admin-2 (the
    21-country adm2 scope), else admin-1. Reads the three payloads/geojsons
    already written by --level 1/2/3 runs and writes hnrp_drought_low.json +
    hnrp_low.geojson — rows carry "lvl" so the site can say which level a unit
    came from.
    """
    base = HERE.parent / "docs" / "data"
    data, geo = {}, {}
    for lvl, suffix in [(1, ""), (2, "_adm2"), (3, "_adm3")]:
        dp = base / f"hnrp_drought{suffix}.json"
        gp = base / (f"hnrp_adm{lvl}.geojson" if lvl > 1 else "hnrp_adm1.geojson")
        if not dp.exists() or not gp.exists():
            sys.exit(f"Missing level-{lvl} outputs ({dp.name} / {gp.name}) — "
                     f"run --level {lvl} first")
        data[lvl] = json.loads(dp.read_text())
        geo[lvl] = json.loads(gp.read_text())

    level_of: dict[str, int] = {}
    for lvl in (1, 2, 3):
        for r in data[lvl]["rows"]:
            level_of[r["iso3"]] = max(level_of.get(r["iso3"], 1), lvl)
    rows = [dict(r, lvl=lvl) for lvl in (1, 2, 3) for r in data[lvl]["rows"]
            if level_of.get(r["iso3"]) == lvl]
    feats = [f for lvl in (1, 2, 3) for f in geo[lvl]["features"]
             if level_of.get(f["properties"].get("iso3")) == lvl]

    payload = {**data[1], "adm_level": "low", "rows": rows}
    out = base / "hnrp_drought_low.json"
    out.write_text(json.dumps(payload, separators=(",", ":")))
    gout = base / "hnrp_low.geojson"
    gout.write_text(json.dumps({"type": "FeatureCollection", "features": feats},
                               separators=(",", ":")))
    from collections import Counter
    mix = Counter(level_of.values())
    print(f"Lowest-level view: {len(rows)} units, {len(feats)} polygons "
          f"({mix.get(3, 0)} countries at adm3, {mix.get(2, 0)} at adm2, "
          f"{mix.get(1, 0)} at adm1)")
    print(f"Wrote {out} ({out.stat().st_size / 1024:.0f} KB) and {gout.name} "
          f"({gout.stat().st_size / 1e6:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild-geometry", action="store_true",
                    help="Rewrite the geojson even if it already exists (it is "
                         "stable between runs; the default skips it when present).")
    ap.add_argument("--level", choices=("1", "2", "3", "low"), default="1",
                    help="Admin level of the export (2/3 write *_admN outputs; "
                         "'low' combines the three per-country finest levels)")
    args = ap.parse_args()
    if args.level == "low":
        build_lowest()
        return
    _set_level(int(args.level))

    print(f"Loading ADM{LEVEL} skill stats: {SKILL_BLOB}")
    skill = stratus.load_parquet_from_blob(SKILL_BLOB, stage="dev")

    engine = stratus.get_engine("prod")
    parent2: dict[str, str] = {}  # adm3 pcode -> parent adm2 pcode (LEVEL 3 only)
    with engine.connect() as conn:
        if LEVEL == 3:
            # public.polygon stops at adm2 — the adm3 reference (pcodes, names,
            # parent adm2) comes straight from the COD shapefiles.
            a2names = pd.read_sql(
                "SELECT pcode, name FROM public.polygon WHERE adm_level=2",
                conn).set_index("pcode")["name"].to_dict()
            parts, parent_names = [], {}
            for iso3 in ADM3_ISO3S:
                g, pcol, ncol, parcol = load_adm3_shp(iso3)
                parts.append(pd.DataFrame({
                    "pcode": g[pcol], "iso3": iso3, "name": g[ncol]}))
                parent2.update(dict(zip(g[pcol], g[parcol])))
                if ncol.upper().startswith("ADM3"):
                    n2col = ncol.replace("3", "2")
                    parent_names.update(dict(zip(g[pcol], g[n2col])))
                else:  # COD: parent names from the adm2 polygon table
                    parent_names.update({z: a2names.get(par)
                                         for z, par in zip(g[pcol], g[parcol])
                                         if a2names.get(par)})
            poly = pd.concat(parts, ignore_index=True)
            print(f"ADM3 reference from shapefiles: {len(poly)} units "
                  f"({', '.join(ADM3_ISO3S)})")
        else:
            poly = pd.read_sql(
                f"SELECT pcode, iso3, name FROM public.polygon WHERE adm_level={LEVEL}",
                conn)
        country_names = pd.read_sql(
            "SELECT iso3, name FROM public.polygon WHERE adm_level=0", conn,
        ).set_index("iso3")["name"].to_dict()
        # Parent admin-1 names for level-2 rows (tooltips/table): no parent column
        # exists, but COD adm2 pcodes extend their adm1 parent's pcode.
        if LEVEL != 3:
            parent_names = {}
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

    # (iso3, folded adm2 name) -> parent admin1 pcode, names unique within country:
    # lets loaders re-attribute placeholder-coded sub-rows whose stated admin-1 is
    # wrong upstream (see MOZ 2024). Level-1 rollups only.
    sub_parent: dict = {}
    if LEVEL == 1:
        with engine.connect() as conn:
            p2 = pd.read_sql(
                "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=2", conn)
        a1_by_iso = {i: sorted(g_["pcode"], key=len, reverse=True)
                     for i, g_ in poly.groupby("iso3")}
        cnt: dict = {}
        for _, r in p2.iterrows():
            k = (r["iso3"], _fold(r["name"]))
            par = next((c for c in a1_by_iso.get(r["iso3"], [])
                        if str(r["pcode"]).startswith(c)), None)
            if par:
                cnt[k] = None if k in cnt else par  # None marks ambiguous names
        sub_parent = {k: v for k, v in cnt.items() if v}

    print("Reconciling humanitarian pcodes to the COD vintage...")
    df_pin_raw, sector_names = load_pin_adm1(sub_parent)
    # Caseloads opt into placeholder name-matching: CAR files 16 units (777k PiN)
    # under CF31-XXX / CAF-XXX-XXX with perfectly good names, and dropping them
    # left Paoua, Batangafo, Bouca, Kabo carrying a severity class and no PiN.
    # The collision guard is what makes it safe — nothing lands on a held unit.
    df_pin = normalize_pcodes(df_pin_raw, poly, "PiN", xxx_name_match=True)
    df_sev = normalize_pcodes(load_severity_adm1(), poly, "severity")
    df_pbs = normalize_pcodes(load_pbs_adm1(), poly, "pbs")
    # Normalization can merge units (reforms, renumberings) — recombine to one row
    # per pcode. Headcounts sum; the per-cycle flags take the max. Column names are
    # discovered because they are per-year (pbY2026_3, pbsTotY2025, …).
    _pbs_num = [c for c in df_pbs.columns
                if c.startswith(("pbY", "pbsTotY"))]
    _pbs_flag = [c for c in df_pbs.columns
                 if c.startswith(("pbsClassedY", "pbsAreaY", "pbsClsY"))]
    # min_count=1, NOT a plain sum: a unit with no row for a cycle has NaN in that
    # cycle's columns, and pandas sums all-NaN to 0.0 — which invents a zero-caseload
    # cycle. That put a phantom 2026 on Venezuela, Burkina Faso, Ukraine and Honduras,
    # none of which publish a 2026 PbS at all. A genuine published zero (Colombia
    # files 672 for 2026) still comes through as 0.
    _sum1 = lambda s: s.sum(min_count=1)  # noqa: E731
    df_pbs = (df_pbs.groupby("pcode", as_index=False)
              .agg({"iso3": "first", "name": "first",
                    **{c: _sum1 for c in _pbs_num},
                    **{c: "max" for c in _pbs_flag}}))
    df_ipc = normalize_pcodes(load_ipc_adm1(), poly, "IPC")
    df_pop = normalize_pcodes(load_population_adm1(), poly, "population",
                              xxx_name_match=True)
    # Normalization can merge units (reforms, renumberings) — sum to one per pcode.
    df_pop = (df_pop.groupby("pcode", as_index=False)
              .agg({"population": "sum", "pop_year": "max"})
              .rename(columns={"population": "pop"}))
    df_hno = normalize_pcodes(load_hno_pop_adm1(sub_parent), poly, "hno-pop")
    df_hno = (df_hno.groupby("pcode", as_index=False)
              .agg({"hno_pop": "sum", "hno_year": "max"}))
    ipc_iso3 = df_ipc.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    ipc_lists = _ipc_lists(df_ipc)
    # Normalization can merge codes (renumberings) — re-aggregate to one row per pcode.
    _sum = lambda s: s.sum(min_count=1)  # noqa: E731 — all-NaN stays None, not 0
    sec_cols = [c for c in df_pin.columns
                if c.startswith(("pin__", "tgt__", "pinY", "tgtY"))]
    df_pin = (df_pin.groupby(["pcode", "iso3"], as_index=False)
              .agg({"name": "first", "pin": _sum, "targeted": _sum,
                    **{c: _sum for c in sec_cols},
                    "ref_year": "max", "pin_admin_level": "max"}))
    df_sev = (df_sev.groupby(["pcode", "iso3"], as_index=False)
              .agg({"name": "first", "sev4": "sum", "sev_total": "sum", "sev_year": "max",
                    **{f"s{c}": "sum" for c in range(1, 6)}}))
    # Union of PiN and severity units; iso3/name from whichever side has them.
    df_hum = df_pin.merge(df_sev, on="pcode", how="outer", suffixes=("", "_sev"))
    for col in ["iso3", "name"]:
        df_hum[col] = df_hum[col].combine_first(df_hum[f"{col}_sev"])
    df_hum = df_hum.drop(columns=["iso3_sev", "name_sev"])
    df_hum = df_hum.merge(df_pbs, on="pcode", how="outer", suffixes=("", "_pbs"))
    for col in ["iso3", "name"]:
        df_hum[col] = df_hum[col].combine_first(df_hum[f"{col}_pbs"])
    df_hum = (df_hum.drop(columns=["iso3_pbs", "name_pbs"])
              .rename(columns={"name": "name_hum"}))

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
    if LEVEL == 3:
        # Forecast inheritance: each adm3 unit carries its parent adm2's skill
        # rows verbatim (no adm3 zonal stats exist).
        kids = pd.DataFrame({"pcode": list(parent2), "_par": list(parent2.values())})
        skill = (skill.rename(columns={"pcode": "_par"})
                 .merge(kids, on="_par").drop(columns=["_par"]))
        print(f"Skill inherited from parent adm2 for {skill['pcode'].nunique()} adm3 units")
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

    # Rainy-season mask (same ERA5 climatology as other exports). At level 3 the
    # climatology, like the skill, is the parent adm2's — queried on parents and
    # mapped onto children.
    pcodes = (sorted({parent2[p] for p in skill["pcode"].dropna() if p in parent2})
              if LEVEL == 3 else sorted(set(skill["pcode"].dropna())))
    ph = ",".join(["%s"] * len(pcodes))
    print(f"Querying ERA5 climatology for {len(pcodes)} pcodes...")
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
    if LEVEL == 3:
        by_par: dict[str, list] = {}
        for (p, t) in rainy_set:
            by_par.setdefault(p, []).append(t)
        rainy_set = {(c, t) for c, p in parent2.items() for t in by_par.get(p, [])}

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
    merged.loc[merged["pop"].notna(), "pop_src"] = ("WorldPop" if LEVEL == 3
                                                     else "COD-PS")
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
    if use_hno.any():  # empty assignment TypeErrors when the HNO layer is empty (adm3)
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
    if LEVEL >= 2:
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

    # IPC downscaling: HAPI publishes IPC at admin-1/2 — units finer than the
    # published level (AFG districts, COD zones de santé…) have no IPC rows and
    # showed zeros. Fill them from the PARENT analysis, prorated by each unit's
    # population weight within the parent, marked "d": <source level> so every
    # tooltip says the numbers are downscaled shares, not unit-level analysis.
    if LEVEL >= 2:
        with engine.connect() as conn:
            poly1 = pd.read_sql(
                "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=1", conn)
        par1 = normalize_pcodes(load_ipc_adm1(key="admin1_code"), poly1, "IPC-adm1")
        lists1 = _ipc_lists(par1)
        lists2 = {}
        if LEVEL == 3:
            with engine.connect() as conn:
                poly2 = pd.read_sql(
                    "SELECT pcode, iso3, name FROM public.polygon WHERE adm_level=2",
                    conn)
            par2 = normalize_pcodes(load_ipc_adm1(key="admin2_code"), poly2, "IPC-adm2")
            lists2 = _ipc_lists(par2)
        a1_by_iso = {i: sorted(g_["pcode"], key=len, reverse=True)
                     for i, g_ in poly1.groupby("iso3")}

        def _src_of(pcode: str, iso3: str):
            if LEVEL == 3:
                p2 = parent2.get(pcode)
                if p2 and p2 in lists2:
                    return p2, 2, lists2[p2]
                base = p2 or pcode
            else:
                base = pcode
            p1 = next((c for c in a1_by_iso.get(iso3, [])
                       if str(base).startswith(c)), None)
            if p1 and p1 in lists1:
                return p1, 1, lists1[p1]
            return None, None, None

        w = merged["pop"].astype(float)
        for col in ["sev_total", *[c for c in merged.columns
                                   if c.startswith("pbsTotY")]]:
            if col in merged.columns:
                w = w.fillna(merged[col].astype(float))
        merged["_w"] = w
        # Σ weight per source parent (over ALL its units, so own-IPC and
        # downscaled units together still sum to ~the parent's figures).
        src_key, src_lvl, src_lists = {}, {}, {}
        for _, r in merged.iterrows():
            k, lv, ls = _src_of(r["pcode"], r["iso3"])
            if k:
                src_key[r["pcode"]], src_lvl[r["pcode"]], src_lists[r["pcode"]] = k, lv, ls
        wsum: dict[str, float] = {}
        for _, r in merged.iterrows():
            k = src_key.get(r["pcode"])
            if k and pd.notna(r["_w"]):
                wsum[k] = wsum.get(k, 0) + float(r["_w"])
        n_ds = 0
        for _, r in merged.iterrows():
            pcode = r["pcode"]
            if pcode in ipc_lists or pcode not in src_key:
                continue
            if pd.isna(r["_w"]) or not wsum.get(src_key[pcode]):
                continue
            frac = float(r["_w"]) / wsum[src_key[pcode]]
            ipc_lists[pcode] = [
                {**c, "p": [round(v * frac) for v in c["p"]],
                 "tot": round(c["tot"] * frac), "d": src_lvl[pcode]}
                for c in src_lists[pcode]
            ]
            n_ds += 1
        merged = merged.drop(columns=["_w"])
        if n_ds:
            print(f"IPC downscaled to {n_ds} unit(s) from parent analyses "
                  f"(population-share proration, flagged 'd')")

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
        # Caseloads per plan cycle: {"2026": [pin, targeted], …}. The site's plan-year
        # selector reads this; a year a unit has no figures for is simply absent.
        cyc: dict[str, list] = {}
        for c in [c for c in sec_cols if c.startswith(("pinY", "tgtY"))]:
            v = rec.pop(c, None)
            if v is not None and pd.notna(v):
                cyc.setdefault(c[4:], [None, None])[0 if c.startswith("pinY") else 1] = int(v)
        sec = {}
        for c in [c for c in sec_cols if "__" in c]:
            v = rec.pop(c, None)
            if v is not None and pd.notna(v):
                pref, code = c.split("__", 1)
                sec.setdefault(code, [None, None])[0 if pref == "pin" else 1] = int(v)
        # PiN-by-severity, PER CYCLE. Each year travels as pbY<yr>_1..5 plus its
        # total/classed/area flags, and folds into "sevc": {"2026": {...}, …} so the
        # site's plan-year selector moves the CLASS as well as the caseload. 29% of
        # units carrying both cycles are classed differently between them.
        sevc: dict[str, dict] = {}
        for c in [c for c in list(rec) if c.startswith("pbsTotY")]:
            yr = c[len("pbsTotY"):]
            tot = rec.pop(c, None)
            classed = rec.pop(f"pbsClassedY{yr}", None)
            area = rec.pop(f"pbsAreaY{yr}", None)
            unit_cls = rec.pop(f"pbsClsY{yr}", None)
            split = [rec.pop(f"pbY{yr}_{k}", None) for k in range(1, 6)]
            # A zero caseload is a real published figure — the plan covers the unit
            # and finds nobody in need (COL files 672 such units for 2026, COD 286).
            # Only a MISSING total means the cycle does not cover this unit.
            if tot is None or pd.isna(tot):
                continue
            ent: dict = {"tot": int(tot)}
            # "c" is the unit's class and is what the map colours by — it exists
            # even where the caseload is zero. "pb" is only the distribution behind
            # it, for the tooltip, and is omitted when there is nothing to split.
            if unit_cls is not None and pd.notna(unit_cls):
                ent["c"] = int(unit_cls)
                pb = [int(v) if pd.notna(v) else 0 for v in split]
                if sum(pb) > 0:
                    ent["pb"] = pb
                if area and pd.notna(area) and int(area):
                    ent["a"] = 1  # class comes from the AREA classification
            sevc[yr] = ent
        # Stray per-year columns for cycles this unit has no total for.
        for c in [c for c in list(rec)
                  if c.startswith(("pbY", "pbsClassedY", "pbsAreaY", "pbsClsY"))]:
            rec.pop(c, None)
        out = {k: (v if isinstance(v, (list, dict)) else None if pd.isna(v) else v)
               for k, v in rec.items()}
        if sevc:
            out["sevc"] = {y: sevc[y] for y in sorted(sevc)}
            # The JIAF PiN-by-severity workbooks carry the newest cycle's caseload
            # (2026) a year before the needs table publishes it subnationally — same
            # measure, verified: where both cover a unit-year they agree, and the
            # 2026 sums reconcile to the published national PiN (Sudan, to the
            # person). Targets are not part of that product, so those cycles carry
            # a PiN and no targeted, which the site says plainly.
            for y, ent in sevc.items():
                cyc.setdefault(y, [None, None])[0] = ent["tot"]
        if cyc:
            out["cyc"] = {y: v for y, v in sorted(cyc.items()) if v[0] is not None or v[1] is not None}
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
            or row.get("sevc")
        ],
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB)")

    if args.rebuild_geometry or not GEO_OUT.exists():
        print("Building ADM1 geometry from COD shapefiles (polygon container)...")
        export_geometry(sorted(merged["iso3"].dropna().unique()), names)


if __name__ == "__main__":
    main()
