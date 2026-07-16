"use strict";

// Standalone CBPF page: the country-level SEAS5 forecast map, but highlighting the 18
// country-based pooled funds in the US "Humanitarian Reset" award (Dec 2025) and dimming
// the rest. Self-contained (no dependency on app.js) so it can't disturb the main site.
// Not linked from the main nav — reached directly at /cbpf.html.

// US "Humanitarian Reset" award countries (Dec 2025) = the 21-country two-tranche set
// minus Ethiopia.
const CBPF_AWARD = new Set([
  "BGD", "MMR", "TCD", "COL", "COD", "SLV", "GTM", "HTI", "HND", "KEN",
  "MOZ", "NGA", "SSD", "SDN", "SYR", "UGA", "UKR", "VEN", "LBN", "CAF",
]);
// All 29 countries with an OCHA pooled-fund envelope — country-based pooled funds plus
// Regional Humanitarian Pooled Fund (RHPF) envelopes (AP / LAC / WCA / ESAHF).
const CBPF_ALL = new Set([
  "AFG", "BFA", "BGD", "CAF", "COD", "COL", "ETH", "FJI", "GTM", "HND",
  "HTI", "KEN", "LBN", "MLI", "MMR", "MOZ", "NGA", "PAK", "PSE", "SLB",
  "SLV", "SDN", "SOM", "SSD", "SYR", "TCD", "UGA", "UKR", "VEN",
]);
// CERF anticipatory-action El Niño country sets.
const CERF_FW = new Set([  // CERF AA framework
  "BFA", "TCD", "SLV", "ETH", "FJI", "GTM", "HND", "MRT", "NER", "PHL", "SOM", "VUT",
]);
const CERF_NF = new Set([  // CERF non-framework AA
  "AGO", "ETH", "LSO", "MDG", "MWI", "MNG", "MOZ", "PER", "SOM", "TLS", "ZWE",
]);
// Tiny countries that are invisible at world scale — drawn as a fixed-size dot showing
// their forecast value. [lat, lon] of a representative point.
const DOT_POINTS = {
  HTI: [19.0, -72.5], SLV: [13.8, -88.9], PSE: [31.95, 35.25],
  FJI: [-17.8, 178.0], SLB: [-9.6, 160.2], LBN: [33.85, 35.88],
  VUT: [-16.5, 168.3], TLS: [-8.8, 125.8], LSO: [-29.6, 28.2],
  BGD: [23.7, 90.4], PHL: [12.0, 122.0], GTM: [15.5, -90.3], HND: [14.8, -86.5],
  MWI: [-13.2, 34.3],
};
const DOT_R = 4;

const AWARD_EDGE = "#e31a1c", AWARD_W = 2.2;   // US-award outline (red, bold)
const CBPF_EDGE = "#ff7f00", CBPF_W = 1.7;     // any-CBPF outline (orange)
const FW_EDGE = "#1f78b4", FW_W = 1.7;         // CERF AA framework (blue)
const NF_EDGE = "#33a02c", NF_W = 1.7;         // CERF non-framework AA (green)
const OTHER_EDGE = "#cfcfcf", OTHER_W = 0.5;   // everyone else (thin grey)
const BASE_EDGE = "#b8b8b8", BASE_W = 1.6;     // hairline on every country to bridge seams
const PALE = 0.5;                              // fill-opacity when a country isn't highlighted
const DASH = 7;                                // dash length when a country is in several sets

// ── Category styling (ported from the main map) ──────────────────────────────────
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
const catBase = (cat) => cat.replace(/_(high|mod)$/, "");
let T = { sev_rp: 3, vsev_rp: 10, r_mod: 0.3, r_high: 0.5 };

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

// ── adm0 SVG hatch patterns (constant screen density; layer points ≈ px) ──────────
function buildPatterns() {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("width", "0"); svg.setAttribute("height", "0");
  svg.style.position = "absolute";
  const defs = document.createElementNS(NS, "defs");
  svg.appendChild(defs);
  const PITCH = 3.5;  // hatch tile size (smaller = tighter lines; reads better on small dots)
  const fillFor = {};
  for (const [cat, [fill, , hatch]] of Object.entries(STYLE)) {
    if (!hatch) { fillFor[cat] = fill; continue; }
    const id = "cbpf-pat-" + cat;
    const stroke = hatch === "white" ? "rgba(255,255,255,0.7)"
                 : hatch === "grey" ? "#CCCCCC" : "#BBBBBB";
    const p = document.createElementNS(NS, "pattern");
    p.setAttribute("id", id);
    p.setAttribute("patternUnits", "userSpaceOnUse");
    p.setAttribute("width", PITCH); p.setAttribute("height", PITCH);
    p.setAttribute("patternTransform", "rotate(45)");
    const bg = document.createElementNS(NS, "rect");
    bg.setAttribute("width", PITCH); bg.setAttribute("height", PITCH); bg.setAttribute("fill", fill);
    p.appendChild(bg);
    const sw = hatch === "cross" ? 1.0 : 1.2;
    const line = (x1, y1, x2, y2) => {
      const l = document.createElementNS(NS, "line");
      l.setAttribute("x1", x1); l.setAttribute("y1", y1);
      l.setAttribute("x2", x2); l.setAttribute("y2", y2);
      l.setAttribute("stroke", stroke); l.setAttribute("stroke-width", sw);
      p.appendChild(l);
    };
    line(0, 0, 0, PITCH);
    if (hatch === "cross") line(0, 0, PITCH, 0);
    defs.appendChild(p);
    fillFor[cat] = `url(#${id})`;
  }
  document.body.appendChild(svg);
  return fillFor;
}

function hatchBg(hatch) {
  const color = hatch === "white" ? "rgba(255,255,255,0.7)" : hatch === "grey" ? "#CCC" : "#BBB";
  const stripe = (deg) => `repeating-linear-gradient(${deg}deg, ${color} 0 1.5px, transparent 1.5px 5px)`;
  return hatch === "cross" ? `${stripe(45)}, ${stripe(135)}` : stripe(135);
}

function buildLegend() {
  const groups = [
    ["Forecast (high skill)", ["drought_vsev_high", "drought_sev_high", "high_none", "flood_sev_high", "flood_vsev_high"]],
    ["Other", ["mid_none", "low_skill", "off_season", "unmonitored"]],
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
  // Outline legend — only the country sets whose toggle is currently on.
  const entries = [
    ["show-award", AWARD_EDGE, 2.5, "US-award country"],
    ["show-cbpf", CBPF_EDGE, 2, "Other CBPF/RhPF country"],
    ["show-fw", FW_EDGE, 2, "CERF AA framework (El Niño)"],
    ["show-nf", NF_EDGE, 2, "CERF non-framework AA (El Niño)"],
  ].filter(([id]) => {
    const cb = document.getElementById(id);
    return cb && cb.checked;
  });
  if (entries.length) {
    const og = document.createElement("div");
    og.className = "legend-group";
    const ot = document.createElement("span");
    ot.style.fontWeight = "600"; ot.textContent = "Outline:";
    og.appendChild(ot);
    for (const [, edge, w, label] of entries) {
      const item = document.createElement("span");
      item.className = "legend-item";
      const sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = "#fff";
      sw.style.border = `${w}px solid ${edge}`;
      const lbl = document.createElement("span");
      lbl.textContent = label;
      item.append(sw, lbl);
      og.appendChild(item);
    }
    root.appendChild(og);
  }
}

const fmtR = (v) => v == null ? "—" : v.toFixed(2);
const fmtRp = (v) => v == null ? "—" : v.toFixed(1);

// ── Main ──────────────────────────────────────────────────────────────────────────
Promise.all([
  fetch("data/forecasts/index.json").then((r) => r.json()),
  fetch("data/countries.geojson").then((r) => r.json()),
]).then(([index, geo]) => {
  T = index.thresholds;
  const latest = index.latest;
  let fc = null, curTriKey = null;
  const fillFor = buildPatterns();
  const OUTLINE_W = 1.1;

  const triSlider = document.getElementById("trimester");
  const triLabel = document.getElementById("trimester-label");
  const seasonality = document.getElementById("seasonality");
  const yearSel = document.getElementById("issued-year");
  const monthSel = document.getElementById("issued-month");
  const awardToggle = document.getElementById("show-award");
  const cbpfToggle = document.getElementById("show-cbpf");
  const fwToggle = document.getElementById("show-fw");
  const nfToggle = document.getElementById("show-nf");
  const seasonalityOn = () => seasonality.checked;
  const currentTri = () => fc.trimesters[+triSlider.value].key;
  // Signed leadtime for the loaded issuance; negative = in-season (trimester underway,
  // elapsed months observed rather than forecast) — pale the slider as a cue.
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

  // Map (plate carrée; flush fit with a temporary zoomSnap 0).
  const map = L.map("map", {
    crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
    attributionControl: false, zoomControl: false, maxBoundsViscosity: 1.0,
  });
  const outlineLayer = L.geoJSON(geo, { interactive: false, style: { opacity: 0, fillOpacity: 0 } });
  const worldBounds = outlineLayer.getBounds();
  const LAT_PAD = 8;
  const viewBounds = L.latLngBounds(
    [worldBounds.getSouth() - LAT_PAD, worldBounds.getWest()],
    [worldBounds.getNorth() + LAT_PAD, worldBounds.getEast()]);
  const aspect = (viewBounds.getEast() - viewBounds.getWest()) /
                 (viewBounds.getNorth() - viewBounds.getSouth());
  document.getElementById("map").style.aspectRatio = String(aspect);
  function fitMap() {
    map.invalidateSize();
    map.setMinZoom(0);
    map.options.zoomSnap = 0;
    map.fitBounds(viewBounds, { padding: [0, 0] });
    map.options.zoomSnap = 1;
    map.setMinZoom(map.getZoom());  // can't zoom out past the starting (fitted) view
  }
  fitMap();
  map.setMaxBounds(viewBounds);  // tight bounds → no off-centre drift at the min (fitted) zoom

  // Title overlay (top-left of the map).
  const titleCtl = L.control({ position: "topleft" });
  titleCtl.onAdd = () => {
    const div = L.DomUtil.create("div", "map-title");
    div.innerHTML =
      '<div class="mt-title">SEAS5 precipitation seasonal forecast</div>' +
      '<div class="mt-sub" id="map-subtitle">—</div>';
    return div;
  };
  titleCtl.addTo(map);
  function updateMapTitle() {
    const el = document.getElementById("map-subtitle");
    if (!el || !fc) return;
    const t = fc.trimesters[+triSlider.value];
    el.textContent = `Issued ${fc.issued_label} · Valid ${t.key} (${t.label})`;
  }

  // ── adm0 layer: all forecasts shown; pooled-fund + CERF sets outlined ─────────────
  const catOf = (f, tri, rainyOn) => classify((fc.data[f.properties.iso3] || {})[tri], rainyOn);

  // Ordered membership "dimensions" for a country — each shown as one coloured outline.
  // Pooled fund is one slot (award red, else CBPF orange); the two CERF sets are the others.
  // A country in several sets gets all its colours as interleaved dashes.
  // Toggles may be absent (the /cbpf page has no CERF sets); missing toggle ⇒ that set off.
  function dimsFor(iso3) {
    const d = [];
    if (awardToggle?.checked && CBPF_AWARD.has(iso3)) d.push({ key: "pool", color: AWARD_EDGE, w: AWARD_W });
    else if (cbpfToggle?.checked && CBPF_ALL.has(iso3)) d.push({ key: "pool", color: CBPF_EDGE, w: CBPF_W });
    if (fwToggle?.checked && CERF_FW.has(iso3)) d.push({ key: "fw", color: FW_EDGE, w: FW_W });
    if (nfToggle?.checked && CERF_NF.has(iso3)) d.push({ key: "nf", color: NF_EDGE, w: NF_W });
    return d;
  }
  const fillOpacityFor = (iso3) => (dimsFor(iso3).length ? 1 : PALE);
  // Whether a country belongs to any set this page offers (a toggle for it exists).
  const onPage = (iso3) =>
    CBPF_AWARD.has(iso3) || CBPF_ALL.has(iso3) ||
    (fwToggle && CERF_FW.has(iso3)) || (nfToggle && CERF_NF.has(iso3));
  function membershipTag(iso3) {
    const t = [];
    if (CBPF_AWARD.has(iso3)) t.push("US award");
    else if (CBPF_ALL.has(iso3)) t.push("CBPF/RhPF");
    if (fwToggle && CERF_FW.has(iso3)) t.push("CERF framework");
    if (nfToggle && CERF_NF.has(iso3)) t.push("CERF non-framework");
    return t.length ? ` · ${t.join(", ")}` : "";
  }
  const tooltipHtml = (f) => {
    const rec = (fc.data[f.properties.iso3] || {})[currentTri()];
    const cat = catOf(f, currentTri(), seasonalityOn());
    const tag = membershipTag(f.properties.iso3);
    let extra = "";
    if (rec && rec.rp != null) {
      extra = `<div>Return period: ${fmtRp(rec.rp)} yr</div><div>Correlation: ${fmtR(rec.r)}</div>`;
    } else if (rec) {
      extra = `<div>Correlation: ${fmtR(rec.r)}</div>`;
    }
    return `<div class="name">${f.properties.name}${tag}</div>` +
      `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>` + extra;
  };
  // Base fills with a grey hairline that bridges narrow seams between countries.
  const admLayer = L.geoJSON(geo, {
    style: (f) => ({
      weight: BASE_W, color: BASE_EDGE, opacity: 1,
      fillColor: "#ffffff", fillOpacity: fillOpacityFor(f.properties.iso3),
    }),
    onEachFeature: (f, layer) => layer.bindTooltip(() => tooltipHtml(f), { sticky: true }),
  }).addTo(map);
  // One top outline layer per dimension (no fill, clipped to each shape). Stacked so a
  // country can carry several coloured outlines at once.
  const mkOutlineLayer = () => L.geoJSON(geo, {
    interactive: false, style: { weight: 0, fill: false, opacity: 1 },
  }).addTo(map);
  const poolLayer = mkOutlineLayer();
  const fwLayer = mkOutlineLayer();
  const nfLayer = mkOutlineLayer();
  const OUTLINE_LAYERS = [{ key: "pool", layer: poolLayer }, { key: "fw", layer: fwLayer },
    { key: "nf", layer: nfLayer }];

  // Dots for hard-to-see countries: a forecast-coloured fill plus up to 3 overlaid rings
  // (one per set) that are dashed/offset so several colours alternate around the dot.
  const RW = 2.2;  // ring stroke width
  const featByIso = {};
  geo.features.forEach((f) => { featByIso[f.properties.iso3] = f; });
  const dots = {};
  for (const iso in DOT_POINTS) {
    const f = featByIso[iso];
    if (!f || !onPage(iso)) continue;  // only dot countries relevant to this page
    const pt = DOT_POINTS[iso];
    const fill = L.circleMarker(pt, {
      radius: DOT_R, stroke: false, fillColor: "#fff", fillOpacity: 1,
    }).addTo(map);
    fill.bindTooltip(() => tooltipHtml(f), { sticky: true });
    const rings = [0, 1, 2].map(() => L.circleMarker(pt, {
      radius: DOT_R, fill: false, weight: RW, color: OTHER_EDGE, opacity: 0, interactive: false,
    }).addTo(map));
    dots[iso] = { fill, rings, feature: f };
  }

  function renderAdm() {
    updateMapTitle();
    const tri = currentTri(), rainyOn = seasonalityOn();
    admLayer.eachLayer((layer) => {
      const el = layer._path;
      if (!el) return;
      const iso3 = layer.feature.properties.iso3;
      el.setAttribute("fill", fillFor[catOf(layer.feature, tri, rainyOn)]);
      el.setAttribute("fill-opacity", fillOpacityFor(iso3));
    });
    for (const iso in dots) {
      const el = dots[iso].fill._path;
      if (!el) continue;
      el.setAttribute("fill", fillFor[catOf(dots[iso].feature, tri, rainyOn)]);
      el.setAttribute("fill-opacity", "1");  // dots stay fully visible
    }
  }
  // Inset outlines via self-clipping: clip each highlighted country to its own shape and
  // double the stroke, so only the inner half shows. Neighbouring outlines then never
  // overlap — an orange country ringed by red still shows its full orange border.
  const SVGNS = "http://www.w3.org/2000/svg";
  const clipPaths = {};   // iso3 -> <path> inside its <clipPath>
  function setInset(layer, iso3, stroke, w) {
    const el = layer._path;
    const svg = el && el.ownerSVGElement;
    if (!svg) return;
    let cp = clipPaths[iso3];
    if (!cp) {
      let defs = svg.querySelector("defs.cbpf-clips");
      if (!defs) {
        defs = document.createElementNS(SVGNS, "defs");
        defs.setAttribute("class", "cbpf-clips");
        svg.appendChild(defs);
      }
      const clip = document.createElementNS(SVGNS, "clipPath");
      clip.setAttribute("id", "cbpf-clip-" + iso3);
      cp = document.createElementNS(SVGNS, "path");
      clip.appendChild(cp);
      defs.appendChild(clip);
      clipPaths[iso3] = cp;
    }
    cp.setAttribute("d", el.getAttribute("d") || "");
    el.setAttribute("clip-path", `url(#cbpf-clip-${iso3})`);
    el.setAttribute("stroke", stroke);
    el.setAttribute("stroke-width", String(w * 2));  // inner (visible) half == w
  }
  // Re-sync clip geometry to the projected outline paths after the map moves/zooms.
  // poolLayer carries every country, so it's enough to refresh the shared clip paths.
  function syncClips() {
    poolLayer.eachLayer((layer) => {
      const cp = clipPaths[layer.feature.properties.iso3];
      if (cp && layer._path) cp.setAttribute("d", layer._path.getAttribute("d") || "");
    });
  }
  map.on("zoomend moveend viewreset", () => requestAnimationFrame(syncClips));

  function applyOutlines() {
    // Fill opacity (on the base layer) follows whether a country is highlighted at all.
    admLayer.eachLayer((layer) => {
      const el = layer._path;
      if (el) el.setAttribute("fill-opacity", fillOpacityFor(layer.feature.properties.iso3));
    });
    // Each outline layer draws its dimension's colour where active. When a country is in
    // several sets, every line is dashed and offset so the colours interleave (all visible).
    for (const { key, layer } of OUTLINE_LAYERS) {
      layer.eachLayer((sub) => {
        const el = sub._path;
        if (!el) return;
        const iso3 = sub.feature.properties.iso3;
        const dims = dimsFor(iso3);
        const idx = dims.findIndex((d) => d.key === key);
        if (idx < 0) {
          el.removeAttribute("clip-path");
          el.setAttribute("stroke", "none");
          el.removeAttribute("stroke-dasharray");
          el.removeAttribute("stroke-dashoffset");
          return;
        }
        setInset(sub, iso3, dims[idx].color, dims[idx].w);
        if (dims.length > 1) {
          el.setAttribute("stroke-dasharray", `${DASH} ${DASH * (dims.length - 1)}`);
          el.setAttribute("stroke-dashoffset", String(idx * DASH));
        } else {
          el.removeAttribute("stroke-dasharray");
          el.removeAttribute("stroke-dashoffset");
        }
      });
    }
    // Dots on top of everything: one ring per active set, all at the same radius, dashed
    // and offset so several colours alternate around the dot (like the big countries).
    for (const iso in dots) {
      const { fill, rings } = dots[iso];
      const dims = dimsFor(iso);
      const N = dims.length;
      rings.forEach((ring, i) => {
        if (i < N) {
          ring.setRadius(DOT_R + RW / 2);
          ring.setStyle({
            color: dims[i].color, weight: RW, opacity: 1,
            dashArray: N > 1 ? `${DASH} ${DASH * (N - 1)}` : null,
            dashOffset: N > 1 ? String(i * DASH) : null,
          });
        } else {
          ring.setStyle({ opacity: 0 });
        }
      });
      if (!N) {  // not in any set — keep a thin grey ring so the dot still reads
        rings[0].setRadius(DOT_R + RW / 2);
        rings[0].setStyle({ color: OTHER_EDGE, weight: 1, opacity: 1, dashArray: null, dashOffset: null });
      }
      fill.bringToFront();
      rings.forEach((r) => r.bringToFront());
    }
    buildLegend();  // reflect which country sets are currently shown
  }

  // ── Issuance browsing (year / month) ──────────────────────────────────────────────
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

  triSlider.addEventListener("input", () => { updateTriLabel(); renderAdm(); });
  seasonality.addEventListener("change", renderAdm);
  [awardToggle, cbpfToggle, fwToggle, nfToggle].filter(Boolean).forEach((t) =>
    t.addEventListener("change", applyOutlines));
  yearSel.addEventListener("change", () => {
    const m = +monthSel.value;
    rebuildMonths(+yearSel.value);
    const months = index.months_by_year[yearSel.value] || [];
    monthSel.value = String(months.includes(m) ? m : months[months.length - 1]);
    loadIssuance(+yearSel.value, +monthSel.value).then(renderAdm);
  });
  monthSel.addEventListener("change", () => loadIssuance(+yearSel.value, +monthSel.value).then(renderAdm));

  buildLegend();
  setDropdowns(latest.year, latest.month);
  loadIssuance(latest.year, latest.month).then(() => { renderAdm(); applyOutlines(); });
}).catch((e) => {
  document.getElementById("subtitle").textContent = "Could not load forecast data.";
  console.error(e);
});
