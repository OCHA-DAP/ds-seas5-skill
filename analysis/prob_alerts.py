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
    from src.constants import PCODE_NAMES, PROJECT_PREFIX, TRIMESTERS

    TRIMESTER_NAMES = list(TRIMESTERS.keys())
    return PCODE_NAMES, PROJECT_PREFIX, TRIMESTER_NAMES


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
def _(mo):
    mo.md("""
    ## Per-country analysis
    """)
    return


@app.cell
def _(PCODE_NAMES, TRIMESTER_NAMES, calendar, df_skill, mo):
    _pcodes = sorted(df_skill["pcode"].unique().tolist())
    _pcode_options = {PCODE_NAMES.get(p, p): p for p in _pcodes}
    pcode_dd = mo.ui.dropdown(
        options=_pcode_options,
        label="Country:",
        value=list(_pcode_options.keys())[0],
    )
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
    mo.hstack([pcode_dd, issued_month_dd, trimester_dd], justify="start")
    return issued_month_dd, pcode_dd, trimester_dd


@app.cell
def _(issued_month_dd, pcode_dd, trimester_dd):
    pcode = pcode_dd.value
    issued_month = issued_month_dd.value
    trimester = trimester_dd.value
    return issued_month, pcode, trimester


@app.cell
def _(TRIMESTER_NAMES, calendar, df_skill, np, pcode, plt):
    _df_p = df_skill[df_skill["pcode"] == pcode]
    _matrix = np.full((12, 12), np.nan)
    for _, _r in _df_p.iterrows():
        _i = int(_r["issued_month"]) - 1
        _j = TRIMESTER_NAMES.index(_r["trimester"])
        _matrix[_i, _j] = _r["pearson_r"]

    _fig, _ax = plt.subplots(figsize=(10, 8), dpi=150)
    # grey fill for NaN cells
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
    _ax.set_title(f"{pcode} — SEAS5 historical skill (Pearson r, no detrending)")
    plt.tight_layout()
    _fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Current forecast probability

    **Statistical model:** for each (issued month, valid trimester) combination, we compute
    the historical distribution of SEAS5 forecast errors (ERA5 − SEAS5). The error mean
    gives the **bias** and the standard deviation gives **σ** (a measure of skill — smaller
    σ means the forecast is consistently close to observations).

    For the current SEAS5 forecast value **F**, we model likely actual rainfall as a
    Gaussian centred at **F + bias** with spread **σ**. A skillful forecast (small σ)
    gives a decisive probability; a poor-skill forecast (large σ) gives a probability
    near the climatological baseline of 33%.
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

    _era5_vals = (
        df_paired[
            (df_paired["pcode"] == pcode)
            & (df_paired["trimester"] == trimester)
            & df_paired["obs_mean"].notna()
        ]
        .drop_duplicates("season_year")["obs_mean"]
        .values
    )

    _fig_bell, _ax = plt.subplots(figsize=(9, 5), dpi=150)

    if not _has_skill or len(_era5_vals) == 0 or pd.isna(_row.iloc[0]["current_forecast_mean"]):
        _ax.text(
            0.5, 0.5, "Insufficient historical data for this combination",
            ha="center", va="center", transform=_ax.transAxes, fontsize=12,
        )
        _ax.set_axis_off()
    else:
        _r = _row.iloc[0]
        _bias = float(_r["bias"])
        _sigma = max(float(_r["sigma"]), 1e-9)
        _F = float(_r["current_forecast_mean"])
        _mu = _F + _bias
        _T = float(np.percentile(_era5_vals, 100 / 3))
        _prob = float(_r["prob_lower_tercile"])
        _is_pred = bool(_r["is_predictive"])
        _year = int(_r["current_forecast_year"])

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
        _ax.axvline(
            _T, color="chocolate", linestyle="--", alpha=0.8,
            label=f"Lower tercile ({_T:.3f} mm/day)",
        )
        _forecast_label = f"{_year} SEAS5 forecast ({_F:.3f} mm/day)"
        if not _is_pred:
            _forecast_label += " [ERA5 now available]"
        _ax.axvline(_F, color="mediumorchid", linestyle="--", label=_forecast_label)

        _issued_str = calendar.month_abbr[issued_month]
        _ax.set_title(
            f"{pcode} — issued {_issued_str}, valid {trimester}  |  "
            f"bias = {_bias:.3f}  σ = {_sigma:.3f}  r = {float(_r['pearson_r']):.2f}"
        )
        _ax.set_xlabel("Mean daily rainfall (mm/day)")
        _ax.set_ylabel("Probability density")
        _ax.legend(fontsize=9)
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_bell
    return


@app.cell
def _(TRIMESTER_NAMES, df_paired, pcode, plt, trimester):
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
    _colors = [
        "chocolate" if t == trimester else "royalblue"
        for t in _df_clim["trimester"]
    ]
    _fig_clim, _ax = plt.subplots(figsize=(10, 4), dpi=150)
    _ax.bar(_df_clim["trimester"], _df_clim["mean_mm_day"], color=_colors)
    _ax.set_xlabel("Trimester")
    _ax.set_ylabel("Mean daily rainfall (mm/day) [ERA5]")
    _ax.set_title(f"{pcode} — ERA5 trimester climatology (selected trimester highlighted)")
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

        # Skill metrics in x-axis label
        _r_val = float(_sr["pearson_r"]) if pd.notna(_sr["pearson_r"]) else float("nan")
        _pp = _df_s["forecast_mean"] < _seas5_t
        _p = _df_s["obs_mean"] < _era5_t
        _tpr = float((_pp & _p).sum() / _p.sum()) if _p.sum() > 0 else float("nan")
        _n = int(_sr["n_years"]) if pd.notna(_sr["n_years"]) else 0

        _issued_str = calendar.month_abbr[issued_month]
        _ax.set_title(f"{pcode} — issued {_issued_str}, valid {trimester}")
        _ax.set_xlabel(
            f"SEAS5 forecast (mm/day)\n"
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
    mo.md("""
    ## Ranked drought alert table
    """)
    return


@app.cell
def _(mo):
    mo.md("""
    Select an issued month and valid trimester to see all countries ranked by their skill-adjusted probability of below-normal rainfall.
    """)
    return


@app.cell
def _(TRIMESTER_NAMES, calendar, mo):
    rank_issued_month_dd = mo.ui.dropdown(
        options={calendar.month_abbr[m]: m for m in range(1, 13)},
        label="Issued month:",
        value=calendar.month_abbr[4],
    )
    rank_trimester_dd = mo.ui.dropdown(
        options=TRIMESTER_NAMES,
        label="Valid trimester:",
        value="JAS",
    )
    mo.hstack([rank_issued_month_dd, rank_trimester_dd], justify="start")
    return rank_issued_month_dd, rank_trimester_dd


@app.cell
def _(PCODE_NAMES, df_skill, mo, pd, rank_issued_month_dd, rank_trimester_dd):
    _rim = rank_issued_month_dd.value
    _rt = rank_trimester_dd.value

    _df_rank = (
        df_skill[
            (df_skill["issued_month"] == _rim)
            & (df_skill["trimester"] == _rt)
            & df_skill["prob_lower_tercile"].notna()
        ]
        .sort_values("prob_lower_tercile", ascending=False)
        .copy()
    )
    _df_rank.insert(0, "rank", range(1, len(_df_rank) + 1))
    _df_rank["country"] = _df_rank["pcode"].map(PCODE_NAMES).fillna(_df_rank["pcode"])
    _df_rank["prob_%"] = (_df_rank["prob_lower_tercile"] * 100).round(1)
    _df_rank["pearson_r"] = _df_rank["pearson_r"].round(3)
    _df_rank["n_years"] = _df_rank["n_years"].astype(pd.Int64Dtype())
    _df_rank["current_forecast_year"] = _df_rank["current_forecast_year"].astype(pd.Int64Dtype())

    _display = _df_rank[
        ["rank", "country", "pcode", "prob_%", "pearson_r", "n_years",
         "current_forecast_year", "is_predictive"]
    ].reset_index(drop=True)

    mo.ui.table(_display)
    return


if __name__ == "__main__":
    app.run()
