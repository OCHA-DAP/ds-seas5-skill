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

import calendar
import json
import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from src.skill import trimester_lead  # noqa: E402
from export_static_site import (  # noqa: E402
    THRESHOLDS, _tri_label, _tri_valid, compute_rainy_set, issued_year_for_season,
)

SKILL_BLOB = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm1.parquet"
OUT = HERE.parent / "docs" / "data" / "hnrp_drought.json"


def load_pin_adm1() -> pd.DataFrame:
    """Intersectoral PiN + targeted per ADM1 pcode, each country's latest reference period.

    Prefers rows published at admin-1; falls back to summing admin-2 rows per
    admin1_code for countries that only publish at admin-2.
    """
    engine = stratus.get_engine("dev")
    q = """
    SELECT location_code, admin1_code, admin2_code, admin_level,
           population_status, population, reference_period_start
    FROM hpc.needs_admin
    WHERE sector_code = 'Intersectoral' AND category = 'total'
      AND population_status IN ('INN', 'TGT') AND admin_level IN (1, 2)
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn, parse_dates=["reference_period_start"])

    latest = df.groupby("location_code")["reference_period_start"].transform("max")
    df = df[df["reference_period_start"] == latest]

    rows = []
    for (loc, ref), g in df.groupby(["location_code", "reference_period_start"]):
        lvl = 1 if (g["admin_level"] == 1).any() else 2
        g = g[g["admin_level"] == lvl]
        agg = g.groupby(["admin1_code", "population_status"])["population"].sum().unstack()
        for pcode, r in agg.iterrows():
            rows.append({
                "pcode": pcode, "iso3": loc,
                "pin": int(r["INN"]) if pd.notna(r.get("INN")) else None,
                "targeted": int(r["TGT"]) if pd.notna(r.get("TGT")) else None,
                "ref_year": int(ref.year),
                "pin_admin_level": lvl,
            })
    out = pd.DataFrame(rows)
    print(f"PiN: {out['iso3'].nunique()} countries, {len(out)} ADM1 units "
          f"(admin-1 direct: {(out['pin_admin_level'] == 1).sum()}, "
          f"admin-2 rollup: {(out['pin_admin_level'] == 2).sum()})")
    return out


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
    SELECT iso3, year, admin1_code, population_group, final_severity, population
    FROM hpc.severity_admin
    """
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    df = df[df["year"] == df.groupby("iso3")["year"].transform("max")]
    df["population_group"] = df["population_group"].fillna("")

    rows = []
    for iso3, g in df.groupby("iso3"):
        groups = set(g["population_group"])
        if "" in groups:
            grp = ""
        elif "Global_Population" in groups:
            grp = "Global_Population"
        else:
            grp = g.groupby("population_group")["population"].sum().idxmax()
        g = g[g["population_group"] == grp]
        sev4 = g[g["final_severity"] >= 4].groupby("admin1_code")["population"].sum()
        total = g.groupby("admin1_code")["population"].sum()
        for pcode, tot in total.items():
            rows.append({
                "pcode": pcode, "iso3": iso3,
                "sev4": int(sev4.get(pcode, 0)),
                "sev_total": int(tot),
                "sev_year": int(g["year"].iloc[0]),
            })
    out = pd.DataFrame(rows)
    print(f"Severity: {out['iso3'].nunique()} countries, {len(out)} ADM1 units, "
          f"{out['sev4'].sum():,} people in severity 4+")
    return out


def main() -> None:
    print(f"Loading ADM1 skill stats: {SKILL_BLOB}")
    skill = stratus.load_parquet_from_blob(SKILL_BLOB, stage="dev")
    df_pin = load_pin_adm1()
    df_sev = load_severity_adm1()
    # Union of PiN and severity units; iso3 from whichever side has it.
    df_hum = df_pin.merge(df_sev, on="pcode", how="outer", suffixes=("", "_sev"))
    df_hum["iso3"] = df_hum["iso3"].combine_first(df_hum["iso3_sev"])
    df_hum = df_hum.drop(columns=["iso3_sev"])

    # Restrict everything downstream to HNRP units — keeps the climatology query small.
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
    engine = stratus.get_engine("prod")
    ph = ",".join(["%s"] * len(pcodes))
    print(f"Querying ERA5 climatology for {len(pcodes)} ADM1 pcodes...")
    with engine.connect() as conn:
        era5 = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
            conn, params=tuple(pcodes), parse_dates=["valid_date"],
        )
        names = pd.read_sql(
            f"SELECT pcode, name FROM public.polygon WHERE adm_level=1 AND pcode IN ({ph})",
            conn, params=tuple(pcodes),
        ).set_index("pcode")["name"].to_dict()
        country_names = pd.read_sql(
            "SELECT iso3, name FROM public.polygon WHERE adm_level=0", conn,
        ).set_index("iso3")["name"].to_dict()
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
        records.append(row)

    df_fc = pd.DataFrame(records)
    merged = df_hum.merge(df_fc, on="pcode", how="left")
    merged["country"] = merged["iso3"].map(country_names)
    n_signal = merged["rp"].notna().sum()
    print(f"{len(merged)} HNRP ADM1 units; {n_signal} with a qualifying drought signal")

    payload = {
        "issued_label": issued_label,
        "issued_month": int(issued_month),
        "issued_year": int(global_max_iy),
        "thresholds": THRESHOLDS,
        "weight_note": (
            "Humanitarian weight is population in JIAF inter-sectoral severity 4+ from the "
            "HNRP severity analysis (ds-hnrp-mirror, latest analysis year per country), with "
            "intersectoral PiN / targeted alongside. Admin-2/3 figures are summed to admin-1."
        ),
        "rows": [
            {k: (None if pd.isna(v) else v) for k, v in rec.items()}
            for rec in merged.drop(columns=["pin_admin_level"]).to_dict("records")
        ],
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"Wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
