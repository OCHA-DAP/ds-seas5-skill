from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, norm

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

    Applied to ALL SEAS5 years (including current forecast) using overlap stats.
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
    """Compute Pearson r, RMSE, and n from matched historical pairs.

    Returns None if fewer than MIN_YEARS overlapping years exist.
    """
    df = df_s.merge(df_e, on="season_year", how="inner")
    if len(df) < MIN_YEARS:
        return None

    errors = df["obs_mean"] - df["forecast_mean"]
    return {
        "rmse": float(np.sqrt((errors**2).mean())),
        "pearson_r": float(df[["forecast_mean", "obs_mean"]].corr().iloc[0, 1]),
        "n_years": len(df),
    }


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
    iso3: str,
    country_name: str,
    df_seas5: pd.DataFrame,
    df_era5: pd.DataFrame,
    progress: Any = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute skill for all 144 (issued_month × trimester) combinations.

    Pipeline:
      1. Aggregate to yearly trimester means
      2. log1p-transform → makes precipitation closer to Gaussian
      3. Normalize SEAS5 in log-space to match ERA5 mean and std
      4. Fit regression: E[log_obs | log_forecast] = (1-r)·μ_ERA5 + r·f
      5. Empirical sigma = std of actual regression residuals (not theoretical)
      6. Probability = Φ((T_log − μ_conditional) / σ_empirical)

    Returns:
        df_skill: one row per combo with skill metrics, probabilities, and RPs
        df_paired: historical (season_year, forecast_mean_log, obs_mean_log) per combo
    """
    skill_rows: list[dict] = []
    paired_rows: list[dict] = []

    for issued_month in range(1, 13):
        for trimester_name, valid_months in TRIMESTERS.items():
            df_s_raw = aggregate_seas5_trimester(df_seas5, issued_month, valid_months)
            df_e_raw = aggregate_era5_trimester(df_era5, valid_months)

            # log1p-transform (handles near-zero values; makes distribution more Gaussian)
            df_s_log = df_s_raw.assign(
                forecast_mean=np.log1p(df_s_raw["forecast_mean"].clip(lower=0))
            )
            df_e_log = df_e_raw.assign(
                obs_mean=np.log1p(df_e_raw["obs_mean"].clip(lower=0))
            )

            # Normalize SEAS5 in log-space
            df_s_norm = normalize_seas5(df_s_log, df_e_log)

            skill = compute_skill_metrics(df_s_norm, df_e_log)

            # ERA5 log-space stats
            era5_mean_log = float(df_e_log["obs_mean"].mean()) if not df_e_log.empty else None
            era5_std_log = float(df_e_log["obs_mean"].std(ddof=1)) if len(df_e_log) > 1 else None

            # Current (latest) forecast in log-space
            current_forecast_year = None
            current_forecast_mean_log = None
            is_predictive = False
            if not df_s_norm.empty:
                current_forecast_year = int(df_s_norm["season_year"].max())
                current_forecast_mean_log = float(
                    df_s_norm.loc[
                        df_s_norm["season_year"] == current_forecast_year, "forecast_mean"
                    ].iloc[0]
                )
                is_predictive = current_forecast_year not in df_e_log["season_year"].values

            # Skill + empirical regression sigma + probabilities
            sigma_empirical = None
            sigma_theoretical = None
            prob = None
            forecast_rp = None
            flood_rp = None
            prob_rp = None
            forecast_percentile = None
            hist_probs_series: pd.Series = pd.Series(dtype=float)
            hist_F: np.ndarray = np.array([])

            if skill is not None and era5_mean_log is not None and era5_std_log is not None:
                r = skill["pearson_r"]
                T_log = float(df_e_log["obs_mean"].quantile(1 / 3))

                # Theoretical sigma (bivariate log-normal assumption)
                sigma_theoretical = era5_std_log * float(np.sqrt(max(1 - r ** 2, 0)))

                # Empirical sigma from actual regression residuals — this is the
                # real test: deviations from sigma_theoretical indicate non-log-normality
                df_overlap = df_s_norm.merge(df_e_log, on="season_year", how="inner")
                E_hat = (1 - r) * era5_mean_log + r * df_overlap["forecast_mean"].values
                residuals = df_overlap["obs_mean"].values - E_hat
                sigma_empirical = float(residuals.std(ddof=1))
                sigma_use = max(sigma_empirical, 1e-9)

                # Historical probabilities (using empirical sigma)
                hist_F = df_overlap["forecast_mean"].values
                mu_hist = (1 - r) * era5_mean_log + r * hist_F
                hist_probs_arr = norm.cdf(T_log, loc=mu_hist, scale=sigma_use)
                hist_probs_series = pd.Series(
                    hist_probs_arr.astype(float), index=df_overlap["season_year"].values
                )

                if current_forecast_mean_log is not None:
                    mu_current = (1 - r) * era5_mean_log + r * current_forecast_mean_log
                    prob = float(norm.cdf(T_log, loc=mu_current, scale=sigma_use))
                    forecast_rp = empirical_rp(
                        current_forecast_mean_log, hist_F, higher_is_more_extreme=False
                    )
                    flood_rp = empirical_rp(
                        current_forecast_mean_log, hist_F, higher_is_more_extreme=True
                    )
                    if len(hist_probs_series) > 0:
                        prob_rp = empirical_rp(
                            prob, hist_probs_series.values, higher_is_more_extreme=True
                        )
                    if len(hist_F) > 0:
                        forecast_percentile = float(
                            100.0 * np.sum(hist_F <= current_forecast_mean_log) / len(hist_F)
                        )

            # Paired yearly rows (log-space values; notebook converts with expm1 for display)
            df_merged = df_s_norm.merge(df_e_log, on="season_year", how="outer")
            for _, row in df_merged.iterrows():
                yr = int(row["season_year"])
                paired_rows.append({
                    "pcode": pcode,
                    "iso3": iso3,
                    "country_name": country_name,
                    "issued_month": issued_month,
                    "trimester": trimester_name,
                    "season_year": yr,
                    "forecast_mean": float(row["forecast_mean"]) if pd.notna(row.get("forecast_mean")) else None,
                    "obs_mean": float(row["obs_mean"]) if pd.notna(row.get("obs_mean")) else None,
                    "hist_prob": float(hist_probs_series.loc[yr]) if yr in hist_probs_series.index else None,
                })

            lower_tercile_mm = (
                float(np.expm1(df_e_log["obs_mean"].quantile(1 / 3)))
                if not df_e_log.empty else None
            )

            skill_rows.append({
                "pcode": pcode,
                "iso3": iso3,
                "country_name": country_name,
                "issued_month": issued_month,
                "trimester": trimester_name,
                "n_years": skill["n_years"] if skill else None,
                "pearson_r": skill["pearson_r"] if skill else None,
                "rmse": skill["rmse"] if skill else None,
                "sigma": sigma_empirical,           # empirical regression residual std (log-space)
                "sigma_theoretical": sigma_theoretical,  # ERA5_std·√(1-r²) for comparison
                "era5_mean": era5_mean_log,          # mean of log1p(ERA5 obs)
                "era5_std": era5_std_log,            # std of log1p(ERA5 obs)
                "lower_tercile_mm": lower_tercile_mm,  # original units (mm/day)
                "current_forecast_year": current_forecast_year,
                "current_forecast_mean": current_forecast_mean_log,  # log-space
                "is_predictive": is_predictive,
                "prob_lower_tercile": prob,
                "forecast_rp": forecast_rp,
                "flood_rp": flood_rp,
                "prob_rp": prob_rp,
                "forecast_percentile": forecast_percentile,
            })

            if progress is not None:
                progress.update(1)

    return pd.DataFrame(skill_rows), pd.DataFrame(paired_rows)


def compute_roc_auc(df_paired: pd.DataFrame) -> pd.DataFrame:
    """ROC-AUC of the deterministic normalized forecast at 3yr and 10yr drought RP thresholds.

    Uses forecast_mean (normalized log-space; lower = drier) as predictor.
    Event labels are defined by percentile thresholds of obs_mean within each group:
      3yr RP  → obs in lowest ~33%  (100/3 th percentile)
      10yr RP → obs in lowest  10%

    Returns one row per (pcode, issued_month, trimester) with columns:
      roc_auc_3yr, roc_auc_10yr  (NaN when too few positive examples).
    """

    def _auc(f_vals: np.ndarray, labels: np.ndarray, higher_is_event: bool) -> float:
        pos_f = f_vals[labels == 1]
        neg_f = f_vals[labels == 0]
        if len(pos_f) < 2 or len(neg_f) < 2:
            return float("nan")
        # drought (lower tail): non-events should have higher forecasts than events
        # flood  (upper tail): events should have higher forecasts than non-events
        a, b = (pos_f, neg_f) if higher_is_event else (neg_f, pos_f)
        stat, _ = mannwhitneyu(a, b, alternative="greater")
        return float(stat / (len(pos_f) * len(neg_f)))

    rows: list[dict] = []
    for (pcode, im, tri), grp in df_paired.dropna(
        subset=["obs_mean", "forecast_mean"]
    ).groupby(["pcode", "issued_month", "trimester"]):
        obs = grp["obs_mean"].values
        f = grp["forecast_mean"].values
        rows.append({
            "pcode": pcode,
            "issued_month": im,
            "trimester": tri,
            "roc_auc_3yr":        _auc(f, (obs <= np.percentile(obs, 100 / 3)).astype(int), False),
            "roc_auc_10yr":       _auc(f, (obs <= np.percentile(obs, 10)).astype(int),       False),
            "roc_auc_3yr_upper":  _auc(f, (obs >= np.percentile(obs, 200 / 3)).astype(int),  True),
            "roc_auc_10yr_upper": _auc(f, (obs >= np.percentile(obs, 90)).astype(int),       True),
        })
    return pd.DataFrame(rows)
