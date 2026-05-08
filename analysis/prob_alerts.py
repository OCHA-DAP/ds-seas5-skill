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
    # Default to the trimester whose first month is issued_month + 1 (e.g. May → JJA)
    _default_idx = next(
        (i for i, t in enumerate(valid_trimesters)
         if min((m - _im) % 12 for m in TRIMESTERS[t]) == 1),
        0,
    )
    trimester_sl = mo.ui.slider(0, len(valid_trimesters) - 1, step=1, value=_default_idx)
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
    detrend_sw = mo.ui.dropdown(
        options={"Raw": "raw", "Detrended": "detrended", "Best skill": "best"},
        value="Best skill",
        label="Forecast version:",
    )
    detrend_sw
    return (detrend_sw,)


@app.cell
def _(df_skill, df_skill_dt, detrend_sw, pd):
    _KEY = ["pcode", "issued_month", "trimester"]
    if detrend_sw.value == "raw":
        df_skill_active = df_skill
        best_dt_combos  = set()
    elif detrend_sw.value == "detrended":
        df_skill_active = df_skill_dt
        best_dt_combos  = set()
    else:  # "best"
        _r_raw = df_skill.set_index(_KEY)["pearson_r"].fillna(-999)
        _r_dt  = df_skill_dt.set_index(_KEY)["pearson_r"].fillna(-999)
        _all   = _r_raw.index.union(_r_dt.index)
        best_dt_combos = set(
            _all[_r_dt.reindex(_all, fill_value=-999) > _r_raw.reindex(_all, fill_value=-999)]
        )
        _raw_rows = df_skill[~df_skill.set_index(_KEY).index.isin(best_dt_combos)]
        _dt_rows  = df_skill_dt[df_skill_dt.set_index(_KEY).index.isin(best_dt_combos)]
        df_skill_active = pd.concat([_raw_rows, _dt_rows], ignore_index=True)
    return df_skill_active, best_dt_combos


@app.cell
def _(best_dt_combos, df_paired, df_paired_dt, detrend_sw, pd):
    if detrend_sw.value == "raw":
        df_paired_active = df_paired
    elif detrend_sw.value == "detrended":
        df_paired_active = df_paired_dt
    else:  # "best"
        _KEY = ["pcode", "issued_month", "trimester"]
        _raw_rows = df_paired[~df_paired.set_index(_KEY).index.isin(best_dt_combos)]
        _dt_rows  = df_paired_dt[df_paired_dt.set_index(_KEY).index.isin(best_dt_combos)]
        df_paired_active = pd.concat([_raw_rows, _dt_rows], ignore_index=True)
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
    severe_rp_sl      = mo.ui.slider(2,    10,   1,    3,    label="Alert RP (yr)")
    very_severe_rp_sl = mo.ui.slider(5,    25,   1,    10,   label="Severe Alert RP (yr)")
    r_mod_sl          = mo.ui.slider(0.10, 0.60, 0.05, 0.25, label="Moderate skill (r ≥)")
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
def _(TRIMESTERS, calendar, detrend_sw, df_skill_active, issued_month, pd, plt, r_high_sl, r_mod_sl, rainy_only_sw, rainy_set, scatter_rp_sw, severe_rp_sl, trimester, very_severe_rp_sl):
    import matplotlib.patches as _mpatch_sc

    _vsev_rp = very_severe_rp_sl.value
    _sev_rp  = severe_rp_sl.value
    _vsev_pct = 100 / _vsev_rp
    _sev_pct  = 100 / _sev_rp
    _r_mod   = r_mod_sl.value
    _r_high  = r_high_sl.value
    # 4 colours — one per RP level; skill shown via hatching only
    _C_DH = "#7B3A1A"  # severe drought (vsev)
    _C_DM = "#C8844A"  # drought (sev)
    _C_FH = "#0D40B0"  # severe flood (vsev)
    _C_FM = "#3D85C8"  # flood (sev)
    _HATCH = "/////"
    _detrend_sfx = {"raw": "", "detrended": " [detrended]", "best": " [best skill]"}.get(detrend_sw.value, "")

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
        if r < _r_mod: return "#444444"
        _d = pct < 50
        if pct <= _vsev_pct or pct >= 100 - _vsev_pct:
            return _C_DH if _d else _C_FH
        if pct <= _sev_pct or pct >= 100 - _sev_pct:
            return _C_DM if _d else _C_FM
        return "#444444"

    def _label_color_rp(rp_abs, is_drought, r):
        if r < _r_mod: return "#444444"
        if rp_abs >= _vsev_rp: return _C_DH if is_drought else _C_FH
        if rp_abs >= _sev_rp:  return _C_DM if is_drought else _C_FM
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

        plt.close("all")
        _fig, _ax = plt.subplots(figsize=(9, 5.5), dpi=150)
        _ax.set_xlim(-_xlim, _xlim)
        _ax.set_ylim(0, 1.0)

        # Grey background zones (drawn first)
        _zone(_ax, -_xlim, _xlim, 0, _r_mod, "white", "xxxxxx", "#CCCCCC", 1.0)          # low skill (cross hatch)
        _zone(_ax, -_sev_rp, _sev_rp, _r_mod, _r_high, "white", "/////", "#DDDDDD", 1.0)  # mod skill no alert (single hatch)

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
        _tri_str_sc = "-".join(calendar.month_abbr[m] for m in TRIMESTERS[trimester])
        _ax.set_title(f"ECMWF SEAS5 precipitation alerts — forecast issued {calendar.month_name[issued_month]} {_yr}, valid {_tri_str_sc}{_detrend_sfx}")

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

        plt.close("all")
        _fig, _ax = plt.subplots(figsize=(9, 5.5), dpi=150)
        _ax.set_xlim(0, 100)
        _ax.set_ylim(0, 1.0)

        # Grey background zones (drawn first)
        _zone(_ax, 0, 100, 0, _r_mod, "white", "xxxxxx", "#CCCCCC", 1.0)                    # low skill (cross hatch)
        _zone(_ax, _sev_pct, 100 - _sev_pct, _r_mod, _r_high, "white", "/////", "#DDDDDD", 1.0)  # mod skill no alert (single hatch)

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
        _tri_str_sc = "-".join(calendar.month_abbr[m] for m in TRIMESTERS[trimester])
        _ax.set_title(f"ECMWF SEAS5 precipitation alerts — forecast issued {calendar.month_name[issued_month]} {_yr}, valid {_tri_str_sc}{_detrend_sfx}")

    _ax.spines["top"].set_visible(False)
    _ax.spines["right"].set_visible(False)
    plt.tight_layout()
    _fig


@app.cell
def _():
    import geopandas as _gpd
    from pathlib import Path as _Path
    # Pre-simplified committed file (50m Natural Earth, tolerance=0.05, 708 KB)
    world_geo = _gpd.read_file(_Path(__file__).resolve().parent / "_world_countries.gpkg")
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
def _(TRIMESTERS, calendar, detrend_sw, df_skill, df_skill_active, issued_month, map_region_dd, pd, plt, r_high_sl, r_mod_sl, rainy_set, severe_rp_sl, trimester, very_severe_rp_sl, world_geo):
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
        "mid_none":          ("#FFFFFF", "#AAAAAA", "/////",  "#CCCCCC"),
        "low_skill":         ("#FFFFFF", "#AAAAAA", "xxxxxx", "#BBBBBB"),
        "drought_vsev_high": ("#7B3A1A", "#5A2A0A", None,   None),
        "drought_vsev_mod":  ("#7B3A1A", "#5A2A0A", "/////", "white"),  # same colour, hatched
        "drought_sev_high":  ("#C8844A", "#A06030", None,   None),
        "drought_sev_mod":   ("#C8844A", "#A06030", "/////", "white"),  # same colour, hatched
        "flood_vsev_high":   ("#0D40B0", "#092E88", None,   None),
        "flood_vsev_mod":    ("#0D40B0", "#092E88", "/////", "white"),  # same colour, hatched
        "flood_sev_high":    ("#3D85C8", "#2060A0", None,   None),
        "flood_sev_mod":     ("#3D85C8", "#2060A0", "/////", "white"),  # same colour, hatched
    }

    # ── Region bounds ───────────────────────────────────────────────────
    _REGIONS = {
        "global":      {"xlim": (-100, 180), "ylim": (-36, 56)},
        "lac":         {"xlim": (-120, -30), "ylim": (-35, 35)},
        "africa":      {"xlim": (-20, 55),   "ylim": (-36, 38)},
        "asia_europe": {"xlim": (15, 131),   "ylim": (5, 56)},
        "sea_pacific": {"xlim": (85, 180),   "ylim": (-23, 30)},
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

    # ── Assign categories and clip ───────────────────────────────────────
    _gdf = world_geo.dropna(subset=["geometry"]).copy()
    _gdf["cat"] = _gdf["iso3"].map(_iso3_cat).fillna("unmonitored")
    _gdf_clip = _gdf.cx[_xl[0]:_xl[1], _yl[0]:_yl[1]]

    # ── Draw (batched for speed) ──────────────────────────────────────────
    plt.close("all")
    _fig_m, _ax_m = plt.subplots(figsize=(_map_w, _fig_h), dpi=150)

    # Single fill pass: per-row face + edge colours, no separate loop per category
    _fc_map = {c: st[0] for c, st in _STYLE.items()}
    _ec_map = {c: st[1] for c, st in _STYLE.items()}
    _fc_list = [_fc_map.get(c, "#F0F0F0") for c in _gdf_clip["cat"]]
    _ec_list = [_ec_map.get(c, "#DDDDDD") for c in _gdf_clip["cat"]]
    _gdf_clip.plot(ax=_ax_m, color=_fc_list, edgecolor=_ec_list, linewidth=0.3)

    # Hatch overlays — one plot call per unique (hatch, hatch_colour) pair
    _hatch_groups: dict = {}
    for _c, _st in _STYLE.items():
        if _st[2]:
            _hatch_groups.setdefault((_st[2], _st[3]), []).append(_c)
    for (_h, _hc), _cats in _hatch_groups.items():
        _sub = _gdf_clip[_gdf_clip["cat"].isin(_cats)]
        if not _sub.empty:
            _sub.plot(ax=_ax_m, color="none", edgecolor=_hc, hatch=_h, linewidth=0)

    # ── Small island dots ─────────────────────────────────────────────────
    # Iterate over ALL countries (not just clipped) so antimeridian islands
    # (negative-longitude Pacific nations) are included.
    _dot_r = 0.0035 * _dx  # ~12px physical size across all regions
    for _, _row in _gdf[_gdf["cat"] != "unmonitored"].iterrows():
        _geom = _row.geometry
        if _geom is None or _geom.area >= 0.5:
            continue
        _cat_dot = _row["cat"]
        _rp = _geom.representative_point()  # always inside polygon; avoids broken centroids for antimeridian-crossing countries
        _cx, _cy = _rp.x, _rp.y
        # Skip if outside latitude band
        if _cy < _yl[0] or _cy > _yl[1]:
            continue
        # Wrap antimeridian only for global/Pacific views
        if _cx < _xl[0] or _cx > _xl[1]:
            if map_region_dd.value not in ("global", "sea_pacific"):
                continue
            # sea_pacific: only wrap genuine Pacific islands (< -130°); skip Atlantic/Caribbean
            if map_region_dd.value == "sea_pacific" and _cx >= -130:
                continue
            if _cx < _xl[0]:
                _cx_wrap = _cx + 360
                _cx = _cx_wrap if _cx_wrap <= _xl[1] else _xl[1] - _dot_r * 1.5
            else:
                _cx = _xl[1] - _dot_r * 1.5
        _st_dot = _STYLE.get(_cat_dot, ("#E0E0E0", "#AAAAAA", None, None))
        _ax_m.add_patch(_mpatch_m.Circle(
            (_cx, _cy), _dot_r,
            facecolor=_st_dot[0], edgecolor=_st_dot[1],
            linewidth=0.5, zorder=5,
        ))
        if _st_dot[2]:
            _ax_m.add_patch(_mpatch_m.Circle(
                (_cx, _cy), _dot_r,
                facecolor="none", edgecolor=_st_dot[3],
                hatch=_st_dot[2], linewidth=0, zorder=6,
            ))

    _ax_m.set_xlim(_xl)
    _ax_m.set_ylim(_yl)
    _ax_m.set_aspect("equal")
    _ax_m.axis("off")
    _yr_map_s = df_skill_active[(df_skill_active["issued_month"] == issued_month) & (df_skill_active["trimester"] == trimester)]["current_forecast_year"].dropna()
    _yr_map = int(_yr_map_s.max()) if not _yr_map_s.empty else ""
    _tri_str = "-".join(calendar.month_abbr[m] for m in TRIMESTERS[trimester])
    _map_detrend_sfx = {"raw": "", "detrended": " [detrended]", "best": " [best skill]"}.get(detrend_sw.value, "")
    _ax_m.set_title(
        f"ECMWF SEAS5 precipitation alerts — forecast issued {calendar.month_name[issued_month]} {_yr_map}, valid {_tri_str}{_map_detrend_sfx}",
        fontsize=11, pad=8,
    )

    # ── Legend: two labelled rows outside the map ────────────────────────
    _LEG_KW = dict(fontsize=7, framealpha=0.95, edgecolor="#CCCCCC",
                   handlelength=2.0, handletextpad=0.5, title_fontsize=7.5)

    _h_row1 = [
        _mpatch_m.Patch(facecolor="#7B3A1A", edgecolor="#5A2A0A", linewidth=0.5, label="Severe drought"),
        _mpatch_m.Patch(facecolor="#C8844A", edgecolor="#A06030", linewidth=0.5, label="Drought"),
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#AAAAAA", linewidth=0.5, label="Neither"),
        _mpatch_m.Patch(facecolor="#3D85C8", edgecolor="#2060A0", linewidth=0.5, label="Flood"),
        _mpatch_m.Patch(facecolor="#0D40B0", edgecolor="#092E88", linewidth=0.5, label="Severe flood"),
    ]
    _h_row2 = [
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#CCCCCC", hatch="/////",  linewidth=0.5, label="Mod skill"),
        _mpatch_m.Patch(facecolor="#FFFFFF", edgecolor="#BBBBBB", hatch="xxxxxx", linewidth=0.5, label="Low skill"),
        _mpatch_m.Patch(facecolor="#D0D0D0", edgecolor="#BBBBBB",               linewidth=0.5, label="Off season"),
        _mpatch_m.Patch(facecolor="#F0F0F0", edgecolor="#DDDDDD",               linewidth=0.5, label="Not monitored"),
    ]

    # Place axes to fill exactly the map slice — no tight_layout gaps
    plt.subplots_adjust(
        left=0.0, right=1.0,
        bottom=_leg_h / _fig_h,
        top=(_leg_h + _map_h) / _fig_h,
    )

    # Two legend rows stacked; each row ≈ 0.40" tall regardless of fig height
    _row_h = 0.40 / _fig_h   # row height in figure fraction
    _axes_bot = _leg_h / _fig_h

    _leg1 = _fig_m.legend(handles=_h_row1, title="Hazard",
                           loc="upper center", bbox_to_anchor=(0.5, _axes_bot),
                           ncol=5, **_LEG_KW)
    _fig_m.add_artist(_leg1)
    _fig_m.legend(handles=_h_row2, title="Filters (high skill prediction unless otherwise indicated)",
                  loc="upper center", bbox_to_anchor=(0.5, _axes_bot - _row_h - 0.003),
                  ncol=4, **_LEG_KW)

    _fig_m


@app.cell
def _(detrend_sw):
    detrend_sw


@app.cell
def _(df_skill_active, issued_month, mo, pd, r_high_sl, r_mod_sl, rainy_set, severe_rp_sl, trimester, very_severe_rp_sl):
    _vsev_rp = very_severe_rp_sl.value
    _sev_rp  = severe_rp_sl.value
    _r_mod   = r_mod_sl.value
    _r_high  = r_high_sl.value

    # Background = category fill colour (matches map); white text on top
    _BG = {
        "drought_vsev_high": "#7B3A1A", "drought_vsev_mod": "#7B3A1A",
        "drought_sev_high":  "#C8844A", "drought_sev_mod":  "#C8844A",
        "flood_vsev_high":   "#0D40B0", "flood_vsev_mod":   "#0D40B0",
        "flood_sev_high":    "#3D85C8", "flood_sev_mod":    "#3D85C8",
    }
    # Thin white diagonal lines layered over the solid fill — text stays readable
    def _stripe(bg):
        return (f"repeating-linear-gradient(45deg,"
                f"rgba(255,255,255,0),rgba(255,255,255,0) 6px,"
                f"rgba(255,255,255,0.35) 6px,rgba(255,255,255,0.35) 8px),"
                f"{bg}")

    # Sort: high-skill first (vsev+high, sev+high), then mod (vsev+mod, sev+mod)
    _SORT = {"vsev_high": 0, "vsev_mod": 1, "sev_high": 2, "sev_mod": 3}

    _df_t = df_skill_active[
        (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
        & df_skill_active["pearson_r"].notna()
        & df_skill_active["forecast_rp"].notna()
        & df_skill_active["flood_rp"].notna()
    ].copy()
    _df_t = _df_t[_df_t["pcode"].apply(lambda p: (p, trimester) in rainy_set)]

    def _categorise(row):
        r = row["pearson_r"]
        if pd.isna(r) or r < _r_mod:
            return None
        sk = "high" if r >= _r_high else "mod"
        pct = row.get("forecast_percentile", 50)
        if pd.notna(pct) and pct < 50:
            rp = row["forecast_rp"]
            if pd.isna(rp): return None
            if rp >= _vsev_rp: return f"drought_vsev_{sk}"
            if rp >= _sev_rp:  return f"drought_sev_{sk}"
        else:
            rp = row["flood_rp"]
            if pd.isna(rp): return None
            if rp >= _vsev_rp: return f"flood_vsev_{sk}"
            if rp >= _sev_rp:  return f"flood_sev_{sk}"
        return None

    _df_t["_cat"]     = _df_t.apply(_categorise, axis=1)
    _df_t             = _df_t[_df_t["_cat"].notna()].copy()
    _df_t["_drought"] = _df_t["_cat"].str.startswith("drought")
    _df_t["_sort"]    = _df_t["_cat"].map(lambda c: _SORT.get("_".join(c.split("_")[1:]), 9))

    def _html_table(df, drought: bool, rp_col: str, rp_label: str) -> str:
        rows = df[df["_drought"] == drought].sort_values(
            ["_sort", rp_col, "pearson_r"], ascending=[True, False, False]
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
            cat    = row["_cat"]
            bg_col = _BG[cat]
            bg_css = _stripe(bg_col) if "_mod" in cat else bg_col
            rp_val = row[rp_col]
            rp_str = f"{rp_val:.1f}" if pd.notna(rp_val) else "—"
            r_str  = f"{row['pearson_r']:.2f}"
            html += (
                f"<tr style='background:{bg_css};border-bottom:1px solid rgba(255,255,255,0.2)'>"
                f"<td style='padding:5px 8px;font-weight:600;color:white'>{row['country_name']}</td>"
                f"<td style='padding:5px 8px;color:white'>{row['iso3']}</td>"
                f"<td style='padding:5px 8px;text-align:right;color:white;font-weight:600'>{rp_str}</td>"
                f"<td style='padding:5px 8px;text-align:right;color:white'>{r_str}</td>"
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
    plt.close("all")
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
def _(TRIMESTERS, df_skill, mo, month_pct_sl, monthly_clim, pcode, plt, trimester, trimester_pct_sl):
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

    plt.close("all")
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
    mo.accordion({"Monthly climatology": _fig_mon})


@app.cell
def _(detrend_sw):
    detrend_sw


@app.cell
def _(df_skill_active, issued_month, mo, pd, pcode, trimester):
    _row = df_skill_active[
        (df_skill_active["pcode"] == pcode)
        & (df_skill_active["issued_month"] == issued_month)
        & (df_skill_active["trimester"] == trimester)
    ]
    _pct = (
        float(_row.iloc[0]["forecast_percentile"])
        if not _row.empty and pd.notna(_row.iloc[0].get("forecast_percentile"))
        else 50.0
    )
    show_drought_rp_sw = mo.ui.switch(label="Show drought RP shading", value=_pct < 50)
    show_flood_rp_sw   = mo.ui.switch(label="Show flood RP shading",   value=_pct >= 50)
    mo.hstack([show_drought_rp_sw, show_flood_rp_sw], justify="start")
    return show_drought_rp_sw, show_flood_rp_sw


@app.cell
def _(
    best_dt_combos,
    calendar,
    detrend_sw,
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

    plt.close("all")
    _fig_scatter2, _ax2 = plt.subplots(figsize=(7, 7), dpi=150)

    if _df_s2.empty or _skill_row2.empty:
        _ax2.text(0.5, 0.5, "No data", ha="center", va="center", transform=_ax2.transAxes)
        _ax2.set_axis_off()
    else:
        _sr2 = _skill_row2.iloc[0]
        # Include current forecast in x range so the forecast line is never clipped
        _cf_orig2_for_range = (
            float(np.expm1(_sr2["current_forecast_mean"]))
            if bool(_sr2.get("is_predictive")) and pd.notna(_sr2.get("current_forecast_mean"))
            else None
        )
        _x_vals = list(_df_s2["forecast_orig"])
        if _cf_orig2_for_range is not None:
            _x_vals.append(_cf_orig2_for_range)
        _xmin, _xmax = min(_x_vals), max(_x_vals)
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

        _is_dt2 = (detrend_sw.value == "detrended") or (
            detrend_sw.value == "best" and (pcode, issued_month, trimester) in best_dt_combos
        )
        _data_lbl2 = f" [{'detrended' if _is_dt2 else 'raw'}]" if detrend_sw.value in ("detrended", "best") else ""
        _ax2.set_title(
            f"{_country2} — issued {calendar.month_abbr[issued_month]}, valid {trimester}{_data_lbl2}"
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
def _(TRIMESTERS, calendar, df_skill, df_skill_dt, issued_month, np, pcode, plt, rainy_set):
    # Valid trimesters sorted by lead time (earliest first)
    _valid = sorted(
        [(name, months) for name, months in TRIMESTERS.items()
         if max((m - issued_month) % 12 for m in months) <= 6],
        key=lambda nm: min((m - issued_month) % 12 for m in nm[1]),
    )
    _tri_names = [t for t, _ in _valid]

    def _skill_row(df):
        return (
            df[(df["pcode"] == pcode) & (df["issued_month"] == issued_month)]
            .set_index("trimester").reindex(_tri_names)
        )

    _df_ov    = _skill_row(df_skill)
    _df_ov_dt = _skill_row(df_skill_dt)
    _country_ov = (
        df_skill[df_skill["pcode"] == pcode]["country_name"].iloc[0]
        if not df_skill[df_skill["pcode"] == pcode].empty else pcode
    )

    _x = np.arange(len(_tri_names))
    _bar_cols = ["royalblue" if (pcode, t) in rainy_set else "lightgrey" for t in _tri_names]
    _C_P = "rebeccapurple"
    _w = 0.38

    plt.close("all")
    _fig_ov, (_ax_r, _ax_p) = plt.subplots(2, 1, figsize=(10, 5), dpi=150, sharex=True)

    # Top: Pearson r — raw (solid) and detrended (hatched, lighter)
    _ax_r.bar(_x - _w/2, _df_ov["pearson_r"].values.astype(float),
              width=_w, color=_bar_cols, alpha=0.80, label="Raw")
    _ax_r.bar(_x + _w/2, _df_ov_dt["pearson_r"].values.astype(float),
              width=_w, color=_bar_cols, alpha=0.40, hatch="/////", edgecolor="white", label="Detrended")
    _ax_r.axhline(0, color="#AAAAAA", linewidth=0.7)
    _ax_r.set_ylim(-1, 1)
    _ax_r.set_ylabel("Pearson r")
    _ax_r.legend(fontsize=7, loc="upper right")
    _ax_r.set_title(
        f"{_country_ov} — skill & forecast severity by trimester"
        f" — issued {calendar.month_abbr[issued_month]}"
    )
    _ax_r.spines["top"].set_visible(False)
    _ax_r.spines["right"].set_visible(False)

    # Bottom: forecast percentile — bars centred on 50th percentile
    _pct_raw = _df_ov["forecast_percentile"].values.astype(float)
    _pct_det = _df_ov_dt["forecast_percentile"].values.astype(float)
    _ax_p.bar(_x - _w/2, _pct_raw - 50,
              bottom=50, width=_w, color=_bar_cols, alpha=0.80, label="Raw")
    _ax_p.bar(_x + _w/2, _pct_det - 50,
              bottom=50, width=_w, color=_bar_cols, alpha=0.40, hatch="/////", edgecolor="white", label="Detrended")
    _ax_p.axhline(50, color=_C_P, linewidth=0.6, linestyle=":", alpha=0.4)
    _ax_p.set_ylim(0, 100)
    _ax_p.set_ylabel("Forecast percentile")
    _ax_p.set_xlabel("Valid trimester  (blue = rainy season)")
    _ax_p.legend(fontsize=7, loc="upper right")
    _ax_p.spines["top"].set_visible(False)
    _ax_p.spines["right"].set_visible(False)

    _ax_p.set_xticks(_x)
    _ax_p.set_xticklabels(_tri_names, rotation=0, fontsize=8)

    plt.tight_layout()
    _fig_ov


@app.cell
def _(calendar, df_paired, issued_month, np, pcode, plt, trimester):
    from scipy.stats import linregress as _linregress

    _raw = (
        df_paired[
            (df_paired["pcode"] == pcode)
            & (df_paired["issued_month"] == issued_month)
            & (df_paired["trimester"] == trimester)
        ]
        .dropna(subset=["forecast_mean", "obs_mean"])
        .sort_values("season_year")
    )

    def _add_trend(ax, x, y, color):
        if len(x) < 3:
            return
        _lr = _linregress(x, y)
        _xfit = np.array([x.min(), x.max()])
        ax.plot(_xfit, _lr.slope * _xfit + _lr.intercept,
                color=color, linewidth=1.4, linestyle="--", alpha=0.8,
                label=f"Trend  p={_lr.pvalue:.3f}")
        ax.legend(fontsize=7, loc="upper right")

    _C_F = "#3D85C8"
    _C_O = "rebeccapurple"
    plt.close("all")
    _fig_ts, (_ax_f, _ax_o) = plt.subplots(2, 1, figsize=(10, 5), dpi=150, sharex=True)

    # Forecast
    if not _raw.empty:
        _yf = np.expm1(_raw["forecast_mean"].values)
        _ax_f.scatter(_raw["season_year"], _yf, color=_C_F, s=20, zorder=3)
        _add_trend(_ax_f, _raw["season_year"].values.astype(float), _yf, _C_F)
    _ax_f.set_ylabel("SEAS5 forecast (mm/day)")
    _ax_f.set_title(
        f"Forecast & reanalysis timeseries — issued {calendar.month_abbr[issued_month]}, valid {trimester}"
    )
    _ax_f.spines["top"].set_visible(False)
    _ax_f.spines["right"].set_visible(False)

    # Observed
    if not _raw.empty:
        _yo = np.expm1(_raw["obs_mean"].values)
        _ax_o.scatter(_raw["season_year"], _yo, color=_C_O, s=20, zorder=3)
        _add_trend(_ax_o, _raw["season_year"].values.astype(float), _yo, _C_O)
    _ax_o.set_ylabel("ERA5 observed (mm/day)")
    _ax_o.set_xlabel("Year")
    _ax_o.spines["top"].set_visible(False)
    _ax_o.spines["right"].set_visible(False)

    plt.tight_layout()
    _fig_ts


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



if __name__ == "__main__":
    app.run()
