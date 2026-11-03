"""Export the data behind the site's unlisted Admin explorer tab (`#explorer`).

The explorer is the static port of the old Azure marimo app's per-country section
(analysis/prob_alerts.py) and the admin-level picker of the ds-seas5-viz explorer:
pick a country, an admin-1 or an admin-2 unit and see, for the LATEST issuance,

* every valid trimester's forecast percentile and skill (Pearson r), raw and detrended;
* the hindcast scatter (normalised SEAS5 vs ERA5, one point per year) for any of them;
* the forecast and observed series with their linear trends;
* the unit's skill for every issue month (leadtime x trimester heatmap) and its
  monthly ERA5 climatology.

Inputs (dev blob, written by compute_skill{,_adm1,_adm2}.py and compute_monthly_clim.py):
skill_stats{,_detrended}{,_adm1,_adm2}.parquet, paired_yearly{,_adm1,_adm2}.parquet and
monthly_clim{,_adm1,_adm2}.parquet. The issuance is the one docs/data/forecast.json shows
(export_static_site.py runs first), so the tab can never claim a different vintage from
the rest of the site; verify_site_data.py checks that.

Size. Only the latest issue month's yearly pairs are shipped, and only the RAW ones, as
log1p(mm/day) x 1e4 integers: the detrended series are re-derived in the browser with the
pipeline's own fit (docs/explorer_math.js, `detrend`; checked against
paired_yearly_detrended*.parquet by pipeline/check_explorer_detrend.py). The scalar stats
(r, percentile, return periods) are the pipeline's, raw and detrended, never recomputed.

Output (docs/data/explorer/, part of the site bundle; ~15 MB for every unit at all
three levels — gzipped, ~45 MB as plain JSON, which the 1 GB Pages limit cannot spare
next to the ENSO slides; the browser inflates with DecompressionStream):
    index.json                 issuance, trimesters, thresholds, countries (+ has_adm2)
    <ISO3>.json.gz             the country (admin 0) + every admin-1 unit
    adm2/<ADM1 pcode>.json.gz  the admin-2 units of one admin-1 unit

Run:  uv run python pipeline/export_explorer_site.py
      uv run python pipeline/export_explorer_site.py --iso3 ETH SOM   # a quick partial build
"""

import argparse
import calendar
import gzip
import io
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import pyarrow.parquet as pq
from scipy.stats import linregress

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.constants import PROJECT_PREFIX, TRIMESTERS  # noqa: E402
from src.skill import season_year_for, trimester_lead  # noqa: E402
from export_static_site import (  # noqa: E402
    SKILL_LEADS, THRESHOLDS, _min_signed, _tri_label, _tri_valid, compute_rainy_set,
)

DOCS_DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
OUT = DOCS_DATA / "explorer"
SUFFIX = {0: "", 1: "_adm1", 2: "_adm2"}
TRI_NAMES = list(TRIMESTERS)          # calendar order (heatmap columns)
SCALE = 10_000                        # log1p values stored as round(x * SCALE)
SKILL_COLS = ["pcode", "iso3", "country_name", "issued_month", "trimester", "n_years",
              "pearson_r", "current_forecast_year", "forecast_percentile",
              "forecast_rp", "flood_rp"]


def _blob(name: str) -> str:
    return f"{PROJECT_PREFIX}/processed/{name}"


def load_skill(level: int, detrended: bool) -> pd.DataFrame:
    name = f"skill_stats{'_detrended' if detrended else ''}{SUFFIX[level]}.parquet"
    return stratus.load_parquet_from_blob(_blob(name), stage="dev")[SKILL_COLS]


def load_paired(level: int, issued_month: int) -> pd.DataFrame:
    """Raw paired_yearly rows for one issue month only — read at the Arrow level with a
    row filter: the admin-2 file is ~35 M rows and must not round-trip through pandas."""
    data = stratus.load_blob_data(_blob(f"paired_yearly{SUFFIX[level]}.parquet"), stage="dev")
    tbl = pq.read_table(
        io.BytesIO(data),
        columns=["pcode", "issued_month", "trimester", "season_year",
                 "forecast_mean", "obs_mean"],
        filters=[("issued_month", "=", issued_month)],
    )
    del data
    return tbl.to_pandas()


def load_clim(level: int) -> pd.DataFrame:
    return stratus.load_parquet_from_blob(
        _blob(f"monthly_clim{SUFFIX[level]}.parquet"), stage="dev")


def _num(v, nd: int = 2):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _enc(v):
    return None if v is None or pd.isna(v) else int(round(float(v) * SCALE))


def _r100(v):
    return None if v is None or pd.isna(v) else int(round(float(v) * 100))


def r_matrix(sk: pd.DataFrame) -> list[list[int | None]]:
    """leadtime x trimester Pearson r (x100), same layout as skill_matrix.json."""
    lut = sk.set_index(["issued_month", "trimester"])["pearson_r"].to_dict()
    rows = []
    for lead in SKILL_LEADS:
        row = []
        for tri in TRI_NAMES:
            im = ((TRIMESTERS[tri][0] - lead - 1) % 12) + 1
            row.append(_r100(lut.get((im, tri))))
        rows.append(row)
    return rows


def _trend(years: np.ndarray, log_vals: np.ndarray) -> list | None:
    """Linear trend of the series in mm/day (as the old app plotted it):
    [slope mm/day per decade, p-value]."""
    ok = ~np.isnan(log_vals)
    if ok.sum() < 10:
        return None
    lr = linregress(years[ok], np.expm1(log_vals[ok]))
    return [round(float(lr.slope) * 10, 4), round(float(lr.pvalue), 4)]


def _stats(row: pd.Series | None, current: bool) -> dict | None:
    if row is None:
        return None
    out = {"r": _num(row["pearson_r"], 3),
           "n": None if pd.isna(row["n_years"]) else int(row["n_years"])}
    if current:
        out.update({
            "pct": _num(row["forecast_percentile"], 1),
            "rp_dry": _num(row["forecast_rp"], 2),
            "rp_wet": _num(row["flood_rp"], 2),
        })
    return out


def build_unit(
    pcode: str,
    name: str,
    sk_raw: pd.DataFrame,
    sk_dt: pd.DataFrame,
    paired: pd.DataFrame,
    clim: pd.Series,
    rainy_set: set,
    issued_month: int,
    issued_year: int,
    valid_tris: list[str],
) -> dict:
    """One unit's record. `sk_*` = all of the unit's skill rows (every issue month);
    `paired` = its raw paired rows at `issued_month`; `clim` = month -> mm/day."""
    raw_at = sk_raw[sk_raw["issued_month"] == issued_month].set_index("trimester")
    dt_at = sk_dt[sk_dt["issued_month"] == issued_month].set_index("trimester")
    tris: dict[str, dict] = {}
    for tri in valid_tris:
        g = paired[paired["trimester"] == tri].sort_values("season_year")
        if g.empty:
            continue
        sy = season_year_for(issued_month, issued_year, TRIMESTERS[tri])
        y0, y1 = int(g["season_year"].min()), int(g["season_year"].max())
        years = np.arange(y0, y1 + 1)
        fc = g.set_index("season_year")["forecast_mean"].reindex(years).to_numpy(float)
        obs = g.set_index("season_year")["obs_mean"].reindex(years).to_numpy(float)
        # A current forecast exists only if THIS issuance's season year has a value; a
        # pipeline run before the in-season ERA5 month landed leaves last year's vintage
        # as the newest row (see export_static_site.py, stale_idx) — never show that.
        has_cur = y0 <= sy <= y1 and not np.isnan(fc[sy - y0])
        rr = raw_at.loc[tri] if tri in raw_at.index else None
        rd = dt_at.loc[tri] if tri in dt_at.index else None
        # The pipeline's "current" stats belong to its newest season year; they are this
        # issuance's only if that year is `sy`.
        cur_raw = has_cur and rr is not None and rr["current_forecast_year"] == sy
        cur_dt = has_cur and rd is not None and rd["current_forecast_year"] == sy
        hist = ~np.isnan(fc) & ~np.isnan(obs)
        if has_cur:
            hist[sy - y0] = False
        tris[tri] = {
            "y0": y0,
            "fc": [_enc(v) for v in fc],
            "obs": [_enc(v) for v in obs],
            "cur": sy if has_cur else None,
            "raw": _stats(rr, cur_raw),
            "dt": _stats(rd, cur_dt),
            "trend": {"fc": _trend(years[hist], fc[hist]),
                      "obs": _trend(years[~np.isnan(obs)], obs[~np.isnan(obs)])},
        }
    return {
        "name": name,
        "clim_monthly": [_num(clim.get(m), 3) for m in range(1, 13)],
        "rainy": [(pcode, t) in rainy_set for t in TRI_NAMES],
        "r_raw": r_matrix(sk_raw),
        "r_dt": r_matrix(sk_dt),
        "tri": tris,
    }


def adm1_parent(adm2_pcodes: list[str], adm1_pcodes: set[str]) -> dict[str, str]:
    """adm2 pcode -> its admin-1 pcode by longest pcode prefix (100 % match on the
    current tables); a miss aborts rather than silently dropping a unit."""
    out, missing = {}, []
    for p in adm2_pcodes:
        par = next((p[:n] for n in range(len(p) - 1, 1, -1) if p[:n] in adm1_pcodes), None)
        if par is None:
            missing.append(p)
        else:
            out[p] = par
    if missing:
        raise SystemExit(f"{len(missing)} admin-2 pcodes have no admin-1 parent: {missing[:10]}")
    return out


def _dump(path: Path, obj) -> int:
    """Write compact JSON; `*.json.gz` paths are gzipped (mtime 0, so a rebuild of the
    same data is byte-identical and the bundle hash does not churn). Returns bytes on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if path.suffix == ".gz":
        raw = gzip.compress(raw, compresslevel=9, mtime=0)
    path.write_bytes(raw)
    return len(raw)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--iso3", nargs="+", help="only these countries (a partial build; "
                    "the index still lists only what was built)")
    args = ap.parse_args()

    fc_meta = json.loads((DOCS_DATA / "forecast.json").read_text())
    issued_year, issued_month = int(fc_meta["issued_year"]), int(fc_meta["issued_month"])
    valid_tris = sorted(
        [t for t, m in TRIMESTERS.items() if _tri_valid(m, issued_month)],
        key=lambda t: _min_signed(TRIMESTERS[t], issued_month),
    )
    print(f"Issuance {calendar.month_name[issued_month]} {issued_year}; trimesters {valid_tris}")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    countries: dict[str, dict] = {}       # iso3 -> {"name", "pcode", "units": {...}, "adm1": [...]}
    adm2_files: dict[str, dict] = {}      # adm1 pcode -> {pcode: unit}
    adm1_names: dict[str, str] = {}       # exported admin-1 units
    adm1_all: set[str] = set()            # every admin-1 pcode, exported or skipped
    adm1_iso3: dict[str, str] = {}
    sizes = {0: 0, 1: 0, 2: 0}
    counts = {0: 0, 1: 0, 2: 0}
    skipped = {0: 0, 1: 0, 2: 0}

    for level in (0, 1, 2):
        print(f"Level {level}: loading skill, paired ({calendar.month_abbr[issued_month]}), clim...")
        sk_raw = load_skill(level, False)
        sk_dt = load_skill(level, True)
        if args.iso3:
            sk_raw = sk_raw[sk_raw["iso3"].isin(args.iso3)]
            sk_dt = sk_dt[sk_dt["iso3"].isin(args.iso3)]
        if sk_raw.empty:
            continue
        paired = load_paired(level, issued_month)
        paired = paired[paired["pcode"].isin(set(sk_raw["pcode"]))]
        clim = load_clim(level)
        clim = clim[clim["pcode"].isin(set(sk_raw["pcode"]))]
        rainy_set = compute_rainy_set(clim)
        clim_by = {p: g.set_index("month")["mean_mm_day"] for p, g in clim.groupby("pcode")}
        raw_by = dict(tuple(sk_raw.groupby("pcode")))
        dt_by = dict(tuple(sk_dt.groupby("pcode")))
        paired_by = dict(tuple(paired.groupby("pcode")))
        meta = sk_raw.drop_duplicates("pcode").set_index("pcode")[["iso3", "country_name"]]

        if level == 2:
            parent = adm1_parent(list(meta.index), adm1_all)
        if level == 1:
            adm1_all = set(meta.index)

        for pcode, (iso3, name) in meta.iterrows():
            if pcode not in dt_by:
                continue
            unit = build_unit(
                pcode, str(name), raw_by[pcode], dt_by[pcode],
                paired_by.get(pcode, paired.iloc[0:0]), clim_by.get(pcode, pd.Series(dtype=float)),
                rainy_set, issued_month, issued_year, valid_tris,
            )
            # No skill anywhere = no ERA5 for the unit (small islands): nothing to show,
            # and the Map / Skill tabs skip these too (export_static_site.build_skill_matrix).
            if all(v is None for row in unit["r_raw"] + unit["r_dt"] for v in row):
                skipped[level] += 1
                continue
            counts[level] += 1
            if level == 0:
                countries[iso3] = {"iso3": iso3, "name": str(name), "pcode": pcode,
                                   "units": {pcode: unit}, "adm1": []}
            elif level == 1:
                if iso3 not in countries:
                    print(f"  WARN admin-1 {pcode} ({iso3}) has no admin-0 row; skipped")
                    continue
                countries[iso3]["units"][pcode] = unit
                countries[iso3]["adm1"].append([pcode, str(name), False])
                adm1_names[pcode] = str(name)
                adm1_iso3[pcode] = iso3
            else:
                if parent[pcode] not in adm1_names:
                    print(f"  WARN admin-2 {pcode} has skill but its admin-1 {parent[pcode]} "
                          "was skipped (no skill); skipped")
                    continue
                adm2_files.setdefault(parent[pcode], {})[pcode] = unit
        del sk_raw, sk_dt, paired, raw_by, dt_by, paired_by

    for par, units in adm2_files.items():
        sizes[2] += _dump(OUT / "adm2" / f"{par}.json.gz", {
            "parent": par, "iso3": adm1_iso3[par],
            "units": dict(sorted(units.items(), key=lambda kv: kv[1]["name"])),
        })
    index_countries = []
    for iso3, c in sorted(countries.items(), key=lambda kv: kv[1]["name"]):
        c["adm1"].sort(key=lambda a: a[1])
        for a in c["adm1"]:
            a[2] = a[0] in adm2_files
        n = _dump(OUT / f"{iso3}.json.gz", c)
        sizes[0] += n                       # (country file = admin 0 + admin 1)
        index_countries.append({"iso3": iso3, "name": c["name"], "n_adm1": len(c["adm1"]),
                                "adm2": any(a[2] for a in c["adm1"])})

    index = {
        "issued_year": issued_year,
        "issued_month": issued_month,
        "issued_label": f"{calendar.month_name[issued_month]} {issued_year}",
        "default_trimester": fc_meta.get("default_trimester"),
        "trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t]),
                        "lead": trimester_lead(issued_month, TRIMESTERS[t]),
                        "season_year": season_year_for(issued_month, issued_year, TRIMESTERS[t])}
                       for t in valid_tris],
        "all_trimesters": [{"key": t, "label": _tri_label(TRIMESTERS[t])} for t in TRI_NAMES],
        "leads": SKILL_LEADS,
        "thresholds": THRESHOLDS,
        "scale": SCALE,
        "countries": index_countries,
    }
    _dump(OUT / "index.json", index)
    print(f"Units: adm0 {counts[0]}, adm1 {counts[1]}, adm2 {counts[2]} "
          f"(skipped, no skill anywhere: {skipped[0]} / {skipped[1]} / {skipped[2]})")
    print(f"Bytes on disk (gzipped): country files (adm0+adm1) {sizes[0] / 1e6:.1f} MB in "
          f"{len(countries)} files, adm2 {sizes[2] / 1e6:.1f} MB in {len(adm2_files)} files -> {OUT}")


if __name__ == "__main__":
    main()
