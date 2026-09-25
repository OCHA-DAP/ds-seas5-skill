"""Build the HDX-signal input tables: one tidy row per admin unit × issuance × trimester.

Joins, per admin level, the three processed products this repo already maintains —

    skill_stats_detrended{_admN}.parquet     hindcast skill (Pearson r vs ERA5) per combo
    paired_yearly_detrended{_admN}.parquet   every year's normalised forecast + obs per combo
    monthly_clim{_admN}.parquet              ERA5 monthly climatology per unit

— into

    ds-seas5-skill/processed/hdx_signal/signal_inputs{_admN}.parquet         all years
    ds-seas5-skill/processed/hdx_signal/signal_inputs{_admN}_latest.parquet  current issuance only
    ds-seas5-skill/processed/hdx_signal/units{_admN}.parquet                 one row per unit

with, per row: the signed leadtime, skill (pearson_r, n_years), the forecast's position
in its own hindcast distribution (pct, dry_rp, wet_rp — Weibull, computed in-sample for
hindcast years exactly as the site does), the same forecast and the combo's hindcast
normal as trimester totals in mm (forecast_mm, hist_mean_mm — the pair the HNRP tab
shows, clipped at 0), the trimester's share of annual rainfall and the two in-season
flags. Everything the signal's four parameters need,
nothing that has to be recomputed. See src/hdx_signal.py for the roll-up and
docs/dev-notes/hdx-signal-data.md for the column reference.

Detrended variant only (what the site and every export use). Leads −2..4 only — the
complete trimesters with ≥1 forecast month inside SEAS5's horizon; negative = in season
(the elapsed months are ERA5 observations, see src/skill.py:aggregate_mixed_trimester).

Run:  uv run python pipeline/build_hdx_signal_inputs.py --level 1
      uv run python pipeline/build_hdx_signal_inputs.py --level 2
      uv run python pipeline/build_hdx_signal_inputs.py --level 0
      uv run python pipeline/build_hdx_signal_inputs.py --level 1 --iso3 ETH --no-upload   # quick local check
"""

import argparse
import io
import sys
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from src.constants import PROJECT_PREFIX, TRIMESTER_DAYS, TRIMESTERS  # noqa: E402
from src.season import APP_TRIMESTER_SHARE, FLAT_TRIMESTER_SHARE, trimester_shares  # noqa: E402
from src.skill import trimester_lead  # noqa: E402
from export_static_site import issued_year_for_season  # noqa: E402

SUFFIX = {0: "", 1: "_adm1", 2: "_adm2"}
OUT_PREFIX = f"{PROJECT_PREFIX}/processed/hdx_signal"
MIN_LEAD, MAX_LEAD = -2, 4

COLUMNS = [
    "pcode", "iso3", "name", "adm_level",
    "issued_year", "issued_month", "trimester", "lead", "season_year",
    "pearson_r", "n_years",
    "forecast_mean_log", "obs_mean_log", "in_sample",
    "pct", "dry_rp", "wet_rp", "forecast_mm", "hist_mean_mm",
    "tri_share_annual", "tri_mean_mm_day", "in_season_flat", "in_season_app",
]


def blob(name: str, level: int) -> str:
    return f"{PROJECT_PREFIX}/processed/{name}{SUFFIX[level]}.parquet"


def read_parquet_blob(path: str, columns: list[str] | None = None,
                      iso3s: list[str] | None = None) -> pd.DataFrame:
    """Read a (possibly large) parquet blob through Arrow with column pruning and
    strings as categoricals — the adm2 paired file is 0.5 GB / 35 M rows."""
    cc = stratus.get_container_client(stage="dev", container_name="projects")
    filters = [("iso3", "in", iso3s)] if iso3s else None
    table = pq.read_table(io.BytesIO(cc.download_blob(path).readall()),
                          columns=columns, filters=filters)
    return table.to_pandas(strings_to_categorical=True)


def position_metrics(paired: pd.DataFrame) -> pd.DataFrame:
    """Per (pcode, issued_month, trimester, season_year): pct, dry_rp, wet_rp, in_sample,
    forecast_mm, hist_mean_mm.

    The hindcast distribution of a combo = the years with BOTH a forecast and an
    observation (so the live forecast year is excluded from it, hindcast years are
    ranked in-sample — the same convention as src.skill / the site's history export).
    Weibull: RP = (n + 1) / rank, rank = 1 + number of hindcast values strictly more
    extreme in the given direction; pct = share of hindcast values ≤ the forecast.

    Amounts: forecast_mm is the row's own forecast (the value the RP is computed from:
    normalised to ERA5, detrended) back-transformed with expm1 and scaled from mm/day to
    the trimester total (TRIMESTER_DAYS, non-leap). hist_mean_mm is the combo's normal:
    expm1 of the MEAN LOG observation over EVERY observed year of the combo (the ERA5
    record, so including 1981 whose forecast predates the hindcast), scaled the same way.
    That is src.skill's era5_mean, the value the HNRP tab pairs the same forecast with
    (export_hnrp_drought.py), so the two products show identical amounts. A log-space
    mean, not the arithmetic mean of the mm values: for skewed rain the arithmetic mean
    sits above the median, so pairing with it would read a median (pct 50) forecast as
    below normal. Detrended log values can fall below 0 in dry combos, so both amounts
    are clipped at 0 after expm1, as src.skill does.
    """
    # NaN forecasts (obs-only years) sort last in their combo; they feed the normal only.
    p = paired.sort_values(["pcode", "issued_month", "trimester", "forecast_mean"])
    keys = ["pcode", "issued_month", "trimester"]
    grp = p.groupby(keys, sort=False, observed=True)
    fc = p["forecast_mean"].to_numpy()
    has_fc = ~np.isnan(fc)
    has_obs = p["obs_mean"].notna().to_numpy()
    in_sample = has_fc & has_obs
    gid = grp.ngroup().to_numpy()
    starts = np.r_[0, np.flatnonzero(np.diff(gid)) + 1, len(p)]

    obs_log = p["obs_mean"].to_numpy()
    n_hist = np.empty(len(p)); n_lt = np.empty(len(p)); n_le = np.empty(len(p)); n_gt = np.empty(len(p))
    hist_log = np.full(len(p), np.nan)
    for a, b in zip(starts[:-1], starts[1:]):
        f = fc[a:b]
        m = in_sample[a:b]
        h = f[m]  # sorted because f is sorted
        n = len(h)
        n_hist[a:b] = n
        if n == 0:
            continue  # no hindcast: pct/RPs/hist_mean_mm stay NaN (no empty-slice mean)
        lt = np.searchsorted(h, f, side="left")
        le = np.searchsorted(h, f, side="right")
        n_lt[a:b], n_le[a:b], n_gt[a:b] = lt, le, n - le
        hist_log[a:b] = obs_log[a:b][has_obs[a:b]].mean()
    ok = (n_hist > 0) & has_fc
    out = p.loc[has_fc, keys + ["season_year", "forecast_mean", "obs_mean"]].copy()
    out["in_sample"] = in_sample[has_fc]
    out["pct"] = np.where(ok, 100.0 * n_le / np.maximum(n_hist, 1), np.nan)[has_fc]
    out["dry_rp"] = np.where(ok, (n_hist + 1) / (n_lt + 1), np.nan)[has_fc]
    out["wet_rp"] = np.where(ok, (n_hist + 1) / (n_gt + 1), np.nan)[has_fc]
    # .map on the categorical maps its 12 categories, no per-row string materialisation.
    days = out["trimester"].map(TRIMESTER_DAYS).to_numpy(dtype=float)
    out["forecast_mm"] = np.expm1(fc[has_fc]).clip(min=0) * days
    out["hist_mean_mm"] = np.expm1(hist_log[has_fc]).clip(min=0) * days
    return out.rename(columns={"forecast_mean": "forecast_mean_log", "obs_mean": "obs_mean_log"})


def build(level: int, iso3s: list[str] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f"[level {level}] loading skill stats…")
    skill = read_parquet_blob(
        blob("skill_stats_detrended", level),
        ["pcode", "iso3", "country_name", "issued_month", "trimester", "pearson_r", "n_years"],
        iso3s,
    )
    print(f"[level {level}] loading paired yearly ({blob('paired_yearly_detrended', level)})…")
    paired = read_parquet_blob(
        blob("paired_yearly_detrended", level),
        ["pcode", "iso3", "issued_month", "trimester", "season_year", "forecast_mean", "obs_mean"],
        iso3s,
    )
    print(f"[level {level}] loading climatology…")
    clim = read_parquet_blob(blob("monthly_clim", level))

    # Unit names: the skill file's country_name column holds the UNIT name at adm1/2
    # (run_all_combinations was written for countries) — take it from the polygon table
    # via the climatology file, which carries pcode/iso3/name for every unit.
    units = clim[["pcode", "iso3", "name"]].drop_duplicates("pcode").copy()
    units["pcode"] = units["pcode"].astype(str)
    units["adm_level"] = level
    if iso3s:
        units = units[units["iso3"].isin(iso3s)]

    print(f"[level {level}] position metrics over {len(paired):,} paired rows…")
    pos = position_metrics(paired)
    del paired

    # Lead filter (−2..4) and issuance year.
    pos["lead"] = [
        trimester_lead(int(im), TRIMESTERS[t])
        for im, t in zip(pos["issued_month"], pos["trimester"].astype(str))
    ]
    pos = pos[(pos["lead"] >= MIN_LEAD) & (pos["lead"] <= MAX_LEAD)].copy()
    pos["issued_year"] = [
        issued_year_for_season(int(sy), int(im), t)
        for sy, im, t in zip(pos["season_year"], pos["issued_month"], pos["trimester"].astype(str))
    ]

    keys = ["pcode", "issued_month", "trimester"]
    for c in ("pcode", "trimester"):
        pos[c] = pos[c].astype(str)
        skill[c] = skill[c].astype(str)
    df = pos.merge(skill[keys + ["pearson_r", "n_years"]], on=keys, how="left")

    shares = trimester_shares(clim.assign(pcode=clim["pcode"].astype(str)))
    shares["in_season_flat"] = shares["tri_share_annual"] >= FLAT_TRIMESTER_SHARE
    shares["in_season_app"] = shares["tri_share_annual"] >= APP_TRIMESTER_SHARE
    df = df.merge(
        shares[["pcode", "trimester", "tri_share_annual", "tri_mean_mm_day",
                "in_season_flat", "in_season_app"]],
        on=["pcode", "trimester"], how="left",
    )
    df = df.merge(units, on="pcode", how="left")
    missing_unit = df["iso3"].isna()
    if missing_unit.any():
        # A unit with skill rows but no climatology (no ERA5 rows at all) — keep it, it
        # simply never qualifies; fill identity from the skill file.
        ident = skill.drop_duplicates("pcode").set_index("pcode")["iso3"].astype(str)
        df.loc[missing_unit, "iso3"] = df.loc[missing_unit, "pcode"].map(ident)
        df.loc[missing_unit, "adm_level"] = level
    df["adm_level"] = df["adm_level"].astype(int)
    df = df[COLUMNS].sort_values(["iso3", "pcode", "issued_year", "issued_month", "trimester"])
    df = df.reset_index(drop=True)

    n_units = df.groupby("iso3", observed=True)["pcode"].nunique().rename("n_units_in_country")
    units = units.merge(n_units, left_on="iso3", right_index=True, how="left")
    has_skill = df.groupby("pcode")["pearson_r"].apply(lambda s: s.notna().any()).rename("has_skill")
    units = units.merge(has_skill, left_on="pcode", right_index=True, how="left")
    units["has_skill"] = units["has_skill"].fillna(False).astype(bool)
    return df, units.reset_index(drop=True)


def latest_issuance(df: pd.DataFrame) -> pd.DataFrame:
    """Rows of the most recent issuance (max issued_year, then max issued_month in it)."""
    iy = int(df["issued_year"].max())
    im = int(df.loc[df["issued_year"] == iy, "issued_month"].max())
    return df[(df["issued_year"] == iy) & (df["issued_month"] == im)].copy()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, choices=(0, 1, 2), default=1)
    ap.add_argument("--iso3", nargs="+", metavar="ISO3", help="Restrict to these countries")
    ap.add_argument("--no-upload", action="store_true",
                    help="Write to outputs/hdx_signal/ locally instead of the blob")
    args = ap.parse_args()
    iso3s = [i.upper() for i in args.iso3] if args.iso3 else None

    df, units = build(args.level, iso3s)
    latest = latest_issuance(df)
    iy, im = int(latest["issued_year"].iloc[0]), int(latest["issued_month"].iloc[0])
    print(f"[level {args.level}] {len(df):,} rows, {df['pcode'].nunique():,} units, "
          f"{df['iso3'].nunique()} countries; latest issuance {iy}-{im:02d} "
          f"({len(latest):,} rows)")

    sfx = SUFFIX[args.level]
    if args.no_upload or iso3s:
        out_dir = HERE.parent / "outputs" / "hdx_signal"
        out_dir.mkdir(parents=True, exist_ok=True)
        tag = ("_" + "_".join(iso3s)) if iso3s else ""
        for name, d in [(f"signal_inputs{sfx}{tag}", df),
                        (f"signal_inputs{sfx}_latest{tag}", latest),
                        (f"units{sfx}{tag}", units)]:
            d.to_parquet(out_dir / f"{name}.parquet", index=False)
            print(f"  wrote {out_dir / name}.parquet")
        if iso3s and not args.no_upload:
            print("  (country subsets are never uploaded — the blob tables are global)")
        return
    for name, d in [(f"signal_inputs{sfx}", df),
                    (f"signal_inputs{sfx}_latest", latest),
                    (f"units{sfx}", units)]:
        path = f"{OUT_PREFIX}/{name}.parquet"
        print(f"  uploading {len(d):,} rows -> {path}")
        stratus.upload_parquet_to_blob(d, path, stage="dev")
    print("Done.")


if __name__ == "__main__":
    main()
