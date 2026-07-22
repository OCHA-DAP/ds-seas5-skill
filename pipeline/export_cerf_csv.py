"""Export the CERF-tab (cerf.html / cbpf.js) map data as a flat CSV.

One row per country per valid trimester for a single issuance (default: latest).
Reproduces the site's classification (docs/cbpf.js `classify`) and the four
country-set memberships hardcoded there, so the CSV matches what the map shows.

Usage:
    uv run pipeline/export_cerf_csv.py [--issued 2026-07] [-o outputs/cerf_tab.csv]
"""

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "data"

# Country sets, kept in sync with docs/cbpf.js.
US_AWARD = {
    "BGD", "MMR", "TCD", "COL", "COD", "SLV", "GTM", "HTI", "HND", "KEN",
    "MOZ", "NGA", "SSD", "SDN", "SYR", "UGA", "UKR", "VEN", "LBN", "CAF",
}
CBPF_ALL = {
    "AFG", "BFA", "BGD", "CAF", "COD", "COL", "ETH", "FJI", "GTM", "HND",
    "HTI", "KEN", "LBN", "MLI", "MMR", "MOZ", "NGA", "PAK", "PSE", "SLB",
    "SLV", "SDN", "SOM", "SSD", "SYR", "TCD", "UGA", "UKR", "VEN",
}
CERF_FW = {"BFA", "TCD", "SLV", "ETH", "FJI", "GTM", "HND", "MRT", "NER", "PHL", "SOM", "VUT"}
CERF_NF = {"AGO", "ETH", "LSO", "MDG", "MWI", "MNG", "MOZ", "PER", "SOM", "TLS", "ZWE"}


def classify(rec, thresholds):
    """Port of docs/cbpf.js classify(): (direction, severity, skill, category_label)."""
    t = thresholds
    if rec is None or rec.get("r") is None or rec.get("pct") is None:
        return "", "", "", "Not monitored"
    r, pct = rec["r"], rec["pct"]
    skill = "high" if r >= t["r_high"] else "moderate" if r >= t["r_mod"] else "low"
    if skill == "low":
        return "", "", skill, "Low skill"
    vsev_m, sev_m = 100 / t["vsev_rp"], 100 / t["sev_rp"]
    direction = "drought (below normal)" if pct < 50 else "flood (above normal)"
    if pct <= vsev_m or pct >= 100 - vsev_m:
        sev = "very severe"
    elif pct <= sev_m or pct >= 100 - sev_m:
        sev = "severe"
    else:
        return "", "none", skill, "Roughly normal"
    updown = "below" if pct < 50 else "above"
    label = f"Strongly {updown} normal" if sev == "very severe" else f"{updown.capitalize()} normal"
    return direction, sev, skill, label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issued", help="Issuance as YYYY-MM (default: latest)")
    ap.add_argument("-o", "--out", default=None, help="Output CSV path")
    args = ap.parse_args()

    index = json.loads((DATA / "forecasts" / "index.json").read_text())
    issued = args.issued or index["latest"]["file"]
    fc = json.loads((DATA / "forecasts" / f"{issued}.json").read_text())
    geo = json.loads((DATA / "countries.geojson").read_text())
    names = {f["properties"]["iso3"]: f["properties"]["name"] for f in geo["features"]}
    thresholds = fc["thresholds"]

    out = Path(args.out) if args.out else ROOT / "outputs" / f"cerf_tab_{issued}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    cols = [
        "iso3", "country", "issued", "season", "season_months", "in_rainy_season",
        "category", "forecast_direction", "forecast_severity",
        "return_period_yr", "forecast_percentile",
        "forecast_skill", "skill_correlation",
        "us_award", "cbpf_rhpf_envelope", "cerf_aa_framework_elnino", "cerf_nonframework_aa_elnino",
    ]
    tri_labels = {t["key"]: t["label"] for t in fc["trimesters"]}

    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for iso3 in sorted(names, key=lambda i: names[i]):
            recs = fc["data"].get(iso3, {})
            for key, label in tri_labels.items():
                rec = recs.get(key)
                direction, sev, skill, cat = classify(rec, thresholds)
                rainy = "" if rec is None else ("yes" if rec.get("rainy") else "no")
                w.writerow([
                    iso3, names[iso3], fc["issued_label"], key, label, rainy,
                    cat, direction, sev,
                    "" if rec is None else rec.get("rp", ""),
                    "" if rec is None else rec.get("pct", ""),
                    skill,
                    "" if rec is None else rec.get("r", ""),
                    "yes" if iso3 in US_AWARD else "no",
                    "yes" if iso3 in (US_AWARD | CBPF_ALL) else "no",
                    "yes" if iso3 in CERF_FW else "no",
                    "yes" if iso3 in CERF_NF else "no",
                ])
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
