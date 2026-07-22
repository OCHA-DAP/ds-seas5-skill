// Forecast × HNRP tab: ADM1 drought forecast vs HNRP severity/targeted caseloads.
// Three linked views — an ADM1 choropleth (same classification as the Map tab), a
// scatter (x = % of analysed population in severity 4+, y = targeted as % of analysed
// population, bubble area = analysed population, fill/hatch = forecast category ×
// skill), and the ranked table. Reuses app.js globals: STYLE, classify, catBase,
// CAT_LABEL, T, buildPatterns.
(async function () {
  let data, geo;
  try {
    [data, geo] = await Promise.all([
      fetch("data/hnrp_drought.json").then((r) => r.json()),
      fetch("data/hnrp_adm1.geojson").then((r) => r.json()),
    ]);
  } catch {
    return; // data files not built yet — leave the tab empty
  }

  const skillSel = document.getElementById("hnrp-skill");
  const rpSel = document.getElementById("hnrp-rp");
  const countrySel = document.getElementById("hnrp-country");
  const issuedEl = document.getElementById("hnrp-issued");
  const thead = document.querySelector("#hnrp-table thead");
  const tbody = document.querySelector("#hnrp-table tbody");
  const emptyEl = document.getElementById("hnrp-empty");

  // buildPatterns() returns the complete cat -> fill map (solid hex or pattern url);
  // app.js already registered the pattern defs, duplicate ids resolve to the first.
  const fillFor = buildPatterns();
  const fillOf = (cat) => fillFor[cat];
  const byPcode = new Map(data.rows.map((r) => [r.pcode, r]));

  issuedEl.textContent = `Forecast issued ${data.issued_label}.`;

  const countries = [...new Set(data.rows.map((r) => r.country).filter(Boolean))].sort();
  for (const c of countries) {
    const o = document.createElement("option");
    o.value = c; o.textContent = c;
    countrySel.appendChild(o);
  }

  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const fmt = (v, d) => (v == null ? "–" : Number(v).toFixed(d));
  const pctOf = (num, den) => (num == null || !den ? null : (100 * num) / den);

  function passes(r) {
    if (r.rp == null) return false; // no qualifying drought slot
    if (r.rp < +rpSel.value) return false;
    if (skillSel.value === "high" && r.r < T.r_high) return false;
    if (countrySel.value && r.country !== countrySel.value) return false;
    return true;
  }
  // Rows passing filters classify exactly like the Map tab (they are rainy-season,
  // drought-side, r ≥ mod by construction); everything else greys out.
  const catOf = (r) => (r && passes(r) ? classify({ pct: r.pct, r: r.r, rainy: true }, false)
                                       : "unmonitored");

  // ── ADM1 choropleth ──────────────────────────────────────────────────────────
  const map = L.map("hnrp-map", {
    crs: L.CRS.EPSG4326, attributionControl: false, maxZoom: 8,
  });
  const tipHtml = (f) => {
    const p = f.properties, r = byPcode.get(p.pcode);
    const cat = catOf(r);
    let rows = "";
    if (r && r.rp != null) {
      rows = `<div>${r.tri_label}${r.lead < 0 ? " · in season" : ""} — RP ${fmt(r.rp, 1)} yr, r ${fmt(r.r, 2)}</div>`;
    }
    if (r && r.sev4 != null) rows += `<div>Severity 4+: ${fmtN(r.sev4)}</div>`;
    if (r && r.targeted != null) rows += `<div>Targeted: ${fmtN(r.targeted)}</div>`;
    return `<div class="name">${p.name ?? p.pcode}</div>` +
      `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || "No qualifying signal"}</div>` + rows;
  };
  const layer = L.geoJSON(geo, {
    filter: (f) => /Polygon/.test(f.geometry.type),
    // Neutral initial style so nothing flashes Leaflet-blue before renderMap runs.
    style: () => ({ weight: 0.6, fillOpacity: 1, color: "#e2e8e8", fillColor: "#f5f7f7" }),
    onEachFeature: (f, l) => l.bindTooltip(() => tipHtml(f), { sticky: true }),
  }).addTo(map);
  map.fitBounds(layer.getBounds());
  function renderMap() {
    layer.eachLayer((l) => {
      const cat = catOf(byPcode.get(l.feature.properties.pcode));
      const el = l._path;
      if (!el) return;
      el.setAttribute("fill", fillOf(cat));
      el.setAttribute("stroke", STYLE[cat][1]);
    });
  }

  // ── Scatter ──────────────────────────────────────────────────────────────────
  const svg = document.getElementById("hnrp-scatter");
  const tip = document.getElementById("hnrp-tip");
  const NS = "http://www.w3.org/2000/svg";
  const M = { l: 58, r: 16, t: 12, b: 46 };

  function scatterRows() {
    return data.rows.filter((r) => passes(r) && r.sev_total > 0 && r.targeted != null);
  }

  function renderScatter() {
    const W = svg.parentElement.clientWidth || 900, H = 460;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
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
    const xmax = Math.min(100, Math.max(10, ...rows.map((r) => pctOf(r.sev4, r.sev_total))) * 1.08);
    const ymax = Math.min(100, Math.max(10, ...rows.map((r) => pctOf(r.targeted, r.sev_total))) * 1.08);
    const X = (v) => M.l + (v / xmax) * (W - M.l - M.r);
    const Y = (v) => H - M.b - (v / ymax) * (H - M.t - M.b);
    const pmax = Math.max(...rows.map((r) => r.sev_total));
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
      .textContent = "Population in severity 4+ (% of analysed population)";
    const yl = g("text", { x: 14, y: (M.t + H - M.b) / 2, "text-anchor": "middle", "font-size": 11, fill: "#555" });
    yl.textContent = "Targeted (% of analysed population)";
    yl.setAttribute("transform", `rotate(-90 14 ${(M.t + H - M.b) / 2})`);

    // Bubbles: big ones first so small ones stay hoverable; 2px surface ring.
    const sorted = [...rows].sort((a, b) => b.sev_total - a.sev_total);
    for (const r of sorted) {
      const cat = classify({ pct: r.pct, r: r.r, rainy: true }, false);
      const c = g("circle", {
        cx: X(pctOf(r.sev4, r.sev_total)), cy: Y(pctOf(r.targeted, r.sev_total)),
        r: R(r.sev_total), fill: fillOf(cat), stroke: "#ffffff", "stroke-width": 2,
      });
      g("circle", {
        cx: c.getAttribute("cx"), cy: c.getAttribute("cy"), r: R(r.sev_total),
        fill: "none", stroke: STYLE[cat][1], "stroke-width": 1,
      });
      c.style.cursor = "pointer";
      c.addEventListener("mouseenter", (ev) => {
        tip.hidden = false;
        tip.innerHTML = `<strong>${r.name ?? r.pcode}</strong> — ${r.country ?? r.iso3}<br>` +
          `Severity 4+: ${fmtN(r.sev4)} (${fmt(pctOf(r.sev4, r.sev_total), 1)}%)<br>` +
          `Targeted: ${fmtN(r.targeted)} (${fmt(pctOf(r.targeted, r.sev_total), 1)}%)<br>` +
          `Analysed population: ${fmtN(r.sev_total)}<br>` +
          `${r.tri_label}${r.lead < 0 ? " · in season" : ""}: RP ${fmt(r.rp, 1)} yr, pct ${fmt(r.pct, 1)}, r ${fmt(r.r, 2)}`;
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
        x: X(pctOf(r.sev4, r.sev_total)), y: Y(pctOf(r.targeted, r.sev_total)) - R(r.sev_total) - 4,
        "text-anchor": "middle", "font-size": 10, fill: "#333",
      }).textContent = r.name ?? r.pcode;
    }
  }

  // ── Table ────────────────────────────────────────────────────────────────────
  const COLS = [
    { key: "country", label: "Country", num: false },
    { key: "name", label: "Admin 1", num: false },
    { key: "sev4", label: "Severity 4+ pop", num: true },
    { key: "pin", label: "PiN", num: true },
    { key: "targeted", label: "Targeted", num: true },
    { key: "tri_label", label: "Season", num: false },
    { key: "rp", label: "Drought RP (yr)", num: true },
    { key: "pct", label: "Percentile", num: true },
    { key: "r", label: "Skill (r)", num: true },
  ];
  let sortKey = "sev4", sortDesc = true;

  function renderTable() {
    thead.innerHTML = "";
    const trh = document.createElement("tr");
    for (const c of COLS) {
      const th = document.createElement("th");
      th.textContent = c.label + (c.key === sortKey ? (sortDesc ? " ↓" : " ↑") : "");
      th.className = (c.num ? "num" : "") + (c.key === sortKey ? " sorted" : "");
      th.addEventListener("click", () => {
        if (sortKey === c.key) sortDesc = !sortDesc;
        else { sortKey = c.key; sortDesc = c.num; }
        renderTable();
      });
      trh.appendChild(th);
    }
    thead.appendChild(trh);

    const rs = data.rows.filter(passes).sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sortDesc ? -cmp : cmp;
    });
    tbody.innerHTML = "";
    emptyEl.hidden = rs.length > 0;
    for (const r of rs) {
      const tr = document.createElement("tr");
      const skillCls = r.r >= T.r_high ? "skill-high" : "skill-mod";
      tr.innerHTML =
        `<td>${r.country ?? r.iso3}</td>` +
        `<td>${r.name ?? r.pcode}</td>` +
        `<td class="num">${fmtN(r.sev4)}</td>` +
        `<td class="num">${fmtN(r.pin)}</td>` +
        `<td class="num">${fmtN(r.targeted)}</td>` +
        `<td>${r.tri_label ?? "–"}${r.lead != null && r.lead < 0 ? ' <span class="in-season-tag">· in season</span>' : ""}</td>` +
        `<td class="num">${fmt(r.rp, 1)}</td>` +
        `<td class="num">${fmt(r.pct, 1)}</td>` +
        `<td class="num ${skillCls}">${fmt(r.r, 2)}</td>`;
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
  function renderAll() { renderMap(); renderScatter(); renderTable(); }
  for (const el of [skillSel, rpSel]) el.addEventListener("change", renderAll);
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
