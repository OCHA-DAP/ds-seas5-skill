"""Rainy-season ("in season") classification from an ERA5 monthly climatology.

One definition, one place. Every export and the HDX-signal inputs call this; the
thresholds are parameters so the same code serves the app's permissive default and
the stricter "flat climatology" rule.

A trimester is *in season* for a unit when its share of the unit's annual rainfall
is at least ``trimester_share``:

    share(pcode, tri) = 3 * mean(monthly_clim over the 3 months) / sum(monthly_clim over 12)

* ``APP_TRIMESTER_SHARE`` (0.15) — the app / static-site default: any trimester that
  carries at least 15 % of the year's rain.
* ``FLAT_TRIMESTER_SHARE`` (0.25) — "above a flat climatology": the trimester carries
  more than its 3/12 share, i.e. its mean monthly rainfall exceeds the annual monthly
  mean. Stricter; the HDX signal's starting point.

``month_share`` optionally also requires each of the three months to carry at least
that share of annual rainfall (the app default is 0 = off).
"""

import pandas as pd

from src.constants import TRIMESTERS

APP_TRIMESTER_SHARE: float = 0.15
FLAT_TRIMESTER_SHARE: float = 0.25
APP_MONTH_SHARE: float = 0.0


def monthly_clim_from_era5(era5: pd.DataFrame) -> pd.DataFrame:
    """Per-pcode monthly ERA5 climatology (mm/day) over the whole record.

    ``era5`` needs columns pcode, valid_date (datetime), mean. Returns
    DataFrame[pcode, month, mean_mm_day] — the schema of monthly_clim*.parquet.
    """
    return (
        era5.assign(month=era5["valid_date"].dt.month)
        .groupby(["pcode", "month"])["mean"].mean()
        .reset_index().rename(columns={"mean": "mean_mm_day"})
    )


def trimester_shares(monthly_clim: pd.DataFrame) -> pd.DataFrame:
    """Per (pcode, trimester): share of annual rainfall and the weakest month's share.

    Returns DataFrame[pcode, trimester, tri_mean_mm_day, annual_mm_day, tri_share_annual,
    min_month_share_annual]. ``annual_mm_day`` is the sum of the 12 monthly means (so
    a 'mm/day-months' total; only ratios are used downstream). Units with fewer than
    12 climatology months are dropped: a partial year cannot be classified.
    """
    mc = monthly_clim[["pcode", "month", "mean_mm_day"]].copy()
    n_months = mc.groupby("pcode")["month"].nunique()
    mc = mc[mc["pcode"].isin(n_months[n_months == 12].index)]
    annual = mc.groupby("pcode")["mean_mm_day"].sum().rename("annual_mm_day")
    mc = mc.merge(annual.reset_index(), on="pcode")
    mc["pct_annual"] = mc["mean_mm_day"] / mc["annual_mm_day"]
    out = []
    for tri, months in TRIMESTERS.items():
        g = mc[mc["month"].isin(months)].groupby("pcode")
        df = pd.DataFrame({
            "tri_mean_mm_day": g["mean_mm_day"].mean(),
            "min_month_share_annual": g["pct_annual"].min(),
        })
        df["annual_mm_day"] = annual.reindex(df.index)
        df["tri_share_annual"] = 3 * df["tri_mean_mm_day"] / df["annual_mm_day"]
        df["trimester"] = tri
        out.append(df.reset_index())
    cols = ["pcode", "trimester", "tri_mean_mm_day", "annual_mm_day",
            "tri_share_annual", "min_month_share_annual"]
    return pd.concat(out, ignore_index=True)[cols]


def compute_rainy_set(
    monthly_clim: pd.DataFrame,
    trimester_share: float = APP_TRIMESTER_SHARE,
    month_share: float = APP_MONTH_SHARE,
) -> set[tuple[str, str]]:
    """{(pcode, trimester)} that are in season at the given thresholds.

    Defaults reproduce the app / static-site rainy flag (trimester ≥ 15 % of annual,
    no per-month minimum). Pass ``trimester_share=FLAT_TRIMESTER_SHARE`` for the
    flat-climatology rule.
    """
    sh = trimester_shares(monthly_clim)
    ok = (sh["tri_share_annual"] >= trimester_share) & (
        sh["min_month_share_annual"].fillna(0) >= month_share
    )
    return set(zip(sh.loc[ok, "pcode"], sh.loc[ok, "trimester"]))
