// Forecast × HNRP tab: ADM1 drought forecast vs HNRP severity/targeted caseloads.
// Three linked views — an ADM1 choropleth (same classification as the Map tab), a
// scatter (x = % of analysed population in severity 4+, y = targeted as % of analysed
// population, bubble area = analysed population, fill/hatch = forecast category ×
// skill), and the ranked table. Reuses app.js globals: STYLE, classify, catBase,
// CAT_LABEL, T, buildPatterns.
(async function () {
  let data, geo, world;
  try {
    [data, geo, world] = await Promise.all([
      fetch("data/hnrp_drought.json").then((r) => r.json()),
      fetch("data/hnrp_adm1.geojson").then((r) => r.json()),
      fetch("data/countries.geojson").then((r) => r.json()),
    ]);
  } catch {
    return; // data files not built yet — leave the tab empty
  }

  const skillSel = document.getElementById("hnrp-skill");
  const rpSel = document.getElementById("hnrp-rp");
  const sectorSel = document.getElementById("hnrp-sector");
  const srcSel = document.getElementById("hnrp-sev-src");
  const ipcPeriodWrap = document.getElementById("hnrp-ipc-period-wrap");
  const ipcPeriodSel = document.getElementById("hnrp-ipc-period");
  const countrySel = document.getElementById("hnrp-country");
  const droughtOnlyEl = document.getElementById("hnrp-drought-only");
  const issuedEl = document.getElementById("hnrp-issued");
  const thead = document.querySelector("#hnrp-table thead");
  const tbody = document.querySelector("#hnrp-table tbody");
  const emptyEl = document.getElementById("hnrp-empty");

  // buildPatterns() returns the complete cat -> fill map (solid hex or pattern url);
  // app.js already registered the pattern defs, duplicate ids resolve to the first.
  const fillFor = buildPatterns();
  const fillOf = (cat) => fillFor[cat];
  const byPcode = new Map(data.rows.map((r) => [r.pcode, r]));

  // Plan cycle varies by country (e.g. Guatemala's latest is the 2025 HNRP) — surface
  // the year everywhere caseload figures appear.
  const planYrOf = (r) => {
    const ys = [r.ref_year, r.sev_year].filter((y) => y != null);
    return ys.length ? Math.max(...ys) : null;
  };
  const planYrByCountry = new Map();
  for (const r of data.rows) {
    const y = planYrOf(r);
    if (r.country && y) planYrByCountry.set(r.country, Math.max(planYrByCountry.get(r.country) ?? 0, y));
  }
  const allYrs = [...new Set(planYrByCountry.values())].sort();
  issuedEl.textContent = `Forecast issued ${data.issued_label}. HNRP plan data ` +
    (allYrs.length > 1 ? `${allYrs[0]}–${allYrs[allYrs.length - 1]} (year varies by country — shown per row)` : `${allYrs[0]}`) + ".";

  const countries = [...new Set(data.rows.map((r) => r.country).filter(Boolean))].sort();
  for (const c of countries) {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = planYrByCountry.has(c) ? `${c} — ${planYrByCountry.get(c)}` : c;
    countrySel.appendChild(o);
  }

  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const fmt = (v, d) => (v == null ? "–" : Number(v).toFixed(d));
  const pctOf = (num, den) => (num == null || !den ? null : (100 * num) / den);
  // Caseload selector: PiN/targeted come from Intersectoral or Food Security (FSC).
  const fscOn = () => sectorSel.value === "fsc";
  const pinOf = (r) => (fscOn() ? r.fsc_pin : r.pin);
  const tgtOf = (r) => (fscOn() ? r.fsc_targeted : r.targeted);
  const secTag = () => (fscOn() ? " (FSC)" : "");

  const droughtOnly = () => droughtOnlyEl.checked;
  // Units with no PiN/severity/targeted are IPC-only (outside any HNRP's analysis) —
  // shown only in IPC mode, where surfacing needs the plan does NOT capture is the point.
  const inHnrp = (r) => r.sev_total > 0 || r.pin != null || r.targeted != null || r.fsc_pin != null;

  // ── Severity source: JIAF inter-sectoral 4+ (default) or IPC/CH phase N+ ─────
  // IPC rows carry a list of analysis periods (current / projections, each with a
  // validity window). Rather than a per-country menu of overlapping rounds, one
  // global choice: "Now" = the most recent estimate covering the issuance month
  // (a 'current' analysis if one covers it, else the newest projection that does);
  // "Forecast window" = the most recent projection overlapping the 6-month
  // forecast horizon. IPC and JIAF use different analysed-population bases and
  // scopes, so shares are not comparable across the two sources.
  const ipcMode = () => srcSel.value !== "jiaf";
  const ym = (s) => { const [y, m] = s.split("-").map(Number); return y * 12 + m - 1; };
  const NOW_YM = data.issued_year * 12 + data.issued_month - 1; // anchor: forecast issuance
  function ipcComboOf(r) {
    const list = r.ipc;
    if (!list || !list.length) return null;
    const covers = (c) => ym(c.s) <= NOW_YM && ym(c.e) >= NOW_YM;
    const newest = (arr) => arr.sort((a, b) => ym(b.s) - ym(a.s))[0];
    if (ipcPeriodSel.value === "fwd") {
      const proj = newest(list.filter((c) => c.t !== "current"
        && ym(c.e) >= NOW_YM && ym(c.s) <= NOW_YM + 6));
      if (proj) return proj;
    }
    const cur = newest(list.filter((c) => c.t === "current" && covers(c)));
    if (cur) return cur;
    const anyNow = newest(list.filter(covers));
    if (anyNow) return anyNow;
    return list.reduce((a, b) => (ym(a.e) >= ym(b.e) ? a : b)); // most recent past window
  }
  function sevValOf(r) {
    if (!ipcMode()) return r.sev4;
    const c = ipcComboOf(r);
    if (!c) return null;
    return c.p.slice(+srcSel.value - 1).reduce((a, b) => a + (b ?? 0), 0);
  }
  const sevTotOf = (r) => (ipcMode() ? (ipcComboOf(r)?.tot ?? null) : r.sev_total);
  const sevLabel = () => (ipcMode() ? `IPC ${srcSel.value === "5" ? "5" : srcSel.value + "+"}` : "Severity 4+");

  function updateIpcPeriodUI() { ipcPeriodWrap.hidden = !ipcMode(); }

  // ── Valid-season selection ───────────────────────────────────────────────────
  // "Worst drought (auto)": each unit shows its worst qualifying drought trimester
  // (fallback: the default lead-1 trimester). An explicit trimester shows THAT
  // season's forecast for every unit. Slot keys are the compact trimester codes (MJJ).
  const triSel = document.getElementById("hnrp-tri");
  const inScope = (r) =>
    (!countrySel.value || r.country === countrySel.value) && (ipcMode() || inHnrp(r));
  function rawSlotOf(r) {
    if (triSel.value === "auto") {
      if (r.rp != null) {
        return { key: r.tri, lead: r.lead, rp: r.rp, pct: r.pct, r: r.r, rainy: true, worst: true };
      }
      if (r.fb_pct == null) return null;
      return { key: r.fb_tri, lead: 1, rp: r.fb_rp, pct: r.fb_pct, r: r.fb_r, rainy: !!r.fb_rainy };
    }
    const t = r.tris && r.tris[triSel.value];
    if (!t || t.pct == null) return null;
    return { key: triSel.value, lead: t.lead, rp: t.rp, pct: t.pct, r: t.r, rainy: !!t.rainy };
  }
  // Qualifying drought signal = drought side, rainy season, skill + RP thresholds.
  function isDrought(s) {
    return !!s && s.pct != null && s.pct < 50 && s.rainy
      && s.r != null && s.r >= (skillSel.value === "high" ? T.r_high : T.r_mod)
      && s.rp != null && s.rp >= +rpSel.value;
  }
  function passes(r) {
    return inScope(r) && isDrought(rawSlotOf(r));
  }
  // Display slot: the qualifying drought slot, else (unless drought-only) the same
  // season's real category — flood/normal/low-skill/off-season, like the Map tab.
  function slotOf(r) {
    if (!inScope(r)) return null;
    const s = rawSlotOf(r);
    if (!s) return null;
    if (isDrought(s)) return s;
    if (droughtOnly()) return null;
    if (s.worst) {
      // auto mode: the worst-drought slot failed the filters — display the default
      // lead-1 trimester instead (the slot shown for every non-drought unit).
      if (r.fb_pct == null) return null;
      return { key: r.fb_tri, lead: 1, rp: r.fb_rp, pct: r.fb_pct, r: r.fb_r, rainy: !!r.fb_rainy };
    }
    return s;
  }
  // Style for HNRP units with nothing to display (drought-only mode, or no forecast):
  // distinct from both the world background and the classified categories.
  const HNRP_MUTED = { fill: "#e9eeee", edge: "#c4d0d1" };
  function catOf(r) {
    const s = r && slotOf(r);
    return s ? classify({ pct: s.pct, r: s.r, rainy: s.rainy }, false) : null;
  }

  // ── ADM1 choropleth ──────────────────────────────────────────────────────────
  const map = L.map("hnrp-map", {
    crs: L.CRS.EPSG4326, attributionControl: false, maxZoom: 8,
  });
  const tipHtml = (f) => {
    const p = f.properties, r = byPcode.get(p.pcode);
    // No PiN/severity row = not part of the plan's admin-level analysis (e.g. Nigeria's
    // HNRP covers only Borno/Adamawa/Yobe) — context only, never labelled "in HNRP".
    if (!r || (!ipcMode() && !inHnrp(r))) {
      return `<div class="name">${p.name ?? p.pcode}</div>` +
        `<div class="cat" style="color:#9db1b3">Not in the HNRP admin-level analysis</div>`;
    }
    const cat = catOf(r);
    const s = slotOf(r);
    let rows = "";
    if (s && s.rp != null) {
      rows = `<div><strong>${s.key}</strong>${s.lead < 0 ? " · in season" : ""} — RP ${fmt(s.rp, 1)} yr, r ${fmt(s.r, 2)}</div>`;
    }
    if (sevValOf(r) != null) {
      const c = ipcMode() ? ipcComboOf(r) : null;
      rows += `<div>${sevLabel()}: ${fmtN(sevValOf(r))}${c ? ` (${c.label})` : ""}</div>`;
    }
    if (tgtOf(r) != null) rows += `<div>Targeted${secTag()}: ${fmtN(tgtOf(r))}</div>`;
    const py = planYrOf(r);
    if (py) rows += `<div>Plan data: ${py}</div>`;
    const catLine = cat
      ? `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>`
      : `<div class="cat" style="color:#9db1b3">In HNRP — ${droughtOnly() ? "no qualifying drought signal" : "no forecast data"}</div>`;
    return `<div class="name">${p.name ?? p.pcode}</div>` + catLine + rows;
  };
  // World countries beneath as context (non-interactive, like the Map tab's backdrop).
  L.geoJSON(world, {
    interactive: false,
    style: { color: "#d9dedf", weight: 0.5, fillColor: "#f7f9f9", fillOpacity: 1 },
  }).addTo(map);
  const layer = L.geoJSON(geo, {
    filter: (f) => /Polygon/.test(f.geometry.type),
    // Neutral initial style so nothing flashes Leaflet-blue before renderMap runs.
    style: () => ({ weight: 0.6, fillOpacity: 1, color: "#e2e8e8", fillColor: "#f5f7f7" }),
    onEachFeature: (f, l) => l.bindTooltip(() => tipHtml(f), { sticky: true }),
  }).addTo(map);
  map.fitBounds(layer.getBounds());
  function renderMap() {
    layer.eachLayer((l) => {
      const el = l._path;
      if (!el) return;
      const r = byPcode.get(l.feature.properties.pcode);
      if (!r || (!ipcMode() && !inHnrp(r))) {
        // Out of scope for the current mode: blend into the world backdrop.
        el.setAttribute("fill", "#f7f9f9");
        el.setAttribute("stroke", "#d9dedf");
        return;
      }
      const cat = catOf(r);
      el.setAttribute("fill", cat ? fillOf(cat) : HNRP_MUTED.fill);
      el.setAttribute("stroke", cat ? STYLE[cat][1] : HNRP_MUTED.edge);
    });
  }

  // ── Scatter ──────────────────────────────────────────────────────────────────
  const svg = document.getElementById("hnrp-scatter");
  const tip = document.getElementById("hnrp-tip");
  const NS = "http://www.w3.org/2000/svg";
  const M = { l: 58, r: 16, t: 12, b: 46 };

  function scatterRows() {
    return data.rows.filter((r) => sevTotOf(r) > 0 && tgtOf(r) != null && slotOf(r) != null);
  }

  function renderScatter() {
    // Square: both axes are population shares, so equal visual weight per axis.
    const W = Math.min(svg.parentElement.clientWidth || 640, 640), H = W;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.style.height = H + "px";
    svg.innerHTML = "";
    const rows = scatterRows();
    const g = (tag, attrs, parent = svg) => {
      const el = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      parent.appendChild(el);
      return el;
    };
    if (!rows.length) {
      g("text", { x: W / 2, y: H / 2, "text-anchor": "middle", fill: "#888", "font-size": 13 })
        .textContent = "No admin-1 units match the current filters.";
      return;
    }
    // Fixed 0–100% axes: shares stay visually comparable across filters and sources.
    const xmax = 100, ymax = 100;
    const clamp = (v) => Math.min(v ?? 0, 100);
    const X = (v) => M.l + (v / xmax) * (W - M.l - M.r);
    const Y = (v) => H - M.b - (v / ymax) * (H - M.t - M.b);
    const pmax = Math.max(...rows.map((r) => sevTotOf(r)));
    const R = (p) => 4 + 22 * Math.sqrt(p / pmax);

    // Recessive grid + axes.
    const ticks = (max) => { const s = max > 50 ? 20 : max > 20 ? 10 : 5; const o = []; for (let v = 0; v <= max; v += s) o.push(v); return o; };
    for (const v of ticks(xmax)) {
      g("line", { x1: X(v), x2: X(v), y1: M.t, y2: H - M.b, stroke: "#eef1f1" });
      g("text", { x: X(v), y: H - M.b + 16, "text-anchor": "middle", "font-size": 10, fill: "#888" }).textContent = v + "%";
    }
    for (const v of ticks(ymax)) {
      g("line", { x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v), stroke: "#eef1f1" });
      g("text", { x: M.l - 6, y: Y(v) + 3, "text-anchor": "end", "font-size": 10, fill: "#888" }).textContent = v + "%";
    }
    g("text", { x: (M.l + W - M.r) / 2, y: H - 8, "text-anchor": "middle", "font-size": 11, fill: "#555" })
      .textContent = `Population in ${sevLabel()} (% of analysed population)`;
    const yl = g("text", { x: 14, y: (M.t + H - M.b) / 2, "text-anchor": "middle", "font-size": 11, fill: "#555" });
    yl.textContent = `Targeted${secTag()} (% of analysed population)`;
    yl.setAttribute("transform", `rotate(-90 14 ${(M.t + H - M.b) / 2})`);

    // Bubbles: big ones first so small ones stay hoverable; 2px surface ring.
    const sorted = [...rows].sort((a, b) => sevTotOf(b) - sevTotOf(a));
    for (const r of sorted) {
      const cat = catOf(r);
      const c = g("circle", {
        cx: X(clamp(pctOf(sevValOf(r), sevTotOf(r)))), cy: Y(clamp(pctOf(tgtOf(r), sevTotOf(r)))),
        r: R(sevTotOf(r)), fill: fillOf(cat), stroke: "#ffffff", "stroke-width": 2,
      });
      g("circle", {
        cx: c.getAttribute("cx"), cy: c.getAttribute("cy"), r: R(sevTotOf(r)),
        fill: "none", stroke: STYLE[cat][1], "stroke-width": 1,
      });
      c.style.cursor = "pointer";
      c.addEventListener("mouseenter", (ev) => {
        const s = slotOf(r);
        tip.hidden = false;
        const combo = ipcMode() ? ipcComboOf(r) : null;
        tip.innerHTML = `<strong>${r.name ?? r.pcode}</strong> — ${r.country ?? r.iso3}<br>` +
          `${sevLabel()}${combo ? ` (${combo.t}, ${combo.label})` : ""}: ${fmtN(sevValOf(r))} (${fmt(pctOf(sevValOf(r), sevTotOf(r)), 1)}%)<br>` +
          `Targeted${secTag()}: ${fmtN(tgtOf(r))} (${fmt(pctOf(tgtOf(r), sevTotOf(r)), 1)}%)<br>` +
          `Analysed population: ${fmtN(sevTotOf(r))}<br>` +
          (s ? `<strong>${s.key}</strong>${s.lead < 0 ? " · in season" : ""}: RP ${fmt(s.rp, 1)} yr, pct ${fmt(s.pct, 1)}, r ${fmt(s.r, 2)}` : "");
      });
      c.addEventListener("mousemove", (ev) => {
        const b = svg.parentElement.getBoundingClientRect();
        tip.style.left = Math.min(ev.clientX - b.left + 14, b.width - 240) + "px";
        tip.style.top = (ev.clientY - b.top + 14) + "px";
      });
      c.addEventListener("mouseleave", () => { tip.hidden = true; });
    }

    // Selective direct labels: the 6 largest analysed populations.
    for (const r of sorted.slice(0, 6)) {
      g("text", {
        x: X(clamp(pctOf(sevValOf(r), sevTotOf(r)))), y: Y(clamp(pctOf(tgtOf(r), sevTotOf(r)))) - R(sevTotOf(r)) - 4,
        "text-anchor": "middle", "font-size": 10, fill: "#333",
      }).textContent = r.name ?? r.pcode;
    }
  }

  // ── Severity-breakdown bars (per admin, when a country is selected) ──────────
  // Population by JIAF class 1–5 (stacked), a tick for the targeted population, and
  // the unit's forecast category as a swatch beside its name. Severity uses the
  // IPC/CH-convention colours — a domain-standard scale this audience reads at a
  // glance, and far more separable than a single-hue ramp.
  const SEV_COLORS = ["#cdfacd", "#fae61e", "#e67800", "#c80000", "#640000"];
  const JIAF_LABELS = ["1 — minimal", "2 — stress", "3 — severe", "4 — extreme", "5 — catastrophic"];
  const IPC_LABELS = ["1 — minimal", "2 — stressed", "3 — crisis", "4 — emergency", "5 — catastrophe"];
  const sevClassLabels = () => (ipcMode() ? IPC_LABELS : JIAF_LABELS);
  // Class breakdown for the bars: JIAF classes or the selected IPC period's phases.
  const segsOf = (r) => (ipcMode()
    ? (ipcComboOf(r)?.p ?? null)
    : [r.s1, r.s2, r.s3, r.s4, r.s5]);
  const barsWrap = document.getElementById("hnrp-bars-wrap");
  const barsHint = document.getElementById("hnrp-bars-hint");
  const barsSvg = document.getElementById("hnrp-bars");
  const barsTitle = document.getElementById("hnrp-bars-title");
  const barsLegend = document.getElementById("hnrp-bars-legend");
  const fmtSI = (v) => (v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
    : v >= 1e3 ? Math.round(v / 1e3) + "k" : String(Math.round(v)));

  function renderBarsLegend() {
    barsLegend.innerHTML =
      SEV_COLORS.map((c, i) => `<span><i style="background:${c}"></i> ${sevClassLabels()[i]}</span>`).join("") +
      `<span><i class="tick"></i> targeted</span>` +
      `<span>left swatch = forecast category</span>`;
  }

  // Sort control: severity 4+ first by default; forecast order puts droughts on top.
  const barSortSel = document.getElementById("hnrp-bar-sort");
  const CAT_ORDER = ["drought_vsev_high", "drought_vsev_mod", "drought_sev_high",
    "drought_sev_mod", "flood_vsev_high", "flood_vsev_mod", "flood_sev_high",
    "flood_sev_mod", "high_none", "mid_none", "low_skill", "off_season"];
  const catRank = (r) => {
    const i = CAT_ORDER.indexOf(catOf(r) ?? "");
    return i === -1 ? CAT_ORDER.length : i;
  };
  const BAR_SORTS = {
    sev4: (a, b) => (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0),
    total: (a, b) => (sevTotOf(b) ?? 0) - (sevTotOf(a) ?? 0),
    targeted: (a, b) => (tgtOf(b) ?? 0) - (tgtOf(a) ?? 0),
    forecast: (a, b) => catRank(a) - catRank(b) || (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0),
    name: (a, b) => String(a.name ?? a.pcode).localeCompare(String(b.name ?? b.pcode)),
  };

  function renderBars() {
    renderBarsLegend();
    const country = countrySel.value;
    const rows = country
      ? data.rows.filter((r) => r.country === country && sevTotOf(r) > 0 && segsOf(r))
          .sort(BAR_SORTS[barSortSel.value] || BAR_SORTS.sev4)
      : [];
    barsWrap.hidden = rows.length === 0;
    barsHint.hidden = rows.length > 0;
    if (!rows.length) return;
    if (ipcMode()) {
      const c = ipcComboOf(rows[0]);
      barsTitle.textContent = `${country} — population by IPC/CH phase, per admin 1` +
        (c ? ` (${c.t}, valid ${c.label})` : "");
    } else {
      barsTitle.textContent = `${country} — population by JIAF severity class, per admin 1 ` +
        `(analysis year ${rows[0].sev_year ?? "–"})`;
    }

    const W = barsSvg.parentElement.clientWidth || 900;
    const ROW = 26, M = { l: 205, r: 24, t: 6, b: 34 };
    const H = M.t + M.b + rows.length * ROW;
    barsSvg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    barsSvg.style.height = H + "px";
    barsSvg.innerHTML = "";
    const g = (tag, attrs, parent = barsSvg) => {
      const el = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      parent.appendChild(el);
      return el;
    };
    const xmax = Math.max(...rows.map((r) => Math.max(sevTotOf(r) ?? 0, tgtOf(r) ?? 0))) * 1.04;
    const X = (v) => M.l + (v / xmax) * (W - M.l - M.r);

    // x grid: 4 round ticks.
    const step = Math.pow(10, Math.floor(Math.log10(xmax / 4)));
    const tick = Math.ceil(xmax / 4 / step) * step;
    for (let v = 0; v <= xmax; v += tick) {
      g("line", { x1: X(v), x2: X(v), y1: M.t, y2: H - M.b, stroke: "#eef1f1" });
      g("text", { x: X(v), y: H - M.b + 16, "text-anchor": "middle", "font-size": 10, fill: "#888" })
        .textContent = fmtSI(v);
    }
    g("text", { x: (M.l + W - M.r) / 2, y: H - 4, "text-anchor": "middle", "font-size": 11, fill: "#555" })
      .textContent = "People (analysed population by severity class)";

    rows.forEach((r, i) => {
      const y = M.t + i * ROW;
      const cat = catOf(r);
      // Forecast-category swatch + admin name.
      g("rect", { x: M.l - 199, y: y + ROW / 2 - 7, width: 13, height: 13,
                  fill: cat ? fillOf(cat) : HNRP_MUTED.fill,
                  stroke: cat ? STYLE[cat][1] : HNRP_MUTED.edge, "stroke-width": 1 });
      const name = (r.name ?? r.pcode).length > 24 ? (r.name ?? r.pcode).slice(0, 23) + "…" : (r.name ?? r.pcode);
      g("text", { x: M.l - 180, y: y + ROW / 2 + 4, "font-size": 11, fill: "#333" }).textContent = name;

      // Stacked class segments with a white spacer between them.
      const segs = segsOf(r) ?? [];
      let acc = 0;
      for (let c = 0; c < 5; c++) {
        const v = segs[c] ?? 0;
        if (v <= 0) continue;
        const seg = g("rect", { x: X(acc), y: y + 4, width: Math.max(X(acc + v) - X(acc) - 1, 0.5),
                                height: ROW - 9, fill: SEV_COLORS[c] });
        const title = document.createElementNS(NS, "title");
        title.textContent = `${r.name ?? r.pcode} — ${ipcMode() ? "IPC phase" : "severity"} ${c + 1}: ${fmtN(v)}`;
        seg.appendChild(title);
        acc += v;
      }
      // Targeted tick (dark vertical line).
      const tgt = tgtOf(r);
      if (tgt != null) {
        g("line", { x1: X(tgt), x2: X(tgt), y1: y + 1, y2: y + ROW - 3,
                    stroke: "#1d2021", "stroke-width": 2 });
      }
    });
  }

  // ── Table ────────────────────────────────────────────────────────────────────
  const COLS = [
    { key: "country", label: "Country", num: false },
    { key: "name", label: "Admin 1", num: false },
    { key: "_plan_yr", label: "Plan", num: true },
    { key: "sev4", label: "Severity 4+ pop", num: true },
    { key: "pin", label: "PiN", num: true },
    { key: "targeted", label: "Targeted", num: true },
    { key: "tri_label", label: "Season", num: false },
    { key: "rp", label: "Drought RP (yr)", num: true },
    { key: "pct", label: "Percentile", num: true },
    { key: "r", label: "Skill (r)", num: true },
  ];
  let sortKey = "sev4", sortDesc = true;
  // PiN/Targeted columns follow the caseload selector.
  const colKey = (k) => (fscOn() && (k === "pin" || k === "targeted") ? "fsc_" + k : k);

  function renderTable() {
    thead.innerHTML = "";
    const trh = document.createElement("tr");
    for (const c of COLS) {
      const th = document.createElement("th");
      let label = c.label + (fscOn() && (c.key === "pin" || c.key === "targeted") ? " (FSC)" : "");
      if (c.key === "sev4") label = `${sevLabel()} pop`;
      th.textContent = label + (c.key === sortKey ? (sortDesc ? " ↓" : " ↑") : "");
      th.className = (c.num ? "num" : "") + (c.key === sortKey ? " sorted" : "");
      th.addEventListener("click", () => {
        if (sortKey === c.key) sortDesc = !sortDesc;
        else { sortKey = c.key; sortDesc = c.num; }
        renderTable();
      });
      trh.appendChild(th);
    }
    thead.appendChild(trh);

    const SLOT_KEYS = { tri_label: "key", rp: "rp", pct: "pct", r: "r" };
    const kv = (row) => (sortKey === "_plan_yr" ? planYrOf(row)
      : sortKey === "sev4" ? sevValOf(row)
      : sortKey in SLOT_KEYS ? slotOf(row)?.[SLOT_KEYS[sortKey]]
      : row[colKey(sortKey)]);
    const rs = data.rows.filter(passes).sort((a, b) => {
      const x = kv(a), y = kv(b);
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sortDesc ? -cmp : cmp;
    });
    tbody.innerHTML = "";
    emptyEl.hidden = rs.length > 0;
    for (const r of rs) {
      const tr = document.createElement("tr");
      const s = slotOf(r);
      const skillCls = s && s.r >= T.r_high ? "skill-high" : "skill-mod";
      tr.innerHTML =
        `<td>${r.country ?? r.iso3}</td>` +
        `<td>${r.name ?? r.pcode}</td>` +
        `<td class="num">${planYrOf(r) ?? "–"}</td>` +
        `<td class="num">${fmtN(sevValOf(r))}</td>` +
        `<td class="num">${fmtN(pinOf(r))}</td>` +
        `<td class="num">${fmtN(tgtOf(r))}</td>` +
        `<td>${s ? s.key : "–"}${s && s.lead < 0 ? ' <span class="in-season-tag">· in season</span>' : ""}</td>` +
        `<td class="num">${fmt(s?.rp, 1)}</td>` +
        `<td class="num">${fmt(s?.pct, 1)}</td>` +
        `<td class="num ${skillCls}">${fmt(s?.r, 2)}</td>`;
      tbody.appendChild(tr);
    }
  }

  function fitCountry() {
    const c = countrySel.value;
    let bounds = null;
    layer.eachLayer((l) => {
      const r = byPcode.get(l.feature.properties.pcode);
      if (c && (!r || r.country !== c)) return;
      bounds = bounds ? bounds.extend(l.getBounds()) : L.latLngBounds(l.getBounds());
    });
    if (bounds) map.fitBounds(bounds, { padding: [10, 10] });
  }
  // Valid-season selector options: auto + each valid trimester at this issuance.
  for (const t of data.trimesters ?? []) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t;
    triSel.appendChild(o);
  }

  function renderAll() { renderMap(); renderScatter(); renderBars(); renderTable(); }
  for (const el of [skillSel, rpSel, sectorSel, droughtOnlyEl, triSel, ipcPeriodSel]) {
    el.addEventListener("change", renderAll);
  }
  barSortSel.addEventListener("change", renderBars);
  srcSel.addEventListener("change", () => { updateIpcPeriodUI(); renderAll(); });
  countrySel.addEventListener("change", () => { renderAll(); fitCountry(); });

  // Hidden-panel sizing: (re)fit when the tab becomes visible.
  window.tabShown = window.tabShown || {};
  window.tabShown.hnrp = () => {
    map.invalidateSize();
    map.fitBounds(layer.getBounds());
    renderAll(); // paths may mount after the panel becomes visible — restyle then
  };
  renderAll();
  requestAnimationFrame(renderMap); // catch paths that mounted after the first pass
})();
