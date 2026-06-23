"use strict";

// ── Category styling — ported from analysis/prob_alerts.py:400-414 ──────────────
// [fill, edge, hatch]  hatch: null | "white" | "grey" | "cross"
const STYLE = {
  off_season:        ["#D0D0D0", "#BBBBBB", null],
  no_data:           ["#E8E8E8", "#CCCCCC", null],
  high_none:         ["#FFFFFF", "#AAAAAA", null],
  mid_none:          ["#FFFFFF", "#AAAAAA", "grey"],
  low_skill:         ["#FFFFFF", "#AAAAAA", "cross"],
  drought_vsev_high: ["#7B3A1A", "#5A2A0A", null],
  drought_vsev_mod:  ["#7B3A1A", "#5A2A0A", "white"],
  drought_sev_high:  ["#C8844A", "#A06030", null],
  drought_sev_mod:   ["#C8844A", "#A06030", "white"],
  flood_vsev_high:   ["#0D40B0", "#092E88", null],
  flood_vsev_mod:    ["#0D40B0", "#092E88", "white"],
  flood_sev_high:    ["#71B3E5", "#4A90C8", null],
  flood_sev_mod:     ["#71B3E5", "#4A90C8", "white"],
  unmonitored:       ["#F5F5F5", "#E0E0E0", null],
};

const CAT_LABEL = {
  drought_vsev: "Strongly below normal", drought_sev: "Below normal",
  flood_sev: "Above normal", flood_vsev: "Strongly above normal",
  high_none: "Roughly normal", mid_none: "Roughly normal (mod skill)",
  low_skill: "Low skill", off_season: "Outside rainy season",
  no_data: "No data", unmonitored: "Not monitored",
};

// Strip the skill suffix so a category maps to its CAT_LABEL key.
const catBase = (cat) => cat.replace(/_(high|mod)$/, "");

let T = { sev_rp: 3, vsev_rp: 10, r_mod: 0.3, r_high: 0.5 };

// Faithful port of the map categorisation (analysis/prob_alerts.py:379-397).
function classify(rec, rainyOn) {
  if (!rec) return "unmonitored";
  if (!rainyOn && !rec.rainy) return "off_season";
  if (rec.r == null || rec.pct == null) return "no_data";
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
                 : hatch === "grey" ? "#CCCCCC" : "#BBBBBB";
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
  2: makeTile((x) => { x.strokeStyle = "#B4B4B4"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.stroke(); }),
  3: makeTile((x) => { x.strokeStyle = "#B4B4B4"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.moveTo(0, 0); x.lineTo(TILE, TILE); x.stroke(); }),
};

// Per category code: fill colour (null = transparent → white basemap) and hatch kind.
const FILL = [null, "#D0D0D0", null, null, null, "#7B3A1A", "#7B3A1A",
              "#C8844A", "#C8844A", "#71B3E5", "#71B3E5", "#0D40B0", "#0D40B0"];
const KIND = [0, 0, 3, 0, 2, 0, 1, 0, 1, 0, 1, 0, 1];

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
    const colorPaths = {}, hatchPaths = { 1: new Path2D(), 2: new Path2D(), 3: new Path2D() };
    const g = this._grid;
    for (let i = 0; i < ny; i++) {
      const y = ey[i], hh = ey[i + 1] - y;
      if (hh <= 0 || ey[i + 1] < 0 || y > H) continue;
      const off = i * nx;
      for (let j = j0; j < j1; j++) {
        const code = g[off + j];
        if (!code) continue;
        const x = ex[j], cw = ex[j + 1] - x;
        if (FILL[code]) (colorPaths[code] || (colorPaths[code] = new Path2D())).rect(x, y, cw, hh);
        const k = KIND[code];
        if (k) hatchPaths[k].rect(x, y, cw, hh);
      }
    }
    ctx.globalAlpha = 0.9;
    for (const code in colorPaths) { ctx.fillStyle = FILL[code]; ctx.fill(colorPaths[code]); }
    ctx.globalAlpha = 1;
    for (const v of [1, 2, 3]) { ctx.fillStyle = ctx.createPattern(TILES[v], "repeat"); ctx.fill(hatchPaths[v]); }
  },
});

const fmtR = (v) => v == null ? "—" : v.toFixed(2);
const fmtRp = (v) => v == null ? "—" : v.toFixed(1);

// Re-fits the map; assigned once the map exists. Called when the Map tab is shown,
// because Leaflet can't measure its container while that tab is hidden.
let onMapShown = null;

Promise.all([
  fetch("data/forecast.json").then((r) => r.json()),
  fetch("data/countries.geojson").then((r) => r.json()),
  fetch("raster/data/meta.json").then((r) => r.json()).catch(() => null),
]).then(([fc, geo, rmeta]) => {
  T = fc.thresholds;
  document.getElementById("subtitle").textContent =
    `Most recent forecast — issued ${fc.issued_label}`;
  document.getElementById("issued-label").textContent = fc.issued_label;

  const fillFor = buildPatterns();
  const OUTLINE_W = 1.1;   // shared border thickness for both Country and Pixel views

  // ── Controls ───────────────────────────────────────────────────────────────
  const triSlider = document.getElementById("trimester");
  const triLabel = document.getElementById("trimester-label");
  const seasonality = document.getElementById("seasonality");
  triSlider.max = fc.trimesters.length - 1;
  triSlider.value = Math.max(0, fc.trimesters.findIndex((t) => t.key === fc.default_trimester));
  const currentTri = () => fc.trimesters[+triSlider.value].key;
  const seasonalityOn = () => seasonality.checked;
  const updateTriLabel = () => {
    const t = fc.trimesters[+triSlider.value];
    triLabel.textContent = `${t.key} (${t.label})`;
  };

  // ── Map ──────────────────────────────────────────────────────────────────────
  const map = L.map("map", {
    crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
    attributionControl: false, zoomControl: false,
    // Leaflet defaults for scroll (whole-level snap, default wheel speed) — feels responsive.
    // The flush initial fit uses a temporary zoomSnap: 0 below, then restores to 1.
  });

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
    map.options.zoomSnap = 0;
    map.fitBounds(viewBounds, { padding: [0, 0] });
    map.options.zoomSnap = 1;
  }
  fitMap();
  map.setMaxBounds(viewBounds.pad(0.15));
  // If the page loaded on another tab, the initial fit ran against a 0-size container;
  // re-fit each time the Map tab becomes visible.
  onMapShown = fitMap;

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
  function renderAdm() {
    const tri = currentTri(), rainyOn = seasonalityOn();
    admLayer.eachLayer((layer) => {
      const cat = catOf(layer.feature, tri, rainyOn);
      const el = layer._path;
      if (!el) return;
      el.setAttribute("fill", fillFor[cat]);
      el.setAttribute("fill-opacity", "1");
      el.setAttribute("stroke", STYLE[cat][1]);
    });
  }

  // ── Pixel (raster) layer ─────────────────────────────────────────────────────
  const rbounds = rmeta ? rmeta.bounds : [[worldBounds.getSouth(), worldBounds.getWest()],
                                          [worldBounds.getNorth(), worldBounds.getEast()]];
  const rasterLayer = new RasterLayer(rbounds);
  const variant = () => (seasonalityOn() ? "all" : "masked");
  function loadPixelGrid() {
    if (!rmeta) return;
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

  // ── Mode toggle (Country / Pixel) ─────────────────────────────────────────────
  let mode = "country";
  function applyMode() {
    if (mode === "country") {
      map.removeLayer(rasterLayer); map.removeLayer(outlineLayer);
      admLayer.addTo(map);
      renderAdm();
    } else {
      map.removeLayer(admLayer);
      outlineLayer.addTo(map); rasterLayer.addTo(map);
      loadPixelGrid();
    }
    buildLegend(mode);
  }
  function refresh() {
    updateTriLabel();
    if (mode === "country") renderAdm(); else loadPixelGrid();
  }

  const viewBtns = document.querySelectorAll("#view-toggle .seg-btn");
  viewBtns.forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.view === mode) return;
    mode = b.dataset.view;
    viewBtns.forEach((x) => x.classList.toggle("active", x.dataset.view === mode));
    applyMode();
  }));
  triSlider.addEventListener("input", refresh);
  seasonality.addEventListener("change", refresh);

  updateTriLabel();
  applyMode();
});

// Tabs (Map / Methodology) with #hash deep-linking.
(function setupTabs() {
  const buttons = document.querySelectorAll(".tab");
  function show(name) {
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.hidden = p.id !== "tab-" + name;
    });
    // Map needs a re-fit once its container is actually visible (rAF = after layout).
    if (name === "map" && typeof onMapShown === "function") requestAnimationFrame(onMapShown);
  }
  buttons.forEach((b) => b.addEventListener("click", () => {
    show(b.dataset.tab);
    history.replaceState(null, "", "#" + b.dataset.tab);
  }));
  const initial = location.hash.replace("#", "");
  if (["map", "skill", "methods"].includes(initial)) show(initial);
})();

// CSS hatch fill for legend swatches — crosshatch for "cross", single stripe otherwise.
function hatchBg(hatch) {
  const color = hatch === "white" ? "rgba(255,255,255,0.7)" : hatch === "grey" ? "#CCC" : "#BBB";
  const stripe = (deg) => `repeating-linear-gradient(${deg}deg, ${color} 0 1.5px, transparent 1.5px 5px)`;
  return hatch === "cross" ? `${stripe(45)}, ${stripe(135)}` : stripe(135);
}

function buildLegend(mode) {
  // Pixel grid is land-only with data everywhere → drop the country-only "no data"/"not monitored".
  const other = mode === "pixel"
    ? ["mid_none", "low_skill", "off_season"]
    : ["mid_none", "low_skill", "off_season", "unmonitored"];
  const groups = [
    ["Forecast (high skill)", ["drought_vsev_high", "drought_sev_high", "high_none", "flood_sev_high", "flood_vsev_high"]],
    ["Other", other],
  ];
  const root = document.getElementById("legend");
  root.innerHTML = "";
  for (const [title, cats] of groups) {
    const g = document.createElement("div");
    g.className = "legend-group";
    const t = document.createElement("span");
    t.style.fontWeight = "600"; t.textContent = title + ":";
    g.appendChild(t);
    for (const cat of cats) {
      const [fill, edge, hatch] = STYLE[cat];
      const item = document.createElement("span");
      item.className = "legend-item";
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = fill; sw.style.borderColor = edge;
      if (hatch) sw.style.backgroundImage = hatchBg(hatch);
      const lbl = document.createElement("span");
      lbl.textContent = CAT_LABEL[catBase(cat)] || cat;
      item.append(sw, lbl);
      g.appendChild(item);
    }
    root.appendChild(g);
  }
}
