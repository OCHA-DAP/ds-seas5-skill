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

    Returns DataFrame[season_year, forecast_mean].
    """
    is_wrapping = 1 in valid_months and 12 in valid_months
    mask = (df["issued_date"].dt.month == issued_month) & (
        df["valid_date"].dt.month.isin(valid_months)
    )
    df_filt = df[mask]
    if df_filt.empty:
        return pd.DataFrame(
            {"season_year": pd.Series(dtype="int64"), "forecast_mean": pd.Series(dtype="float64")}
        )

    df_yr = (
        df_filt.groupby(df_filt["issued_date"].dt.year)["mean"]
        .mean()
        .reset_index()
        .rename(columns={"issued_date": "season_year", "mean": "forecast_mean"})
    )
    df_yr["season_year"] = df_yr["season_year"].astype("int64")
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


def normalize_seas5(df_s: pd.DataFrame, df_e: pd.DataFrame) -> pd.DataFrame:
    """Scale SEAS5 so its mean and std match ERA5 over the historical overlap.

    Transformation is fit on overlap years only, then applied to ALL SEAS5 years
    (including the current forecast). After normalization:
      - mean(SEAS5_norm[overlap]) = mean(ERA5[overlap])  →  bias ≡ 0
      - std(SEAS5_norm[overlap])  = std(ERA5[overlap])   →  same spread as obs
    Returns a copy of df_s with forecast_mean replaced by the normalized values.
    """
    df_overlap = df_s.merge(df_e, on="season_year", how="inner")
    if len(df_overlap) < 2:
        return df_s.copy()

    s_mean = df_overlap["forecast_mean"].mean()
    s_std = max(df_overlap["forecast_mean"].std(ddof=1), 1e-9)
    e_mean = df_overlap["obs_mean"].mean()
    e_std = df_overlap["obs_mean"].std(ddof=1)

    df_out = df_s.copy()
    df_out["forecast_mean"] = (df_s["forecast_mean"] - s_mean) / s_std * e_std + e_mean
    return df_out


def compute_skill_metrics(
    df_s: pd.DataFrame,
    df_e: pd.DataFrame,
) -> dict[str, float] | None:
    """Compute bias, sigma, RMSE, and Pearson r from matched historical pairs.

    Returns None if fewer than MIN_YEARS overlapping years exist.
    When called on normalized SEAS5, bias will be ~0 by construction.
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
    """P(obs < lower ERA5 tercile) using the Gaussian error model.

    After normalization bias ≈ 0, so the bell curve peaks at current_forecast_mean.
    """
    T = float(df_e["obs_mean"].quantile(1 / 3))
    mu = current_forecast_mean + skill["bias"]
    sigma = max(skill["sigma"], 1e-9)
    return float(norm.cdf(T, loc=mu, scale=sigma))


def compute_hist_probs(
    df_s_norm: pd.DataFrame,
    df_e: pd.DataFrame,
    sigma: float,
) -> pd.Series:
    """P(lower tercile) for each historical overlap year using the same Gaussian model.

    Returns a Series indexed by season_year.
    """
    T = float(df_e["obs_mean"].quantile(1 / 3))
    df = df_s_norm.merge(df_e, on="season_year", how="inner")
    probs = norm.cdf(T, loc=df["forecast_mean"].values, scale=max(sigma, 1e-9))
    return pd.Series(probs, index=df["season_year"].values)


def empirical_rp(
    current: float,
    hist: np.ndarray,
    higher_is_more_extreme: bool,
) -> float:
    """Weibull empirical return period of current among historical values.

    higher_is_more_extreme=True  → high current = rare (e.g. high drought probability)
    higher_is_more_extreme=False → low current = rare (e.g. low/dry forecast)
    """
    n = len(hist)
    if n == 0 or np.isnan(current):
        return float("nan")
    if higher_is_more_extreme:
        rank = max(int(np.sum(hist >= current)), 1)
    else:
        rank = max(int(np.sum(hist <= current)), 1)
    return (n + 1) / rank


def run_all_combinations(
    pcode: str,
    country_name: str,
    df_seas5: pd.DataFrame,
    df_era5: pd.DataFrame,
    progress: Any = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute skill for all 144 (issued_month × trimester) combinations.

    SEAS5 is normalized to ERA5 space before any skill calculation.

    Returns:
        df_skill: one row per combo with skill metrics, probabilities, and RPs
        df_paired: historical (season_year, forecast_mean, obs_mean, hist_prob) per combo
    """
    skill_rows: list[dict] = []
    paired_rows: list[dict] = []

    for issued_month in range(1, 13):
        for trimester_name, valid_months in TRIMESTERS.items():
            df_s_raw = aggregate_seas5_trimester(df_seas5, issued_month, valid_months)
            df_e = aggregate_era5_trimester(df_era5, valid_months)

            # Normalize SEAS5 to ERA5 space before all further calculations
            df_s = normalize_seas5(df_s_raw, df_e)

            skill = compute_skill_metrics(df_s, df_e)

            # Historical probabilities and overlap forecasts for RP calculation
            hist_probs_series: pd.Series = pd.Series(dtype=float)
            hist_F: np.ndarray = np.array([])
            if skill is not None:
                hist_probs_series = compute_hist_probs(df_s, df_e, skill["sigma"])
                overlap_years = set(df_e["season_year"].values)
                hist_F = df_s[df_s["season_year"].isin(overlap_years)]["forecast_mean"].values

            # Current forecast (normalized value)
            current_forecast_year = None
            current_forecast_mean = None
            is_predictive = False
            if not df_s.empty:
                current_forecast_year = int(df_s["season_year"].max())
                current_forecast_mean = float(
                    df_s.loc[df_s["season_year"] == current_forecast_year, "forecast_mean"].iloc[0]
                )
                is_predictive = current_forecast_year not in df_e["season_year"].values

            # Probability and RPs
            lower_tercile_mm = float(df_e["obs_mean"].quantile(1 / 3)) if not df_e.empty else None
            prob = None
            forecast_rp = None
            prob_rp = None

            if skill is not None and current_forecast_mean is not None:
                prob = compute_prob_lower_tercile(df_e, skill, current_forecast_mean)
                forecast_rp = empirical_rp(current_forecast_mean, hist_F, higher_is_more_extreme=False)
                if len(hist_probs_series) > 0:
                    prob_rp = empirical_rp(prob, hist_probs_series.values, higher_is_more_extreme=True)

            # Paired yearly rows (one per season_year, outer join so current year included)
            df_merged = df_s.merge(df_e, on="season_year", how="outer")
            for _, row in df_merged.iterrows():
                yr = int(row["season_year"])
                hist_prob_val = (
                    float(hist_probs_series.loc[yr])
                    if yr in hist_probs_series.index
                    else None
                )
                paired_rows.append({
                    "pcode": pcode,
                    "country_name": country_name,
                    "issued_month": issued_month,
                    "trimester": trimester_name,
                    "season_year": yr,
                    "forecast_mean": float(row["forecast_mean"]) if pd.notna(row.get("forecast_mean")) else None,
                    "obs_mean": float(row["obs_mean"]) if pd.notna(row.get("obs_mean")) else None,
                    "hist_prob": hist_prob_val,
                })

            skill_rows.append({
                "pcode": pcode,
                "country_name": country_name,
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
                "forecast_rp": forecast_rp,
                "prob_rp": prob_rp,
            })

            if progress is not None:
                progress.update(1)

    return pd.DataFrame(skill_rows), pd.DataFrame(paired_rows)
