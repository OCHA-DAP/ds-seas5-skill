"""HDX signal for SEAS5: per-unit conditions and the country roll-up.

The signal, in words: a country gets a signal for a trimester when at least ``frac`` of
its admin units (admin-1 by default) each satisfy, for that trimester at the current
issuance,

    * SEAS5 forecasts the trimester at a return period of at least ``rp`` years
      (dry: ``dry_rp``; wet: ``wet_rp``),
    * with hindcast skill (detrended Pearson r vs ERA5) of at least ``r_min``,
    * and the trimester is in season for that unit (its share of annual rainfall is at
      least ``season_share``; 0.25 = above a flat climatology).

The four numbers are the free parameters. Defaults are the September 2026 starting
point; everything is a column in the inputs table, so any of them can be swept.

Inputs are the tables written by pipeline/build_hdx_signal_inputs.py (one row per
unit × issuance × trimester, hindcast years included), see docs/dev-notes/hdx-signal-data.md.
"""

from typing import Literal

import pandas as pd

DEFAULTS = dict(frac=0.60, rp=5.0, r_min=0.30, season_share=0.25)


def unit_condition(
    df: pd.DataFrame,
    direction: Literal["dry", "wet"] = "dry",
    rp: float = DEFAULTS["rp"],
    r_min: float = DEFAULTS["r_min"],
    season_share: float = DEFAULTS["season_share"],
) -> pd.Series:
    """Boolean per row of the inputs table: does this unit × issuance × trimester qualify?

    Rows with no skill (``pearson_r`` NaN: fewer than 10 hindcast years, or a unit
    that misses every SEAS5 pixel) never qualify.
    """
    rp_col = "dry_rp" if direction == "dry" else "wet_rp"
    return (
        (df[rp_col] >= rp)
        & (df["pearson_r"] >= r_min)
        & (df["tri_share_annual"] >= season_share)
    ).fillna(False)


def country_signal(
    df: pd.DataFrame,
    direction: Literal["dry", "wet"] = "dry",
    frac: float = DEFAULTS["frac"],
    rp: float = DEFAULTS["rp"],
    r_min: float = DEFAULTS["r_min"],
    season_share: float = DEFAULTS["season_share"],
    denominator: Literal["all", "in_season"] = "all",
) -> pd.DataFrame:
    """Roll the unit condition up to countries.

    One row per (iso3, issued_year, issued_month, trimester) with the number of units,
    the number qualifying, their fraction and the ``signal`` flag (fraction ≥ ``frac``).

    ``denominator="all"`` counts every unit of the country that has a row for that
    issuance × trimester (i.e. every unit with zonal stats — the literal "fraction of
    the admin-1s"). ``"in_season"`` restricts the denominator to units for which the
    trimester is in season, so arid units cannot dilute a country's signal.
    """
    d = df.copy()
    d["qual"] = unit_condition(d, direction, rp, r_min, season_share)
    if denominator == "in_season":
        d = d[d["tri_share_annual"] >= season_share]
    keys = ["iso3", "issued_year", "issued_month", "trimester", "lead", "season_year"]
    out = (
        d.groupby(keys, observed=True)
        .agg(n_units=("pcode", "nunique"), n_qualifying=("qual", "sum"))
        .reset_index()
    )
    out["frac_qualifying"] = out["n_qualifying"] / out["n_units"]
    out["signal"] = out["frac_qualifying"] >= frac
    out["direction"] = direction
    return out
