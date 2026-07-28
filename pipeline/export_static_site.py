"""Build the static GitHub Pages site data (docs/data/).

Reads the processed skill stats (blob) and ERA5 climatology (DB) ONCE and writes:

  docs/data/forecast.json     — latest forecast per iso3 per valid trimester (+ rainy flag)
  docs/data/skill_matrix.json — per-country Pearson-r matrix (lead x trimester) + climatology
  docs/data/countries.geojson — simplified country boundaries (iso3, name)

Also uploads a flat parquet of the forecast data to blob storage so downstream
consumers can access it without the static site:

  ds-seas5-skill/processed/forecast_site.parquet

Run:  uv run python pipeline/export_static_site.py
"""

import calendar
import json
import sys
from pathlib import Path

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
import topojson as tp
from shapely.geometry import MultiPolygon, Polygon, box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX, TRIMESTERS
from src.skill import trimester_lead

# Defaults mirror the marimo app's out-of-the-box controls.
USE_DETRENDED = True  # app default "Forecast version: Detrended"
RAINY_TRIMESTER_PCT = 0.15  # trimester_pct_sl default
RAINY_MONTH_PCT = 0.00  # month_pct_sl default (no per-month minimum)
THRESHOLDS = {"sev_rp": 3, "vsev_rp": 10, "r_mod": 0.3, "r_high": 0.5}

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
GEO_SRC = Path(__file__).resolve().parent.parent / "analysis" / "_world_countries.gpkg"
FORECAST_SITE_BLOB = f"{PROJECT_PREFIX}/processed/forecast_site.parquet"
SIMPLIFY_TOLERANCE = 0.05  # topology-preserving (see geometry block); finer than the old 0.1
# (min_lon, min_lat, max_lon, max_lat) — trims antimeridian overflow + polar clutter.
CLIP_BOUNDS = (-180, -60, 180, 84)


def issued_year_for_season(season_year: int, im: int, tri: str) -> int:
    """Map season_year back to issue year (inverse of src.skill.season_year_for).

    Fully-forecast trimesters: +1 year back for cross-year (issued before a next-year
    trimester). In-season issuances (negative lead): the issue month falls inside the
    trimester — same calendar year for non-wrapping trimesters; for wrapping ones the
    season anchors on December's year, so an issue month on the ≤6 side is year + 1.
    """
    months = TRIMESTERS[tri]
    is_wrap = 12 in months and 1 in months
    if trimester_lead(im, months) < 0:
        if is_wrap:
            return int(season_year) + (0 if im > 6 else 1)
        return int(season_year)
    is_cross = not is_wrap and min(months) < im
    return int(season_year) - (1 if is_cross else 0)


def _actual_issued_year(row) -> int:
    return issued_year_for_season(
        row["current_forecast_year"], row["issued_month"], row["trimester"]
    )


def _tri_valid(months: list[int], im: int, min_lead: int = -2, max_lead: int = 4) -> bool:
    """Valid = lead −2 … 4: complete trimesters with ≥1 forecast month in SEAS5's horizon.

    Leads 0–4 are fully-forecast trimesters (lead 5–6 would spill past the 6-month
    horizon). Leads −1/−2 are in-season (mixed) trimesters: 1–2 months already observed
    from ERA5, the rest forecast from this issuance. Other forecast systems can pass
    their own lead window (CMA CMME: 1–4, no in-season trimesters).
    """
    return min_lead <= trimester_lead(im, months) <= max_lead


def _min_signed(months: list[int], im: int) -> int:
    return min(o if (o := (m - im) % 12) <= 6 else o - 12 for m in months)


def _tri_label(months: list[int]) -> str:
    return "–".join(calendar.month_abbr[m] for m in months)


def _default_tri(valid_tris: list[str], im: int) -> str:
    """Default selection = the lead-1 trimester (first fully-forecast one after the
    current month's), unchanged from before in-season trimesters were added."""
    for t in valid_tris:
        if trimester_lead(im, TRIMESTERS[t]) == 1:
            return t
    return valid_tris[len(valid_tris) // 2]


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


# Leadtime rows for the skill heatmap = months from issue to the trimester's first month.
# Only complete trimesters are kept: SEAS5's horizon is 7 months (leads 0–6), so a trimester
# whose first month is at lead L spans leads L..L+2 and fits only when L ≤ 4. Leads 5–6 would
# average just the 1–2 in-horizon months against the full 3-month ERA5 obs (mislabelled skill),
# so they are excluded. Negative leads are in-season (mixed) trimesters: the already-observed
# months come from ERA5, only the remainder is forecast — so skill is naturally much higher.
SKILL_LEADS = [-2, -1, 0, 1, 2, 3, 4]


def build_skill_matrix(
    df: pd.DataFrame,
    monthly_clim: pd.DataFrame,
    rainy_set: set[tuple[str, str]],
    pcode_to_iso3: dict[str, str],
    leads: list[int] = SKILL_LEADS,
) -> dict:
    """Per-country correlation matrix (leadtime × trimester) + trimester climatology.

    Consumed by the static site's "Skill" tab: a climatology bar chart over the 12
    trimesters with an aligned Pearson-r heatmap (x = valid trimester, y = leadtime).
    """
    tri_names = list(TRIMESTERS)  # calendar order
    # (pcode, issued_month, trimester) -> pearson_r, for fast lookup.
    r_lookup = (
        df.set_index(["pcode", "issued_month", "trimester"])["pearson_r"].to_dict()
    )
    # Trimester mean rainfall (mm/day) per pcode = mean of the 3 monthly climatology means.
    clim_by_pcode = monthly_clim.set_index(["pcode", "month"])["mean_mm_day"]

    countries: dict[str, dict] = {}
    for pcode, iso3 in pcode_to_iso3.items():
        # r matrix: rows = leadtime (`leads`), cols = trimester (calendar order).
        matrix: list[list[float | None]] = []
        any_r = False
        for lead in leads:
            row: list[float | None] = []
            for tri in tri_names:
                start = TRIMESTERS[tri][0]
                im = ((start - lead - 1) % 12) + 1
                r = r_lookup.get((pcode, im, tri))
                if r is not None and pd.notna(r):
                    row.append(round(float(r), 3))
                    any_r = True
                else:
                    row.append(None)
            matrix.append(row)
        if not any_r:
            continue  # countries with no skill anywhere (e.g. islands missing ERA5) — skip

        clim: list[float | None] = []
        for tri in tri_names:
            vals = [clim_by_pcode.get((pcode, m)) for m in TRIMESTERS[tri]]
            vals = [v for v in vals if v is not None and pd.notna(v)]
            clim.append(round(float(sum(vals) / len(vals)), 3) if vals else None)

        clim_monthly: list[float | None] = []
        for m in range(1, 13):
            v = clim_by_pcode.get((pcode, m))
            clim_monthly.append(round(float(v), 3) if v is not None and pd.notna(v) else None)

        name = df.loc[df["pcode"] == pcode, "country_name"].iloc[0]
        countries[iso3] = {
            "name": str(name),
            "clim": clim,
            "clim_monthly": clim_monthly,
            "rainy": [(pcode, tri) in rainy_set for tri in tri_names],
            "r": matrix,
        }

    return {
        "leads": leads,
        "thresholds": {"r_mod": THRESHOLDS["r_mod"], "r_high": THRESHOLDS["r_high"]},
        "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in tri_names],
        "countries": dict(sorted(countries.items(), key=lambda kv: kv[1]["name"])),
    }


def build_forecast_df(
    data: dict[str, dict],
    issued_month: int,
    issued_year: int,
    country_name_by_iso3: dict[str, str],
) -> pd.DataFrame:
    """Flatten forecast site data into a tidy DataFrame for blob upload.

    Each row represents one (iso3, trimester) combination from the latest
    issued forecast, with the same fields used by the static site.

    Args:
        data: Nested dict ``{iso3: {trimester: {pct, r, rp, rainy}}}``.
        issued_month: Calendar month of the latest forecast issue (1–12).
        issued_year: Year of the latest forecast issue.
        country_name_by_iso3: Mapping from iso3 to country name.

    Returns:
        DataFrame with columns: issued_month, issued_year, iso3, country_name,
        trimester, forecast_pct, return_period, pearson_r, is_rainy.
    """
    rows = [
        {
            "issued_month": issued_month,
            "issued_year": issued_year,
            "iso3": iso3,
            "country_name": country_name_by_iso3.get(iso3),
            "trimester": tri,
            "forecast_pct": vals["pct"],
            "return_period": vals["rp"],
            "pearson_r": vals["r"],
            "is_rainy": vals["rainy"],
        }
        for iso3, tris in data.items()
        for tri, vals in tris.items()
    ]
    return pd.DataFrame(rows)


def _polygonal(geom):
    """Keep only polygon parts (make_valid can emit GeometryCollections / lines)."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, (Polygon, MultiPolygon)):
        return geom
    parts: list[Polygon] = []
    for sub in getattr(geom, "geoms", []):
        if isinstance(sub, Polygon):
            parts.append(sub)
        elif isinstance(sub, MultiPolygon):
            parts.extend(sub.geoms)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


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
    default_trimester = _default_tri(valid_tris, issued_month)
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

    country_name_by_iso3 = (
        df.drop_duplicates("iso3").set_index("iso3")["country_name"].to_dict()
    )
    forecast_df = build_forecast_df(data, issued_month, issued_year, country_name_by_iso3)
    stratus.upload_parquet_to_blob(forecast_df, FORECAST_SITE_BLOB, stage="dev")
    print(f"Uploaded {FORECAST_SITE_BLOB}  ({len(forecast_df)} rows)")

    # Per-country skill matrix (all issued months × trimesters) + trimester climatology.
    skill_matrix = build_skill_matrix(df, monthly_clim, rainy_set, pcode_to_iso3)
    sm_path = DOCS_DATA / "skill_matrix.json"
    sm_path.write_text(json.dumps(skill_matrix, separators=(",", ":")))
    print(
        f"Wrote {sm_path}  ({sm_path.stat().st_size/1024:.1f} KB, "
        f"{len(skill_matrix['countries'])} countries)"
    )

    # Country geometry, clipped to a sane lon/lat window. The clip removes antimeridian
    # overflow (e.g. Kiribati stored with longitudes > 180°) and trims polar clutter.
    # Simplify with TOPOLOGY preserved (shared borders simplified once) so adjacent countries
    # never develop slivers/gaps between them — independent per-polygon simplify did.
    print(f"Loading + simplifying geometry: {GEO_SRC}")
    g0 = gpd.read_file(GEO_SRC)[["iso3", "name", "geometry"]].dropna(subset=["geometry"]).copy()
    g0 = gpd.clip(g0, box(*CLIP_BOUNDS))
    g0 = g0[~g0.geometry.is_empty & g0.geometry.notna()]
    topo = tp.Topology(g0, prequantize=True, shared_coords=True)
    g = topo.toposimplify(SIMPLIFY_TOLERANCE).to_gdf()
    g["geometry"] = g["geometry"].make_valid().apply(_polygonal)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    # Restore any micro-state that simplified away entirely, at full detail.
    missing = set(g0["iso3"]) - set(g["iso3"])
    if missing:
        g = pd.concat([g, g0[g0["iso3"].isin(missing)]], ignore_index=True)
        print(f"  restored {len(missing)} collapsed geometries at full detail: {sorted(missing)}")
    geo_path = DOCS_DATA / "countries.geojson"
    geo_path.write_text(g.to_json())
    print(f"Wrote {geo_path}  ({geo_path.stat().st_size/1024:.1f} KB, {len(g)} features)")
    print("Done.")


if __name__ == "__main__":
    main()
