"""Export every historical country-level forecast for the Map tab's year/month browser.

The static Map tab originally showed only the latest issuance. This recomputes each
issuance's forecast metrics — percentile within the historical distribution + directional
return period — from the per-year paired data (`paired_yearly_detrended.parquet`), exactly
as the marimo app does on the fly (in-sample reconstructions). It writes one small JSON per
(issued_year, issued_month) plus an index; the browser fetches the selected issuance on
demand, so each load stays tiny.

Freeze (default): existing issuance files are NOT rewritten — a monthly run only adds the
new month's file (plus the index). Historical percentiles drift trivially each month as the
in-sample distribution grows; freezing keeps the repo small and past issuances stable. Pass
--rebuild to regenerate every file (e.g. after a methodology change or a data correction).

The fully observed trimester (lead -3, ERA5 only) has no forecast: its paired rows carry
obs_mean alone. Each season is ranked against the EARLIER seasons of that series (the rule
src.skill.observed_trimester_stats applies live), so the newest issuance's observed values
equal forecast.json's. Older seasons are ranked on the newest run's detrending, which only
matters under --rebuild. Issuance files frozen before the observed trimester existed lack it.

Run:  uv run python pipeline/export_history_site.py            # freeze: add the new issuance
      uv run python pipeline/export_history_site.py --rebuild  # rewrite all issuances
"""

import argparse
import calendar
import json
import sys
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # for src
sys.path.insert(0, str(HERE))         # for the sibling export_static_site module

from src.constants import MIN_YEARS, OBSERVED_LEAD, PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from src.skill import trimester_lead  # noqa: E402
from export_static_site import (  # noqa: E402  (reuse the latest-forecast helpers)
    SITE_MIN_LEAD, THRESHOLDS, compute_rainy_set, _default_tri, _min_signed, _tri_label,
    _tri_valid, issued_year_for_season,
)

OUT = HERE.parent / "docs" / "data" / "forecasts"


def compute_metrics(paired: pd.DataFrame) -> pd.DataFrame:
    """Per (pcode, issued_month, trimester, season_year): forecast percentile + directional RP.

    Mirrors src.skill.forecast_metrics_for_year / empirical_rp, vectorised over years. The
    historical distribution is the overlap years (forecast & obs both present). The fully
    observed trimester (no forecast) ranks each ERA5 season against the earlier ones, as
    src.skill.observed_trimester_stats does (obs = True on those rows).
    """
    rows: list[tuple] = []
    for (pcode, im, tri), g in paired.groupby(["pcode", "issued_month", "trimester"], sort=False):
        if trimester_lead(int(im), TRIMESTERS[tri]) == OBSERVED_LEAD:
            o = g.dropna(subset=["obs_mean"]).sort_values("season_year")
            yrs, v = o["season_year"].to_numpy(dtype=int), o["obs_mean"].to_numpy(dtype=float)
            for k in range(MIN_YEARS, len(v)):
                hist, cur = v[:k], v[k]
                n = len(hist)
                p = 100.0 * float(np.sum(hist <= cur)) / n
                rp = (n + 1) / (int(np.sum(hist < cur)) + 1) if p < 50 else \
                    (n + 1) / (int(np.sum(hist > cur)) + 1)
                rows.append((pcode, im, tri, int(yrs[k]), p, float(rp), True))
            continue
        g = g.dropna(subset=["forecast_mean"])
        if g.empty:
            continue
        fc = g["forecast_mean"].values.astype(float)
        hist = g.loc[g["obs_mean"].notna(), "forecast_mean"].values.astype(float)
        n = len(hist)
        if n == 0:
            continue
        le = (hist[None, :] <= fc[:, None])
        pct = 100.0 * le.mean(axis=1)
        rp_low = (n + 1) / ((hist[None, :] < fc[:, None]).sum(axis=1) + 1)
        rp_high = (n + 1) / ((hist[None, :] > fc[:, None]).sum(axis=1) + 1)
        rp = np.where(pct < 50, rp_low, rp_high)
        for y, p, r in zip(g["season_year"].values, pct, rp):
            rows.append((pcode, im, tri, int(y), float(p), float(r), False))
    return pd.DataFrame(rows, columns=["pcode", "issued_month", "trimester", "season_year", "pct",
                                       "rp", "obs"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="Rewrite ALL issuance files. Default: freeze — only write issuances "
                         "that don't exist yet, so a monthly run adds one file instead of "
                         "re-touching every historical file (their in-sample percentiles drift "
                         "trivially each month; freezing keeps the repo small and history stable).")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    print("Loading paired_yearly + skill stats (detrended)...")
    paired = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/paired_yearly_detrended.parquet", stage="dev")
    skill = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats_detrended.parquet", stage="dev")

    pcode_to_iso3 = skill.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    r_lookup = skill.set_index(["pcode", "issued_month", "trimester"])["pearson_r"].to_dict()

    # ERA5 monthly climatology → rainy-season set (same query/logic as the latest export).
    pcodes = skill["pcode"].dropna().unique().tolist()
    engine = stratus.get_engine("prod")
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

    print("Computing per-year forecast metrics...")
    met = compute_metrics(paired)
    # Keep only valid (in-horizon, complete) trimesters per issue month, and attach issue year.
    met = met[met.apply(lambda r: _tri_valid(TRIMESTERS[r["trimester"]], r["issued_month"],
                                             min_lead=SITE_MIN_LEAD), axis=1)].copy()
    met["issued_year"] = [issued_year_for_season(sy, im, t)
                          for sy, im, t in zip(met["season_year"], met["issued_month"], met["trimester"])]
    met["iso3"] = met["pcode"].map(pcode_to_iso3)
    met = met[met["iso3"].notna()]
    # An observed season needs only ERA5: keep it only where SEAS5 has a forecast for that
    # issuance AND country, so it can never open an issuance (or move the index's latest)
    # on its own, nor give a country SEAS5 does not cover an observed-only record.
    # Likewise per country: no observed-only record where SEAS5 has no forecast that issuance
    # (export_static_site.blank_observed_without_forecast; history writes no empty records).
    fc = met.loc[~met["obs"], ["issued_year", "issued_month", "pcode"]].drop_duplicates()
    met = met.merge(fc.assign(_fc=True), on=["issued_year", "issued_month", "pcode"], how="left")
    met = met[~met["obs"] | met["_fc"].fillna(False).astype(bool)].drop(columns="_fc")

    years = sorted(met["issued_year"].unique().tolist())
    months_by_year: dict[str, list[int]] = {}
    n_written = n_frozen = 0
    # The newest issuance is never frozen: a SEAS5-only refresh (refresh job with
    # inseason=skip) publishes it without its in-season trimesters, and the full
    # refresh after ERA5 lands must be able to rewrite it with all of them.
    newest = max(zip(met["issued_year"].astype(int), met["issued_month"].astype(int)))
    for (iy, im), grp in met.groupby(["issued_year", "issued_month"]):
        # Every issuance goes in the index regardless of whether we (re)write its file.
        months_by_year.setdefault(str(int(iy)), []).append(int(im))
        path = OUT / f"{int(iy)}-{int(im):02d}.json"
        if path.exists() and not args.rebuild and (int(iy), int(im)) != newest:
            n_frozen += 1  # freeze: keep the existing file, don't rewrite
            continue
        valid_tris = sorted(grp["trimester"].unique(),
                            key=lambda t: _min_signed(TRIMESTERS[t], im))
        default_tri = _default_tri(valid_tris, im)
        data: dict[str, dict] = {}
        for _, row in grp.iterrows():
            iso3, tri = row["iso3"], row["trimester"]
            rec = {
                "pct": round(float(row["pct"]), 2),
                "r": None if row["obs"] else (lambda v: round(float(v), 3) if pd.notna(v) else None)(
                    r_lookup.get((row["pcode"], im, tri))),
                "rp": round(float(row["rp"]), 1),
                "rainy": (row["pcode"], tri) in rainy_set,
            }
            if row["obs"]:
                rec["obs"] = True
            data.setdefault(iso3, {})[tri] = rec
        payload = {
            "issued_label": f"{calendar.month_name[im]} {int(iy)}",
            "issued_month": int(im),
            "issued_year": int(iy),
            "thresholds": THRESHOLDS,
            "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in valid_tris],
            "default_trimester": default_tri,
            "data": data,
        }
        path.write_text(json.dumps(payload, separators=(",", ":")))
        n_written += 1

    for y in months_by_year:
        months_by_year[y].sort()
    latest_year = years[-1]
    latest_month = max(months_by_year[str(latest_year)])
    index = {
        "thresholds": THRESHOLDS,
        "years": years,
        "months_by_year": months_by_year,
        "latest": {"year": latest_year, "month": latest_month,
                   "file": f"{latest_year}-{latest_month:02d}"},
        "month_names": {str(m): calendar.month_name[m] for m in range(1, 13)},
    }
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    size = sum(f.stat().st_size for f in OUT.glob("*.json")) / 1024
    print(f"Wrote {n_written} new/updated issuance file(s), froze {n_frozen} existing, + index.json "
          f"to {OUT}  ({size:.0f} KB total)")
    print(f"Years {years[0]}–{years[-1]}; latest issuance {latest_year}-{latest_month:02d}")


if __name__ == "__main__":
    main()
