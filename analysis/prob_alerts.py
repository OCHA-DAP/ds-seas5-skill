import marimo

__generated_with = "0.23.3"
app = marimo.App(app_title="SEAS5 Skill — Probabilistic Drought Alerts")


@app.cell
def _():
    import calendar

    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np
    import ocha_stratus as stratus
    import pandas as pd
    from scipy.stats import norm

    return calendar, mo, norm, np, pd, plt, stratus


@app.cell
def _():
    from src.constants import PROJECT_PREFIX, TRIMESTERS

    TRIMESTER_NAMES = list(TRIMESTERS.keys())
    return PROJECT_PREFIX, TRIMESTER_NAMES


@app.cell
def _(PROJECT_PREFIX, stratus):
    df_skill = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats.parquet", stage="dev"
    )
    df_paired = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/paired_yearly.parquet", stage="dev"
    )
    return df_paired, df_skill


@app.cell
def _(TRIMESTER_NAMES, calendar, mo):
    issued_month_dd = mo.ui.dropdown(
        options={calendar.month_abbr[m]: m for m in range(1, 13)},
        label="Issued month:",
        value=calendar.month_abbr[4],
    )
    trimester_dd = mo.ui.dropdown(
        options=TRIMESTER_NAMES,
        label="Valid trimester:",
        value="JAS",
    )
    mo.hstack([issued_month_dd, trimester_dd], justify="start")
    return issued_month_dd, trimester_dd


@app.cell
def _(issued_month_dd, trimester_dd):
    issued_month = issued_month_dd.value
    trimester = trimester_dd.value
    return issued_month, trimester


@app.cell
def _(mo):
    mo.md("""
    ## Ranked drought alert table

    Countries ranked by skill-adjusted probability of below-normal rainfall.
    **⚠** = negative historical correlation (forecast anti-correlated; probability unreliable).
    `fcst_pctile_%` = percentile of current forecast among historical (low = dry forecast).
    Same low percentile + higher Pearson r → higher drought probability, as expected.
    """)
    return


@app.cell
def _(df_skill, issued_month, mo, pd, trimester):
    _df_rank = (
        df_skill[
            (df_skill["issued_month"] == issued_month)
            & (df_skill["trimester"] == trimester)
            & df_skill["prob_lower_tercile"].notna()
        ]
        .sort_values("prob_lower_tercile", ascending=False)
        .copy()
    )
    _df_rank.insert(0, "rank", range(1, len(_df_rank) + 1))
    _df_rank["neg_skill"] = _df_rank["pearson_r"].apply(
        lambda x: "⚠" if pd.notna(x) and x < 0 else ""
    )
    _df_rank["prob_%"] = (_df_rank["prob_lower_tercile"] * 100).round(1)
    _df_rank["pearson_r"] = _df_rank["pearson_r"].round(3)
    _df_rank["n_years"] = _df_rank["n_years"].astype(pd.Int64Dtype())
    _df_rank["current_forecast_year"] = _df_rank["current_forecast_year"].astype(
        pd.Int64Dtype()
    )
    _df_rank["forecast_rp"] = _df_rank["forecast_rp"].apply(
        lambda x: f"1-in-{x:.0f}" if pd.notna(x) else "—"
    )
    _df_rank["prob_rp"] = _df_rank["prob_rp"].apply(
        lambda x: f"1-in-{x:.0f}" if pd.notna(x) else "—"
    )
    _df_rank["fcst_pctile_%"] = _df_rank["forecast_percentile"].apply(
        lambda x: f"{x:.0f}%" if pd.notna(x) else "—"
    )

    _display = _df_rank[
        [
            "rank", "country_name", "pcode", "prob_%", "fcst_pctile_%",
            "forecast_rp", "prob_rp", "pearson_r", "neg_skill",
            "n_years", "current_forecast_year", "is_predictive",
        ]
    ].reset_index(drop=True)

    mo.ui.table(_display)
    return


@app.cell
def _(mo):
    mo.md("""
    ## Per-country analysis
    """)
    return


@app.cell
def _(df_skill, mo):
    _names_df = (
        df_skill[["pcode", "country_name"]]
        .drop_duplicates()
        .sort_values("country_name")
    )
    _pcode_options = dict(zip(_names_df["country_name"], _names_df["pcode"]))
    pcode_dd = mo.ui.dropdown(
        options=_pcode_options,
        label="Country:",
        value=list(_pcode_options.keys())[0],
    )
    pcode_dd
    return (pcode_dd,)


@app.cell
def _(pcode_dd):
    pcode = pcode_dd.value
    return (pcode,)


@app.cell
def _(TRIMESTER_NAMES, calendar, df_skill, np, pcode, plt):
    _df_p = df_skill[df_skill["pcode"] == pcode]
    _matrix = np.full((12, 12), np.nan)
    for _, _r in _df_p.iterrows():
        _i = int(_r["issued_month"]) - 1
        _j = TRIMESTER_NAMES.index(_r["trimester"])
        _matrix[_i, _j] = _r["pearson_r"]

    _fig, _ax = plt.subplots(figsize=(10, 8), dpi=150)
    _nan_mask = np.where(np.isnan(_matrix), 1.0, np.nan)
    _ax.imshow(_nan_mask, cmap="Greys", vmin=0, vmax=1, aspect="auto", alpha=0.25)
    _masked = np.ma.masked_invalid(_matrix)
    _im = _ax.imshow(_masked, cmap="RdYlGn", vmin=-0.5, vmax=0.8, aspect="auto")
    plt.colorbar(_im, ax=_ax, label="Pearson r", shrink=0.8)
    for _i in range(12):
        for _j in range(12):
            if not np.isnan(_matrix[_i, _j]):
                _ax.text(
                    _j, _i, f"{_matrix[_i, _j]:.2f}",
                    ha="center", va="center", fontsize=7,
                )
    _ax.set_xticks(range(12))
    _ax.set_xticklabels(TRIMESTER_NAMES, rotation=45, ha="right")
    _ax.set_yticks(range(12))
    _ax.set_yticklabels([calendar.month_abbr[m] for m in range(1, 13)])
    _ax.set_xlabel("Valid trimester")
    _ax.set_ylabel("Issued month")
    _ax.set_title(f"{pcode} — SEAS5 historical skill (Pearson r)")
    plt.tight_layout()
    _fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Current forecast probability

    **Statistical model:** SEAS5 forecasts are first normalized to match ERA5 mean and
    std. The conditional distribution of actual rainfall given normalized forecast **F** is
    then modelled using **linear regression**:

    > E[obs | F] = (1 − r) · μ_ERA5 + r · F
    > std[obs | F] = σ_ERA5 · √(1 − r²)

    The bell curve is centred at a **weighted blend** of the forecast and the climatological
    mean — the lower the skill r, the more the centre is pulled toward climatology.
    At r = 0 the forecast is ignored entirely (P = 33%); at r = 1 the forecast is taken
    at face value (zero uncertainty). This gives much stronger differentiation by skill
    than an additive error model would.

    The **purple line** shows the raw SEAS5 forecast (F); the **bell curve centre** shows
    where the model expects observations to cluster. Return periods are computed
    empirically. `is_predictive` = ERA5 not yet available (timing, not skill quality).
    """)
    return


@app.cell
def _(
    calendar,
    df_paired,
    df_skill,
    issued_month,
    norm,
    np,
    pcode,
    pd,
    plt,
    trimester,
):
    _row = df_skill[
        (df_skill["pcode"] == pcode)
        & (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
    ]
    _has_skill = not _row.empty and pd.notna(_row.iloc[0]["pearson_r"])

    _era5_df = (
        df_paired[
            (df_paired["pcode"] == pcode)
            & (df_paired["trimester"] == trimester)
            & df_paired["obs_mean"].notna()
        ]
        .drop_duplicates("season_year")[["season_year", "obs_mean"]]
        .sort_values("obs_mean")
        .reset_index(drop=True)
    )
    _era5_vals = _era5_df["obs_mean"].values

    _fig_bell, _ax = plt.subplots(figsize=(9, 5), dpi=150)

    if not _has_skill or len(_era5_vals) == 0 or pd.isna(_row.iloc[0]["current_forecast_mean"]):
        _ax.text(
            0.5, 0.5, "Insufficient historical data for this combination",
            ha="center", va="center", transform=_ax.transAxes, fontsize=12,
        )
        _ax.set_axis_off()
    else:
        _r = _row.iloc[0]
        _r_val = float(_r["pearson_r"])
        _sigma = max(float(_r["sigma"]), 1e-9)   # ERA5_std · √(1-r²)
        _era5_mean = float(_r["era5_mean"])
        _F = float(_r["current_forecast_mean"])   # raw normalized SEAS5 forecast
        _mu = (1 - _r_val) * _era5_mean + _r_val * _F  # regression-shrunk centre
        _T = float(np.percentile(_era5_vals, 100 / 3))
        _prob = float(_r["prob_lower_tercile"])
        _is_pred = bool(_r["is_predictive"])
        _year = int(_r["current_forecast_year"])
        _forecast_rp = _r["forecast_rp"]
        _prob_rp = _r["prob_rp"]

        _x_min = min(float(_era5_vals.min()), _mu - 4 * _sigma)
        _x_max = max(float(_era5_vals.max()), _mu + 4 * _sigma)
        _x = np.linspace(_x_min, _x_max, 500)
        _pdf = norm.pdf(_x, loc=_mu, scale=_sigma)

        _ax.plot(_x, _pdf, color="k", linewidth=2)
        _low_mask = _x <= _T
        _ax.fill_between(
            _x[_low_mask], _pdf[_low_mask],
            color="chocolate", alpha=0.35,
            label=f"P(lower tercile) = {_prob:.1%}",
        )

        _ax.plot(
            _era5_vals, np.zeros_like(_era5_vals), "|",
            color="royalblue", markersize=18, markeredgewidth=2, alpha=0.7,
            label="Historical ERA5 seasons",
        )
        _n_label = 3
        _label_yrs = set(
            _era5_df["season_year"].head(_n_label).tolist()
            + _era5_df["season_year"].tail(_n_label).tolist()
        )
        for _, _yr_row in _era5_df.iterrows():
            if int(_yr_row["season_year"]) in _label_yrs:
                _ax.annotate(
                    str(int(_yr_row["season_year"])),
                    (_yr_row["obs_mean"], 0),
                    xytext=(0, -18), textcoords="offset points",
                    ha="center", fontsize=7, rotation=45, color="royalblue",
                )

        _ax.axvline(
            _T, color="chocolate", linestyle="--", alpha=0.8,
            label=f"Lower tercile ({_T:.3f} mm/day)",
        )
        _forecast_label = f"{_year} SEAS5 forecast ({_F:.3f} mm/day)"
        if not _is_pred:
            _forecast_label += " [ERA5 now available]"
        _ax.axvline(_F, color="mediumorchid", linestyle="--", label=_forecast_label)
        # Show the regression-shrunk centre (may differ from F when r < 1)
        if abs(_mu - _F) > 0.001:
            _ax.axvline(
                _mu, color="steelblue", linestyle=":",
                label=f"Conditional E[obs] ({_mu:.3f}) — r={_r_val:.2f} shrinkage",
            )

        _rp_lines = []
        if pd.notna(_forecast_rp):
            _rp_lines.append(f"Forecast RP: 1-in-{_forecast_rp:.0f} yr")
        if pd.notna(_prob_rp):
            _rp_lines.append(f"Probability RP: 1-in-{_prob_rp:.0f} yr")
        if _rp_lines:
            _ax.text(
                0.97, 0.97, "\n".join(_rp_lines),
                transform=_ax.transAxes, ha="right", va="top",
                fontsize=10, fontweight="bold",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.6),
            )

        if _r_val < 0:
            _ax.text(
                0.5, 0.98,
                "⚠ Negative skill (r < 0) — forecast is anti-correlated with observations. "
                "Probability estimate is unreliable.",
                transform=_ax.transAxes, ha="center", va="top",
                fontsize=9, color="darkred", style="italic",
                bbox=dict(boxstyle="round", facecolor="mistyrose", alpha=0.8),
            )

        _issued_str = calendar.month_abbr[issued_month]
        _ax.set_title(
            f"{_r['country_name']} — issued {_issued_str}, valid {trimester}  |  "
            f"r = {_r_val:.2f}  σ_cond = {_sigma:.3f}"
        )
        _ax.set_xlabel("Normalized mean daily rainfall (mm/day)")
        _ax.set_ylabel("Probability density")
        _ax.legend(fontsize=9)
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_bell
    return


@app.cell
def _(TRIMESTER_NAMES, df_paired, df_skill, pcode, plt, trimester):
    _df_clim = (
        df_paired[df_paired["pcode"] == pcode]
        .dropna(subset=["obs_mean"])
        .drop_duplicates(["trimester", "season_year"])
        .groupby("trimester")["obs_mean"]
        .mean()
        .reindex(TRIMESTER_NAMES)
        .reset_index()
        .rename(columns={"obs_mean": "mean_mm_day"})
    )
    _annual = _df_clim["mean_mm_day"].sum()
    _is_rainy = (
        {row["trimester"]: (3 * row["mean_mm_day"] / _annual >= 0.25)
         for _, row in _df_clim.iterrows()}
        if _annual > 0 else {}
    )
    _country = (
        df_skill[df_skill["pcode"] == pcode]["country_name"].iloc[0]
        if not df_skill[df_skill["pcode"] == pcode].empty else pcode
    )
    _face_colors = ["chocolate" if t == trimester else "royalblue" for t in _df_clim["trimester"]]
    _edge_colors = ["darkorange" if _is_rainy.get(t, False) else "none" for t in _df_clim["trimester"]]
    _fig_clim, _ax = plt.subplots(figsize=(10, 4), dpi=150)
    _ax.bar(
        _df_clim["trimester"], _df_clim["mean_mm_day"],
        color=_face_colors, edgecolor=_edge_colors, linewidth=2.5,
    )
    _ax.set_xlabel("Trimester  (orange outline = ≥25% of annual rainfall)")
    _ax.set_ylabel("Mean daily rainfall (mm/day) [ERA5]")
    _ax.set_title(f"{_country} — ERA5 trimester climatology (selected trimester highlighted)")
    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig_clim
    return


@app.cell
def _(calendar, df_paired, df_skill, issued_month, pcode, pd, plt, trimester):
    _df_s = df_paired[
        (df_paired["pcode"] == pcode)
        & (df_paired["issued_month"] == issued_month)
        & (df_paired["trimester"] == trimester)
        & df_paired["obs_mean"].notna()
        & df_paired["forecast_mean"].notna()
    ].copy()

    _skill_row = df_skill[
        (df_skill["pcode"] == pcode)
        & (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
    ]

    _fig_scatter, _ax = plt.subplots(figsize=(7, 7), dpi=150)

    if _df_s.empty or _skill_row.empty:
        _ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=_ax.transAxes)
        _ax.set_axis_off()
    else:
        _sr = _skill_row.iloc[0]
        _xmin, _xmax = _df_s["forecast_mean"].min(), _df_s["forecast_mean"].max()
        _ymin, _ymax = _df_s["obs_mean"].min(), _df_s["obs_mean"].max()
        _xpad = 0.1 * (_xmax - _xmin) if _xmax > _xmin else 0.1
        _ypad = 0.1 * (_ymax - _ymin) if _ymax > _ymin else 0.1
        _xlim = (_xmin - _xpad, _xmax + _xpad)
        _ylim = (_ymin - _ypad, _ymax + _ypad)

        _seas5_t = _df_s["forecast_mean"].quantile(1 / 3)
        _era5_t = _df_s["obs_mean"].quantile(1 / 3)

        _ax.axvspan(_xlim[0], _seas5_t, color="chocolate", alpha=0.08, zorder=-2)
        _ax.axhspan(_ylim[0], _era5_t, color="chocolate", alpha=0.08, zorder=-2)

        for _, _yr in _df_s.iterrows():
            _ax.annotate(
                str(int(_yr["season_year"])),
                (_yr["forecast_mean"], _yr["obs_mean"]),
                fontsize=8, ha="center", va="center", color="k", zorder=3,
            )

        if bool(_sr["is_predictive"]) and pd.notna(_sr["current_forecast_mean"]):
            _cf = float(_sr["current_forecast_mean"])
            _ax.axvline(_cf, color="mediumorchid", linestyle="--", zorder=-1)
            _ax.annotate(
                f"  {int(_sr['current_forecast_year'])} forecast",
                (_cf, _ylim[0]),
                rotation=90, va="bottom", ha="right",
                color="mediumorchid", fontstyle="italic",
            )

        _ax.set_xlim(_xlim)
        _ax.set_ylim(_ylim)

        _r_val = float(_sr["pearson_r"]) if pd.notna(_sr["pearson_r"]) else float("nan")
        _pp = _df_s["forecast_mean"] < _seas5_t
        _p = _df_s["obs_mean"] < _era5_t
        _tpr = float((_pp & _p).sum() / _p.sum()) if _p.sum() > 0 else float("nan")
        _n = int(_sr["n_years"]) if pd.notna(_sr["n_years"]) else 0

        _issued_str = calendar.month_abbr[issued_month]
        _country_name = _sr["country_name"] if "country_name" in _sr.index else pcode
        _neg_skill_suffix = "  ⚠ negative skill" if _r_val < 0 else ""
        _ax.set_title(
            f"{_country_name} — issued {_issued_str}, valid {trimester}{_neg_skill_suffix}",
            color="darkred" if _r_val < 0 else "black",
        )
        _ax.set_xlabel(
            f"Normalized SEAS5 forecast (mm/day)\n"
            f"Pearson r = {_r_val:.2f}  |  Lower-tercile hit rate = {_tpr:.2f}  |  n = {_n}"
        )
        _ax.set_ylabel("ERA5 observed (mm/day)")
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_scatter
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Bell curve validation: skill vs normalised residual error

    Each point is one country for the selected (issued month, trimester). The y-axis is σ
    divided by the mean ERA5 observation for that country — a coefficient of variation
    that is comparable across countries regardless of absolute rainfall amounts.

    The dashed curve shows the theoretical relationship σ/μ = CV_ERA5 × √(2(1−r)),
    using the median coefficient of variation across countries. Points should track
    this curve if the Gaussian error model is calibrated correctly.
    """)
    return


@app.cell
def _(df_skill, issued_month, np, pd, plt, trimester):
    _df_val = df_skill[
        (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
        & df_skill["pearson_r"].notna()
        & df_skill["sigma"].notna()
        & df_skill["era5_mean"].notna()
    ].copy()

    # sigma is already ERA5_std·√(1-r²); normalise by ERA5 mean for comparability
    _df_val["sigma_norm"] = _df_val["sigma"] / _df_val["era5_mean"]

    _fig_val, _ax = plt.subplots(figsize=(8, 5), dpi=150)

    if _df_val.empty or _df_val["sigma_norm"].isna().all():
        _ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=_ax.transAxes)
        _ax.set_axis_off()
    else:
        _r_vals = _df_val["pearson_r"].values
        _sn_vals = _df_val["sigma_norm"].values

        # Theoretical curve: σ_norm = CV_ERA5 · √(1-r²)  (regression conditional std)
        # sigma is now ERA5_std·√(1-r²), so CV_ERA5 = sigma / (era5_mean·√(1-r²))
        _cv_era5 = _df_val["era5_std"] / _df_val["era5_mean"]
        _median_cv = float(np.nanmedian(_cv_era5.values))
        _r_curve = np.linspace(-0.6, 1.0, 300)
        _sn_curve = _median_cv * np.sqrt(np.clip(1 - _r_curve ** 2, 0, None))
        _ax.plot(
            _r_curve, _sn_curve, color="grey", linestyle="--", linewidth=1,
            label=f"theoretical (median CV_ERA5 = {_median_cv:.2f})", zorder=1,
        )

        _ax.axvline(0, color="grey", linewidth=0.5, alpha=0.4)

        _neg_mask = _df_val["pearson_r"] < 0
        _sc = _ax.scatter(
            _df_val.loc[~_neg_mask, "pearson_r"],
            _df_val.loc[~_neg_mask, "sigma_norm"],
            c=_df_val.loc[~_neg_mask, "prob_lower_tercile"].fillna(0.33),
            cmap="RdYlBu_r", vmin=0.1, vmax=0.6, s=60, zorder=3,
        )
        if _neg_mask.any():
            _ax.scatter(
                _df_val.loc[_neg_mask, "pearson_r"],
                _df_val.loc[_neg_mask, "sigma_norm"],
                c="darkred", marker="^", s=80, zorder=4, label="negative skill ⚠",
            )
        plt.colorbar(_sc, ax=_ax, label="P(lower tercile)", shrink=0.8)

        for _, _pt in _df_val.iterrows():
            if pd.notna(_pt["pearson_r"]) and pd.notna(_pt["sigma_norm"]):
                _ax.annotate(
                    _pt["country_name"].split(" ")[0],
                    (_pt["pearson_r"], _pt["sigma_norm"]),
                    xytext=(4, 3), textcoords="offset points",
                    fontsize=7, color="darkred" if _pt["pearson_r"] < 0 else "k",
                )

        _issued_str = ""
        _ax.set_xlabel("Pearson r (historical correlation)")
        _ax.set_ylabel("Normalised σ  (residual std / ERA5 mean)")
        _ax.set_title(
            f"Skill validation — all countries, issued month {issued_month}, valid {trimester}"
        )
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        _ax.legend(fontsize=8)
        plt.tight_layout()

    _fig_val
    return


if __name__ == "__main__":
    app.run()
