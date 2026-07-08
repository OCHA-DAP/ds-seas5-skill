import marimo

__generated_with = "0.23.3"
app = marimo.App(
    width="medium",
    app_title="ERA5 seasonality explorer",
)


@app.cell
def _():
    import calendar

    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np
    import ocha_stratus as stratus
    import pandas as pd

    return calendar, mo, np, pd, plt, stratus


@app.cell
def _():
    from src.constants import PROJECT_PREFIX, TRIMESTERS

    TRIMESTER_NAMES = list(TRIMESTERS.keys())
    return PROJECT_PREFIX, TRIMESTERS, TRIMESTER_NAMES


@app.cell
def _(PROJECT_PREFIX, pd, stratus):
    df_skill = stratus.load_parquet_from_blob(
        f"{PROJECT_PREFIX}/processed/skill_stats.parquet", stage="dev"
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
    return df_skill, monthly_clim


@app.cell
def _():
    import geopandas as _gpd
    from pathlib import Path as _Path

    world_geo = _gpd.read_file(_Path(__file__).resolve().parent / "_world_countries.gpkg")
    return (world_geo,)


@app.cell
def _(TRIMESTERS, month_pct_sl, monthly_clim, pd, trimester_pct_sl):
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
def _(mo):
    mo.md("# ERA5 seasonality explorer")
    return


@app.cell
def _():
    import anywidget
    import traitlets

    class TriSelector(anywidget.AnyWidget):
        _esm = """
        function render({ model, el }) {
          const SIZE = 210, cx = 105, cy = 105;
          const R_RING = 72, R_LABEL = 86, R_HL = 14;
          const ns = "http://www.w3.org/2000/svg";

          const svg = document.createElementNS(ns, "svg");
          svg.setAttribute("width", SIZE);
          svg.setAttribute("height", SIZE);
          svg.style.cssText = "cursor:pointer;user-select:none;touch-action:none;display:block;";
          el.appendChild(svg);

          const make = (tag, attrs, text) => {
            const el = document.createElementNS(ns, tag);
            for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
            if (text !== undefined) el.textContent = text;
            return el;
          };

          let hover = -1;

          function draw() {
            const sel = model.get("value");
            const names = model.get("trimester_names");
            svg.innerHTML = "";
            svg.appendChild(make("circle", {
              cx, cy, r: R_RING, fill: "none", stroke: "#E4E4E4", "stroke-width": 2
            }));
            for (let i = 0; i < names.length; i++) {
              const angle = (-90 + i * 30) * Math.PI / 180;
              const isSel = i === sel, isHov = i === hover && !isSel;
              const tx1 = cx + (R_RING - 5) * Math.cos(angle);
              const ty1 = cy + (R_RING - 5) * Math.sin(angle);
              const tx2 = cx + (R_RING + 5) * Math.cos(angle);
              const ty2 = cy + (R_RING + 5) * Math.sin(angle);
              svg.appendChild(make("line", {
                x1: tx1.toFixed(1), y1: ty1.toFixed(1),
                x2: tx2.toFixed(1), y2: ty2.toFixed(1),
                stroke: isSel ? "#26A69A" : isHov ? "#9ACFCB" : "#D8D8D8",
                "stroke-width": 2, "stroke-linecap": "round"
              }));
              const lx = cx + R_LABEL * Math.cos(angle);
              const ly = cy + R_LABEL * Math.sin(angle);
              if (isSel || isHov) {
                svg.appendChild(make("circle", {
                  cx: lx.toFixed(1), cy: ly.toFixed(1), r: R_HL,
                  fill: isSel ? "#26A69A" : "#9ACFCB"
                }));
              }
              svg.appendChild(make("text", {
                x: lx.toFixed(1), y: ly.toFixed(1),
                "text-anchor": "middle", "dominant-baseline": "central",
                "font-size": 10, "font-family": "monospace",
                "font-weight": isSel ? "bold" : "normal",
                fill: (isSel || isHov) ? "white" : "#666"
              }, names[i]));
            }
          }

          function angleToIndex(clientX, clientY) {
            const rect = svg.getBoundingClientRect();
            const x = clientX - rect.left - cx;
            const y = clientY - rect.top - cy;
            if (Math.sqrt(x * x + y * y) < 20) return -1;
            let angle = Math.atan2(y, x) * 180 / Math.PI + 90;
            if (angle < 0) angle += 360;
            return Math.round(angle / 30) % 12;
          }

          let dragging = false;

          svg.addEventListener("mousedown", (e) => {
            const i = angleToIndex(e.clientX, e.clientY);
            if (i < 0) return;
            dragging = true;
            model.set("value", i); model.save_changes(); draw();
          });
          svg.addEventListener("mousemove", (e) => {
            const i = angleToIndex(e.clientX, e.clientY);
            if (dragging && i >= 0) { model.set("value", i); model.save_changes(); }
            hover = dragging ? -1 : i;
            draw();
          });
          svg.addEventListener("mouseup", () => { dragging = false; });
          svg.addEventListener("mouseleave", () => { dragging = false; hover = -1; draw(); });
          svg.addEventListener("touchstart", (e) => {
            e.preventDefault();
            const t = e.touches[0], i = angleToIndex(t.clientX, t.clientY);
            if (i < 0) return;
            dragging = true; model.set("value", i); model.save_changes(); draw();
          }, { passive: false });
          svg.addEventListener("touchmove", (e) => {
            e.preventDefault();
            if (!dragging) return;
            const t = e.touches[0], i = angleToIndex(t.clientX, t.clientY);
            if (i >= 0) { model.set("value", i); model.save_changes(); }
            draw();
          }, { passive: false });
          svg.addEventListener("touchend", () => { dragging = false; });

          model.on("change:value", draw);
          draw();
        }
        export default { render };
        """
        value = traitlets.Int(0).tag(sync=True)
        trimester_names = traitlets.List(traitlets.Unicode()).tag(sync=True)

    return (TriSelector,)


@app.cell
def _(TRIMESTER_NAMES, TriSelector, mo):
    tri_widget = mo.ui.anywidget(TriSelector(
        value=TRIMESTER_NAMES.index("JJA"),
        trimester_names=TRIMESTER_NAMES,
    ))
    tri_widget
    return (tri_widget,)


@app.cell
def _(TRIMESTER_NAMES, tri_widget):
    trimester = TRIMESTER_NAMES[tri_widget.value["value"]]
    return (trimester,)


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
def _(
    TRIMESTERS,
    calendar,
    df_skill,
    map_region_dd,
    np,
    plt,
    rainy_set,
    trimester,
    world_geo,
):
    import geopandas as _gpd_m
    import matplotlib.patches as _mpatch_m

    _pcode_to_iso3 = df_skill.drop_duplicates("pcode").set_index("pcode")["iso3"].to_dict()
    _iso3_cat: dict = {}
    for _pcode, _iso3 in _pcode_to_iso3.items():
        _iso3_cat[_iso3] = "in_season" if (_pcode, trimester) in rainy_set else "off_season"

    _STYLE = {
        "in_season":   ("#26A69A", "#1A7A70", None, None),
        "off_season":  ("#D0D0D0", "#BBBBBB", None, None),
        "unmonitored": ("#F0F0F0", "#DDDDDD", None, None),
    }

    _REGIONS = {
        "global":      {"xlim": (-100, 180), "ylim": (-36, 56)},
        "lac":         {"xlim": (-120, -30), "ylim": (-35, 35)},
        "africa":      {"xlim": (-20, 55),   "ylim": (-36, 38)},
        "asia_europe": {"xlim": (15, 131),   "ylim": (5, 56)},
        "sea_pacific": {"xlim": (85, 180),   "ylim": (-23, 30)},
    }
    _reg = _REGIONS[map_region_dd.value]
    _xl, _yl = _reg["xlim"], _reg["ylim"]

    _dx = _xl[1] - _xl[0]
    _dy = _yl[1] - _yl[0]
    _map_w   = 12.0
    _map_h   = _map_w * _dy / _dx
    _title_h = 0.30
    _leg_h   = 0.55
    _fig_h   = _map_h + _title_h + _leg_h

    _gdf = world_geo.dropna(subset=["geometry"]).copy()
    _gdf["cat"] = _gdf["iso3"].map(_iso3_cat).fillna("unmonitored")
    _gdf_clip = _gdf.cx[_xl[0]:_xl[1], _yl[0]:_yl[1]]

    plt.close("all")
    _fig_m, _ax_m = plt.subplots(figsize=(_map_w, _fig_h), dpi=150)

    _fc_map = {c: st[0] for c, st in _STYLE.items()}
    _ec_map = {c: st[1] for c, st in _STYLE.items()}
    _fc_list = [_fc_map.get(c, "#F0F0F0") for c in _gdf_clip["cat"]]
    _ec_list = [_ec_map.get(c, "#DDDDDD") for c in _gdf_clip["cat"]]
    _gdf_clip.plot(ax=_ax_m, color=_fc_list, edgecolor=_ec_list, linewidth=0.3)

    _hatch_groups: dict = {}
    for _c, _st in _STYLE.items():
        if _st[2]:
            _hatch_groups.setdefault((_st[2], _st[3]), []).append(_c)
    for (_h, _hc), _cats in _hatch_groups.items():
        _sub = _gdf_clip[_gdf_clip["cat"].isin(_cats)]
        if not _sub.empty:
            _sub.plot(ax=_ax_m, color="none", edgecolor=_hc, hatch=_h, linewidth=0)

    _gdf_clip.plot(ax=_ax_m, color="none", edgecolor="#CCCCCC", linewidth=0.3)

    _dot_r = 0.0035 * _dx

    def _is_dot_country(geom):
        if geom is None: return False
        if geom.area < 0.5: return True
        _max_p = max(p.area for p in geom.geoms) if hasattr(geom, "geoms") else geom.area
        _compact = 4 * np.pi * geom.area / (geom.length ** 2) if geom.length > 0 else 1.0
        return _max_p < 1.5 and _compact < 0.4

    for _, _row in _gdf[_gdf["cat"] != "unmonitored"].iterrows():
        _geom = _row.geometry
        if not _is_dot_country(_geom):
            continue
        _cat_dot = _row["cat"]
        _largest = max(_geom.geoms, key=lambda p: p.area) if hasattr(_geom, "geoms") else _geom
        _rp = _largest.representative_point()
        _cx, _cy = _rp.x, _rp.y
        if _cy < _yl[0] or _cy > _yl[1]:
            continue
        if _cx < _xl[0] or _cx > _xl[1]:
            if map_region_dd.value not in ("global", "sea_pacific"):
                continue
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

    _ax_m.set_xlim(_xl)
    _ax_m.set_ylim(_yl)
    _ax_m.set_aspect("equal")
    _ax_m.axis("off")

    _tri_str = "-".join(calendar.month_abbr[m] for m in TRIMESTERS[trimester])
    _ax_m.set_title(f"Rainy season — {trimester} ({_tri_str})", fontsize=11, pad=8)

    _LEG_KW = dict(fontsize=7, framealpha=0.95, edgecolor="#CCCCCC",
                   handlelength=2.0, handletextpad=0.5, title_fontsize=7.5)
    _h_leg = [
        _mpatch_m.Patch(facecolor="#26A69A", edgecolor="#1A7A70", linewidth=0.5, label="In season"),
        _mpatch_m.Patch(facecolor="#D0D0D0", edgecolor="#BBBBBB", linewidth=0.5, label="Off season"),
        _mpatch_m.Patch(facecolor="#F0F0F0", edgecolor="#DDDDDD", linewidth=0.5, label="Not monitored"),
    ]

    plt.subplots_adjust(
        left=0.0, right=1.0,
        bottom=_leg_h / _fig_h,
        top=(_leg_h + _map_h) / _fig_h,
    )
    _row_h = 0.40 / _fig_h
    _axes_bot = _leg_h / _fig_h
    _fig_m.legend(handles=_h_leg, title="Rainy season status",
                  loc="upper center", bbox_to_anchor=(0.5, _axes_bot),
                  ncol=3, **_LEG_KW)

    _fig_m
    return


@app.cell
def _(mo):
    trimester_pct_sl = mo.ui.slider(0.10, 0.50, 0.05, 0.15, label="Rainy: trimester ≥ X of annual")
    month_pct_sl     = mo.ui.slider(0.00, 0.20, 0.01, 0.05, label="Rainy: each month ≥ X of annual")
    mo.hstack([trimester_pct_sl, month_pct_sl], justify="start")
    return month_pct_sl, trimester_pct_sl


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
def _(
    TRIMESTERS,
    TRIMESTER_NAMES,
    df_skill,
    monthly_clim,
    pcode,
    pd,
    plt,
    rainy_set,
    trimester,
    trimester_pct_sl,
):
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
    _fig_clim, _ax = plt.subplots(figsize=(10, 4), dpi=150)
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
    _fig_clim
    return


@app.cell
def _(
    TRIMESTERS,
    df_skill,
    month_pct_sl,
    monthly_clim,
    pcode,
    plt,
    trimester,
    trimester_pct_sl,
):
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
    _fig_mon
    return


if __name__ == "__main__":
    app.run()
