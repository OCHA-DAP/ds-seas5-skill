from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.constants import MIN_YEARS, TRIMESTERS


def _assign_season_year(s: pd.Series, valid_months: list[int]) -> pd.Series:
    """Map a datetime Series to season_year, anchoring wrapping trimesters on months > 6."""
    is_wrapping = 1 in valid_months and 12 in valid_months
    if is_wrapping:
        return s.apply(lambda d: d.year if d.month > 6 else d.year - 1)
    return s.dt.year


def aggregate_seas5_trimester(
    df: pd.DataFrame,
    issued_month: int,
    valid_months: list[int],
) -> pd.DataFrame:
    """Aggregate SEAS5 to yearly trimester means for a specific issued month.

    Groups by issued_date.year (the forecast year), then shifts forward by 1 for
    non-wrapping trimesters where all valid months fall in the next calendar year
    (e.g. issued=Nov, valid=JFM).

    Returns DataFrame[season_year, forecast_mean].
    """
    is_wrapping = 1 in valid_months and 12 in valid_months
    mask = (df["issued_date"].dt.month == issued_month) & (
        df["valid_date"].dt.month.isin(valid_months)
    )
    df_filt = df[mask]
    if df_filt.empty:
        return pd.DataFrame({"season_year": pd.Series(dtype="int64"), "forecast_mean": pd.Series(dtype="float64")})

    df_yr = (
        df_filt.groupby(df_filt["issued_date"].dt.year)["mean"]
        .mean()
        .reset_index()
        .rename(columns={"issued_date": "season_year", "mean": "forecast_mean"})
    )
    df_yr["season_year"] = df_yr["season_year"].astype("int64")
    # Shift year when valid months are entirely in the next calendar year
    if not is_wrapping and min(valid_months) < issued_month:
        df_yr["season_year"] = df_yr["season_year"] + 1

    return df_yr


def aggregate_era5_trimester(
    df: pd.DataFrame,
    valid_months: list[int],
) -> pd.DataFrame:
    """Aggregate ERA5 to yearly trimester means. Only complete seasons are kept.

    Returns DataFrame[season_year, obs_mean].
    """
    is_wrapping = 1 in valid_months and 12 in valid_months
    df_filt = df[df["valid_date"].dt.month.isin(valid_months)].copy()
    df_filt["month"] = df_filt["valid_date"].dt.month
    df_filt["season_year"] = _assign_season_year(df_filt["valid_date"], valid_months).astype("int64")

    complete = (
        df_filt.groupby("season_year")["month"]
        .nunique()
        .loc[lambda x: x == len(valid_months)]
        .index
    )
    df_filt = df_filt[df_filt["season_year"].isin(complete)]

    return (
        df_filt.groupby("season_year")["mean"]
        .mean()
        .reset_index()
        .rename(columns={"mean": "obs_mean"})
    )


def compute_skill_metrics(
    df_s: pd.DataFrame,
    df_e: pd.DataFrame,
) -> dict[str, float] | None:
    """Compute bias, sigma, RMSE, and Pearson r from matched historical pairs.

    Returns None if fewer than MIN_YEARS overlapping years exist.
    """
    df = df_s.merge(df_e, on="season_year", how="inner")
    if len(df) < MIN_YEARS:
        return None

    errors = df["obs_mean"] - df["forecast_mean"]
    return {
        "bias": float(errors.mean()),
        "sigma": float(errors.std(ddof=1)),
        "rmse": float(np.sqrt((errors**2).mean())),
        "pearson_r": float(df[["forecast_mean", "obs_mean"]].corr().iloc[0, 1]),
        "n_years": len(df),
    }


def compute_prob_lower_tercile(
    df_e: pd.DataFrame,
    skill: dict[str, float],
    current_forecast_mean: float,
) -> float:
    """P(obs < lower ERA5 tercile) using a bias-corrected Gaussian error model.

    Models actual rainfall as N(F + bias, σ²) where F is the current forecast.
    """
    T = float(df_e["obs_mean"].quantile(1 / 3))
    mu = current_forecast_mean + skill["bias"]
    sigma = max(skill["sigma"], 1e-9)
    return float(norm.cdf(T, loc=mu, scale=sigma))


def run_all_combinations(
    pcode: str,
    df_seas5: pd.DataFrame,
    df_era5: pd.DataFrame,
    progress: Any = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute skill for all 144 (issued_month × trimester) combinations.

    Returns:
        df_skill: one row per combo with skill metrics and current forecast probability
        df_paired: historical (season_year, forecast_mean, obs_mean) per combo
    """
    skill_rows: list[dict] = []
    paired_rows: list[dict] = []

    for issued_month in range(1, 13):
        for trimester_name, valid_months in TRIMESTERS.items():
            df_s = aggregate_seas5_trimester(df_seas5, issued_month, valid_months)
            df_e = aggregate_era5_trimester(df_era5, valid_months)

            df_merged = df_s.merge(df_e, on="season_year", how="outer")
            for _, row in df_merged.iterrows():
                paired_rows.append({
                    "pcode": pcode,
                    "issued_month": issued_month,
                    "trimester": trimester_name,
                    "season_year": int(row["season_year"]),
                    "forecast_mean": float(row["forecast_mean"]) if pd.notna(row.get("forecast_mean")) else None,
                    "obs_mean": float(row["obs_mean"]) if pd.notna(row.get("obs_mean")) else None,
                })

            skill = compute_skill_metrics(df_s, df_e)

            current_forecast_year = None
            current_forecast_mean = None
            is_predictive = False
            if not df_s.empty:
                current_forecast_year = int(df_s["season_year"].max())
                current_forecast_mean = float(
                    df_s.loc[df_s["season_year"] == current_forecast_year, "forecast_mean"].iloc[0]
                )
                is_predictive = current_forecast_year not in df_e["season_year"].values

            lower_tercile_mm = float(df_e["obs_mean"].quantile(1 / 3)) if not df_e.empty else None
            prob = (
                compute_prob_lower_tercile(df_e, skill, current_forecast_mean)
                if skill is not None and current_forecast_mean is not None
                else None
            )

            skill_rows.append({
                "pcode": pcode,
                "issued_month": issued_month,
                "trimester": trimester_name,
                "n_years": skill["n_years"] if skill else None,
                "bias": skill["bias"] if skill else None,
                "sigma": skill["sigma"] if skill else None,
                "rmse": skill["rmse"] if skill else None,
                "pearson_r": skill["pearson_r"] if skill else None,
                "lower_tercile_mm": lower_tercile_mm,
                "current_forecast_year": current_forecast_year,
                "current_forecast_mean": current_forecast_mean,
                "is_predictive": is_predictive,
                "prob_lower_tercile": prob,
            })

            if progress is not None:
                progress.update(1)

    return pd.DataFrame(skill_rows), pd.DataFrame(paired_rows)
