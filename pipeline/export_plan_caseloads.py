"""Country-level plan headline caseloads from the HPC API.

Writes docs/data/plan_caseloads.json: {iso3: {plan_year, plan_name, pin, target,
pop}} — the LATEST released single-country HNRP/HRP per country whose headline
caseload carries a People-in-Need figure.

Why this exists: the admin-level mirror (hpc.needs_admin) lags the plan cycle —
as of Aug 2026 it holds the 2025 caseloads while the 2026 plans are published.
The plan-level headline is available immediately from the public HPC API, so the
country-level alerts table reads THIS file for PiN and % of population, while the
per-admin views keep the mirror's (older) admin PiN until the mirror refreshes
(tracked in OCHA-DAP/ds-hnrp-mirror#1).

Selection rules, validated against humanitarianaction.info's GHO dashboard
(plan-level figures reconcile exactly — see the #alerts tab work, Aug 2026):
- single-country plans only (one distinct adminLevel-0 location);
- plan category "Humanitarian needs and response plan" or "Humanitarian response
  plan" — flash appeals (e.g. Mozambique (Drought) 2025) and regional RRP/RMRP/
  3RP/JRP/MRP plans are NOT the country's headline HNRP;
- headline caseload = the BP1 attachment (else the largest inNeed);
- per country, the newest plan year wins, but only if its inNeed is non-null —
  a released plan with no published caseload must not erase last year's figure.
"""

import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

API = "https://api.hpc.tools"
PLAN_TYPES = {"Humanitarian needs and response plan", "Humanitarian response plan"}
OUT = Path(__file__).parent.parent / "docs" / "data" / "plan_caseloads.json"


def get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def headline_caseload(plan_id):
    d = get(f"{API}/v2/public/attachment?objectIds={plan_id}&objectType=plan&type=caseload")
    cls = []
    for a in d.get("data", []):
        try:
            totals = a["attachmentVersion"]["value"]["metrics"]["values"]["totals"]
        except (KeyError, TypeError):
            continue  # disaggregation-only attachment, no plan totals
        tot = {t["type"]: t["value"] for t in totals}
        cls.append((a.get("composedReference", ""), tot))
    if not cls:
        return None
    for ref, tot in cls:
        if ref == "BP1":
            return tot
    return max((t for _, t in cls), key=lambda t: t.get("inNeed") or 0)


def main():
    this_year = date.today().year
    out = {}
    for year in (this_year - 1, this_year, this_year + 1):  # next year's plans appear ~Nov
        try:
            plans = get(f"{API}/v1/public/plan/year/{year}")["data"]
        except Exception as e:  # noqa: BLE001 — a missing year must not kill the run
            print(f"  {year}: plan list unavailable ({e})", file=sys.stderr)
            continue
        for p in plans:
            if not p.get("isReleased"):
                continue
            cats = {c["name"] for c in p.get("categories", [])}
            if not cats & PLAN_TYPES:
                continue
            iso3s = {l["iso3"] for l in p.get("locations", [])
                     if l.get("adminLevel") == 0 and l.get("iso3")}
            if len(iso3s) != 1:
                continue
            (iso3,) = iso3s
            name = p["planVersion"]["shortName"] or p["planVersion"]["name"]
            try:
                tot = headline_caseload(p["id"])
            except Exception as e:  # noqa: BLE001
                print(f"  {iso3} {year} ({name}): caseload fetch failed ({e})", file=sys.stderr)
                continue
            time.sleep(0.2)
            if not tot or tot.get("inNeed") is None:
                continue  # never let an empty newer plan erase an older figure
            prev = out.get(iso3)
            # Same-year siblings (Myanmar 2025: (Original) + (Earthquake)): the
            # country headline is the LARGER caseload, not the last one listed.
            if prev and prev["plan_year"] == year and prev["pin"] >= tot["inNeed"]:
                continue
            out[iso3] = {
                "plan_year": year,
                "plan_name": name,
                "pin": tot["inNeed"],
                "target": tot.get("target"),
                "pop": tot.get("totalPopulation"),
            }
            print(f"  {iso3} {year}: PiN {tot['inNeed']:,.0f} ({name})")

    OUT.write_text(json.dumps({"generated": str(date.today()), "plans": out},
                              indent=1, ensure_ascii=False) + "\n")
    print(f"{len(out)} countries -> {OUT}")


if __name__ == "__main__":
    main()
