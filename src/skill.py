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


def trimester_lead(issued_month: int, valid_months: list[int]) -> int:
    """Signed leadtime: months from the issue month to the trimester's first month.

    0–6 = the whole trimester is forecast; negative = the issuance falls inside the
    trimester (an in-season / mixed trimester: months before the issue month are already
    observed, the rest are forecast).
    """
    o = (valid_months[0] - issued_month) % 12
    return o if o <= 6 else o - 12


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


def aggregate_mixed_trimester(
    df_seas5: pd.DataFrame,
    df_era5: pd.DataFrame,
    issued_month: int,
    valid_months: list[int],
) -> pd.DataFrame:
    """Trimester means for in-season issuances (negative leads, e.g. JAS issued in Sep).

    Months before the issue month are taken from ERA5 (already observed at issuance:
    monthly ERA5 lands early the following month); the issue month and later come from
    this issuance's SEAS5. Each forecast month is bias-corrected against ERA5 for that
    calendar month — mean/std matched in log space over the overlap years — before the
    three months are averaged, since SEAS5's bias is month- and lead-specific and the
    trimester-level normalization can't fix a biased single month blended with obs.

    Returns DataFrame[season_year, forecast_mean] in original units (mm/day), the same
    contract as aggregate_seas5_trimester, so the downstream log1p / trimester
    normalization / detrending pipeline is unchanged.
    """
    empty = pd.DataFrame(
        {"season_year": pd.Series(dtype="int64"), "forecast_mean": pd.Series(dtype="float64")}
    )
    monthly: list[pd.DataFrame] = []
    for m in valid_months:
        o = (m - issued_month) % 12
        signed = o if o <= 6 else o - 12
        e_m = df_era5[df_era5["valid_date"].dt.month == m]
        e_ser = pd.DataFrame({
            "season_year": _assign_season_year(e_m["valid_date"], valid_months).astype("int64"),
            "value": e_m["mean"].values,
        })
        if signed < 0:  # observed month
            monthly.append(e_ser)
            continue
        # Forecast month: SEAS5 horizon < 12 months, so (issued month, valid month)
        # uniquely determines the leadtime — no explicit leadtime filter needed.
        f_m = df_seas5[
            (df_seas5["issued_date"].dt.month == issued_month)
            & (df_seas5["valid_date"].dt.month == m)
        ]
        if f_m.empty:
            return empty
        f_ser = pd.DataFrame({
            "season_year": _assign_season_year(f_m["valid_date"], valid_months).astype("int64"),
            "value": f_m["mean"].values,
        })
        # Per-month bias correction in log space over the overlap years.
        f_log = np.log1p(f_ser["value"].clip(lower=0))
        e_by_year = e_ser.assign(e_log=np.log1p(e_ser["value"].clip(lower=0)))
        ov = f_ser.assign(f_log=f_log).merge(
            e_by_year[["season_year", "e_log"]], on="season_year", how="inner"
        )
        if len(ov) >= 2:
            f_mu = ov["f_log"].mean()
            f_sd = max(ov["f_log"].std(ddof=1), 1e-9)
            e_mu = ov["e_log"].mean()
            e_sd = ov["e_log"].std(ddof=1)
            corrected = (f_log - f_mu) / f_sd * e_sd + e_mu
            f_ser = f_ser.assign(value=np.expm1(corrected).clip(lower=0))
        monthly.append(f_ser)

    allm = pd.concat([p.assign(_i=i) for i, p in enumerate(monthly)], ignore_index=True)
    piv = allm.pivot_table(index="season_year", columns="_i", values="value", aggfunc="mean")
    piv = piv.reindex(columns=range(len(valid_months))).dropna()  # complete trimesters only
    if piv.empty:
        return empty
    return piv.mean(axis=1).rename("forecast_mean").reset_index()


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
        rank = int(np.sum(hist > current)) + 1
    else:
        rank = int(np.sum(hist < current)) + 1
    return (n + 1) / rank


def season_year_for(
    issued_month: int,
    issued_year: int,
    valid_months: list[int],
) -> int:
    """Forward map (issued_month, issued_year, trimester) -> season_year.

    Inverse of the season_year assignment in aggregate_seas5_trimester /
    aggregate_mixed_trimester. Wrapping trimesters (contain both Dec and Jan) anchor on
    December; non-wrapping trimesters use the year of the last future (<=6 ahead) month.
    For in-season issuances (negative lead) the issue month falls inside the trimester:
    non-wrapping → same calendar year; wrapping → the anchor (Dec) year, which is the
    issue year only while the issue month is still on the >6 side of the wrap.
    """
    is_wrap = 12 in valid_months and 1 in valid_months
    if trimester_lead(issued_month, valid_months) < 0:
        if is_wrap:
            return issued_year if issued_month > 6 else issued_year - 1
        return issued_year
    if is_wrap:
        return issued_year
    future_offs = [o for m in valid_months if 1 <= (o := (m - issued_month) % 12) <= 6]
    if not future_offs:
        return issued_year
    last_cal = ((issued_month - 1 + max(future_offs)) % 12) + 1
    return issued_year + (1 if last_cal < issued_month else 0)


def forecast_metrics_for_year(
    combo_df: pd.DataFrame,
    season_year: int,
) -> dict[str, float | bool | None]:
    """Recompute a single forecast's position metrics for a chosen season_year.

    `combo_df` is the paired_yearly rows for one (pcode, issued_month, trimester),
    with columns season_year, forecast_mean (normalized log-space), obs_mean. The
    historical forecast distribution is the overlap years (forecast & obs both present),
    matching how the pipeline computes the latest forecast at run_all_combinations.

    Returns forecast_mean / forecast_percentile / forecast_rp / flood_rp / is_predictive,
    all None/NaN if the chosen year has no forecast.
    """
    hist_F = combo_df.loc[
        combo_df["forecast_mean"].notna() & combo_df["obs_mean"].notna(), "forecast_mean"
    ].values
    sel = combo_df[combo_df["season_year"] == season_year]
    if sel.empty or pd.isna(sel["forecast_mean"].iloc[0]) or len(hist_F) == 0:
        return {
            "forecast_mean": None,
            "forecast_percentile": None,
            "forecast_rp": None,
            "flood_rp": None,
            "is_predictive": False,
        }
    fc = float(sel["forecast_mean"].iloc[0])
    return {
        "forecast_mean": fc,
        "forecast_percentile": float(100.0 * np.sum(hist_F <= fc) / len(hist_F)),
        "forecast_rp": empirical_rp(fc, hist_F, higher_is_more_extreme=False),
        "flood_rp": empirical_rp(fc, hist_F, higher_is_more_extreme=True),
        "is_predictive": bool(pd.isna(sel["obs_mean"].iloc[0])),
    }


def run_all_combinations(
    pcode: str,
    iso3: str,
    country_name: str,
    df_seas5: pd.DataFrame,
    df_era5: pd.DataFrame,
    progress: Any = None,
    detrend: bool = False,
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
            # In-season (mixed) trimesters — issuance falls inside the trimester, so the
            # already-observed months come from ERA5 and only the rest is forecast.
            if trimester_lead(issued_month, valid_months) in (-1, -2):
                df_s_raw = aggregate_mixed_trimester(
                    df_seas5, df_era5, issued_month, valid_months
                )
            else:
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

            if detrend:
                _hist_yrs = sorted(set(df_e_log["season_year"]) & set(df_s_norm["season_year"]))
                if len(_hist_yrs) >= MIN_YEARS:
                    _x_hist = np.array(_hist_yrs, dtype=float)
                    _A_dt = np.column_stack([_x_hist, np.ones(len(_x_hist))])

                    # Detrend forecast_mean: fit on overlap, apply to all (incl. current year)
                    _fc_hist = (
                        df_s_norm[df_s_norm["season_year"].isin(_hist_yrs)]
                        .sort_values("season_year")["forecast_mean"].values
                    )
                    _a, _b = np.linalg.lstsq(_A_dt, _fc_hist, rcond=None)[0]
                    _x_s = df_s_norm["season_year"].values.astype(float)
                    df_s_norm = df_s_norm.copy()
                    df_s_norm["forecast_mean"] = (
                        df_s_norm["forecast_mean"].values - (_a * _x_s + _b) + _fc_hist.mean()
                    )

                    # Detrend obs_mean: fit on overlap, apply to all ERA5 years
                    _obs_hist = (
                        df_e_log[df_e_log["season_year"].isin(_hist_yrs)]
                        .sort_values("season_year")["obs_mean"].values
                    )
                    _a, _b = np.linalg.lstsq(_A_dt, _obs_hist, rcond=None)[0]
                    _x_e = df_e_log["season_year"].values.astype(float)
                    df_e_log = df_e_log.copy()
                    df_e_log["obs_mean"] = (
                        df_e_log["obs_mean"].values - (_a * _x_e + _b) + _obs_hist.mean()
                    )

            skill = compute_skill_metrics(df_s_norm, df_e_log)

            # ERA5 log-space stats. Full ERA5 record, whereas normalize_seas5 matched
            # mean/std over the SEAS5∩ERA5 overlap years — in practice identical, since
            # both records start in 1981 (obs years ⊆ forecast years), so the overlap IS
            # the full obs record. Would only diverge if ERA5 ever predated the hindcast.
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
