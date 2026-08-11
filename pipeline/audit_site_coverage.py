"""Would every selectable country actually show something? Run after a build.

This exists because of a failure that shipped: Myanmar drew 330 areas on the map
and an EMPTY bar chart, for weeks, because it publishes no JIAF workbook and
HAPI's 2026 rows for it stop at the national total. Every check written at the
time asked "does the payload have data?" and answered yes — 3,471 units across
16 countries. None asked "which of the 50 countries in the selector are NOT in
that 16, and what do they look like on screen?".

That is the absent-data rule turned on the verification itself: a coverage check
that only counts what is present cannot see what is missing. So this audit is
written the other way round — it enumerates every country the user can pick, in
every mode and cycle, and reports the ones that would render nothing.

Most gaps are legitimate: 27 countries are in the payload for their IPC alone and
have no HNRP plan at any level, so a plan-mode chart is empty by definition.
Those are declared below. Anything NOT declared is a finding, and the script
exits non-zero so a build cannot quietly reintroduce one.

    uv run python pipeline/audit_site_coverage.py
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent / "docs" / "data"

# Countries with no HNRP at any level — present for their IPC analysis alone, so
# an empty plan-mode chart is the correct rendering, not a gap. Keyed by ISO3
# because the payload's country names carry articles ("Sudan (the)").
NO_PLAN = {
    "BGD", "BEN", "CPV", "CIV", "DJI", "ECU", "GMB", "GHA", "GIN", "GNB", "KEN",
    "LBN", "LSO", "LBR", "MDG", "MWI", "MRT", "NAM", "PAK", "DOM", "SEN", "SLE",
    "PSE", "TGO", "UGA", "TZA", "ZMB",
}
# Countries with no IPC/CH analysis at all — drawn in IPC mode for their forecast
# alone (see combine_ipc_view), so an empty IPC chart is likewise correct.
NO_IPC = {"BFA", "COL", "SLV", "MMR", "SYR", "UKR", "VEN"}
# Countries with a plan in earlier cycles but nothing subnational in a given one,
# for a reason that has been checked. The cycle is named so this cannot silently
# cover a future year: 2027 data going missing would still be a finding.
NO_CYCLE = {
    ("GTM", "2026"): "no 2026 JIAF workbook on HDX and not among the 20 monitored"
                     " plans — its last subnational cycle is 2025",
    ("HND", "2026"): "as Guatemala — no 2026 workbook, not monitored",
    ("SLV", "2026"): "as Guatemala — no 2026 workbook, not monitored",
}


def rows_for(payload):
    return payload["rows"]


def cycles(rows):
    ys = set()
    for r in rows:
        ys.update((r.get("cyc") or {}).keys())
        if r.get("mon_yr"):
            ys.add(r["mon_yr"])
    return sorted(y for y in ys if int(y) >= 2025)


def mon_live(r, year):
    return r["mon"] if r.get("mon") and r.get("mon_yr") == year else None


def charts_in_plan_mode(r, year):
    """Mirror the tab's own filter, not the payload's shape."""
    m = mon_live(r, year)
    pin = (r.get("cyc") or {}).get(year, [None])[0]
    if pin is None and m:
        pin = m[0]
    return pin is not None or (m is not None and any(v is not None for v in m))


def charts_in_ipc_mode(r):
    return any(any(v > 0 for v in c["p"]) for c in (r.get("ipc") or []))


# Monitored units a country may lose between the mirror and the payload before it
# counts as a finding. What remains is aggregation, not loss: Ukraine plans on 48
# front-line bands that sum onto 24 oblasts plus one occupied-territory row, and
# Somalia's two Mogadishu districts fold onto the single Banadir polygon our
# vintage holds. A row count necessarily falls in both cases without a figure
# going missing.
#
# Mali, Burkina and CAR used to need budgets of 15-70% here. They no longer do:
# units with no polygon are kept as geometry-less rows rather than dropped, so
# they reach the payload and this check counts them. Do not re-add a budget
# without establishing that the units behind it are genuinely gone.
LOSS_BUDGET = {"UKR": 0.55}
DEFAULT_LOSS_BUDGET = 0.05


def audit_monitoring_placement():
    """How much of the monitoring mirror actually reaches the map?

    Separate from the coverage check above because it compares the payload with
    its SOURCE, not with itself. It exists because 418 of 3,917 monitored units —
    DR Congo's entire 262 among them — were being dropped for want of a name to
    reconcile their pcodes by, and nothing in the build said so out loud.
    """
    try:
        import ocha_stratus as stratus
        import pandas as pd
    except ImportError:
        print("\n(monitoring placement check skipped — ocha_stratus not available)")
        return []
    try:
        engine = stratus.get_engine("dev")
        with engine.connect() as conn:
            mirror = pd.read_sql(
                "SELECT iso3, count(*) AS n FROM hpc.monitoring_admin "
                "WHERE cluster_name = 'HNRP' AND snapshot_date = "
                "(SELECT max(snapshot_date) FROM hpc.monitoring_admin) "
                "GROUP BY iso3", conn)
    except Exception as exc:
        print(f"\n(monitoring placement check skipped — {exc.__class__.__name__})")
        return []
    payload = json.loads((BASE / "hnrp_drought_low.json").read_text())
    on_site = {}
    for r in payload["rows"]:
        if r.get("mon") and r.get("mon_yr"):
            on_site.setdefault(r["iso3"], set()).add(r["pcode"])
    out = []
    print("\nmonitoring placement (mirror -> map):")
    for _, row in mirror.sort_values("iso3").iterrows():
        iso3, n = row["iso3"], int(row["n"])
        placed = len(on_site.get(iso3, ()))
        lost = n - placed
        if not lost:
            continue
        budget = LOSS_BUDGET.get(iso3, DEFAULT_LOSS_BUDGET)
        share = lost / n
        flag = "" if share <= budget else "  <-- OVER BUDGET"
        print(f"  {iso3}: {placed}/{n} placed, {lost} lost ({share:.0%}, "
              f"budget {budget:.0%}){flag}")
        if share > budget:
            out.append(f"monitoring: {iso3} loses {lost} of {n} units ({share:.0%}) "
                       f"between the mirror and the map, over its {budget:.0%} budget")
    return out


# The country total a reader sees must equal the plan's published figure exactly
# — that is the number on Humanitarian Action, and "exactly" is the whole point,
# so there is no tolerance on it.
#
# The SUBNATIONAL sum is a separate question and deliberately not an equality.
# It cannot reach the national figure in general, for two unrelated reasons:
#
#   national figure  ->  what the SOURCE attributes to areas  ->  what we PLACE
#
# The first gap is the plan's own reporting — Chad leaves 24% of its PiN and
# DR Congo 34% of its target on no area at all — and no p-code work here touches
# it. The second gap is ours: units the source located that we failed to draw.
# Only the second is a finding, and it is budgeted per country below.
NATIONAL_TOLERANCE = 0.02
# Empty on purpose. Every country now carries 100% of what the source attributes
# to areas, because a unit with no polygon is kept as a GEOMETRY-LESS ROW — in
# the bar chart and in every total, just not drawn — instead of being dropped.
# Before that, Mali needed 50% here, Burkina 25%, CAR 20%.
#
# A country appearing in this dict again means figures are being DELETED
# somewhere, which is exactly what this check exists to catch. Establish where
# before granting a budget.
PLACEMENT_BUDGET = {}
DEFAULT_PLACEMENT_BUDGET = 0.02
# How the awkward units are handled, for the record:
#
#   HTI HT01xx "ZMPP" (147k PiN) is the Zone Metropolitaine de Port-au-Prince, a
#   planning area OVERLAPPING ten communes the plan already reports separately
#   (Port-au-Prince, Delmas, Carrefour, Cite Soleil...). Rolling it onto any one
#   of them, or onto Ouest, would draw the same people twice — so it is counted
#   as a geometry-less row and never placed.
#
#   MLI / BFA / CAF reform units (Mali's post-2023 cercles, Burkina's post-2024
#   provinces, CAR's post-2020 sub-prefectures) have no boundary in ANY published
#   COD — checked against fieldmaps' current originals, which are the same
#   vintage we hold. Also geometry-less rows.
#
#   Six Malian cercles (San, Tominian, Douentza, Bandiagara, Koro, Bankass)
#   name-match onto a unit another row already holds. They keep their own code
#   and name as geometry-less rows rather than being summed onto a neighbour.
#
#   SOM SO2203 Daynile and SO2210 Kahda ARE placed, via REFORM_XWALK: they are
#   Mogadishu districts, our vintage holds Banadir whole as SO2201, and the
#   source publishes no other Banadir row — an exact roll-up with no double
#   count, worth a quarter of Somalia's target.


def audit_national_totals():
    """Two separate questions, deliberately not conflated.

    1. Does the country total the SITE shows equal the plan's published figure?
       This must hold exactly, for every country and every measure. It is what
       a reader checks against Humanitarian Action, and it is why the payload
       carries `mon_national` instead of summing the units.

    2. How much of that published figure do the mapped areas account for? This
       is expected to fall short and is budgeted, not asserted — see above.
    """
    try:
        import ocha_stratus as stratus
        import pandas as pd
    except ImportError:
        return []
    try:
        with stratus.get_engine("dev").connect() as conn:
            national = pd.read_sql(
                "SELECT m.iso3, m.year, m.in_need, m.targeted, m.prioritized_target, "
                "m.reached, m.prioritized_reached FROM hpc.monitoring_national m "
                "JOIN (SELECT plan_id, max(snapshot_date) AS d "
                "      FROM hpc.monitoring_national GROUP BY plan_id) l "
                "  ON l.plan_id = m.plan_id AND l.d = m.snapshot_date "
                "WHERE m.cluster_name = 'HNRP' AND m.iso3 IS NOT NULL", conn)
            attributed = pd.read_sql(
                "SELECT iso3, year, sum(in_need) AS in_need, "
                "sum(targeted) AS targeted FROM hpc.monitoring_admin "
                "WHERE cluster_name = 'HNRP' AND snapshot_date = "
                "(SELECT max(snapshot_date) FROM hpc.monitoring_admin) "
                "GROUP BY iso3, year", conn)
    except Exception as exc:
        print(f"\n(national reconciliation skipped — {exc.__class__.__name__})")
        return []
    payload = json.loads((BASE / "hnrp_drought_low.json").read_text())
    shown = payload.get("mon_national") or {}
    placed = {}
    for r in payload["rows"]:
        m = r.get("mon")
        if not m or not r.get("mon_yr"):
            continue
        a = placed.setdefault((r["iso3"], r["mon_yr"]), [0, 0, 0, 0, 0])
        for i in range(5):
            if m[i] is not None:
                a[i] += m[i]
    cols = ["in_need", "targeted", "prioritized_target", "reached",
            "prioritized_reached"]
    labels = ["PiN", "target", "prioritized", "reached", "prio reached"]
    out = []

    # (1) exact equality between the published figure and what the site carries
    print("\nnational totals — does the payload carry the PUBLISHED figure?")
    for _, row in national.sort_values("iso3").iterrows():
        iso3, year = row["iso3"], str(int(row["year"]))
        got = shown.get(iso3)
        if not got:
            out.append(f"national: {iso3} has a published {year} caseload but the "
                       f"payload carries no mon_national entry — its country total "
                       f"would fall back to the sum of its areas, an undercount")
            continue
        if got.get("year") != year:
            out.append(f"national: {iso3} mon_national is for {got.get('year')}, "
                       f"the published caseload is {year}")
            continue
        want = [None if pd.isna(row[c]) else int(row[c]) for c in cols]
        if got["mon"] != want:
            bad = [f"{labels[i]} {got['mon'][i]:,} != {want[i]:,}"
                   for i in range(5) if got["mon"][i] != want[i]]
            out.append(f"national: {iso3} payload disagrees with the published "
                       f"caseload — {'; '.join(bad)}")
    if not out:
        print(f"  all {len(national)} countries carry their published caseload exactly")

    # (2) published -> attributed by the source -> placed by us. Only the second
    # arrow is ours, and only it can be a finding.
    attr = {(r["iso3"], str(int(r["year"]))): (r["in_need"] or 0, r["targeted"] or 0)
            for _, r in attributed.iterrows()}
    print("\nsubnational placement — published -> attributed by source -> placed here:")
    for _, row in national.sort_values("iso3").iterrows():
        iso3, year = row["iso3"], str(int(row["year"]))
        got, src = placed.get((iso3, year)), attr.get((iso3, year))
        if got is None or src is None:
            continue
        budget = PLACEMENT_BUDGET.get(iso3, DEFAULT_PLACEMENT_BUDGET)
        for i in (0, 1):   # PiN and target: the two allocated planning figures
            nat = row[cols[i]]
            if not nat or pd.isna(nat):
                continue
            unattributed = 1 - (src[i] / nat)
            ours = 1 - (got[i] / src[i]) if src[i] else 0.0
            if unattributed <= NATIONAL_TOLERANCE and ours <= NATIONAL_TOLERANCE:
                continue
            flag = "  <-- OVER BUDGET" if ours > budget else ""
            print(f"  {iso3} {labels[i]}: {nat:,.0f} -> {src[i]:,.0f} "
                  f"({unattributed:.0%} unattributed at source) -> {got[i]:,.0f} "
                  f"({ours:.0%} lost here, budget {budget:.0%}){flag}")
            if ours > budget:
                out.append(f"placement: {iso3} {labels[i]} — the source locates "
                           f"{src[i]:,.0f} but only {got[i]:,.0f} reaches the map, "
                           f"{ours:.0%} lost against a {budget:.0%} budget")
    return out


def main():
    findings = []
    for view, path in (("plan", "hnrp_drought_low.json"), ("ipc", "hnrp_drought_ipc.json")):
        payload = json.loads((BASE / path).read_text())
        rows = rows_for(payload)
        by_country = {}
        for r in rows:
            by_country.setdefault((r["iso3"], r.get("country")), []).append(r)
        yrs = cycles(rows)
        print(f"{view} view: {len(by_country)} countries, cycles {', '.join(yrs)}")
        for (iso3, name), rs in sorted(by_country.items()):
            if view == "ipc":
                if not any(charts_in_ipc_mode(r) for r in rs) and iso3 not in NO_IPC:
                    findings.append(f"{view}: {name} ({iso3}) has no IPC classification "
                                    f"in {len(rs)} units and is not in NO_IPC")
                continue
            if iso3 in NO_PLAN:
                continue
            for y in yrs:
                if any(charts_in_plan_mode(r, y) for r in rs):
                    continue
                why = NO_CYCLE.get((iso3, y))
                if why:
                    print(f"  expected: {name} ({iso3}) {y} — {why}")
                    continue
                findings.append(
                    f"{view}: {name} ({iso3}) charts NOTHING for the {y} cycle "
                    f"— {len(rs)} units on the map")
    findings += audit_monitoring_placement()
    findings += audit_national_totals()
    if findings:
        print(f"\n{len(findings)} problem(s) found:")
        for f in findings:
            print(f"  {f}")
        print("\nEither the payload lost data, or the case belongs in one of the "
              "declared allowlists WITH A REASON. Do not silence this without "
              "establishing which.")
        return 1
    print("\nEvery selectable country renders something in every cycle, or is "
          "declared as having no plan / no IPC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
