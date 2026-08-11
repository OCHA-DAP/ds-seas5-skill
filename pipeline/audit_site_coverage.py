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
    ("UKR", "2026"): "monitored, but on planning units (UA12_U20A, UA05_UALL —"
                     " oblast x settlement size) that have no COD admin polygon,"
                     " so none of its 48 rows can be placed on the map",
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
    if findings:
        print(f"\n{len(findings)} country/cycle combination(s) would render an empty "
              f"chart without being declared expected:")
        for f in findings:
            print(f"  {f}")
        print("\nEither the payload lost data, or the country belongs in NO_PLAN /"
              " NO_IPC with a reason. Do not silence this without checking which.")
        return 1
    print("\nEvery selectable country renders something in every cycle, or is "
          "declared as having no plan / no IPC.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
