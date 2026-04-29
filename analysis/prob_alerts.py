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
    _df_rank["fcst_pctile"] = _df_rank["forecast_percentile"].apply(
        lambda x: f"{x:.0f}%" if pd.notna(x) else "—"
    )
    _df_rank["r"] = _df_rank["pearson_r"].apply(
        lambda x: f"{x:.2f}" if pd.notna(x) else "—"
    )
    _df_rank["prob"] = (_df_rank["prob_lower_tercile"] * 100).apply(
        lambda x: f"{x:.1f}%"
    )
    _df_rank["rp"] = _df_rank["prob_rp"].apply(
        lambda x: f"1-in-{x:.0f} yr" if pd.notna(x) else "—"
    )

    _headers = ["Country", "Fcst percentile", "Correlation (r)", "P(lower tercile)", "Probability RP"]
    _cols = ["country_name", "fcst_pctile", "r", "prob", "rp"]

    _th = "padding:6px 12px;text-align:left;border-bottom:2px solid #bbb;white-space:nowrap"
    _td = "padding:5px 12px;white-space:nowrap"
    _header_html = "".join(f"<th style='{_th}'>{h}</th>" for h in _headers)
    _rows_html = ""
    for _, _row in _df_rank.iterrows():
        _is_rainy = (_row["pcode"], trimester) in rainy_set
        _neg = pd.notna(_row["pearson_r"]) and _row["pearson_r"] < 0
        _bg = "background:#ddeeff;" if _is_rainy else ""
        _cells = ""
        for _c in _cols:
            _color = "color:darkred;" if (_c == "r" and _neg) else ""
            _name_suffix = " ⚠" if (_c == "country_name" and _neg) else ""
            _val = str(_row[_c]) + _name_suffix
            _cells += f"<td style='{_td}{_color}'>{_val}</td>"
        _rows_html += f"<tr style='{_bg}'>{_cells}</tr>"

    mo.Html(f"""
    <style>.skt{{border-collapse:collapse;font-size:13px}}
    .skt tbody tr:hover{{filter:brightness(0.94)}}</style>
    <p style='font-size:11px;color:#666;margin:4px 0'>
      Blue rows = rainy season for this trimester &nbsp;|&nbsp; ⚠ = negative correlation
    </p>
    <table class='skt'>
    <thead><tr>{_header_html}</tr></thead>
    <tbody>{_rows_html}</tbody>
    </table>""")
    return


@app.cell
def _(df_skill, issued_month, plt, rainy_set, trimester):
    _df_sc = df_skill[
        (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
        & df_skill["forecast_percentile"].notna()
        & df_skill["pearson_r"].notna()
    ].copy()

    import matplotlib.colors as _mcolors
    _bounds = [0.0, 0.33, 0.50, 0.67, 1.0]
    _cmap_cat = _mcolors.ListedColormap(["#4575b4", "#fee08b", "#f46d43", "#d73027"])
    _norm_cat = _mcolors.BoundaryNorm(_bounds, _cmap_cat.N)

    _fig_sc, _ax = plt.subplots(figsize=(8, 5), dpi=150)
    if not _df_sc.empty:
        _sc = _ax.scatter(
            _df_sc["forecast_percentile"], _df_sc["pearson_r"],
            c=_df_sc["prob_lower_tercile"].clip(0, 1),
            cmap=_cmap_cat, norm=_norm_cat,
            s=80, zorder=3, edgecolors="k", linewidths=0.4,
        )
        _cbar = plt.colorbar(_sc, ax=_ax, shrink=0.8)
        _cbar.set_ticks(_bounds)
        _cbar.set_ticklabels(["0%", "33% (clim.)", "50%", "67%", "100%"])
        _cbar.set_label("P(lower tercile)")
        _ax.axhline(0, color="grey", linewidth=0.6, linestyle="--", alpha=0.5)
        for _, _rr in _df_sc.iterrows():
            _in_rainy = (_rr["pcode"], trimester) in rainy_set
            _ax.annotate(
                _rr["country_name"].split(" ")[0],
                (_rr["forecast_percentile"], _rr["pearson_r"]),
                xytext=(5, 3), textcoords="offset points", fontsize=9,
                fontstyle="normal" if _in_rainy else "italic",
            )
        _ax.text(0.99, 0.01, "italic = not a rainy trimester for that country",
                 transform=_ax.transAxes, ha="right", va="bottom",
                 fontsize=8, fontstyle="italic", color="grey")
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

    # Highlight rainy trimester columns with blue outline
    for _j, _tname in enumerate(TRIMESTER_NAMES):
        if (pcode, _tname) in rainy_set:
            _ax.add_patch(plt.Rectangle(
                (_j - 0.5, -0.5), 1, 12,
                fill=False, edgecolor="royalblue", linewidth=2.5, zorder=5,
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
    _ax.set_xlabel("Valid trimester  (blue outline = rainy season, ≥25% annual rainfall)")
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
        # True conditional mean E[obs|F] = expm1(μ_log + σ²/2) for a log-normal
        _mean_orig = float(np.expm1(_mu_log + _sigma_log**2 / 2))
        _clim_mean_orig = float(_era5_orig.mean())
        _era5_std_log = float(_r["era5_std"])

        # x range covering both climatological and conditional distributions
        _x_min = max(0.0, float(np.expm1(min(_era5_mean_log, _mu_log) - 4 * max(_era5_std_log, _sigma_log))))
        _x_max = max(float(_era5_orig.max()), float(np.expm1(max(_era5_mean_log, _mu_log) + 4 * max(_era5_std_log, _sigma_log))))
        _x = np.linspace(max(1e-6, _x_min), _x_max, 600)

        _PURPLE = "rebeccapurple"

        # Climatological distribution (grey dashed) — ERA5 marginal
        _clim_pdf = norm.pdf(np.log1p(_x), loc=_era5_mean_log, scale=_era5_std_log) / (1 + _x)
        _ax.plot(_x, _clim_pdf, color="grey", linewidth=1.5, linestyle="--", alpha=0.8, zorder=0)

        # Conditional distribution (purple) — given current forecast
        _pdf = norm.pdf(np.log1p(_x), loc=_mu_log, scale=_sigma_log) / (1 + _x)
        _pdf_max = float(max(_pdf.max(), _clim_pdf.max()))

        _ax.plot(_x, _pdf, color=_PURPLE, linewidth=2)
        _low_mask = _x <= _T_orig
        _ax.fill_between(_x[_low_mask], _pdf[_low_mask], color=_PURPLE, alpha=0.30)

        # P(lower tercile) — label outside the shaded region with an arrow pointing in
        if _low_mask.sum() > 0:
            _x_prob = float(_x[_low_mask].mean())
            _idx_prob = int(np.argmin(np.abs(_x - _x_prob)))
            _y_prob_tip = float(_pdf[_idx_prob]) * 0.35  # arrow tip inside shaded area
            _ax.annotate(
                f"P = {_prob:.1%}",
                xy=(_x_prob, _y_prob_tip),
                xytext=(0.06, 0.78),
                xycoords="data", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=_PURPLE, lw=1.5),
                color=_PURPLE, fontsize=12, fontweight="bold", ha="left", va="center",
            )

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
                    xytext=(0, -8), textcoords="offset points",
                    ha="center", fontsize=7, rotation=90, color="royalblue",
                    va="top",
                )

        # Reference lines — vertical inline labels
        _lbl_y = _pdf_max * 0.95
        # Lower tercile: grey bold
        _ax.axvline(_T_orig, color="grey", linewidth=2.5, alpha=0.9)
        _ax.text(_T_orig, _lbl_y, "lower tercile ",
                 color="grey", fontsize=8, rotation=90, ha="right", va="top", fontweight="bold")

        # Climatological mean: thin grey
        _ax.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.5, linewidth=1)

        # Raw SEAS5 forecast: purple solid
        _ax.axvline(_F_orig, color=_PURPLE, linestyle="-", linewidth=2)
        _fcst_lbl = f"{_year} fcst " + ("" if _is_pred else "(verified) ")
        _ax.text(_F_orig, _lbl_y, _fcst_lbl,
                 color=_PURPLE, fontsize=8, rotation=90, ha="right", va="top")

        # Conditional mean: purple dashed
        if abs(_mean_orig - _F_orig) > 0.001 * max(_clim_mean_orig, 1e-9):
            _ax.axvline(_mean_orig, color=_PURPLE, linestyle="--", linewidth=1.5)
            _ax.text(_mean_orig, _lbl_y, f"cond. mean (r={_r_val:.2f}) ",
                     color=_PURPLE, fontsize=8, rotation=90, ha="right", va="top")

        # Arrow annotations pointing exactly at the peaks of each distribution
        _peak_cond_x = float(_x[np.argmax(_pdf)])
        _peak_cond_y = float(_pdf.max())
        _peak_clim_x = float(_x[np.argmax(_clim_pdf)])
        _peak_clim_y = float(_clim_pdf.max())
        # Predicted distribution: label on the LEFT (arrow points right to peak)
        _ax.annotate(
            f"predicted\ndistribution ({_year})",
            xy=(_peak_cond_x, _peak_cond_y),
            xytext=(0.12, 0.92),
            xycoords="data", textcoords="axes fraction",
            arrowprops=dict(arrowstyle="-|>", color=_PURPLE, lw=1.2),
            color=_PURPLE, fontsize=8, ha="center", va="top",
        )
        # Climatology: label on the RIGHT (arrow points left to peak)
        _ax.annotate(
            "climatology",
            xy=(_peak_clim_x, _peak_clim_y),
            xytext=(0.88, 0.92),
            xycoords="data", textcoords="axes fraction",
            arrowprops=dict(arrowstyle="-|>", color="grey", lw=1.2),
            color="grey", fontsize=8, ha="center", va="top",
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
    _face_colors = ["rebeccapurple" if t == trimester else "lightgrey" for t in _df_clim["trimester"]]
    _edge_colors = ["royalblue" if _is_rainy.get(t, False) else "none" for t in _df_clim["trimester"]]
    _fig_clim, _ax = plt.subplots(figsize=(10, 4), dpi=150)
    _ax.bar(
        _df_clim["trimester"], _df_clim["mean_mm_day"],
        color=_face_colors, edgecolor=_edge_colors, linewidth=2.5,
    )
    _ax.set_xlabel("Trimester  (blue outline = rainy season, ≥25% of annual rainfall)")
    _ax.set_ylabel("Mean daily rainfall (mm/day) [ERA5]")
    _ax.set_title(f"{_country} — ERA5 trimester climatology (selected trimester highlighted)")
    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig_clim
    return


@app.cell
def _(df_paired, df_skill, issued_month, np, pcode, plt, trimester):
    _df_hp = df_paired[
        (df_paired["pcode"] == pcode)
        & (df_paired["issued_month"] == issued_month)
        & (df_paired["trimester"] == trimester)
        & df_paired["hist_prob"].notna()
        & df_paired["obs_mean"].notna()
    ].copy()

    _skill_row_hp = df_skill[
        (df_skill["pcode"] == pcode)
        & (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
    ]

    _fig_hp, _ax = plt.subplots(figsize=(5, 5), dpi=150)

    if _df_hp.empty or _skill_row_hp.empty:
        _ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=_ax.transAxes)
        _ax.set_axis_off()
    else:
        _tercile_log = _df_hp["obs_mean"].quantile(1 / 3)
        _T_obs_orig = float(np.expm1(_tercile_log))
        _df_hp["was_lower"] = _df_hp["obs_mean"] <= _tercile_log
        _df_hp["obs_orig"] = np.expm1(_df_hp["obs_mean"])

        _colors_hp = ["royalblue" if v else "lightcoral" for v in _df_hp["was_lower"]]
        _ax.scatter(_df_hp["hist_prob"], _df_hp["obs_orig"], c=_colors_hp, s=40, alpha=0.85, zorder=3)
        for _, _yr in _df_hp.iterrows():
            _ax.annotate(
                str(int(_yr["season_year"])), (_yr["hist_prob"], _yr["obs_orig"]),
                xytext=(3, 2), textcoords="offset points", fontsize=6, color="k",
            )

        _ax.axvline(1 / 3, color="grey", linestyle="--", linewidth=0.8, alpha=0.6)
        _ax.axhline(_T_obs_orig, color="grey", linewidth=1.5, linestyle="--")
        _ax.text(0.99, _T_obs_orig, "lower tercile (obs)",
                 ha="right", va="bottom", fontsize=7, color="grey")
        _ax.set_xlim(0, 1)
        _ax.set_xlabel("Predicted P(lower tercile)")
        _ax.set_ylabel("Observed ERA5 rainfall (mm/day)")
        _ax.set_title(
            f"{_skill_row_hp.iloc[0]['country_name']} — predicted probability vs. observations\n"
            f"Blue = year was in lower tercile  |  Red = above tercile"
        )
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_hp
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


@app.cell
def _(mo):
    mo.md("""
    ## Step-by-step: how we build the probability estimate
    """)
    return


@app.cell
def _(
    calendar,
    df_paired,
    df_skill,
    issued_month,
    mo,
    norm,
    np,
    pcode,
    pd,
    plt,
    trimester,
):
    import io as _io, base64 as _b64

    _PURPLE_TAB = "rebeccapurple"

    def _fig_to_html(fig):
        _buf = _io.BytesIO()
        fig.savefig(_buf, format="png", dpi=130, bbox_inches="tight")
        _buf.seek(0)
        _enc = _b64.b64encode(_buf.read()).decode()
        plt.close(fig)
        return mo.Html(f'<img src="data:image/png;base64,{_enc}" style="max-width:100%"/>')

    # ── Shared data ────────────────────────────────────────────────────────────
    _row = df_skill[
        (df_skill["pcode"] == pcode)
        & (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
    ]
    _df_tab = df_paired[
        (df_paired["pcode"] == pcode)
        & (df_paired["issued_month"] == issued_month)
        & (df_paired["trimester"] == trimester)
        & df_paired["obs_mean"].notna()
    ].copy()

    if _row.empty or _df_tab.empty or pd.isna(_row.iloc[0]["pearson_r"]):
        _tabs_content = mo.md("*Insufficient data for this selection.*")
    else:
        _r = _row.iloc[0]
        _r_val = float(_r["pearson_r"])
        _sigma_log = max(float(_r["sigma"]), 1e-9)
        _era5_mean_log = float(_r["era5_mean"])
        _era5_std_log = float(_r["era5_std"])
        _F_log = float(_r["current_forecast_mean"])
        _mu_log = (1 - _r_val) * _era5_mean_log + _r_val * _F_log
        _T_orig = float(_r["lower_tercile_mm"])
        _prob = float(_r["prob_lower_tercile"])
        _year = int(_r["current_forecast_year"])
        _F_orig = float(np.expm1(_F_log))
        _mean_orig = float(np.expm1(_mu_log + _sigma_log**2 / 2))
        _era5_log = _df_tab.drop_duplicates("season_year")["obs_mean"].sort_values().values
        _era5_orig = np.expm1(_era5_log)

        _x_min = max(1e-6, float(np.expm1(min(_era5_mean_log, _mu_log) - 4 * max(_era5_std_log, _sigma_log))))
        _x_max = max(float(_era5_orig.max()), float(np.expm1(max(_era5_mean_log, _mu_log) + 4 * max(_era5_std_log, _sigma_log))))
        _x = np.linspace(_x_min, _x_max, 500)
        _clim_pdf = norm.pdf(np.log1p(_x), loc=_era5_mean_log, scale=_era5_std_log) / (1 + _x)
        _cond_pdf = norm.pdf(np.log1p(_x), loc=_mu_log, scale=_sigma_log) / (1 + _x)
        _pdf_max = float(max(_clim_pdf.max(), _cond_pdf.max())) * 1.05
        _clim_mean_orig = float(np.expm1(_era5_mean_log + _era5_std_log**2 / 2))
        _issued_str = calendar.month_abbr[issued_month]

        def _base_ax(ax):
            ax.set_xlim(_x_min, _x_max)
            ax.set_ylim(0, _pdf_max)
            ax.set_xlabel("Mean daily rainfall (mm/day)")
            ax.set_ylabel("Probability density")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        # ── Tab 1: Before / after normalization (synthetic illustration) ────
        # "Before": SEAS5 has a systematic wet bias and different spread in log-space.
        # We illustrate this with plausible dummy offsets since raw SEAS5 is not stored.
        _s_mean_raw = _era5_mean_log + 0.28   # ~32% wet bias in log-space before norm
        _s_std_raw = _era5_std_log * 1.20      # slightly more spread
        _pdf_s_raw = norm.pdf(np.log1p(_x), loc=_s_mean_raw, scale=_s_std_raw) / (1 + _x)
        _pdf_e = norm.pdf(np.log1p(_x), loc=_era5_mean_log, scale=_era5_std_log) / (1 + _x)

        _fig1, (_axB, _axA) = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
        # Before
        _axB.plot(_x, _pdf_e, color="grey", linewidth=2, linestyle="--", label="ERA5")
        _axB.plot(_x, _pdf_s_raw, color=_PURPLE_TAB, linewidth=2, label="SEAS5 (raw)")
        _axB.axvline(float(np.expm1(_s_mean_raw + _s_std_raw**2/2)), color=_PURPLE_TAB, linestyle=":", alpha=0.7)
        _axB.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.7)
        _axB.text(0.5, 0.97, "Different mean & spread\n→ probabilities not comparable",
                  transform=_axB.transAxes, ha="center", va="top", fontsize=8,
                  color="darkred", bbox=dict(boxstyle="round", facecolor="mistyrose", alpha=0.7))
        _axB.set_title("Before normalization")
        _axB.set_xlabel("Mean daily rainfall (mm/day)")
        _axB.set_ylabel("Probability density")
        _axB.legend(fontsize=8)
        _axB.spines["top"].set_visible(False)
        _axB.spines["right"].set_visible(False)
        # After
        _axA.plot(_x, _pdf_e, color="grey", linewidth=2, linestyle="--", label="ERA5")
        _axA.plot(_x, _clim_pdf, color=_PURPLE_TAB, linewidth=2, label="SEAS5 (normalized)")
        _axA.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.7)
        _axA.text(0.5, 0.97, "Same mean & spread\n→ directly comparable",
                  transform=_axA.transAxes, ha="center", va="top", fontsize=8,
                  color="darkgreen", bbox=dict(boxstyle="round", facecolor="#e8f5e9", alpha=0.8))
        _axA.set_title("After normalization")
        _axA.set_xlabel("Mean daily rainfall (mm/day)")
        _axA.legend(fontsize=8)
        _axA.spines["top"].set_visible(False)
        _axA.spines["right"].set_visible(False)
        _fig1.suptitle(f"Step 1 — Log-transform + normalization  ({_r['country_name']}, {_issued_str}, {trimester})",
                       fontsize=10)
        plt.tight_layout()

        # ── Tab 2: Climatology + reference lines ────────────────────────────
        _fig2, _ax2 = plt.subplots(figsize=(7, 4))
        _ax2.plot(_x, _clim_pdf, color="grey", linewidth=2, linestyle="--")
        _ax2.plot(_era5_orig, np.zeros_like(_era5_orig), "|",
                  color="royalblue", markersize=15, markeredgewidth=2, alpha=0.7)
        _ax2.axvline(_T_orig, color="grey", linewidth=2.5)
        _ax2.text(_T_orig, _pdf_max * 0.9, "lower tercile ",
                  rotation=90, ha="right", va="top", color="grey", fontsize=8, fontweight="bold")
        _ax2.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.6)
        _ax2.text(_clim_mean_orig, _pdf_max * 0.9, "clim mean ",
                  rotation=90, ha="right", va="top", color="grey", fontsize=8)
        _ax2.set_title("Step 2 — ERA5 climatological distribution\n"
                       "Without a forecast, 33% of seasons fall below the lower tercile by definition")
        _base_ax(_ax2)
        plt.tight_layout()

        # ── Tab 3: Add normalized forecast ────────────────────────────────
        _fig3, _ax3 = plt.subplots(figsize=(7, 4))
        _ax3.plot(_x, _clim_pdf, color="grey", linewidth=2, linestyle="--", alpha=0.7)
        _ax3.plot(_era5_orig, np.zeros_like(_era5_orig), "|",
                  color="royalblue", markersize=15, markeredgewidth=2, alpha=0.7)
        _ax3.axvline(_T_orig, color="grey", linewidth=2, alpha=0.7)
        _ax3.axvline(_F_orig, color=_PURPLE_TAB, linestyle="-", linewidth=2.5)
        _ax3.text(_F_orig, _pdf_max * 0.9, f" {_year} SEAS5\n forecast",
                  color=_PURPLE_TAB, fontsize=9, va="top")
        _ax3.set_title("Step 3 — Normalized SEAS5 forecast\n"
                       "Solid purple: where the normalized forecast sits on the ERA5 scale")
        _base_ax(_ax3)
        plt.tight_layout()

        # ── Tab 4: Shrinkage / conditional mean ────────────────────────────
        _fig4, _ax4 = plt.subplots(figsize=(7, 4))
        _ax4.plot(_x, _clim_pdf, color="grey", linewidth=2, linestyle="--", alpha=0.7)
        _ax4.plot(_era5_orig, np.zeros_like(_era5_orig), "|",
                  color="royalblue", markersize=15, markeredgewidth=2, alpha=0.7)
        _ax4.axvline(_T_orig, color="grey", linewidth=2, alpha=0.7)
        _ax4.axvline(_F_orig, color=_PURPLE_TAB, linestyle="-", linewidth=1.5, alpha=0.5)
        _ax4.axvline(_mean_orig, color=_PURPLE_TAB, linestyle="--", linewidth=2.5)
        _ax4.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.5)
        _ax4.annotate("", xy=(_mean_orig, _pdf_max * 0.6), xytext=(_F_orig, _pdf_max * 0.6),
                      arrowprops=dict(arrowstyle="->", color=_PURPLE_TAB, lw=1.5))
        _ax4.text((_F_orig + _mean_orig) / 2, _pdf_max * 0.65,
                  f"shrinkage (r={_r_val:.2f})", ha="center", fontsize=8, color=_PURPLE_TAB)
        _ax4.text(0.03, 0.95,
                  f"E[obs | F] = (1−r)·μ + r·F\n"
                  f"= {1-_r_val:.2f}·{_clim_mean_orig:.3f} + {_r_val:.2f}·{_F_orig:.3f}\n"
                  f"= {_mean_orig:.3f}",
                  transform=_ax4.transAxes, va="top", fontsize=9, color=_PURPLE_TAB,
                  bbox=dict(boxstyle="round", facecolor="lavender", alpha=0.85))
        _ax4.set_title("Step 4 — Regression shrinkage\n"
                       "Dashed purple: conditional mean pulled toward climatology by factor (1−r)")
        _base_ax(_ax4)
        plt.tight_layout()

        # ── Tab 5: Conditional distribution ───────────────────────────────
        _fig5, _ax5 = plt.subplots(figsize=(7, 4))
        _ax5.plot(_x, _clim_pdf, color="grey", linewidth=2, linestyle="--", alpha=0.7)
        _ax5.plot(_era5_orig, np.zeros_like(_era5_orig), "|",
                  color="royalblue", markersize=15, markeredgewidth=2, alpha=0.7)
        _ax5.plot(_x, _cond_pdf, color=_PURPLE_TAB, linewidth=2.5)
        _ax5.axvline(_T_orig, color="grey", linewidth=2, alpha=0.7)
        _ax5.axvline(_mean_orig, color=_PURPLE_TAB, linestyle="--", linewidth=1.5)
        _ax5.text(0.97, 0.95,
                  f"Width = SE of regression\nσ_log = {_sigma_log:.3f}",
                  transform=_ax5.transAxes, ha="right", va="top", fontsize=9,
                  bbox=dict(boxstyle="round", facecolor="lavender", alpha=0.85))
        _ax5.set_title("Step 5 — Conditional distribution\n"
                       "Purple curve: distribution of actual rainfall given this forecast\n"
                       "Width = standard error of the regression (narrower = more skill)")
        _base_ax(_ax5)
        plt.tight_layout()

        # ── Tab 6: Probability ─────────────────────────────────────────────
        _fig6, _ax6 = plt.subplots(figsize=(7, 4))
        _ax6.plot(_x, _clim_pdf, color="grey", linewidth=2, linestyle="--", alpha=0.7)
        _ax6.plot(_era5_orig, np.zeros_like(_era5_orig), "|",
                  color="royalblue", markersize=15, markeredgewidth=2, alpha=0.7)
        _ax6.plot(_x, _cond_pdf, color=_PURPLE_TAB, linewidth=2.5)
        _low = _x <= _T_orig
        _ax6.fill_between(_x[_low], _cond_pdf[_low], color=_PURPLE_TAB, alpha=0.35)
        _ax6.axvline(_T_orig, color="grey", linewidth=2.5)
        _ax6.axvline(_mean_orig, color=_PURPLE_TAB, linestyle="--", linewidth=1.5)
        _ax6.text(_T_orig, _pdf_max * 0.9, "lower tercile ",
                  rotation=90, ha="right", va="top", color="grey", fontsize=8, fontweight="bold")
        if _low.sum() > 0:
            _xc = float(_x[_low].mean())
            _ic = int(np.argmin(np.abs(_x - _xc)))
            _ax6.text(_xc, float(_cond_pdf[_ic]) * 0.45, f"P = {_prob:.1%}",
                      ha="center", va="center", fontsize=12, fontweight="bold", color="white")
        _ax6.set_title("Step 6 — Lower-tercile probability\n"
                       "Shaded area = P(rainfall below lower tercile) given this forecast and skill level")
        _base_ax(_ax6)
        plt.tight_layout()

        _tabs_content = mo.ui.tabs({
            "1 — Normalization": _fig_to_html(_fig1),
            "2 — Climatology": _fig_to_html(_fig2),
            "3 — Forecast": _fig_to_html(_fig3),
            "4 — Shrinkage": _fig_to_html(_fig4),
            "5 — Conditional dist.": _fig_to_html(_fig5),
            "6 — Probability": _fig_to_html(_fig6),
        })

    _tabs_content
    return


@app.cell
def _(mo):
    mo.md("""
    ### Examples: how skill and forecast value interact

    Below are four scenarios using the selected country's climatological parameters
    (mean and variability), but with different combinations of skill (Pearson r) and
    forecast dryness. This illustrates how the model behaves across cases.
    """)
    return


@app.cell
def _(df_skill, issued_month, mo, norm, np, pcode, pd, plt, trimester):
    import io as _io2, base64 as _b642

    _row_ex = df_skill[
        (df_skill["pcode"] == pcode)
        & (df_skill["issued_month"] == issued_month)
        & (df_skill["trimester"] == trimester)
    ]
    _fig_ex = None

    if not _row_ex.empty and pd.notna(_row_ex.iloc[0]["era5_mean"]):
        _re = _row_ex.iloc[0]
        _eml = float(_re["era5_mean"])
        _esl = float(_re["era5_std"])
        _T_ex = float(_re["lower_tercile_mm"])
        _T_log = float(np.log1p(_T_ex))
        _clim_mean_ex = float(np.expm1(_eml + _esl**2 / 2))

        # 4 example scenarios: (label, r, F_percentile)
        _examples = [
            ("High skill\ndry forecast",   0.80, 0.10),
            ("Low skill\ndry forecast",    0.10, 0.10),
            ("Medium skill\nvery dry",     0.50, 0.05),
            ("Medium skill\nwet forecast", 0.50, 0.90),
        ]

        _x_ex_min = max(1e-6, float(np.expm1(_eml - 4.5 * _esl)))
        _x_ex_max = float(np.expm1(_eml + 4.5 * _esl))
        _x_ex = np.linspace(_x_ex_min, _x_ex_max, 500)
        _clim_ex = norm.pdf(np.log1p(_x_ex), loc=_eml, scale=_esl) / (1 + _x_ex)
        _clim_ex_max = float(_clim_ex.max())

        _PURPLE_EX = "rebeccapurple"
        _fig_ex, _axes = plt.subplots(1, 4, figsize=(14, 4), sharey=True)

        for (_ax_i, (_lbl, _r_i, _pctile)) in zip(_axes, _examples):
            # Forecast in log-space at the given percentile
            _F_log_i = _eml + norm.ppf(_pctile) * _esl
            _F_orig_i = float(np.expm1(_F_log_i))
            _mu_log_i = (1 - _r_i) * _eml + _r_i * _F_log_i
            _sigma_log_i = _esl * float(np.sqrt(max(1 - _r_i**2, 0)))
            _mean_orig_i = float(np.expm1(_mu_log_i + _sigma_log_i**2 / 2))

            _cond_i = norm.pdf(np.log1p(_x_ex), loc=_mu_log_i, scale=max(_sigma_log_i, 1e-9)) / (1 + _x_ex)
            _pdf_top_i = float(max(_cond_i.max(), _clim_ex_max)) * 1.1
            _low_i = _x_ex <= _T_ex
            _prob_i = float(norm.cdf(_T_log, loc=_mu_log_i, scale=max(_sigma_log_i, 1e-9)))

            _ax_i.plot(_x_ex, _clim_ex, color="grey", linewidth=1.5, linestyle="--", alpha=0.8)
            _ax_i.plot(_x_ex, _cond_i, color=_PURPLE_EX, linewidth=2)
            _ax_i.fill_between(_x_ex[_low_i], _cond_i[_low_i], color=_PURPLE_EX, alpha=0.30)
            _ax_i.axvline(_T_ex, color="grey", linewidth=2)
            _ax_i.axvline(_F_orig_i, color=_PURPLE_EX, linestyle="-", linewidth=1.5, alpha=0.6)
            _ax_i.axvline(_mean_orig_i, color=_PURPLE_EX, linestyle="--", linewidth=1.5)

            if _low_i.sum() > 0:
                _xc_i = float(_x_ex[_low_i].mean())
                _ic_i = int(np.argmin(np.abs(_x_ex - _xc_i)))
                _y_i = float(_cond_i[_ic_i]) * 0.45
                _ax_i.text(_xc_i, _y_i, f"P={_prob_i:.0%}",
                           ha="center", va="center", fontsize=10, fontweight="bold", color="white")

            _ax_i.set_title(f"{_lbl}\n(r={_r_i}, fcst={int(_pctile*100)}th pctile)", fontsize=9)
            _ax_i.set_xlabel("Rainfall (mm/day)", fontsize=8)
            _ax_i.set_xlim(_x_ex_min, _x_ex_max)
            _ax_i.set_ylim(0, _pdf_top_i)
            _ax_i.spines["top"].set_visible(False)
            _ax_i.spines["right"].set_visible(False)
            _ax_i.tick_params(labelsize=7)

        _axes[0].set_ylabel("Probability density")
        _fig_ex.suptitle("Solid purple = raw forecast  |  Dashed purple = conditional mean  |  Grey bold = lower tercile",
                         fontsize=8, color="grey")
        plt.tight_layout()

        _buf_ex = _io2.BytesIO()
        _fig_ex.savefig(_buf_ex, format="png", dpi=130, bbox_inches="tight")
        _buf_ex.seek(0)
        _enc_ex = _b642.b64encode(_buf_ex.read()).decode()
        plt.close(_fig_ex)
        _out_ex = mo.Html(f'<img src="data:image/png;base64,{_enc_ex}" style="max-width:100%"/>')
    else:
        _out_ex = mo.md("*Select a country with sufficient data to see examples.*")
    _out_ex
    return


if __name__ == "__main__":
    app.run()
