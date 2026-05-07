import marimo

__generated_with = "0.23.3"
app = marimo.App(app_title="SEAS5 Skill — Probabilistic Drought Alerts", width="medium")


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
    return PROJECT_PREFIX, TRIMESTER_NAMES, TRIMESTERS


@app.cell
def _(PROJECT_PREFIX, pd, stratus):
    df_skill = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats.parquet", stage="dev"
    )
    df_skill_dt = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats_detrended.parquet", stage="dev"
    )
    _pcodes = df_skill["pcode"].dropna().unique().tolist()
    _engine = stratus.get_engine("prod")
    _ph = ",".join(["%s"] * len(_pcodes))
    with _engine.connect() as _conn:
        _df_era5_all = pd.read_sql(
            f"SELECT pcode, valid_date, mean FROM public.era5 WHERE pcode IN ({_ph})",
            _conn, params=tuple(_pcodes), parse_dates=["valid_date"],
        )
    monthly_clim = (
        _df_era5_all.assign(month=_df_era5_all["valid_date"].dt.month)
        .groupby(["pcode", "month"])["mean"].mean()
        .reset_index().rename(columns={"mean": "mean_mm_day"})
    )
    return df_skill, df_skill_dt, monthly_clim


@app.cell
def _(TRIMESTERS, monthly_clim, pd, trimester_pct_sl, month_pct_sl):
    _mc = monthly_clim.copy()
    _annual = _mc.groupby("pcode")["mean_mm_day"].sum().rename("annual")
    _mc = _mc.merge(_annual.reset_index(), on="pcode")
    _mc["pct_annual"] = _mc["mean_mm_day"] / _mc["annual"]
    _rows = []
    for _tri, _months in TRIMESTERS.items():
        _tri_mc = _mc[_mc["month"].isin(_months)]
        _tri_mean = _tri_mc.groupby("pcode")["mean_mm_day"].mean()
        _tri_annual = _annual.reindex(_tri_mean.index)
        _tri_ok = 3 * _tri_mean / _tri_annual >= trimester_pct_sl.value
        _month_ok = _tri_mc.groupby("pcode")["pct_annual"].min().reindex(_tri_ok.index, fill_value=0) >= month_pct_sl.value
        for _pcode, _is_rainy in (_tri_ok & _month_ok).items():
            _rows.append({"pcode": _pcode, "trimester": _tri, "is_rainy": bool(_is_rainy)})
    _df_r = pd.DataFrame(_rows)
    rainy_set = set(zip(_df_r[_df_r["is_rainy"]]["pcode"], _df_r[_df_r["is_rainy"]]["trimester"]))
    return (rainy_set,)


@app.cell
def _(calendar, df_skill, mo):
    _max_year = int(df_skill["current_forecast_year"].dropna().max())
    _latest_month = int(
        df_skill[df_skill["current_forecast_year"] == _max_year]["issued_month"].max()
    )
    _months_ordered = [((_latest_month - i - 1) % 12) + 1 for i in range(12)]
    issued_month_dd = mo.ui.dropdown(
        options={calendar.month_abbr[m]: m for m in _months_ordered},
        label="Issued month:",
        value=calendar.month_abbr[_latest_month],
    )
    return (issued_month_dd,)


@app.cell
def _(TRIMESTERS, issued_month_dd, mo):
    _im = issued_month_dd.value
    valid_trimesters = [
        name for name, months in TRIMESTERS.items()
        if max((m - _im) % 12 for m in months) <= 6
    ]
    trimester_sl = mo.ui.slider(0, len(valid_trimesters) - 1, step=1, value=0)
    return trimester_sl, valid_trimesters


@app.cell
def _(issued_month_dd, mo, trimester_sl, valid_trimesters):
    issued_month = issued_month_dd.value
    trimester = valid_trimesters[trimester_sl.value]
    mo.hstack([
        issued_month_dd,
        mo.vstack([
            mo.md(f"Valid trimester: **{trimester}**"),
            trimester_sl,
        ], align="start"),
    ], justify="start")
    return issued_month, trimester


@app.cell
def _(mo):
    detrend_sw = mo.ui.switch(value=False, label="Detrend forecast & reanalysis")
    detrend_sw
    return (detrend_sw,)


@app.cell
def _(df_skill, df_skill_dt, detrend_sw):
    df_skill_active = df_skill_dt if detrend_sw.value else df_skill
    return (df_skill_active,)


@app.cell
def _(df_paired, df_paired_dt, detrend_sw):
    df_paired_active = df_paired_dt if detrend_sw.value else df_paired
    return (df_paired_active,)


@app.cell
def _(mo):
    mo.md("## Deterministic")
    return


@app.cell
def _(mo):
    mo.md("### Severity × skill")
    return


@app.cell
def _(mo):
    severe_rp_sl      = mo.ui.slider(2,    10,   1,    3,    label="Severe RP (yr)")
    very_severe_rp_sl = mo.ui.slider(5,    25,   1,    10,   label="Very severe RP (yr)")
    r_mod_sl          = mo.ui.slider(0.10, 0.60, 0.05, 0.30, label="Moderate skill (r ≥)")
    r_high_sl         = mo.ui.slider(0.20, 0.80, 0.05, 0.50, label="High skill (r ≥)")
    mo.hstack([severe_rp_sl, very_severe_rp_sl, r_mod_sl, r_high_sl], justify="start")
    return r_high_sl, r_mod_sl, severe_rp_sl, very_severe_rp_sl


@app.cell
def _(mo):
    rainy_only_sw = mo.ui.switch(label="Show all countries (incl. off-season)", value=False)
    scatter_rp_sw = mo.ui.switch(label="RP view", value=False)
    mo.hstack([rainy_only_sw, scatter_rp_sw], justify="start")
    return rainy_only_sw, scatter_rp_sw


@app.cell
def _(detrend_sw, df_skill_active, issued_month, pd, plt, r_high_sl, r_mod_sl, rainy_only_sw, rainy_set, scatter_rp_sw, severe_rp_sl, trimester, very_severe_rp_sl):
    import matplotlib.patches as _mpatch_sc

    _vsev_rp = very_severe_rp_sl.value
    _sev_rp  = severe_rp_sl.value
    _vsev_pct = 100 / _vsev_rp
    _sev_pct  = 100 / _sev_rp
    _r_mod   = r_mod_sl.value
    _r_high  = r_high_sl.value
    # 8 colors: dark/medium for RP severity, vivid/muted for skill (matches map)
    _C_DVH = "#7B3A1A"  # drought vsev high
    _C_DVM = "#A8623A"  # drought vsev mod
    _C_DSH = "#C8844A"  # drought sev high
    _C_DSM = "#DFAA80"  # drought sev mod
    _C_FVH = "#0D40B0"  # flood vsev high
    _C_FVM = "#2E60B8"  # flood vsev mod
    _C_FSH = "#3D85C8"  # flood sev high
    _C_FSM = "#7AAED8"  # flood sev mod
    # Scatter zone fill colors (just 4 — hatching shows skill within each zone)
    _C_DH = _C_DVH
    _C_DM = _C_DSH
    _C_FH = _C_FVH
    _C_FM = _C_FSH
    _HATCH = "///"
    _detrend_sfx = " [detrended]" if detrend_sw.value else ""

    def _zone(ax, x0, x1, y0, y1, color, hatch=None, hatch_color="white", alpha=0.20):
        ax.add_patch(_mpatch_sc.Rectangle(
            (x0, y0), x1 - x0, y1 - y0,
            facecolor=color, alpha=alpha, linewidth=0, zorder=0,
        ))
        if hatch:
            ax.add_patch(_mpatch_sc.Rectangle(
                (x0, y0), x1 - x0, y1 - y0,
                facecolor="none", edgecolor=hatch_color, hatch=hatch, linewidth=0, zorder=0,
            ))

    def _label_color_pct(pct, r):
        if r < _r_mod:
            return "#444444"
        _d = pct < 50
        _hi = r >= _r_high
        if pct <= _vsev_pct or pct >= 100 - _vsev_pct:
            return (_C_DVH if _hi else _C_DVM) if _d else (_C_FVH if _hi else _C_FVM)
        if pct <= _sev_pct or pct >= 100 - _sev_pct:
            return (_C_DSH if _hi else _C_DSM) if _d else (_C_FSH if _hi else _C_FSM)
        return "#444444"

    def _label_color_rp(rp_abs, is_drought, r):
        if r < _r_mod:
            return "#444444"
        _hi = r >= _r_high
        if rp_abs >= _vsev_rp:
            return (_C_DVH if _hi else _C_DVM) if is_drought else (_C_FVH if _hi else _C_FVM)
        if rp_abs >= _sev_rp:
            return (_C_DSH if _hi else _C_DSM) if is_drought else (_C_FSH if _hi else _C_FSM)
        return "#444444"

    if scatter_rp_sw.value:
        # ── RP view ─────────────────────────────────────────────────────
        _df = df_skill_active[
            (df_skill_active["issued_month"] == issued_month)
            & (df_skill_active["trimester"] == trimester)
            & df_skill_active["forecast_percentile"].notna()
            & df_skill_active["pearson_r"].notna()
            & df_skill_active["forecast_rp"].notna()
            & df_skill_active["flood_rp"].notna()
        ].copy()
        if not rainy_only_sw.value:
            _df = _df[_df["pcode"].apply(lambda p: (p, trimester) in rainy_set)]
        _df["_x"] = _df.apply(
            lambda row: -row["forecast_rp"] if row["forecast_percentile"] < 50 else row["flood_rp"],
            axis=1,
        )
        _max_rp = max(
            _df["forecast_rp"].max() if not _df.empty else _vsev_rp,
            _df["flood_rp"].max()    if not _df.empty else _vsev_rp,
            _vsev_rp,
        )
        _xlim = _max_rp + 2

        _fig, _ax = plt.subplots(figsize=(9, 5.5), dpi=150)
        _ax.set_xlim(-_xlim, _xlim)
        _ax.set_ylim(0, 1.0)

        # Grey background zones (drawn first)
        _zone(_ax, -_xlim, _xlim, 0, _r_mod, "white", "xxxx", "#CCCCCC", 1.0)          # low skill (cross hatch)
        _zone(_ax, -_sev_rp, _sev_rp, _r_mod, _r_high, "white", "///", "#DDDDDD", 1.0)  # mod skill no alert (single hatch)

        # Drought zones (x < 0; more negative = worse)
        _zone(_ax, -_xlim, -_vsev_rp, _r_high, 1.0,    _C_DH)               # vsev + high skill
        _zone(_ax, -_xlim, -_vsev_rp, _r_mod,  _r_high, _C_DH, _HATCH)      # vsev + mod skill
        _zone(_ax, -_vsev_rp, -_sev_rp, _r_high, 1.0,  _C_DM)               # sev + high skill
        _zone(_ax, -_vsev_rp, -_sev_rp, _r_mod, _r_high, _C_DM, _HATCH)     # sev + mod skill
        # Flood zones (x > 0)
        _zone(_ax, _sev_rp, _vsev_rp, _r_high, 1.0,    _C_FM)               # sev + high skill
        _zone(_ax, _sev_rp, _vsev_rp, _r_mod,  _r_high, _C_FM, _HATCH)      # sev + mod skill
        _zone(_ax, _vsev_rp, _xlim,   _r_high, 1.0,    _C_FH)               # vsev + high skill
        _zone(_ax, _vsev_rp, _xlim,   _r_mod,  _r_high, _C_FH, _HATCH)      # vsev + mod skill

        _ax.axvspan(-1, 1, color="#EEEEEE", zorder=0)
        _ax.axvline(0, color="#BBBBBB", linewidth=0.6, zorder=2)
        for _xv in [-_vsev_rp, -_sev_rp, _sev_rp, _vsev_rp]:
            _ax.axvline(_xv, color="#888", linewidth=0.8, linestyle="--", alpha=0.7, zorder=2)
        for _yv in [_r_mod, _r_high]:
            _ax.axhline(_yv, color="#888", linewidth=0.8, linestyle="--", alpha=0.7, zorder=2)

        _ax.text(-_vsev_rp, 0.98, f"{_vsev_rp}yr", ha="center", va="top", fontsize=8, color="#666")
        _ax.text(-_sev_rp,  0.98, f"{_sev_rp}yr",  ha="center", va="top", fontsize=8, color="#666")
        _ax.text( _sev_rp,  0.98, f"{_sev_rp}yr",  ha="center", va="top", fontsize=8, color="#666")
        _ax.text( _vsev_rp, 0.98, f"{_vsev_rp}yr", ha="center", va="top", fontsize=8, color="#666")
        _ax.text(-_xlim + 0.5, _r_mod + 0.01,  f"r = {_r_mod:.2f}",  ha="left", va="bottom", fontsize=8, color="#666")
        _ax.text(-_xlim + 0.5, _r_high + 0.01, f"r = {_r_high:.2f}", ha="left", va="bottom", fontsize=8, color="#666")
        _ax.text(-_xlim * 0.7, 0.01, "← drought", ha="center", va="bottom", fontsize=8, color="#999", style="italic")
        _ax.text( _xlim * 0.7, 0.01, "flood →",   ha="center", va="bottom", fontsize=8, color="#999", style="italic")

        for _, _row in _df.iterrows():
            _r  = _row["pearson_r"]
            _x  = _row["_x"]
            _iso = _row["iso3"]
            _col = _label_color_rp(abs(_x), _row["forecast_percentile"] < 50, _r)
            if _r >= 0:
                _ax.text(_x, _r, _iso, ha="center", va="center",
                         color=_col, fontsize=9, fontweight="bold", zorder=4)
            else:
                _ax.text(_x, 0.025, _iso, ha="center", va="bottom",
                         color=_col, fontsize=9, fontweight="bold", zorder=4)
                _ax.annotate("", xy=(_x, 0.002), xytext=(_x, 0.022),
                             arrowprops=dict(arrowstyle="-|>", color=_col, lw=0.9, mutation_scale=6), zorder=4)

        _ax.set_xlabel("← Drought RP (yr)   |   Flood RP (yr) →")
        _ax.set_ylabel("Skill (Pearson r)")
        _ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: str(int(abs(x)))))
        _yr = int(_df["current_forecast_year"].dropna().max()) if not _df["current_forecast_year"].dropna().empty else "—"
        _ax.set_title(f"Severity × skill (RP) — issued month {issued_month}, valid {trimester}, forecast year {_yr}{_detrend_sfx}")

    else:
        # ── Percentile view (default) ────────────────────────────────────
        _df = df_skill_active[
            (df_skill_active["issued_month"] == issued_month)
            & (df_skill_active["trimester"] == trimester)
            & df_skill_active["forecast_percentile"].notna()
            & df_skill_active["pearson_r"].notna()
        ].copy()
        if not rainy_only_sw.value:
            _df = _df[_df["pcode"].apply(lambda p: (p, trimester) in rainy_set)]

        _fig, _ax = plt.subplots(figsize=(9, 5.5), dpi=150)
        _ax.set_xlim(0, 100)
        _ax.set_ylim(0, 1.0)

        # Grey background zones (drawn first)
        _zone(_ax, 0, 100, 0, _r_mod, "white", "xxxx", "#CCCCCC", 1.0)                    # low skill (cross hatch)
        _zone(_ax, _sev_pct, 100 - _sev_pct, _r_mod, _r_high, "white", "///", "#DDDDDD", 1.0)  # mod skill no alert (single hatch)

        # Drought zones (left)
        _zone(_ax, 0, _vsev_pct, _r_high, 1.0,     _C_DH)                        # vsev + high skill
        _zone(_ax, 0, _vsev_pct, _r_mod,  _r_high,  _C_DH, _HATCH)               # vsev + mod skill
        _zone(_ax, _vsev_pct, _sev_pct, _r_high, 1.0,  _C_DM)                    # sev + high skill
        _zone(_ax, _vsev_pct, _sev_pct, _r_mod,  _r_high, _C_DM, _HATCH)         # sev + mod skill
        # Flood zones (right)
        _zone(_ax, 100 - _sev_pct, 100 - _vsev_pct, _r_high, 1.0, _C_FM)         # sev + high skill
        _zone(_ax, 100 - _sev_pct, 100 - _vsev_pct, _r_mod, _r_high, _C_FM, _HATCH)  # sev + mod skill
        _zone(_ax, 100 - _vsev_pct, 100, _r_high, 1.0,  _C_FH)                   # vsev + high skill
        _zone(_ax, 100 - _vsev_pct, 100, _r_mod,  _r_high, _C_FH, _HATCH)        # vsev + mod skill

        for _xv in [_vsev_pct, _sev_pct, 100 - _sev_pct, 100 - _vsev_pct]:
            _ax.axvline(_xv, color="#888", linewidth=0.8, linestyle="--", alpha=0.7, zorder=2)
        for _yv in [_r_mod, _r_high]:
            _ax.axhline(_yv, color="#888", linewidth=0.8, linestyle="--", alpha=0.7, zorder=2)

        _ax.text(_vsev_pct,       0.98, f"{_vsev_rp}yr", ha="center", va="top", fontsize=8, color="#666")
        _ax.text(_sev_pct,        0.98, f"{_sev_rp}yr",  ha="center", va="top", fontsize=8, color="#666")
        _ax.text(100 - _sev_pct,  0.98, f"{_sev_rp}yr",  ha="center", va="top", fontsize=8, color="#666")
        _ax.text(100 - _vsev_pct, 0.98, f"{_vsev_rp}yr", ha="center", va="top", fontsize=8, color="#666")
        _ax.text(1, _r_mod + 0.01,  f"r = {_r_mod:.2f}",  ha="left", va="bottom", fontsize=8, color="#666")
        _ax.text(1, _r_high + 0.01, f"r = {_r_high:.2f}", ha="left", va="bottom", fontsize=8, color="#666")
        _ax.text(5,  0.01, "← drought", ha="center", va="bottom", fontsize=8, color="#999", style="italic")
        _ax.text(95, 0.01, "flood →",   ha="center", va="bottom", fontsize=8, color="#999", style="italic")

        for _, _row in _df.iterrows():
            _pct = _row["forecast_percentile"]
            _r   = _row["pearson_r"]
            _iso = _row["iso3"]
            _col = _label_color_pct(_pct, _r)
            if _r >= 0:
                _ax.text(_pct, _r, _iso, ha="center", va="center",
                         color=_col, fontsize=9, fontweight="bold", zorder=4)
            else:
                _ax.text(_pct, 0.025, _iso, ha="center", va="bottom",
                         color=_col, fontsize=9, fontweight="bold", zorder=4)
                _ax.annotate("", xy=(_pct, 0.002), xytext=(_pct, 0.022),
                             arrowprops=dict(arrowstyle="-|>", color=_col, lw=0.9, mutation_scale=6), zorder=4)

        _ax.set_xlabel("Forecast percentile among historical (0 = driest, 100 = wettest)")
        _ax.set_ylabel("Skill (Pearson r)")
        _yr = int(_df["current_forecast_year"].dropna().max()) if not _df["current_forecast_year"].dropna().empty else "—"
        _ax.set_title(f"Severity × skill — issued month {issued_month}, valid {trimester}, forecast year {_yr}{_detrend_sfx}")

    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig


@app.cell
def _():
    import geopandas as _gpd
    from pathlib import Path as _Path
    _CACHE = _Path(__file__).resolve().parent / "_ne_110m_countries.gpkg"
    if not _CACHE.exists():
        _raw = _gpd.read_file(
            "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
        )
        _raw["iso3"] = _raw["ISO_A3"].where(_raw["ISO_A3"] != "-99", _raw["ISO_A3_EH"])
        _raw[["iso3", "NAME", "geometry"]].rename(columns={"NAME": "name"}).to_file(
            _CACHE, driver="GPKG"
        )
    world_geo = _gpd.read_file(_CACHE)
    return (world_geo,)


@app.cell
def _(mo):
    map_region_dd = mo.ui.dropdown(
        options={
            "Global":      "global",
            "LAC":         "lac",
            "Africa":      "africa",
            "Asia/Europe": "asia_europe",
            "SEA/Pacific": "sea_pacific",
        },
        label="Map region:",
        value="Global",
    )
    map_region_dd
    return (map_region_dd,)


@app.cell
def _(calendar, df_skill, df_skill_active, issued_month, map_region_dd, pd, plt, r_high_sl, r_mod_sl, rainy_set, severe_rp_sl, trimester, very_severe_rp_sl, world_geo):
    import geopandas as _gpd_map  # needed for GeoDataFrame methods in this cell
    import matplotlib.patches as _mpatch_m

    _vsev_m  = 100 / very_severe_rp_sl.value
    _sev_m   = 100 / severe_rp_sl.value
    _rmod_m  = r_mod_sl.value
    _rhigh_m = r_high_sl.value
    _vsev_yr = very_severe_rp_sl.value
    _sev_yr  = severe_rp_sl.value

    # ── Build per-country category ──────────────────────────────────────
    _pcode_to_iso3 = df_skill.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    _monitored_pcodes = set(_pcode_to_iso3.keys())
    _df_m = df_skill_active[
        (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
    ]
    _iso3_cat: dict = {}
    for _, _rm in _df_m.iterrows():
        _pc = _rm["pcode"]
        if _pc not in _monitored_pcodes:
            continue
        _ic  = _pcode_to_iso3[_pc]
        _r   = float(_rm["pearson_r"])           if pd.notna(_rm.get("pearson_r"))           else None
        _pct = float(_rm["forecast_percentile"]) if pd.notna(_rm.get("forecast_percentile")) else None
        if (_pc, trimester) not in rainy_set:
            _iso3_cat[_ic] = "off_season"
        elif _r is None or _pct is None:
            _iso3_cat[_ic] = "no_data"
        elif _r < _rmod_m:
            _iso3_cat[_ic] = "low_skill"
        else:
            _vsev = _pct <= _vsev_m or _pct >= 100 - _vsev_m
            _sev  = (_vsev_m < _pct <= _sev_m) or (100 - _sev_m <= _pct < 100 - _vsev_m)
            _d    = _pct < 50
            _sk   = "high" if _r >= _rhigh_m else "mod"
            if _vsev:
                _iso3_cat[_ic] = f"drought_vsev_{_sk}" if _d else f"flood_vsev_{_sk}"
            elif _sev:
                _iso3_cat[_ic] = f"drought_sev_{_sk}" if _d else f"flood_sev_{_sk}"
            elif _r >= _rhigh_m:
                _iso3_cat[_ic] = "high_none"
            else:
                _iso3_cat[_ic] = "mid_none"

    # ── Styling: (facecolor, edgecolor, hatch, hatch_edgecolor) ────────
    _STYLE = {
        "off_season":        ("#D0D0D0", "#BBBBBB", None,  None),
        "no_data":           ("#E8E8E8", "#CCCCCC", None,  None),
        "high_none":         ("#FFFFFF", "#AAAAAA", None,  None),
        "mid_none":          ("#FFFFFF", "#AAAAAA", "///", "#CCCCCC"),
        "low_skill":         ("#FFFFFF", "#AAAAAA", "xxxx", "#BBBBBB"),
        "drought_vsev_high": ("#7B3A1A", "#5A2A0A", None,  None),
        "drought_vsev_mod":  ("#A8623A", "#7B3A1A", "///", "white"),
        "drought_sev_high":  ("#C8844A", "#A06030", None,  None),
        "drought_sev_mod":   ("#DFAA80", "#C08050", "///", "white"),
        "flood_vsev_high":   ("#0D40B0", "#092E88", None,  None),
        "flood_vsev_mod":    ("#2E60B8", "#0D40B0", "///", "white"),
        "flood_sev_high":    ("#3D85C8", "#2060A0", None,  None),
        "flood_sev_mod":     ("#7AAED8", "#5090B8", "///", "white"),
    }

    # ── Region bounds ───────────────────────────────────────────────────
    _REGIONS = {
        "global":      {"xlim": (-120, 180), "ylim": (-36, 56)},
        "lac":         {"xlim": (-120, -30), "ylim": (-35, 35)},
        "africa":      {"xlim": (-20, 55),   "ylim": (-40, 40)},
        "asia_europe": {"xlim": (15, 131),   "ylim": (5, 56)},
        "sea_pacific": {"xlim": (85, 180),   "ylim": (-36, 30)},
    }
    _reg = _REGIONS[map_region_dd.value]
    _xl, _yl = _reg["xlim"], _reg["ylim"]

    # Compute figsize: map + explicit title slice + legend slice → zero gaps
    _dx = _xl[1] - _xl[0]
    _dy = _yl[1] - _yl[0]
    _map_w   = 12.0
    _map_h   = _map_w * _dy / _dx
    _title_h = 0.30   # inches for title
    _leg_h   = 1.6    # inches for two legend rows
    _fig_h   = _map_h + _title_h + _leg_h

    # ── Clip world to region ─────────────────────────────────────────────
    _gdf = world_geo.dropna(subset=["geometry"]).copy()
    _gdf["cat"] = _gdf["iso3"].map(_iso3_cat).fillna("unmonitored")
    _gdf_clip = _gdf.cx[_xl[0]:_xl[1], _yl[0]:_yl[1]]

    # ── Draw ─────────────────────────────────────────────────────────────
    _fig_m, _ax_m = plt.subplots(figsize=(_map_w, _fig_h), dpi=150)

    # Unmonitored base layer
    _gdf_clip.plot(ax=_ax_m, color="#F0F0F0", edgecolor="#DDDDDD", linewidth=0.3)

    # Each monitored category
    _CAT_ORDER = [
        "off_season", "no_data", "high_none", "mid_none", "low_skill",
        "drought_vsev_high", "drought_vsev_mod", "drought_sev_high", "drought_sev_mod",
        "flood_vsev_high",   "flood_vsev_mod",   "flood_sev_high",   "flood_sev_mod",
    ]
    for _cat in _CAT_ORDER:
        _st = _STYLE[_cat]
        _sub = _gdf_clip[_gdf_clip["cat"] == _cat]
        if _sub.empty:
            continue
        # Solid fill + border
        _sub.plot(ax=_ax_m, color=_st[0], edgecolor=_st[1], linewidth=0.4)
        # Hatch overlay (if any)
        if _st[2]:
            _sub.plot(ax=_ax_m, color="none", edgecolor=_st[3], hatch=_st[2], linewidth=0)

    # Small island dots for SEA/Pacific — all countries with small bbox, incl. unmonitored
    if map_region_dd.value == "sea_pacific":
        for _, _row in _gdf_clip.iterrows():
            _geom = _row.geometry
            if _geom is None:
                continue
            _bb = _geom.bounds
            if (_bb[2] - _bb[0]) * (_bb[3] - _bb[1]) < 3.0:
                _cx, _cy = _geom.centroid.x, _geom.centroid.y
                _cat_dot = _row["cat"]
                if _cat_dot == "unmonitored":
                    _fc_dot, _ec_dot = "#DDDDDD", "#AAAAAA"
                elif _cat_dot in ("high_none", "mid_none", "no_data"):
                    _fc_dot, _ec_dot = "#CCCCCC", "#888888"
                elif _cat_dot == "off_season":
                    _fc_dot, _ec_dot = "#C0C0C0", "#888888"
                else:
                    _st_dot = _STYLE.get(_cat_dot, ("#CCCCCC", "#888888", None, None))
                    _fc_dot, _ec_dot = _st_dot[0], _st_dot[1]
                _ax_m.plot(_cx, _cy, "o", color=_fc_dot, markersize=7, zorder=5,
                           markeredgecolor=_ec_dot, markeredgewidth=0.7)

    _ax_m.set_xlim(_xl)
    _ax_m.set_ylim(_yl)
    _ax_m.set_aspect("equal")
    _ax_m.axis("off")
    _ax_m.set_title(
        f"SEAS5 severity and skill alerts — {calendar.month_name[issued_month]} issued, {trimester} valid",
        fontsize=11, pad=8,
    )

    # ── Legend: two labelled rows outside the map ────────────────────────
    _LEG_KW = dict(fontsize=7, framealpha=0.95, edgecolor="#CCCCCC",
                   handlelength=2.0, handletextpad=0.5, title_fontsize=7.5)

    _h_row1 = [
        _mpatch_m.Patch(facecolor="#7B3A1A", edgecolor="#5A2A0A", linewidth=0.5, label="Very severe drought"),
        _mpatch_m.Patch(facecolor="#C8844A", edgecolor="#A06030", linewidth=0.5, label="Severe drought"),
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#AAAAAA", linewidth=0.5, label="Neither"),
        _mpatch_m.Patch(facecolor="#3D85C8", edgecolor="#2060A0", linewidth=0.5, label="Severe flood"),
        _mpatch_m.Patch(facecolor="#0D40B0", edgecolor="#092E88", linewidth=0.5, label="Very severe flood"),
    ]
    _h_row2 = [
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#CCCCCC", hatch="///",  linewidth=0.5, label="Mod skill"),
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#BBBBBB", hatch="xxxx", linewidth=0.5, label="Low skill"),
        _mpatch_m.Patch(facecolor="#D0D0D0", edgecolor="#BBBBBB",               linewidth=0.5, label="Off season"),
        _mpatch_m.Patch(facecolor="#F0F0F0", edgecolor="#DDDDDD",               linewidth=0.5, label="Not monitored"),
    ]

    # Place axes to fill exactly the map slice — no tight_layout gaps
    plt.subplots_adjust(
        left=0.0, right=1.0,
        bottom=_leg_h / _fig_h,
        top=(_leg_h + _map_h) / _fig_h,
    )

    # Row 1: upper edge flush with axes bottom — no gap
    _axes_bot = _leg_h / _fig_h
    _leg1 = _fig_m.legend(handles=_h_row1, title="Hazard (high skill)",
                           loc="upper center", bbox_to_anchor=(0.5, _axes_bot),
                           ncol=5, **_LEG_KW)
    _fig_m.canvas.draw()
    _leg1_bot = _leg1.get_window_extent().transformed(
        _fig_m.transFigure.inverted()
    ).y0

    _fig_m.add_artist(_leg1)
    # Row 2: hangs immediately below row 1
    _fig_m.legend(handles=_h_row2, title="Filters",
                  loc="upper center", bbox_to_anchor=(0.5, _leg1_bot - 0.003),
                  ncol=4, **_LEG_KW)

    _fig_m


@app.cell
def _(df_skill_active, issued_month, mo, pd, r_high_sl, r_mod_sl, rainy_set, severe_rp_sl, trimester, very_severe_rp_sl):
    _vsev_pct = 100 / very_severe_rp_sl.value
    _sev_pct  = 100 / severe_rp_sl.value
    _r_mod    = r_mod_sl.value
    _r_high   = r_high_sl.value

    _vsev_yr = very_severe_rp_sl.value
    _sev_yr  = severe_rp_sl.value

    _df_t = df_skill_active[
        (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
        & df_skill_active["pearson_r"].notna()
        & df_skill_active["forecast_percentile"].notna()
    ].copy()
    _df_t = _df_t[_df_t["pcode"].apply(lambda p: (p, trimester) in rainy_set)]

    def _tier(pct, r):
        _vsev = pct <= _vsev_pct or pct >= 100 - _vsev_pct
        _sev  = (_vsev_pct < pct <= _sev_pct) or (100 - _sev_pct <= pct < 100 - _vsev_pct)
        if _vsev and r >= _r_high: return 1   # very severe + high skill
        if _vsev and r >= _r_mod:  return 2   # very severe + mod skill
        if _sev  and r >= _r_high: return 3   # severe + high skill
        return 0

    _df_t["_tier"] = _df_t.apply(lambda r: _tier(r["forecast_percentile"], r["pearson_r"]), axis=1)
    _df_t = _df_t[_df_t["_tier"] > 0].copy()
    _df_t["_drought"] = _df_t["forecast_percentile"] < 50

    # (drought, tier): (dark_color, light_color, bg)
    _dark  = {True: "#7B3A1A", False: "#0D40B0"}
    _light = {True: "#C8844A", False: "#3D85C8"}
    _bg    = {(True, 1): "#F9EDE8", (True, 2): "#FDF3EC", (True, 3): "#FDF3EC",
              (False, 1): "#E8EEF9", (False, 2): "#ECF3FD", (False, 3): "#ECF3FD"}

    def _html_table(df, drought: bool, rp_col: str, rp_label: str) -> str:
        rows = df[df["_drought"] == drought].sort_values(
            ["_tier", rp_col, "pearson_r"], ascending=[True, False, False]
        )
        if rows.empty:
            return "<p style='color:#888;font-style:italic;margin:4px 0'>No alerts</p>"
        html = (
            "<table style='border-collapse:collapse;width:100%;font-size:13px'>"
            "<thead><tr style='border-bottom:2px solid #ccc'>"
            f"<th style='text-align:left;padding:5px 8px'>Country</th>"
            f"<th style='text-align:left;padding:5px 8px'>ISO3</th>"
            f"<th style='text-align:right;padding:5px 8px'>{rp_label}</th>"
            f"<th style='text-align:right;padding:5px 8px'>Pearson r</th>"
            "</tr></thead><tbody>"
        )
        for _, row in rows.iterrows():
            tier  = int(row["_tier"])
            dk    = _dark[drought]
            lk    = _light[drought]
            bg    = _bg[(drought, tier)]
            rp_val = row[rp_col]
            rp_str = f"{rp_val:.1f}" if pd.notna(rp_val) else "—"
            r_str  = f"{row['pearson_r']:.2f}"
            if tier == 1:
                name_c, rp_c, r_c = dk, dk, dk
            elif tier == 2:   # very severe + mod skill → highlight RP
                name_c, rp_c, r_c = lk, dk, lk
            else:             # severe + high skill → highlight r
                name_c, rp_c, r_c = lk, lk, dk
            html += (
                f"<tr style='background:{bg};border-bottom:1px solid #e8e8e8'>"
                f"<td style='padding:5px 8px;font-weight:600;color:{name_c}'>{row['country_name']}</td>"
                f"<td style='padding:5px 8px;color:{name_c}'>{row['iso3']}</td>"
                f"<td style='padding:5px 8px;text-align:right;color:{rp_c};font-weight:{'600' if tier==2 else '400'}'>{rp_str}</td>"
                f"<td style='padding:5px 8px;text-align:right;color:{r_c};font-weight:{'600' if tier==3 else '400'}'>{r_str}</td>"
                "</tr>"
            )
        html += "</tbody></table>"
        return html

    _drought_html = _html_table(_df_t, True,  "forecast_rp", "Drought RP (yr)")
    _flood_html   = _html_table(_df_t, False, "flood_rp",    "Flood RP (yr)")

    mo.hstack([
        mo.vstack([mo.md("**Drought alerts**"), mo.Html(_drought_html)]),
        mo.vstack([mo.md("**Flood alerts**"),   mo.Html(_flood_html)]),
    ], justify="start", gap="2rem")


@app.cell
def _(mo):
    mo.md("### Per-country analysis")
    return


@app.cell
def _(df_skill, mo):
    _names_df = (
        df_skill[["pcode", "country_name"]]
        .drop_duplicates()
        .dropna(subset=["pcode", "country_name"])
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
def _(mo):
    trimester_pct_sl = mo.ui.slider(0.10, 0.50, 0.05, 0.25, label="Rainy: trimester ≥ X of annual")
    month_pct_sl     = mo.ui.slider(0.00, 0.20, 0.01, 0.05, label="Rainy: each month ≥ X of annual")
    mo.hstack([trimester_pct_sl, month_pct_sl], justify="start")
    return month_pct_sl, trimester_pct_sl


@app.cell
def _(TRIMESTER_NAMES, TRIMESTERS, df_skill, monthly_clim, pd, pcode, plt, rainy_set, trimester, trimester_pct_sl):
    _mc_p = monthly_clim[monthly_clim["pcode"] == pcode].set_index("month")["mean_mm_day"]
    _df_clim = pd.DataFrame([
        {"trimester": _tri, "mean_mm_day": _mc_p.reindex(_months).mean()}
        for _tri, _months in TRIMESTERS.items()
    ]).set_index("trimester").reindex(TRIMESTER_NAMES).reset_index()
    _country = (
        df_skill[df_skill["pcode"] == pcode]["country_name"].iloc[0]
        if not df_skill[df_skill["pcode"] == pcode].empty else pcode
    )
    _annual_tri = _mc_p.sum()
    _tri_thresh_line = _annual_tri * trimester_pct_sl.value / 3
    _face_colors = ["rebeccapurple" if t == trimester else "lightgrey" for t in _df_clim["trimester"]]
    _edge_colors = ["royalblue" if (pcode, t) in rainy_set else "none" for t in _df_clim["trimester"]]
    _fig_clim2, _ax = plt.subplots(figsize=(10, 4), dpi=150)
    _ax.bar(
        _df_clim["trimester"], _df_clim["mean_mm_day"],
        color=_face_colors, edgecolor=_edge_colors, linewidth=2.5,
    )
    _ax.axhline(_tri_thresh_line, color="#E55", linewidth=1.2, linestyle="--",
                label=f"Trimester threshold ({trimester_pct_sl.value:.0%} of annual ÷ 3 = {_tri_thresh_line:.2f} mm/day)")
    _ax.legend(fontsize=8, loc="upper right")
    _ax.set_xlabel("Trimester  (blue outline = rainy season per current thresholds)")
    _ax.set_ylabel("Mean daily rainfall (mm/day) [ERA5]")
    _ax.set_title(f"{_country} — ERA5 trimester climatology (selected trimester highlighted)")
    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig_clim2


@app.cell
def _(TRIMESTERS, df_skill, month_pct_sl, monthly_clim, pcode, plt, trimester, trimester_pct_sl):
    _country2 = (
        df_skill[df_skill["pcode"] == pcode]["country_name"].iloc[0]
        if not df_skill[df_skill["pcode"] == pcode].empty else pcode
    )

    _mc = monthly_clim[monthly_clim["pcode"] == pcode].set_index("month")["mean_mm_day"]
    _annual2 = _mc.sum()
    _mon_thresh2 = month_pct_sl.value
    _tri_thresh2 = trimester_pct_sl.value
    _tri_months = set(TRIMESTERS[trimester])

    _mon_ok = {m: (_mc.get(m, 0) / _annual2 >= _mon_thresh2) for m in range(1, 13)}

    _tri_is_rainy = {}
    for _tri, _months in TRIMESTERS.items():
        _tri_mean = _mc.reindex(_months).mean()
        _tri_is_rainy[_tri] = (3 * _tri_mean / _annual2 >= _tri_thresh2) and all(_mon_ok.get(m, False) for m in _months)

    _mon_labels = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    _heights = [_mc.get(m, 0) for m in range(1, 13)]
    _face_c = ["rebeccapurple" if m in _tri_months else "lightgrey" for m in range(1, 13)]
    _edge_c = ["royalblue" if _mon_ok[m] else "none" for m in range(1, 13)]

    _fig_mon, _ax_mon = plt.subplots(figsize=(10, 3.5), dpi=150)
    _ax_mon.bar(_mon_labels, _heights, color=_face_c, edgecolor=_edge_c, linewidth=2.5)
    _thresh_line = _mon_thresh2 * _annual2
    _ax_mon.axhline(_thresh_line, color="#E55", linewidth=1.2, linestyle="--",
                    label=f"Monthly threshold ({_mon_thresh2:.0%} of annual = {_thresh_line:.2f} mm/day)")
    _ax_mon.legend(fontsize=8, loc="upper right")
    _ax_mon.set_xlabel("Month  (blue outline = meets monthly threshold; purple = in selected trimester)")
    _ax_mon.set_ylabel("Mean daily rainfall (mm/day)")
    _ax_mon.set_title(f"{_country2} — ERA5 monthly climatology  |  Selected trimester ({trimester}): {'rainy ✓' if _tri_is_rainy.get(trimester) else 'not rainy ✗'}")
    _ax_mon.spines["top"].set_visible(False)
    _ax_mon.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig_mon


@app.cell
def _(mo):
    show_drought_rp_sw = mo.ui.switch(label="Show drought RP shading", value=True)
    show_flood_rp_sw   = mo.ui.switch(label="Show flood RP shading",   value=True)
    mo.hstack([show_drought_rp_sw, show_flood_rp_sw], justify="start")
    return show_drought_rp_sw, show_flood_rp_sw


@app.cell
def _(
    calendar,
    df_paired_active,
    df_skill_active,
    issued_month,
    np,
    pcode,
    pd,
    plt,
    severe_rp_sl,
    show_drought_rp_sw,
    show_flood_rp_sw,
    trimester,
    very_severe_rp_sl,
):
    _df_s2 = df_paired_active[
        (df_paired_active["pcode"] == pcode)
        & (df_paired_active["issued_month"] == issued_month)
        & (df_paired_active["trimester"] == trimester)
        & df_paired_active["obs_mean"].notna()
        & df_paired_active["forecast_mean"].notna()
    ].copy()
    _df_s2["forecast_orig"] = np.expm1(_df_s2["forecast_mean"])
    _df_s2["obs_orig"] = np.expm1(_df_s2["obs_mean"])

    _skill_row2 = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
    ]

    _fig_scatter2, _ax2 = plt.subplots(figsize=(7, 7), dpi=150)

    if _df_s2.empty or _skill_row2.empty:
        _ax2.text(0.5, 0.5, "No data", ha="center", va="center", transform=_ax2.transAxes)
        _ax2.set_axis_off()
    else:
        _sr2 = _skill_row2.iloc[0]
        _xmin, _xmax = _df_s2["forecast_orig"].min(), _df_s2["forecast_orig"].max()
        _ymin, _ymax = _df_s2["obs_orig"].min(), _df_s2["obs_orig"].max()
        _xpad = 0.1 * (_xmax - _xmin) if _xmax > _xmin else 0.1
        _ypad = 0.1 * (_ymax - _ymin) if _ymax > _ymin else 0.1
        _xlim2 = (_xmin - _xpad, _xmax + _xpad)
        _ylim2 = (_ymin - _ypad, _ymax + _ypad)

        _sev_rp  = severe_rp_sl.value
        _vsev_rp = very_severe_rp_sl.value
        _C_DH = "#7B3A1A"
        _C_DM = "#C8844A"
        _C_FH = "#0D40B0"
        _C_FM = "#3D85C8"

        # RP quantile thresholds on both axes
        _x_sev_d  = float(_df_s2["forecast_orig"].quantile(1 / _sev_rp))
        _x_vsev_d = float(_df_s2["forecast_orig"].quantile(1 / _vsev_rp))
        _x_sev_f  = float(_df_s2["forecast_orig"].quantile(1 - 1 / _sev_rp))
        _x_vsev_f = float(_df_s2["forecast_orig"].quantile(1 - 1 / _vsev_rp))
        _y_sev_d  = float(_df_s2["obs_orig"].quantile(1 / _sev_rp))
        _y_vsev_d = float(_df_s2["obs_orig"].quantile(1 / _vsev_rp))
        _y_sev_f  = float(_df_s2["obs_orig"].quantile(1 - 1 / _sev_rp))
        _y_vsev_f = float(_df_s2["obs_orig"].quantile(1 - 1 / _vsev_rp))

        # linewidth=0 removes edge borders so only facecolor is rendered
        if show_drought_rp_sw.value:
            _ax2.axvspan(_xlim2[0], _x_sev_d,  color=_C_DM, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axvspan(_xlim2[0], _x_vsev_d, color=_C_DH, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axhspan(_ylim2[0], _y_sev_d,  color=_C_DM, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axhspan(_ylim2[0], _y_vsev_d, color=_C_DH, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.text(_x_sev_d,  _ylim2[1], f" {_sev_rp}yr",  color=_C_DM, fontsize=7, va="top", ha="center", rotation=90)
            _ax2.text(_x_vsev_d, _ylim2[1], f" {_vsev_rp}yr", color=_C_DH, fontsize=7, va="top", ha="center", rotation=90)
            _ax2.text(_xlim2[1], _y_sev_d,  f" {_sev_rp}yr",  color=_C_DM, fontsize=7, va="center", ha="right")
            _ax2.text(_xlim2[1], _y_vsev_d, f" {_vsev_rp}yr", color=_C_DH, fontsize=7, va="center", ha="right")
        if show_flood_rp_sw.value:
            _ax2.axvspan(_x_sev_f,  _xlim2[1], color=_C_FM, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axvspan(_x_vsev_f, _xlim2[1], color=_C_FH, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axhspan(_y_sev_f,  _ylim2[1], color=_C_FM, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.axhspan(_y_vsev_f, _ylim2[1], color=_C_FH, alpha=0.12, linewidth=0, zorder=-2)
            _ax2.text(_x_sev_f,  _ylim2[1], f" {_sev_rp}yr",  color=_C_FM, fontsize=7, va="top", ha="center", rotation=90)
            _ax2.text(_x_vsev_f, _ylim2[1], f" {_vsev_rp}yr", color=_C_FH, fontsize=7, va="top", ha="center", rotation=90)
            _ax2.text(_xlim2[1], _y_sev_f,  f" {_sev_rp}yr",  color=_C_FM, fontsize=7, va="center", ha="right")
            _ax2.text(_xlim2[1], _y_vsev_f, f" {_vsev_rp}yr", color=_C_FH, fontsize=7, va="center", ha="right")

        # 45-degree reference line (dashed)
        _diag_min = max(_xlim2[0], _ylim2[0])
        _diag_max = min(_xlim2[1], _ylim2[1])
        if _diag_max > _diag_min:
            _ax2.plot([_diag_min, _diag_max], [_diag_min, _diag_max],
                      color="#AAAAAA", linewidth=0.9, linestyle="--", zorder=-1)

        for _, _yr2 in _df_s2.iterrows():
            _ax2.annotate(
                str(int(_yr2["season_year"])),
                (_yr2["forecast_orig"], _yr2["obs_orig"]),
                fontsize=8, ha="center", va="center", color="k", zorder=3,
            )

        if bool(_sr2["is_predictive"]) and pd.notna(_sr2["current_forecast_mean"]):
            _cf_orig2 = float(np.expm1(_sr2["current_forecast_mean"]))
            _ax2.axvline(_cf_orig2, color="mediumorchid", linestyle="--", zorder=-1)
            _ax2.annotate(
                f"  {int(_sr2['current_forecast_year'])} forecast",
                (_cf_orig2, _ylim2[0]),
                rotation=90, va="bottom", ha="right",
                color="mediumorchid", fontstyle="italic",
            )

        _ax2.set_xlim(_xlim2)
        _ax2.set_ylim(_ylim2)

        _r_val2  = float(_sr2["pearson_r"]) if pd.notna(_sr2["pearson_r"]) else float("nan")
        _seas5_t2 = _df_s2["forecast_orig"].quantile(1 / 3)
        _era5_t2  = _df_s2["obs_orig"].quantile(1 / 3)
        _pp2     = _df_s2["forecast_orig"] < _seas5_t2
        _p2      = _df_s2["obs_orig"] < _era5_t2
        _tpr2    = float((_pp2 & _p2).sum() / _p2.sum()) if _p2.sum() > 0 else float("nan")
        _n2      = int(_sr2["n_years"]) if pd.notna(_sr2["n_years"]) else 0
        _country2 = _sr2["country_name"] if "country_name" in _sr2.index else pcode

        _ax2.set_title(
            f"{_country2} — issued {calendar.month_abbr[issued_month]}, valid {trimester}"
            + ("  ⚠ negative skill" if _r_val2 < 0 else ""),
            color="darkred" if _r_val2 < 0 else "black",
        )
        _ax2.set_xlabel(
            f"Normalized SEAS5 forecast (mm/day)\n"
            f"Pearson r = {_r_val2:.2f}  |  Lower-tercile hit rate = {_tpr2:.2f}  |  n = {_n2}"
        )
        _ax2.set_ylabel("ERA5 observed (mm/day)")
        _ax2.spines["top"].set_visible(False)
        _ax2.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_scatter2




@app.cell
def _(PROJECT_PREFIX, stratus):
    # Loaded here so the deterministic section above renders before this completes.
    df_paired = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/paired_yearly.parquet", stage="dev"
    )
    return (df_paired,)


@app.cell
def _(PROJECT_PREFIX, stratus):
    df_paired_dt = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/paired_yearly_detrended.parquet", stage="dev"
    )
    return (df_paired_dt,)


@app.cell
def _(mo):
    mo.md("## Probabilistic")
    return


@app.cell
def _(mo):
    mo.md("""
    ### Ranked drought alert table

    Countries ranked by skill-adjusted probability of below-normal rainfall.
    **⚠** = negative historical correlation (forecast anti-correlated; probability unreliable).
    `fcst_pctile_%` = percentile of current forecast among historical (low = dry forecast).
    Same low percentile + higher Pearson r → higher drought probability, as expected.
    """)
    return


@app.cell
def _(df_skill_active, issued_month, mo, pd, rainy_set, trimester):
    _df_rank = (
        df_skill_active[
            (df_skill_active["issued_month"] == issued_month)
            & (df_skill_active["trimester"] == trimester)
            & df_skill_active["prob_lower_tercile"].notna()
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
def _(TRIMESTER_NAMES, calendar, df_skill_active, np, pcode, plt, rainy_set):
    _df_p = df_skill_active[df_skill_active["pcode"] == pcode]
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
def _(mo):
    show_lines_sw = mo.ui.switch(label="Show forecast reference lines", value=False)
    mo.hstack([show_lines_sw], justify="start")
    return (show_lines_sw,)


@app.cell
def _(
    calendar,
    df_paired_active,
    df_skill_active,
    issued_month,
    norm,
    np,
    pcode,
    pd,
    plt,
    show_lines_sw,
    trimester,
):
    _row = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
    ]
    _has_skill = not _row.empty and pd.notna(_row.iloc[0]["pearson_r"])

    # obs_mean in df_paired_active is log1p-transformed; era5_df has log-space values
    _era5_df = (
        df_paired_active[
            (df_paired_active["pcode"] == pcode)
            & (df_paired_active["trimester"] == trimester)
            & df_paired_active["obs_mean"].notna()
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
        _show_lines = show_lines_sw.value

        _ax.plot(_x, _pdf, color=_PURPLE, linewidth=2)
        _low_mask = _x <= _T_orig
        _ax.fill_between(_x[_low_mask], _pdf[_low_mask], color=_PURPLE, alpha=0.30)

        # P(lower tercile) — label below pred-dist annotation, arrow to middle of fill
        if _low_mask.sum() > 0:
            # Arrow tip at 65% of the way from left edge to T_orig — visually central
            _x_prob_tip = float(_x[_low_mask][0] + 0.65 * (_T_orig - _x[_low_mask][0]))
            _idx_prob = int(np.argmin(np.abs(_x - _x_prob_tip)))
            _y_prob_tip = float(_pdf[_idx_prob]) * 0.55
            _ax.annotate(
                f"P = {_prob:.1%}",
                xy=(_x_prob_tip, _y_prob_tip),
                xytext=(0.06, 0.68),
                xycoords="data", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=_PURPLE, lw=1.5),
                color=_PURPLE, fontsize=12, fontweight="bold", ha="left", va="center",
            )

        # Rug marks — grey to match climatology theme
        _ax.plot(
            _era5_orig, np.zeros_like(_era5_orig), "|",
            color="grey", markersize=18, markeredgewidth=1.5, alpha=0.6,
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
                    ha="center", fontsize=7, rotation=90, color="grey",
                    va="top",
                )

        # Lower tercile always on — bold grey
        _lbl_y = _pdf_max * 1.28  # well above the curve
        _ax.axvline(_T_orig, color="grey", linewidth=2.5, alpha=0.9)
        _ax.text(_T_orig, _lbl_y, "lower tercile ",
                 color="grey", fontsize=8, rotation=90, ha="right", va="top", fontweight="bold")

        # Optional reference lines (toggled by switch)
        if _show_lines:
            _ax.axvline(_clim_mean_orig, color="grey", linestyle=":", alpha=0.5, linewidth=1)
            _ax.axvline(_F_orig, color=_PURPLE, linestyle="-", linewidth=2)
            _fcst_lbl = f"{_year} fcst " + ("" if _is_pred else "(verified) ")
            _ax.text(_F_orig, _lbl_y, _fcst_lbl,
                     color=_PURPLE, fontsize=8, rotation=90, ha="right", va="top")
            if abs(_mean_orig - _F_orig) > 0.001 * max(_clim_mean_orig, 1e-9):
                _ax.axvline(_mean_orig, color=_PURPLE, linestyle="--", linewidth=1.5)
                _ax.text(_mean_orig, _lbl_y, f"cond. mean (r={_r_val:.2f}) ",
                         color=_PURPLE, fontsize=8, rotation=90, ha="right", va="top")

        # Arrow annotations pointing to the SLOPES of each distribution
        _peak_cond_x = float(_x[np.argmax(_pdf)])
        _peak_clim_x = float(_x[np.argmax(_clim_pdf)])
        # Conditional: point to LEFT slope (rising side) — label is on the left
        _left_cond = _x < _peak_cond_x
        if _left_cond.sum() > 0:
            _slope_idx_c = int(np.argmin(np.abs(_pdf[_left_cond] - _pdf.max() * 0.55)))
            _arrow_xc = float(_x[_left_cond][_slope_idx_c])
            _arrow_yc = float(_pdf[_left_cond][_slope_idx_c])
        else:
            _arrow_xc, _arrow_yc = _peak_cond_x, float(_pdf.max())
        # Climatology: point to right slope (falling side)
        _right_clim = _x > _peak_clim_x
        if _right_clim.sum() > 0:
            _slope_idx_g = int(np.argmin(np.abs(_clim_pdf[_right_clim] - _clim_pdf.max() * 0.55)))
            _arrow_xg = float(_x[_right_clim][_slope_idx_g])
            _arrow_yg = float(_clim_pdf[_right_clim][_slope_idx_g])
        else:
            _arrow_xg, _arrow_yg = _peak_clim_x, float(_clim_pdf.max())

        # Predicted distribution: label LEFT, arrow → right slope of conditional
        _ax.annotate(
            f"predicted\ndistribution ({_year})",
            xy=(_arrow_xc, _arrow_yc),
            xytext=(0.10, 0.92),
            xycoords="data", textcoords="axes fraction",
            arrowprops=dict(arrowstyle="-|>", color=_PURPLE, lw=1.2),
            color=_PURPLE, fontsize=8, ha="center", va="top",
        )
        # Climatology: label RIGHT, arrow → right slope of grey curve
        _ax.annotate(
            "climatology",
            xy=(_arrow_xg, _arrow_yg),
            xytext=(0.90, 0.92),
            xycoords="data", textcoords="axes fraction",
            arrowprops=dict(arrowstyle="-|>", color="grey", lw=1.2),
            color="grey", fontsize=8, ha="center", va="top",
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
        _rp_str = f"  |  P-RP: 1-in-{_prob_rp:.0f} yr" if pd.notna(_prob_rp) else ""
        _ax.set_title(
            f"{_r['country_name']} — issued {_issued_str}, valid {trimester}  |  "
            f"r = {_r_val:.2f}  σ_log = {_sigma_log:.3f}{_rp_str}"
        )
        _ax.set_xlabel("Mean daily rainfall (mm/day)")
        _ax.set_ylabel("Probability density")
        _ax.set_ylim(0, _pdf_max * 1.35)  # headroom above peaks for labels
        _ax.spines["top"].set_visible(False)
        _ax.spines["right"].set_visible(False)
        plt.tight_layout()

    _fig_bell
    return




@app.cell
def _(df_paired_active, df_skill_active, issued_month, np, pcode, plt, trimester):
    _df_hp = df_paired_active[
        (df_paired_active["pcode"] == pcode)
        & (df_paired_active["issued_month"] == issued_month)
        & (df_paired_active["trimester"] == trimester)
        & df_paired_active["hist_prob"].notna()
        & df_paired_active["obs_mean"].notna()
    ].copy()

    _skill_row_hp = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
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
def _(df_paired_active, pd, plt):
    # Calibration diagram pooling all (country, issued_month, trimester, year) combinations.
    # For each row, determine if obs_mean was actually in the lower tercile for that
    # (pcode, trimester) — computed within the pooled data (in log-space; monotone so same result).
    _df_cal = df_paired_active[
        df_paired_active["hist_prob"].notna() & df_paired_active["obs_mean"].notna()
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
    df_paired_active,
    df_skill_active,
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
    _row = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
    ]
    _df_tab = df_paired_active[
        (df_paired_active["pcode"] == pcode)
        & (df_paired_active["issued_month"] == issued_month)
        & (df_paired_active["trimester"] == trimester)
        & df_paired_active["obs_mean"].notna()
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
def _(df_skill_active, issued_month, mo, norm, np, pcode, pd, plt, trimester):
    import io as _io2, base64 as _b642

    _row_ex = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
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

        # Pre-compute all conditional PDFs to get a global ylim
        _all_cond_pdfs = []
        for (_, _r_i, _pctile) in _examples:
            _F_log_tmp = _eml + norm.ppf(_pctile) * _esl
            _mu_tmp = (1 - _r_i) * _eml + _r_i * _F_log_tmp
            _s_tmp = _esl * float(np.sqrt(max(1 - _r_i**2, 0)))
            _c_tmp = norm.pdf(np.log1p(_x_ex), loc=_mu_tmp, scale=max(_s_tmp, 1e-9)) / (1 + _x_ex)
            _all_cond_pdfs.append(_c_tmp)
        _global_ymax = max(max(c.max() for c in _all_cond_pdfs), _clim_ex_max) * 1.3

        _fig_ex, _axes_2d = plt.subplots(2, 2, figsize=(10, 8))
        _axes = _axes_2d.flatten()

        for (_ax_i, (_lbl, _r_i, _pctile), _cond_pre) in zip(_axes, _examples, _all_cond_pdfs):
            # Forecast in log-space at the given percentile
            _F_log_i = _eml + norm.ppf(_pctile) * _esl
            _F_orig_i = float(np.expm1(_F_log_i))
            _mu_log_i = (1 - _r_i) * _eml + _r_i * _F_log_i
            _sigma_log_i = _esl * float(np.sqrt(max(1 - _r_i**2, 0)))
            _mean_orig_i = float(np.expm1(_mu_log_i + _sigma_log_i**2 / 2))

            _cond_i = _cond_pre  # pre-computed above
            _pdf_top_i = _global_ymax
            _low_i = _x_ex <= _T_ex
            _prob_i = float(norm.cdf(_T_log, loc=_mu_log_i, scale=max(_sigma_log_i, 1e-9)))

            _ax_i.plot(_x_ex, _clim_ex, color="grey", linewidth=1.5, linestyle="--", alpha=0.8)
            _ax_i.plot(_x_ex, _cond_i, color=_PURPLE_EX, linewidth=2)
            _ax_i.fill_between(_x_ex[_low_i], _cond_i[_low_i], color=_PURPLE_EX, alpha=0.30)
            _ax_i.axvline(_T_ex, color="grey", linewidth=2)
            _ax_i.axvline(_F_orig_i, color=_PURPLE_EX, linestyle="-", linewidth=1.5, alpha=0.6)
            _ax_i.axvline(_mean_orig_i, color=_PURPLE_EX, linestyle="--", linewidth=1.5)

            # Predicted distribution annotation — left slope, top-left label
            _pk_cond_i = float(_x_ex[np.argmax(_cond_i)])
            _left_cond_i = _x_ex < _pk_cond_i
            if _left_cond_i.sum() > 0:
                _ls_idx = int(np.argmin(np.abs(_cond_i[_left_cond_i] - _cond_i.max() * 0.55)))
                _axc_i = float(_x_ex[_left_cond_i][_ls_idx])
                _ayc_i = float(_cond_i[_left_cond_i][_ls_idx])
            else:
                _axc_i, _ayc_i = _pk_cond_i, float(_cond_i.max())
            _ax_i.annotate(
                "predicted\ndistribution",
                xy=(_axc_i, _ayc_i),
                xytext=(0.06, 0.92),
                xycoords="data", textcoords="axes fraction",
                arrowprops=dict(arrowstyle="-|>", color=_PURPLE_EX, lw=1.0),
                color=_PURPLE_EX, fontsize=7, ha="left", va="top",
            )

            # P annotation — below pred dist label, arrow to middle of fill
            if _low_i.sum() > 0:
                _xt_i = float(_x_ex[_low_i][0] + 0.65 * (_T_ex - _x_ex[_low_i][0]))
                _it_i = int(np.argmin(np.abs(_x_ex - _xt_i)))
                _yt_i = float(_cond_i[_it_i]) * 0.55
                _ax_i.annotate(
                    f"P = {_prob_i:.0%}",
                    xy=(_xt_i, _yt_i),
                    xytext=(0.06, 0.68),
                    xycoords="data", textcoords="axes fraction",
                    arrowprops=dict(arrowstyle="-|>", color=_PURPLE_EX, lw=1.2),
                    color=_PURPLE_EX, fontsize=10, fontweight="bold", ha="left", va="center",
                )

            # Climatology annotation — right slope of grey curve
            _pk_clim_i = float(_x_ex[np.argmax(_clim_ex)])
            _right_clim_i = _x_ex > _pk_clim_i
            if _right_clim_i.sum() > 0:
                _sidx_i = int(np.argmin(np.abs(_clim_ex[_right_clim_i] - _clim_ex.max() * 0.55)))
                _axg_i = float(_x_ex[_right_clim_i][_sidx_i])
                _ayg_i = float(_clim_ex[_right_clim_i][_sidx_i])
                _ax_i.annotate(
                    "climatology",
                    xy=(_axg_i, _ayg_i),
                    xytext=(0.88, 0.88),
                    xycoords="data", textcoords="axes fraction",
                    arrowprops=dict(arrowstyle="-|>", color="grey", lw=1.0),
                    color="grey", fontsize=7, ha="center", va="top",
                )

            _ax_i.set_title(f"{_lbl}\n(r={_r_i}, fcst={int(_pctile*100)}th pctile)", fontsize=9)
            _ax_i.set_xlabel("Rainfall (mm/day)", fontsize=8)
            _ax_i.set_xlim(_x_ex_min, _x_ex_max)
            _ax_i.set_ylim(0, _pdf_top_i)
            _ax_i.spines["top"].set_visible(False)
            _ax_i.spines["right"].set_visible(False)
            _ax_i.tick_params(labelsize=7)

        for _axi in [_axes[0], _axes[2]]:
            _axi.set_ylabel("Probability density")
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
