"use strict";

// ── adm0 legend (verbatim from docs/app.js) ──────────────────────────────────
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
};
const CAT_LABEL = {
  drought_vsev: "Strongly below normal", drought_sev: "Below normal",
  flood_sev: "Above normal", flood_vsev: "Strongly above normal",
  high_none: "Roughly normal", mid_none: "Roughly normal (mod skill)",
  low_skill: "Low skill", off_season: "Outside rainy season", no_data: "No data",
};
const catBase = (cat) => cat.replace(/_(high|mod)$/, "");
function hatchBg(hatch) {
  const color = hatch === "white" ? "rgba(255,255,255,0.7)" : hatch === "grey" ? "#CCC" : "#BBB";
  const stripe = (d) => `repeating-linear-gradient(${d}deg, ${color} 0 1.5px, transparent 1.5px 5px)`;
  return hatch === "cross" ? `${stripe(45)}, ${stripe(135)}` : stripe(135);
}
function buildLegend() {
  const groups = [
    ["Forecast (high skill)", ["drought_vsev_high", "drought_sev_high", "high_none", "flood_sev_high", "flood_vsev_high"]],
    ["Other", ["mid_none", "low_skill", "off_season"]],
  ];
  const root = document.getElementById("legend");
  for (const [title, cats] of groups) {
    const g = document.createElement("div");
    g.className = "legend-group";
    const t = document.createElement("span");
    t.style.fontWeight = "600";
    t.textContent = title + ":";
    g.appendChild(t);
    for (const cat of cats) {
      const [fill, edge, hatch] = STYLE[cat];
      const item = document.createElement("span");
      item.className = "legend-item";
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = fill;
      sw.style.borderColor = edge;
      if (hatch) sw.style.backgroundImage = hatchBg(hatch);
      const lbl = document.createElement("span");
      lbl.textContent = CAT_LABEL[catBase(cat)] || cat;
      item.append(sw, lbl);
      g.appendChild(item);
    }
    root.appendChild(g);
  }
}

// ── Screen-fixed hatch tiles (constant coarseness at every zoom) ─────────────
const TILE = 9, LW = 0.85;
function makeTile(draw) {
  const c = document.createElement("canvas"); c.width = c.height = TILE;
  const x = c.getContext("2d"); x.lineWidth = LW; draw(x); return c;
}
const TILES = {
  1: makeTile((x) => { x.strokeStyle = "rgba(255,255,255,0.85)"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.stroke(); }),         // white "/"
  2: makeTile((x) => { x.strokeStyle = "#B4B4B4"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.stroke(); }),                        // grey "/"
  3: makeTile((x) => { x.strokeStyle = "#B4B4B4"; x.beginPath(); x.moveTo(0, TILE); x.lineTo(TILE, 0); x.moveTo(0, 0); x.lineTo(TILE, TILE); x.stroke(); }), // grey "X"
};

// Per category code: fill colour (null = transparent → white basemap) and hatch kind.
const FILL = [null, "#D0D0D0", null, null, null, "#7B3A1A", "#7B3A1A",
              "#C8844A", "#C8844A", "#71B3E5", "#71B3E5", "#0D40B0", "#0D40B0"];
const KIND = [0, 0, 3, 0, 2, 0, 1, 0, 1, 0, 1, 0, 1];

// Canvas layer: draws the colour fills AND the skill hatch from ONE category grid, so they share
// the exact same pixel grid (no drift). Animates with the map on zoom; the hatch is a screen-space
// pattern so its coarseness stays constant.
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
    // Columns: linear in longitude (true for Web Mercator and lat/lon).
    const xW = m.latLngToLayerPoint([n, w0]).x - ox, xE = m.latLngToLayerPoint([n, e0]).x - ox;
    const dx = (xE - xW) / nx;
    const ex = new Int32Array(nx + 1);
    for (let j = 0; j <= nx; j++) ex[j] = Math.round(xW + j * dx);
    // Rows: follow each grid latitude (non-linear in Mercator). Shared rounded edges → exact tiling.
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

Promise.all([
  fetch("data/meta.json").then((r) => r.json()),
  fetch("../data/countries.geojson").then((r) => r.json()).catch(() => null),
]).then(([meta, world]) => {
  document.getElementById("subtitle").textContent =
    `Most recent forecast — issued ${meta.issued_label}  ·  SEAS5 0.4° grid`;

  const b = meta.bounds;                       // [[s,w],[n,e]]
  const bounds = L.latLngBounds([b[0][0], b[0][1]], [b[1][0], b[1][1]]);
  const map = L.map("map", {
    crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
    attributionControl: false, zoomControl: false, maxBounds: bounds.pad(0.2),
  });
  map.fitBounds(bounds);

  // Optional deep-link view: #zoom,lat,lng
  const h = location.hash.slice(1).split(",").map(Number);
  if (h.length === 3 && h.every((n) => !isNaN(n))) map.setView([h[1], h[2]], h[0]);

  // Controls
  const slider = document.getElementById("trimester");
  const label = document.getElementById("trimester-label");
  const seasonality = document.getElementById("seasonality");
  slider.max = meta.trimesters.length - 1;
  slider.value = Math.max(0, meta.trimesters.findIndex((t) => t.key === meta.default_trimester));

  const triKey = () => meta.trimesters[+slider.value].key;
  const variant = () => (seasonality.checked ? "all" : "masked");
  const codeUrl = () => `data/${triKey()}_${variant()}.png`;

  // Raster (colour + hatch) canvas, then country outlines on top
  const raster = new RasterLayer(b).addTo(map);
  if (world) {
    L.geoJSON(world, { interactive: false,
      style: { color: "#8a8a8a", weight: 0.6, fillOpacity: 0, opacity: 0.9 } }).addTo(map);
  }

  // Read a category-code PNG (grayscale value = code 0-12) into a Uint8 grid.
  function loadCodeGrid() {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
      const cx = c.getContext("2d"); cx.drawImage(img, 0, 0);
      const d = cx.getImageData(0, 0, img.width, img.height).data;
      const grid = new Uint8Array(img.width * img.height);
      for (let i = 0; i < grid.length; i++) grid[i] = d[i * 4];
      raster.setGrid(grid, img.width, img.height);
    };
    img.src = codeUrl();
  }

  const update = () => {
    const t = meta.trimesters[+slider.value];
    label.textContent = `${t.key} (${t.label})`;
    loadCodeGrid();
  };
  slider.addEventListener("input", update);
  seasonality.addEventListener("change", update);
  update();
  buildLegend();
});
