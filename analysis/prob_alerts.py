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
def _(df_paired, np):
    # Precompute which (pcode, trimester) pairs are "rainy" (≥25% of annual rainfall).
    _clim = (
        df_paired.dropna(subset=["obs_mean"])
        .drop_duplicates(["pcode", "trimester", "season_year"])
        .assign(obs_orig=lambda d: np.expm1(d["obs_mean"]))
        .groupby(["pcode", "trimester"])["obs_orig"]
        .mean()
        .reset_index(name="mean_mm_day")
    )
    _annual = _clim.groupby("pcode")["mean_mm_day"].sum().rename("annual")
    _clim = _clim.merge(_annual.reset_index(), on="pcode")
    _clim["is_rainy"] = 3 * _clim["mean_mm_day"] / _clim["annual"] >= 0.25
    rainy_set = set(
        zip(_clim[_clim["is_rainy"]]["pcode"], _clim[_clim["is_rainy"]]["trimester"])
    )
    return (rainy_set,)


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
def _(df_skill, issued_month, mo, pd, rainy_set, trimester):
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
    _df_rank["rainy"] = _df_rank["pcode"].apply(
        lambda p: "🌧" if (p, trimester) in rainy_set else ""
    )
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
            "rank", "country_name", "rainy", "pcode", "prob_%", "fcst_pctile_%",
            "forecast_rp", "prob_rp", "pearson_r", "neg_skill",
            "n_years", "current_forecast_year", "is_predictive",
        ]
    ].reset_index(drop=True)

    mo.ui.table(_display)
    return


@app.cell
def _(df_skill, issued_month, plt, trimester):
    _df_sc = df_skill[
        (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
        & df_skill["forecast_percentile"].notna()
        & df_skill["pearson_r"].notna()
    ].copy()

    _fig_sc, _ax = plt.subplots(figsize=(8, 5), dpi=150)
    if not _df_sc.empty:
        _sc = _ax.scatter(
            _df_sc["forecast_percentile"], _df_sc["pearson_r"],
            c=_df_sc["prob_lower_tercile"], cmap="RdYlBu_r", vmin=0.1, vmax=0.6,
            s=80, zorder=3, edgecolors="k", linewidths=0.4,
        )
        plt.colorbar(_sc, ax=_ax, label="P(lower tercile)", shrink=0.8)
        _ax.axhline(0, color="grey", linewidth=0.6, linestyle="--", alpha=0.5)
        for _, _rr in _df_sc.iterrows():
            _ax.annotate(
                _rr["country_name"].split(" ")[0],
                (_rr["forecast_percentile"], _rr["pearson_r"]),
                xytext=(5, 3), textcoords="offset points", fontsize=9,
            )
        _ax.set_xlabel("Forecast percentile (0 = driest, 100 = wettest)")
        _ax.set_ylabel("Pearson r (historical skill)")
        _ax.set_title(f"All countries — issued month {issued_month}, valid {trimester}")
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()
    _fig_sc
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
def _(TRIMESTER_NAMES, calendar, df_skill, np, pcode, plt, rainy_set):
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
    _im = _ax.imshow(_masked, cmap="RdYlGn", vmin=-1, vmax=1, aspect="auto")
    plt.colorbar(_im, ax=_ax, label="Pearson r", shrink=0.8)

    # Highlight rainy trimester columns
    for _j, _tname in enumerate(TRIMESTER_NAMES):
        if (pcode, _tname) in rainy_set:
            _ax.add_patch(plt.Rectangle(
                (_j - 0.5, -0.5), 1, 12,
                facecolor="darkorange", alpha=0.13, zorder=0,
            ))

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
    _ax.set_xlabel("Valid trimester  (orange shading = rainy season, ≥25% annual rainfall)")
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

    # obs_mean in df_paired is log1p-transformed; era5_df has log-space values
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
    _era5_log = _era5_df["obs_mean"].values          # log-space (for sorting)
    _era5_orig = np.expm1(_era5_log)                  # mm/day (for display)

    _fig_bell, _ax = plt.subplots(figsize=(9, 5), dpi=150)

    if not _has_skill or len(_era5_log) == 0 or pd.isna(_row.iloc[0]["current_forecast_mean"]):
        _ax.text(
            0.5, 0.5, "Insufficient historical data for this combination",
            ha="center", va="center", transform=_ax.transAxes, fontsize=12,
        )
        _ax.set_axis_off()
    else:
        _r = _row.iloc[0]
        _r_val = float(_r["pearson_r"])
        _sigma_log = max(float(_r["sigma"]), 1e-9)     # conditional std in log-space
        _era5_mean_log = float(_r["era5_mean"])
        _F_log = float(_r["current_forecast_mean"])     # log-space SEAS5 forecast
        _mu_log = (1 - _r_val) * _era5_mean_log + _r_val * _F_log  # log-space conditional mean
        _T_orig = float(_r["lower_tercile_mm"])         # mm/day (original units)
        _prob = float(_r["prob_lower_tercile"])
        _is_pred = bool(_r["is_predictive"])
        _year = int(_r["current_forecast_year"])
        _forecast_rp = _r["forecast_rp"]
        _prob_rp = _r["prob_rp"]

        # Display quantities in original space (mm/day)
        _F_orig = float(np.expm1(_F_log))
        _mode_orig = float(np.expm1(_mu_log - _sigma_log**2))  # mode = peak of log-normal PDF
        _clim_mean_orig = float(_era5_orig.mean())

        # Log-normal PDF in original space: f(x) = φ(log1p(x); μ, σ) / (1+x)
        _x_min = max(0.0, float(np.expm1(_mu_log - 4 * _sigma_log)))
        _x_max = max(float(_era5_orig.max()), float(np.expm1(_mu_log + 4 * _sigma_log)))
        _x = np.linspace(max(1e-6, _x_min), _x_max, 600)
        _pdf = norm.pdf(np.log1p(_x), loc=_mu_log, scale=_sigma_log) / (1 + _x)
        _pdf_max = float(_pdf.max())

        _ax.plot(_x, _pdf, color="k", linewidth=2)
        _low_mask = _x <= _T_orig
        _ax.fill_between(_x[_low_mask], _pdf[_low_mask], color="chocolate", alpha=0.35)

        # Rug marks in original space
        _ax.plot(
            _era5_orig, np.zeros_like(_era5_orig), "|",
            color="royalblue", markersize=18, markeredgewidth=2, alpha=0.7,
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
                    (np.expm1(_yr_row["obs_mean"]), 0),
                    xytext=(0, -20), textcoords="offset points",
                    ha="center", fontsize=7, rotation=90, color="royalblue",
                    va="top",
                )

        # Reference lines with inline labels (no legend)
        _ax.axvline(_T_orig, color="chocolate", linestyle="--", alpha=0.8)
        _ax.text(_T_orig, _pdf_max * 0.92, "  lower tercile", color="chocolate", fontsize=8, va="top")

        _ax.axvline(_clim_mean_orig, color="steelblue", linestyle=":")
        _ax.text(_clim_mean_orig, _pdf_max * 0.78, "  clim mean", color="steelblue", fontsize=8, va="top")

        _ax.axvline(_F_orig, color="mediumorchid", linestyle="--")
        _fcst_lbl = f"  {_year} fcst" + ("" if _is_pred else " (verified)")
        _ax.text(_F_orig, _pdf_max * 0.64, _fcst_lbl, color="mediumorchid", fontsize=8, va="top")

        if abs(_mode_orig - _F_orig) > 0.001 * max(_clim_mean_orig, 1e-9):
            _ax.axvline(_mode_orig, color="darkorange", linestyle=":")
            _ax.text(_mode_orig, _pdf_max * 0.50,
                     f"  cond. mode\n  (r={_r_val:.2f})",
                     color="darkorange", fontsize=8, va="top")

        # P(lower tercile) in top-left corner
        _ax.text(0.02, 0.97, f"P(lower tercile) = {_prob:.1%}",
                 transform=_ax.transAxes, fontsize=12, fontweight="bold",
                 color="chocolate", va="top")

        # RP box — top right
        _rp_lines = []
        if pd.notna(_forecast_rp):
            _rp_lines.append(f"Forecast RP: 1-in-{_forecast_rp:.0f} yr")
        if pd.notna(_prob_rp):
            _rp_lines.append(f"Probability RP: 1-in-{_prob_rp:.0f} yr")
        if _rp_lines:
            _ax.text(
                0.97, 0.97, "\n".join(_rp_lines),
                transform=_ax.transAxes, ha="right", va="top",
                fontsize=9, fontweight="bold",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.6),
            )

        if _r_val < 0:
            _ax.text(
                0.5, 0.98,
                "⚠ Negative skill (r < 0) — forecast anti-correlated; probability unreliable.",
                transform=_ax.transAxes, ha="center", va="top",
                fontsize=9, color="darkred", style="italic",
                bbox=dict(boxstyle="round", facecolor="mistyrose", alpha=0.8),
            )

        _issued_str = calendar.month_abbr[issued_month]
        _ax.set_title(
            f"{_r['country_name']} — issued {_issued_str}, valid {trimester}  |  "
            f"r = {_r_val:.2f}  σ_log = {_sigma_log:.3f}"
        )
        _ax.set_xlabel("Mean daily rainfall (mm/day)")
        _ax.set_ylabel("Probability density")
        _ax.set_ylim(bottom=0)
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_bell
    return


@app.cell
def _(TRIMESTER_NAMES, df_paired, df_skill, np, pcode, plt, trimester):
    _df_clim = (
        df_paired[df_paired["pcode"] == pcode]
        .dropna(subset=["obs_mean"])
        .drop_duplicates(["trimester", "season_year"])
        .assign(obs_orig=lambda d: np.expm1(d["obs_mean"]))  # back to mm/day
        .groupby("trimester")["obs_orig"]
        .mean()
        .reindex(TRIMESTER_NAMES)
        .reset_index()
        .rename(columns={"obs_orig": "mean_mm_day"})
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
def _(
    calendar,
    df_paired,
    df_skill,
    issued_month,
    np,
    pcode,
    pd,
    plt,
    trimester,
):
    # df_paired stores log1p values — convert to mm/day for display
    _df_s = df_paired[
        (df_paired["pcode"] == pcode)
        & (df_paired["issued_month"] == issued_month)
        & (df_paired["trimester"] == trimester)
        & df_paired["obs_mean"].notna()
        & df_paired["forecast_mean"].notna()
    ].copy()
    _df_s["forecast_orig"] = np.expm1(_df_s["forecast_mean"])
    _df_s["obs_orig"] = np.expm1(_df_s["obs_mean"])

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
        _xmin, _xmax = _df_s["forecast_orig"].min(), _df_s["forecast_orig"].max()
        _ymin, _ymax = _df_s["obs_orig"].min(), _df_s["obs_orig"].max()
        _xpad = 0.1 * (_xmax - _xmin) if _xmax > _xmin else 0.1
        _ypad = 0.1 * (_ymax - _ymin) if _ymax > _ymin else 0.1
        _xlim = (_xmin - _xpad, _xmax + _xpad)
        _ylim = (_ymin - _ypad, _ymax + _ypad)

        _seas5_t = _df_s["forecast_orig"].quantile(1 / 3)
        _era5_t = _df_s["obs_orig"].quantile(1 / 3)

        _ax.axvspan(_xlim[0], _seas5_t, color="chocolate", alpha=0.08, zorder=-2)
        _ax.axhspan(_ylim[0], _era5_t, color="chocolate", alpha=0.08, zorder=-2)

        for _, _yr in _df_s.iterrows():
            _ax.annotate(
                str(int(_yr["season_year"])),
                (_yr["forecast_orig"], _yr["obs_orig"]),
                fontsize=8, ha="center", va="center", color="k", zorder=3,
            )

        if bool(_sr["is_predictive"]) and pd.notna(_sr["current_forecast_mean"]):
            _cf_orig = float(np.expm1(_sr["current_forecast_mean"]))
            _ax.axvline(_cf_orig, color="mediumorchid", linestyle="--", zorder=-1)
            _ax.annotate(
                f"  {int(_sr['current_forecast_year'])} forecast",
                (_cf_orig, _ylim[0]),
                rotation=90, va="bottom", ha="right",
                color="mediumorchid", fontstyle="italic",
            )

        _ax.set_xlim(_xlim)
        _ax.set_ylim(_ylim)

        _r_val = float(_sr["pearson_r"]) if pd.notna(_sr["pearson_r"]) else float("nan")
        _pp = _df_s["forecast_orig"] < _seas5_t
        _p = _df_s["obs_orig"] < _era5_t
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
    ### Probability calibration

    For every historical (country, issued month, trimester, year) we have a model-predicted
    probability of lower-tercile rainfall. A well-calibrated model means that seasons predicted
    at X% probability actually verify as lower-tercile X% of the time.

    **Note:** the σ vs r scatter previously shown here was tautological — σ is derived
    analytically from r, so all points lie on the curve by construction. This calibration
    diagram is the meaningful validation: it tests whether the predicted probabilities
    match observed frequencies across all combinations.
    """)
    return


@app.cell
def _(df_paired, pd, plt):
    # Calibration diagram pooling all (country, issued_month, trimester, year) combinations.
    # For each row, determine if obs_mean was actually in the lower tercile for that
    # (pcode, trimester) — computed within the pooled data (in log-space; monotone so same result).
    _df_cal = df_paired[
        df_paired["hist_prob"].notna() & df_paired["obs_mean"].notna()
    ].copy()

    # Lower tercile flag: obs < 33rd pctile of obs for that (pcode, trimester)
    _terciles = (
        _df_cal.groupby(["pcode", "trimester"])["obs_mean"]
        .quantile(1 / 3)
        .rename("obs_tercile")
    )
    _df_cal = _df_cal.merge(_terciles.reset_index(), on=["pcode", "trimester"], how="left")
    _df_cal["was_lower"] = _df_cal["obs_mean"] <= _df_cal["obs_tercile"]

    _n_bins = 10
    _df_cal["bin"] = pd.cut(_df_cal["hist_prob"], bins=_n_bins, labels=False)
    _cal = (
        _df_cal.groupby("bin", observed=False)
        .agg(predicted=("hist_prob", "mean"), observed_rate=("was_lower", "mean"), n=("hist_prob", "count"))
        .dropna(subset=["predicted"])
    )

    _fig_cal, _ax = plt.subplots(figsize=(6, 6), dpi=150)
    _ax.plot([0, 1], [0, 1], "k--", linewidth=1, alpha=0.5, label="Perfect calibration")
    _ax.axhline(1 / 3, color="chocolate", linestyle=":", linewidth=0.8, alpha=0.6)
    _ax.axvline(1 / 3, color="chocolate", linestyle=":", linewidth=0.8, alpha=0.6)

    _sc2 = _ax.scatter(
        _cal["predicted"], _cal["observed_rate"],
        s=_cal["n"] / _cal["n"].max() * 300 + 30,
        c=_cal["predicted"], cmap="RdYlBu_r", vmin=0.15, vmax=0.55,
        edgecolors="k", linewidths=0.5, zorder=3,
    )
    for _, _b in _cal.iterrows():
        _ax.annotate(
            f"n={int(_b['n'])}",
            (_b["predicted"], _b["observed_rate"]),
            xytext=(4, 4), textcoords="offset points", fontsize=6, color="k",
        )

    _ax.set_xlim(0, 0.75)
    _ax.set_ylim(0, 0.75)
    _ax.set_xlabel("Predicted P(lower tercile)")
    _ax.set_ylabel("Observed frequency of lower tercile")
    _ax.set_title(
        f"Calibration — all countries, issued month {_df_cal['issued_month'].iloc[0] if len(_df_cal) else '?'}\n"
        f"(pooled across all issued months & trimesters, {len(_df_cal):,} season-years)"
    )
    _ax.legend(fontsize=8)
    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig_cal
    return


if __name__ == "__main__":
    app.run()
