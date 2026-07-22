"use strict";

// ── Category styling — HDX redesign brand tokens (data.humdata.org v2) ───────────
// [fill, edge, hatch]  hatch: null | "white" | "grey" | "cross"
const STYLE = {
  off_season:        ["#b1c1c2", "#9db1b3", null],
  high_none:         ["#e2e8e8", "#c4d0d1", null],
  mid_none:          ["#e2e8e8", "#c4d0d1", "grey"],
  low_skill:         ["#ffffff", "#c4d0d1", "cross"],
  drought_vsev_high: ["#7f5619", "#5c3e12", null],
  drought_vsev_mod:  ["#7f5619", "#5c3e12", "white"],
  drought_sev_high:  ["#dda555", "#b98634", null],
  drought_sev_mod:   ["#dda555", "#b98634", "white"],
  flood_vsev_high:   ["#134ead", "#0e3b82", null],
  flood_vsev_mod:    ["#134ead", "#0e3b82", "white"],
  flood_sev_high:    ["#74a1e8", "#4681e0", null],
  flood_sev_mod:     ["#74a1e8", "#4681e0", "white"],
  unmonitored:       ["#f5f7f7", "#e2e8e8", null],
};

const CAT_LABEL = {
  drought_vsev: "Strongly below normal", drought_sev: "Below normal",
  flood_sev: "Above normal", flood_vsev: "Strongly above normal",
  high_none: "Roughly normal", mid_none: "Roughly normal (mod skill)",
  low_skill: "Low skill", off_season: "Outside rainy season",
  unmonitored: "Not monitored",
};

// Strip the skill suffix so a category maps to its CAT_LABEL key.
const catBase = (cat) => cat.replace(/_(high|mod)$/, "");

let T = { sev_rp: 3, vsev_rp: 10, r_mod: 0.3, r_high: 0.5 };

// Faithful port of the map categorisation (analysis/prob_alerts.py:379-397).
function classify(rec, rainyOn) {
  if (!rec) return "unmonitored";
  if (!rainyOn && !rec.rainy) return "off_season";
  // Missing r/pct means the country isn't effectively monitored for this slot.
  if (rec.r == null || rec.pct == null) return "unmonitored";
  if (rec.r < T.r_mod) return "low_skill";
  const vsev_m = 100 / T.vsev_rp, sev_m = 100 / T.sev_rp;
  const pct = rec.pct, r = rec.r;
  const vsev = pct <= vsev_m || pct >= 100 - vsev_m;
  const sev = (pct > vsev_m && pct <= sev_m) || (pct >= 100 - sev_m && pct < 100 - vsev_m);
  const drought = pct < 50;
  const sk = r >= T.r_high ? "high" : "mod";
  if (vsev) return (drought ? "drought" : "flood") + "_vsev_" + sk;
  if (sev) return (drought ? "drought" : "flood") + "_sev_" + sk;
  return r >= T.r_high ? "high_none" : "mid_none";
}

// ── adm0 SVG hatch patterns (constant screen density on Leaflet; layer points ≈ px) ──
// Defined in a hidden <svg> in the document; Leaflet paths reference them by url(#id).
function buildPatterns() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
  svg.style.position = "absolute";
  const defs = document.createElementNS(NS, "defs");
  svg.appendChild(defs);
  const fillFor = {};
  for (const [cat, [fill, , hatch]] of Object.entries(STYLE)) {
    if (!hatch) { fillFor[cat] = fill; continue; }
    const id = "pat-" + cat;
    const stroke = hatch === "white" ? "rgba(255,255,255,0.7)"
                 : hatch === "grey" ? "#9db1b3" : "#b1c1c2";
    const p = document.createElementNS(NS, "pattern");
    p.setAttribute("id", id);
    p.setAttribute("patternUnits", "userSpaceOnUse");
    p.setAttribute("width", "5"); p.setAttribute("height", "5");
    p.setAttribute("patternTransform", "rotate(45)");
    const bg = document.createElementNS(NS, "rect");
    bg.setAttribute("width", "5"); bg.setAttribute("height", "5"); bg.setAttribute("fill", fill);
    p.appendChild(bg);
    const sw = hatch === "cross" ? 1.1 : 1.4;
    const line = (x1, y1, x2, y2) => {
      const l = document.createElementNS(NS, "line");
      l.setAttribute("x1", x1); l.setAttribute("y1", y1);
      l.setAttribute("x2", x2); l.setAttribute("y2", y2);
      l.setAttribute("stroke", stroke); l.setAttribute("stroke-width", sw);
      p.appendChild(l);
    };
    line(0, 0, 0, 5);
    if (hatch === "cross") line(0, 0, 5, 0);
    defs.appendChild(p);
    fillFor[cat] = `url(#${id})`;
  }
  document.body.appendChild(svg);
  return fillFor;
}

// ── Pixel layer: screen-fixed hatch tiles (constant coarseness at every zoom) ────────
const TILE = 9, LW = 0.85;
function makeTile(draw) {
  const c = document.createElement("canvas"); c.width = c.height = TILE;
  const x = c.getContext("2d"); x.lineWidth = LW; draw(x); return c;
}
const TILES = {
  1: makeTile((x) => { x.strokeStyle = "rgba(255,255,255,0.85)"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.stroke(); }),
  2: makeTile((x) => { x.strokeStyle = "#9db1b3"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.stroke(); }),
  3: makeTile((x) => { x.strokeStyle = "#9db1b3"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.moveTo(0, 0); x.lineTo(TILE, TILE); x.stroke(); }),
};

// Per category code: fill colour (null = transparent → white basemap) and hatch kind.
// Codes: 1 off-season · 2 low skill · 3/4 roughly normal (high/mod skill) ·
// 5/6 strongly below · 7/8 below · 9/10 above · 11/12 strongly above.
const FILL = [null, "#b1c1c2", null, "#e2e8e8", "#e2e8e8", "#7f5619", "#7f5619",
              "#dda555", "#dda555", "#74a1e8", "#74a1e8", "#134ead", "#134ead"];
const KIND = [0, 0, 3, 0, 2, 0, 1, 0, 1, 0, 1, 0, 1];
// Pixel category codes per legend key, for legend-hover highlighting.
const CODE_GROUPS = {
  drought_vsev: [5, 6], drought_sev: [7, 8], none: [3, 4],
  flood_sev: [9, 10], flood_vsev: [11, 12],
  low_skill: [2], off_season: [1], unmonitored: [],
  skill_high: [3, 5, 7, 9, 11], skill_mod: [4, 6, 8, 10, 12],
};

// Canvas layer: draws colour fills AND skill hatch from ONE category grid (shared pixel grid,
// no drift). Animates with the map on zoom; the hatch is a screen-space pattern (constant coarseness).
const RasterLayer = L.Layer.extend({
  initialize(bounds) { this._b = bounds; this._grid = null; this._pad = 0.1; },
  onAdd(map) {
    this._map = map;
    this._cv = L.DomUtil.create("canvas", "hatch-canvas leaflet-zoom-animated");
    this._ctx = this._cv.getContext("2d");
    map.getPanes().overlayPane.appendChild(this._cv);
    map.on("moveend zoomend resize viewreset", this._update, this);
    if (map.options.zoomAnimation) map.on("zoomanim", this._animateZoom, this);
    this._update();
  },
  onRemove(map) {
    L.DomUtil.remove(this._cv);
    map.off("moveend zoomend resize viewreset", this._update, this);
    map.off("zoomanim", this._animateZoom, this);
  },
  setGrid(grid, nx, ny) { this._grid = grid; this._nx = nx; this._ny = ny; this._draw(); },
  // Legend hover: highlight a set of category codes (null = show all normally).
  setHighlight(codes) { this._hl = codes ? new Set(codes) : null; this._draw(); },
  _update() {
    const m = this._map, p = this._pad, size = m.getSize();
    this._origin = m.containerPointToLayerPoint(size.multiplyBy(-p)).round();
    this._size = size.multiplyBy(1 + 2 * p).round();
    this._zoom = m.getZoom();
    L.DomUtil.setTransform(this._cv, this._origin, 1);
    this._cv.width = this._size.x; this._cv.height = this._size.y;
    this._draw();
  },
  _animateZoom(e) {
    const scale = this._map.getZoomScale(e.zoom, this._zoom);
    const offset = this._map._latLngToNewLayerPoint(
      this._map.layerPointToLatLng(this._origin), e.zoom, e.center);
    L.DomUtil.setTransform(this._cv, offset, scale);
  },
  _draw() {
    const ctx = this._ctx;
    if (!ctx) return;  // layer not on the map (e.g. Country mode) — nothing to draw
    ctx.clearRect(0, 0, this._size.x, this._size.y);
    if (!this._grid) return;
    const m = this._map, b = this._b, W = this._size.x, H = this._size.y;
    const n = b[1][0], s = b[0][0], w0 = b[0][1], e0 = b[1][1];
    const ox = this._origin.x, oy = this._origin.y, nx = this._nx, ny = this._ny;
    const xW = m.latLngToLayerPoint([n, w0]).x - ox, xE = m.latLngToLayerPoint([n, e0]).x - ox;
    const dx = (xE - xW) / nx;
    const ex = new Int32Array(nx + 1);
    for (let j = 0; j <= nx; j++) ex[j] = Math.round(xW + j * dx);
    const ey = new Int32Array(ny + 1);
    for (let i = 0; i <= ny; i++) ey[i] = Math.round(m.latLngToLayerPoint([n - i * (n - s) / ny, w0]).y - oy);
    let j0 = 0; while (j0 < nx && ex[j0 + 1] < 0) j0++;
    let j1 = nx; while (j1 > 0 && ex[j1 - 1] > W) j1--;
    // With a legend-hover highlight active, non-matching cells are drawn dimmed and
    // their hatch is suppressed so the highlighted category stands out.
    const hl = this._hl;
    const colorPaths = {}, dimPaths = {}, hatchPaths = { 1: new Path2D(), 2: new Path2D(), 3: new Path2D() };
    const g = this._grid;
    for (let i = 0; i < ny; i++) {
      const y = ey[i], hh = ey[i + 1] - y;
      if (hh <= 0 || ey[i + 1] < 0 || y > H) continue;
      const off = i * nx;
      for (let j = j0; j < j1; j++) {
        const code = g[off + j];
        if (!code) continue;
        const dim = hl && !hl.has(code);
        const x = ex[j], cw = ex[j + 1] - x;
        if (FILL[code]) ((dim ? dimPaths : colorPaths)[code] ||
          ((dim ? dimPaths : colorPaths)[code] = new Path2D())).rect(x, y, cw, hh);
        const k = KIND[code];
        if (k && !dim) hatchPaths[k].rect(x, y, cw, hh);
      }
    }
    ctx.globalAlpha = 0.9;
    for (const code in colorPaths) { ctx.fillStyle = FILL[code]; ctx.fill(colorPaths[code]); }
    ctx.globalAlpha = 0.12;
    for (const code in dimPaths) { ctx.fillStyle = FILL[code]; ctx.fill(dimPaths[code]); }
    ctx.globalAlpha = 1;
    for (const v of [1, 2, 3]) { ctx.fillStyle = ctx.createPattern(TILES[v], "repeat"); ctx.fill(hatchPaths[v]); }
  },
});

const fmtR = (v) => v == null ? "—" : v.toFixed(2);
const fmtRp = (v) => v == null ? "—" : v.toFixed(1);

// Registry of "this tab became visible" callbacks, keyed by tab name. Leaflet can't
// measure a map while its tab is hidden, so each map registers a re-fit here.
window.tabShown = window.tabShown || {};

Promise.all([
  fetch("data/forecasts/index.json").then((r) => r.json()),
  fetch("data/countries.geojson").then((r) => r.json()),
  fetch("raster/data/meta.json").then((r) => r.json()).catch(() => null),
]).then(([index, geo, rmeta]) => {
  T = index.thresholds;
  const latest = index.latest;
  let fc = null;          // the currently loaded issuance (fetched on demand)
  let curTriKey = null;   // preserve the trimester selection across issuance changes

  const fillFor = buildPatterns();
  const OUTLINE_W = 1.1;   // shared border thickness for both Country and Pixel views

  // ── Controls ───────────────────────────────────────────────────────────────
  const triSlider = document.getElementById("trimester");
  const triLabel = document.getElementById("trimester-label");
  const seasonality = document.getElementById("seasonality");
  const yearSel = document.getElementById("issued-year");
  const monthSel = document.getElementById("issued-month");
  const seasonalityOn = () => seasonality.checked;
  const currentTri = () => fc.trimesters[+triSlider.value].key;
  // Signed leadtime of a trimester for the loaded issuance; negative = in-season
  // (the trimester already started — its elapsed months are observed, not forecast).
  const TRI_START = { JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6,
    JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 12 };
  const triLead = (key) => {
    const o = (TRI_START[key] - fc.issued_month + 12) % 12;
    return o <= 6 ? o : o - 12;
  };
  const updateTriLabel = () => {
    const t = fc.trimesters[+triSlider.value];
    curTriKey = t.key;
    const inSeason = triLead(t.key) < 0;
    triSlider.classList.toggle("in-season", inSeason);
    triLabel.innerHTML = `${t.key} (${t.label})` +
      (inSeason ? ` <span class="in-season-tag">· in season</span>` : "");
  };

  // ── Map ──────────────────────────────────────────────────────────────────────
  const map = L.map("map", {
    crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
    attributionControl: false, zoomControl: false, maxBoundsViscosity: 1.0,
    // Leaflet defaults for scroll (whole-level snap, default wheel speed) — feels responsive.
    // The flush initial fit uses a temporary zoomSnap: 0 below, then restores to 1.
  });
  L.control.zoom({ position: "topleft" }).addTo(map);

  // Grey country outlines: the basemap under the pixel grid (and reference everywhere).
  const outlineLayer = L.geoJSON(geo, {
    interactive: false,
    style: { color: "#5a5a5a", weight: OUTLINE_W, fillOpacity: 0, opacity: 0.95 },
  });
  const worldBounds = outlineLayer.getBounds();
  // Add a little N/S breathing room, keep full E/W. Match the map box to these padded bounds'
  // aspect (plate carrée: 1° lon = 1° lat in px) so the fit fills the box flush left/right with a
  // small top/bottom margin.
  const LAT_PAD = 8;
  const viewBounds = L.latLngBounds(
    [worldBounds.getSouth() - LAT_PAD, worldBounds.getWest()],
    [worldBounds.getNorth() + LAT_PAD, worldBounds.getEast()]);
  const aspect = (viewBounds.getEast() - viewBounds.getWest()) /
                 (viewBounds.getNorth() - viewBounds.getSouth());
  document.getElementById("map").style.aspectRatio = String(aspect);
  function fitMap() {
    map.invalidateSize();
    // Fit at an exact (unsnapped) zoom so the view fills the box, then restore the scroll snap.
    map.setMinZoom(0);
    map.options.zoomSnap = 0;
    map.fitBounds(viewBounds, { padding: [0, 0] });
    map.options.zoomSnap = 1;
    map.setMinZoom(map.getZoom());  // can't zoom out past the starting (fitted) view
  }
  fitMap();
  map.setMaxBounds(viewBounds);  // tight bounds → no off-centre drift at the min (fitted) zoom
  // If the page loaded on another tab, the initial fit ran against a 0-size container;
  // re-fit each time the Map tab becomes visible.
  window.tabShown.map = fitMap;

  // ── Country (adm0) choropleth layer ──────────────────────────────────────────
  const catOf = (f, tri, rainyOn) => {
    const iso3 = f.properties.iso3;
    if (!fc.data[iso3]) return "unmonitored";
    return classify(fc.data[iso3][tri], rainyOn);
  };
  const tooltipHtml = (f) => {
    const iso3 = f.properties.iso3;
    const rec = (fc.data[iso3] || {})[currentTri()];
    const cat = catOf(f, currentTri(), seasonalityOn());
    let rpLine = "";
    if (rec && rec.rp != null) {
      rpLine = `<div>Return period: ${fmtRp(rec.rp)} yr</div><div>Correlation: ${fmtR(rec.r)}</div>`;
    } else if (rec) {
      rpLine = `<div>Correlation: ${fmtR(rec.r)}</div>`;
    }
    return `<div class="name">${f.properties.name}</div>` +
      `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>` + rpLine;
  };
  const admLayer = L.geoJSON(geo, {
    style: () => ({ weight: OUTLINE_W, fillOpacity: 1, opacity: 1 }),
    onEachFeature: (f, layer) => {
      layer.bindTooltip(() => tooltipHtml(f), { sticky: true });
    },
  });
  // Legend hover state: a predicate over full category names (null = no highlight).
  let hlMatch = null;
  function renderAdm() {
    const tri = currentTri(), rainyOn = seasonalityOn();
    admLayer.eachLayer((layer) => {
      const cat = catOf(layer.feature, tri, rainyOn);
      const el = layer._path;
      if (!el) return;
      const dim = hlMatch && !hlMatch(cat);
      el.setAttribute("fill", fillFor[cat]);
      el.setAttribute("fill-opacity", dim ? "0.12" : "1");
      el.setAttribute("stroke", STYLE[cat][1]);
      el.setAttribute("stroke-opacity", dim ? "0.25" : "1");
    });
  }
  // Legend hover → highlight matching areas, dim the rest (both views).
  function setHighlight(key) {
    if (key == null) {
      hlMatch = null;
      rasterLayer.setHighlight(null);
    } else if (key === "skill_high" || key === "skill_mod") {
      const suf = key === "skill_high" ? "_high" : "_mod";
      hlMatch = (cat) => cat.endsWith(suf) || (key === "skill_high" && cat === "high_none")
        || (key === "skill_mod" && cat === "mid_none");
      rasterLayer.setHighlight(CODE_GROUPS[key]);
    } else {
      hlMatch = (cat) => catBase(cat) === key ||
        (key === "none" && (cat === "high_none" || cat === "mid_none"));
      rasterLayer.setHighlight(CODE_GROUPS[key] || []);
    }
    if (mode === "country") renderAdm();
  }

  // ── Pixel (raster) layer ─────────────────────────────────────────────────────
  const rbounds = rmeta ? rmeta.bounds : [[worldBounds.getSouth(), worldBounds.getWest()],
                                          [worldBounds.getNorth(), worldBounds.getEast()]];
  const rasterLayer = new RasterLayer(rbounds);
  const variant = () => (seasonalityOn() ? "all" : "masked");
  // Baked pixel PNGs exist only for the trimesters listed in rmeta (incl. the in-season
  // mixed obs+forecast ones); the note is a fallback for trimesters missing from rmeta.
  const pixTriNote = document.getElementById("pixel-tri-note");
  const pixelHasTri = (key) => !!rmeta && rmeta.trimesters.some((t) => t.key === key);
  function loadPixelGrid() {
    if (!rmeta) return;
    if (!pixelHasTri(currentTri())) {
      if (pixTriNote) pixTriNote.hidden = false;
      rasterLayer.setGrid(null, 0, 0);
      return;
    }
    if (pixTriNote) pixTriNote.hidden = true;
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
      const cx = c.getContext("2d"); cx.drawImage(img, 0, 0);
      const d = cx.getImageData(0, 0, img.width, img.height).data;
      const grid = new Uint8Array(img.width * img.height);
      for (let i = 0; i < grid.length; i++) grid[i] = d[i * 4];
      rasterLayer.setGrid(grid, img.width, img.height);
    };
    img.src = `raster/data/${currentTri()}_${variant()}.png`;
  }

  // ── Issuance browsing (year / month) ──────────────────────────────────────────
  function rebuildMonths(year) {
    const months = index.months_by_year[String(year)] || [];
    monthSel.innerHTML = "";
    for (const m of months) {
      const o = document.createElement("option");
      o.value = String(m); o.textContent = index.month_names[String(m)];
      monthSel.appendChild(o);
    }
  }
  function setDropdowns(year, month) {
    yearSel.value = String(year);
    rebuildMonths(year);
    monthSel.value = String(month);
  }
  function loadIssuance(year, month) {
    return fetch(`data/forecasts/${year}-${String(month).padStart(2, "0")}.json`)
      .then((r) => r.json())
      .then((f) => {
        fc = f;
        document.getElementById("subtitle").textContent = `Forecast — issued ${fc.issued_label}`;
        document.getElementById("issued-label").textContent = fc.issued_label;
        triSlider.max = fc.trimesters.length - 1;
        let idx = curTriKey ? fc.trimesters.findIndex((t) => t.key === curTriKey) : -1;
        if (idx < 0) idx = Math.max(0, fc.trimesters.findIndex((t) => t.key === fc.default_trimester));
        triSlider.value = idx;
        updateTriLabel();
      });
  }
  for (const y of index.years) {
    const o = document.createElement("option");
    o.value = String(y); o.textContent = String(y);
    yearSel.appendChild(o);
  }

  // ── Mode toggle (Country / Pixel) ─────────────────────────────────────────────
  let mode = "country";
  const isLatest = () => fc && fc.issued_year === latest.year && fc.issued_month === latest.month;
  function setControlsEnabled(on) {
    yearSel.disabled = !on; monthSel.disabled = !on;
    document.getElementById("issued-lock-note").hidden = on;
  }
  function applyMode() {
    if (mode === "country") {
      setControlsEnabled(true);
      if (pixTriNote) pixTriNote.hidden = true;
      map.removeLayer(rasterLayer); map.removeLayer(outlineLayer);
      admLayer.addTo(map);
      renderAdm();
    } else {
      setControlsEnabled(false);   // pixel layer exists only for the latest issuance
      map.removeLayer(admLayer);
      outlineLayer.addTo(map); rasterLayer.addTo(map);
      loadPixelGrid();
    }
    buildLegend(mode, setHighlight);
  }
  function refresh() {
    updateTriLabel();
    if (mode === "country") renderAdm(); else loadPixelGrid();
  }

  const viewBtns = document.querySelectorAll("#view-toggle .seg-btn");
  viewBtns.forEach((b) => b.addEventListener("click", async () => {
    if (b.dataset.view === mode) return;
    mode = b.dataset.view;
    viewBtns.forEach((x) => x.classList.toggle("active", x.dataset.view === mode));
    // Pixel rasters only exist for the most recent issuance — snap to it.
    if (mode === "pixel" && !isLatest()) {
      setDropdowns(latest.year, latest.month);
      await loadIssuance(latest.year, latest.month);
    }
    applyMode();
  }));
  triSlider.addEventListener("input", refresh);
  seasonality.addEventListener("change", refresh);
  yearSel.addEventListener("change", () => {
    const m = +monthSel.value;
    rebuildMonths(+yearSel.value);
    const months = index.months_by_year[yearSel.value] || [];
    monthSel.value = String(months.includes(m) ? m : months[months.length - 1]);
    loadIssuance(+yearSel.value, +monthSel.value).then(refresh);
  });
  monthSel.addEventListener("change", () => loadIssuance(+yearSel.value, +monthSel.value).then(refresh));

  // Initial load: latest issuance, country mode.
  setDropdowns(latest.year, latest.month);
  loadIssuance(latest.year, latest.month).then(applyMode);
});

// Tabs (Map / Methodology) with #hash deep-linking.
(function setupTabs() {
  const buttons = document.querySelectorAll(".tab");
  function show(name) {
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.hidden = p.id !== "tab-" + name;
    });
    // Maps need a re-fit once their container is actually visible (rAF = after layout),
    // plus a settle pass — async web-font loading can shift layout just after the rAF.
    const cb = window.tabShown[name];
    if (typeof cb === "function") {
      requestAnimationFrame(cb);
      setTimeout(cb, 350);
    }
  }
  buttons.forEach((b) => b.addEventListener("click", () => {
    show(b.dataset.tab);
    history.replaceState(null, "", "#" + b.dataset.tab);
  }));
  const initial = location.hash.replace("#", "");
  if (["map", "skillmap", "skill", "hnrp", "methods"].includes(initial)) show(initial);
})();

// CSS hatch fill for legend swatches — crosshatch for "cross", single stripe otherwise.
function hatchBg(hatch) {
  const color = hatch === "white" ? "rgba(255,255,255,0.7)" : hatch === "grey" ? "#9db1b3" : "#b1c1c2";
  const stripe = (deg) => `repeating-linear-gradient(${deg}deg, ${color} 0 1.5px, transparent 1.5px 5px)`;
  return hatch === "cross" ? `${stripe(45)}, ${stripe(135)}` : stripe(135);
}

// Redesign legend: a contiguous colour strip drier→wetter, a skill strip below it
// (solid = high skill, hatched = moderate), and standalone swatches for the rest.
// Hovering a segment/swatch highlights matching areas on the map and dims the rest.
function buildLegend(mode, onHover) {
  const root = document.getElementById("legend");
  root.innerHTML = "";
  const hover = (el, key) => {
    if (!onHover) return;
    el.addEventListener("mouseenter", () => onHover(key));
    el.addEventListener("mouseleave", () => onHover(null));
  };

  function strip(title, segs, segWidth) {
    const block = document.createElement("div");
    block.className = "legend-block";
    const t = document.createElement("span");
    t.className = "lb-title"; t.textContent = title;
    const row = document.createElement("div");
    row.className = "legend-strip" + (onHover ? " interactive" : "");
    for (const s of segs) {
      const seg = document.createElement("span");
      seg.className = "ls-seg"; seg.style.width = segWidth + "px";
      const cell = document.createElement("span");
      cell.className = "ls-cell"; cell.style.background = s.fill;
      if (s.hatch) cell.style.backgroundImage = hatchBg(s.hatch);
      if (s.border) cell.style.boxShadow = "inset 0 0 0 1px #c4d0d1";
      const lbl = document.createElement("span");
      lbl.className = "ls-lbl"; lbl.textContent = s.label;
      seg.append(cell, lbl);
      hover(seg, s.key);
      row.appendChild(seg);
    }
    block.append(t, row);
    root.appendChild(block);
  }

  // 1) Forecast anomaly: drier → wetter (solid = high-skill rendering of each category).
  strip("Forecast", [
    { key: "drought_vsev", fill: STYLE.drought_vsev_high[0], label: "Strongly below" },
    { key: "drought_sev", fill: STYLE.drought_sev_high[0], label: "Below" },
    { key: "none", fill: STYLE.high_none[0], label: "Roughly normal" },
    { key: "flood_sev", fill: STYLE.flood_sev_high[0], label: "Above" },
    { key: "flood_vsev", fill: STYLE.flood_vsev_high[0], label: "Strongly above" },
  ], 86);

  // 2) Skill: how the fills above are rendered — white cells with grey hatching so the
  // strip demos the pattern without reading as a forecast category.
  strip("Skill", [
    { key: "skill_high", fill: "#ffffff", border: true, label: "High — solid" },
    { key: "skill_mod", fill: "#ffffff", border: true, hatch: "grey", label: "Moderate — hatched" },
    { key: "low_skill", fill: "#ffffff", border: true, hatch: "cross", label: "Low — no alert" },
  ], 118);

  // 3) The rest as plain swatches.
  const other = mode === "pixel" ? ["off_season"] : ["off_season", "unmonitored"];
  const g = document.createElement("div");
  g.className = "legend-group";
  g.style.paddingTop = "18px";
  for (const cat of other) {
    const [fill, edge, hatch] = STYLE[cat];
    const item = document.createElement("span");
    item.className = "legend-item" + (onHover ? " interactive" : "");
    const sw = document.createElement("span");
    sw.className = "swatch";
    sw.style.background = fill; sw.style.borderColor = edge;
    if (hatch) sw.style.backgroundImage = hatchBg(hatch);
    const lbl = document.createElement("span");
    lbl.textContent = CAT_LABEL[cat] || cat;
    item.append(sw, lbl);
    hover(item, cat);
    g.appendChild(item);
  }
  root.appendChild(g);
}
