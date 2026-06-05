"""Build the static GitHub Pages site data (docs/data/).

Reads the processed skill stats (blob) and ERA5 climatology (DB) ONCE and writes two
static files that the docs/ page consumes at runtime with no backend:

  docs/data/forecast.json     — latest forecast per iso3 per valid trimester (+ rainy flag)
  docs/data/countries.geojson — simplified country boundaries (iso3, name)

Run:  uv run python pipeline/export_static_site.py
"""

import calendar
import json
import sys
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX, TRIMESTERS

# Defaults mirror the marimo app's out-of-the-box controls.
USE_DETRENDED = True  # app default "Forecast version: Detrended"
RAINY_TRIMESTER_PCT = 0.25  # trimester_pct_sl default
RAINY_MONTH_PCT = 0.05  # month_pct_sl default
THRESHOLDS = {"sev_rp": 3, "vsev_rp": 10, "r_mod": 0.25, "r_high": 0.5}

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
GEO_SRC = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"
SIMPLIFY_TOLERANCE = 0.1
# (min_lon, min_lat, max_lon, max_lat) — trims antimeridian overflow + polar clutter.
CLIP_BOUNDS = (-180, -60, 180, 84)


def _actual_issued_year(row) -> int:
    """Map season_year back to issue year (year+1 for cross-year trimesters)."""
    tri = TRIMESTERS[row["trimester"]]
    is_wrap = 12 in tri and 1 in tri
    is_cross = not is_wrap and min(tri) < row["issued_month"]
    return int(row["current_forecast_year"]) - (1 if is_cross else 0)


def _tri_valid(months: list[int], im: int) -> bool:
    """Valid = no past month and at least one future month <= 6 ahead (app's rule)."""
    signed = [o if (o := (m - im) % 12) <= 6 else o - 12 for m in months]
    future = [s for s in signed if s > 0]
    return all(s >= 0 for s in signed) and bool(future) and max(future) <= 6


def _min_signed(months: list[int], im: int) -> int:
    return min(o if (o := (m - im) % 12) <= 6 else o - 12 for m in months)


def _tri_label(months: list[int]) -> str:
    return "–".join(calendar.month_abbr[m] for m in months)


def compute_rainy_set(monthly_clim: pd.DataFrame) -> set[tuple[str, str]]:
    """Port of the app's rainy-season cell (analysis/prob_alerts.py:65-82)."""
    mc = monthly_clim.copy()
    annual = mc.groupby("pcode")["mean_mm_day"].sum().rename("annual")
    mc = mc.merge(annual.reset_index(), on="pcode")
    mc["pct_annual"] = mc["mean_mm_day"] / mc["annual"]
    rainy = set()
    for tri, months in TRIMESTERS.items():
        tri_mc = mc[mc["month"].isin(months)]
        tri_mean = tri_mc.groupby("pcode")["mean_mm_day"].mean()
        tri_annual = annual.reindex(tri_mean.index)
        tri_ok = 3 * tri_mean / tri_annual >= RAINY_TRIMESTER_PCT
        month_ok = (
            tri_mc.groupby("pcode")["pct_annual"].min().reindex(tri_ok.index, fill_value=0)
            >= RAINY_MONTH_PCT
        )
        for pcode, is_rainy in (tri_ok & month_ok).items():
            if bool(is_rainy):
                rainy.add((pcode, tri))
    return rainy


def main() -> None:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)

    skill_blob = (
        f"{PROJECT_PREFIX}/processed/"
        + ("skill_stats_detrended.parquet" if USE_DETRENDED else "skill_stats.parquet")
    )
    print(f"Loading skill stats: {skill_blob}")
    df = stratus.load_parquet_from_blob(skill_blob, stage="dev")

    # Monthly ERA5 climatology for the rainy-season mask (same query as the app).
    pcodes = df["pcode"].dropna().unique().tolist()
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

    # Latest issued month + year, and its valid trimesters (app's selector logic).
    iy = df[df["current_forecast_year"].notna()].copy()
    iy["_iy"] = iy.apply(_actual_issued_year, axis=1)
    max_iy_by_month = iy.groupby("issued_month")["_iy"].max().astype(int).to_dict()
    global_max_iy = max(max_iy_by_month.values())
    issued_month = max(m for m, y in max_iy_by_month.items() if y == global_max_iy)
    issued_year = global_max_iy
    issued_label = f"{calendar.month_name[issued_month]} {issued_year}"

    valid_tris = sorted(
        [name for name, months in TRIMESTERS.items() if _tri_valid(months, issued_month)],
        key=lambda t: _min_signed(TRIMESTERS[t], issued_month),
    )
    default_trimester = valid_tris[1] if len(valid_tris) > 1 else valid_tris[0]
    print(f"Latest issue: {issued_label}  valid trimesters: {valid_tris}  default: {default_trimester}")

    pcode_to_iso3 = df.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()

    # Build per-iso3 per-trimester records (latest forecast only).
    data: dict[str, dict] = {}
    for tri in valid_tris:
        sub = df[(df["issued_month"] == issued_month) & (df["trimester"] == tri)]
        for _, row in sub.iterrows():
            pc = row["pcode"]
            iso3 = pcode_to_iso3.get(pc)
            if iso3 is None:
                continue
            pct = row["forecast_percentile"]
            r = row["pearson_r"]
            # Directional return period: drought RP (forecast_rp) when dry (pct<50),
            # else flood RP (flood_rp). Matches the alert tables / scatter RP view.
            if pd.notna(pct):
                rp = row["forecast_rp"] if pct < 50 else row["flood_rp"]
            else:
                rp = None
            data.setdefault(iso3, {})[tri] = {
                "pct": round(float(pct), 2) if pd.notna(pct) else None,
                "r": round(float(r), 3) if pd.notna(r) else None,
                "rp": round(float(rp), 1) if pd.notna(rp) else None,
                "rainy": (pc, tri) in rainy_set,
            }

    forecast = {
        "issued_label": issued_label,
        "issued_month": int(issued_month),
        "issued_year": int(issued_year),
        "thresholds": THRESHOLDS,
        "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in valid_tris],
        "default_trimester": default_trimester,
        "data": data,
    }
    fc_path = DOCS_DATA / "forecast.json"
    fc_path.write_text(json.dumps(forecast, separators=(",", ":")))
    print(f"Wrote {fc_path}  ({fc_path.stat().st_size/1024:.1f} KB, {len(data)} countries)")

    # Simplified country geometry, clipped to a sane lon/lat window. The clip removes
    # antimeridian overflow (e.g. Kiribati stored with longitudes > 180°, which otherwise
    # projects into one giant band across the whole map in D3) and trims polar clutter.
    print(f"Loading + simplifying geometry: {GEO_SRC}")
    g = gpd.read_file(GEO_SRC)[["iso3", "name", "geometry"]].dropna(subset=["geometry"]).copy()
    g["geometry"] = g["geometry"].simplify(SIMPLIFY_TOLERANCE)
    g = gpd.clip(g, box(*CLIP_BOUNDS))
    g = g[~g.geometry.is_empty & g.geometry.notna()]
    geo_path = DOCS_DATA / "countries.geojson"
    geo_path.write_text(g.to_json())
    print(f"Wrote {geo_path}  ({geo_path.stat().st_size/1024:.1f} KB, {len(g)} features)")
    print("Done.")


if __name__ == "__main__":
    main()
