"""Export every historical ADM1-level forecast for a subnational map browser.

Mirrors pipeline/export_history_site.py but reads from the ADM1 blob paths
produced by compute_skill_adm1.py and writes JSON files keyed by pcode
(not iso3, since multiple ADM1 units exist per country).

Output: one JSON per (issued_year, issued_month) in docs/data/forecasts/adm1/
        plus an index.json listing available issuances.

Run:  uv run python pipeline/export_history_site_adm1.py
"""

import calendar
import json
import sys
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from export_static_site import (  # noqa: E402
    THRESHOLDS, compute_rainy_set, _min_signed, _tri_label, _tri_valid,
)

SKILL_BLOB     = f"{PROJECT_PREFIX}/processed/skill_stats_detrended_adm1.parquet"
PAIRED_BLOB    = f"{PROJECT_PREFIX}/processed/paired_yearly_detrended_adm1.parquet"

OUT = HERE.parent / "docs" / "data" / "forecasts" / "adm1"


def issued_year_of(season_year: int, im: int, tri: str) -> int:
    """Map (season_year, issue month, trimester) back to the calendar issue year.

    Args:
        season_year: The season year assigned during skill computation.
        im: Issued month (1-12).
        tri: Trimester key (e.g. "JJA").

    Returns:
        The calendar year in which the forecast was issued.
    """
    months = TRIMESTERS[tri]
    is_wrap = 12 in months and 1 in months
    is_cross = (not is_wrap) and (min(months) < im)
    return int(season_year) - (1 if is_cross else 0)


def compute_metrics(paired: pd.DataFrame) -> pd.DataFrame:
    """Compute per-year forecast percentile and directional return period.

    For each (pcode, issued_month, trimester, season_year) combination,
    compute the forecast percentile within the historical distribution and
    the empirical directional return period.  Mirrors the logic in
    export_history_site.compute_metrics but operates on ADM1 pcodes.

    Args:
        paired: DataFrame with columns pcode, issued_month, trimester,
            season_year, forecast_mean, obs_mean.

    Returns:
        DataFrame with columns pcode, issued_month, trimester, season_year,
        pct, rp.
    """
    rows: list[tuple] = []
    for (pcode, im, tri), g in paired.groupby(["pcode", "issued_month", "trimester"], sort=False):
        g = g.dropna(subset=["forecast_mean"])
        if g.empty:
            continue
        fc = g["forecast_mean"].values.astype(float)
        hist = g.loc[g["obs_mean"].notna(), "forecast_mean"].values.astype(float)
        n = len(hist)
        if n == 0:
            continue
        le = hist[None, :] <= fc[:, None]
        pct = 100.0 * le.mean(axis=1)
        rp_low  = (n + 1) / ((hist[None, :] < fc[:, None]).sum(axis=1) + 1)
        rp_high = (n + 1) / ((hist[None, :] > fc[:, None]).sum(axis=1) + 1)
        rp = np.where(pct < 50, rp_low, rp_high)
        for y, p, r in zip(g["season_year"].values, pct, rp):
            rows.append((pcode, im, tri, int(y), float(p), float(r)))
    return pd.DataFrame(rows, columns=["pcode", "issued_month", "trimester", "season_year", "pct", "rp"])


def main() -> None:
    """Export per-issuance ADM1 forecast JSON files to docs/data/forecasts/adm1/."""
    OUT.mkdir(parents=True, exist_ok=True)

    print("Loading ADM1 paired_yearly + skill stats (detrended)...")
    paired = stratus.load_parquet_from_blob(PAIRED_BLOB, stage="dev")
    skill  = stratus.load_parquet_from_blob(SKILL_BLOB,  stage="dev")

    r_lookup = skill.set_index(["pcode", "issued_month", "trimester"])["pearson_r"].to_dict()

    # ERA5 monthly climatology for rainy-season classification, same logic as ADM0 export.
    pcodes = skill["pcode"].dropna().unique().tolist()
    engine = stratus.get_engine("prod")
    ph = ",".join(["%s"] * len(pcodes))
    print(f"Querying ERA5 climatology for {len(pcodes)} ADM1 pcodes...")
    with engine.connect() as conn:
        era5 = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({ph})",
            conn,
            params=tuple(pcodes),
            parse_dates=["valid_date"],
        )
    monthly_clim = (
        era5.assign(month=era5["valid_date"].dt.month)
        .groupby(["pcode", "month"])["mean"].mean()
        .reset_index()
        .rename(columns={"mean": "mean_mm_day"})
    )
    rainy_set = compute_rainy_set(monthly_clim)

    print("Computing per-year ADM1 forecast metrics...")
    met = compute_metrics(paired)
    met = met[
        met.apply(lambda r: _tri_valid(TRIMESTERS[r["trimester"]], r["issued_month"]), axis=1)
    ].copy()
    met["issued_year"] = [
        issued_year_of(sy, im, t)
        for sy, im, t in zip(met["season_year"], met["issued_month"], met["trimester"])
    ]
    # Keep only pcodes that appear in skill (have sufficient data).
    valid_pcodes = set(skill["pcode"].dropna().unique())
    met = met[met["pcode"].isin(valid_pcodes)]

    years = sorted(met["issued_year"].unique().tolist())
    months_by_year: dict[str, list[int]] = {}
    n_files = 0

    for (iy, im), grp in met.groupby(["issued_year", "issued_month"]):
        valid_tris = sorted(
            grp["trimester"].unique(),
            key=lambda t: _min_signed(TRIMESTERS[t], im),
        )
        default_tri = valid_tris[1] if len(valid_tris) > 1 else valid_tris[0]
        data: dict[str, dict] = {}
        for _, row in grp.iterrows():
            pcode, tri = row["pcode"], row["trimester"]
            data.setdefault(pcode, {})[tri] = {
                "pct": round(float(row["pct"]), 2),
                "r": (
                    (lambda v: round(float(v), 3) if pd.notna(v) else None)(
                        r_lookup.get((pcode, im, tri))
                    )
                ),
                "rp": round(float(row["rp"]), 1),
                "rainy": (pcode, tri) in rainy_set,
            }
        payload = {
            "issued_label": f"{calendar.month_name[im]} {int(iy)}",
            "issued_month": int(im),
            "issued_year": int(iy),
            "thresholds": THRESHOLDS,
            "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in valid_tris],
            "default_trimester": default_tri,
            "data": data,
        }
        (OUT / f"{int(iy)}-{int(im):02d}.json").write_text(
            json.dumps(payload, separators=(",", ":"))
        )
        months_by_year.setdefault(str(int(iy)), []).append(int(im))
        n_files += 1

    for y in months_by_year:
        months_by_year[y].sort()
    latest_year  = years[-1]
    latest_month = max(months_by_year[str(latest_year)])
    index = {
        "thresholds": THRESHOLDS,
        "years": years,
        "months_by_year": months_by_year,
        "latest": {
            "year": latest_year,
            "month": latest_month,
            "file": f"{latest_year}-{latest_month:02d}",
        },
        "month_names": {str(m): calendar.month_name[m] for m in range(1, 13)},
    }
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    size = sum(f.stat().st_size for f in OUT.glob("*.json")) / 1024
    print(f"Wrote {n_files} issuance files + index.json to {OUT}  ({size:.0f} KB total)")
    print(f"Years {years[0]}-{years[-1]}; latest issuance {latest_year}-{latest_month:02d}")


if __name__ == "__main__":
    main()
