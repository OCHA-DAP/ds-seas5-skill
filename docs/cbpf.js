"use strict";

// Standalone CBPF page: the country-level SEAS5 forecast map, but highlighting the 18
// country-based pooled funds in the US "Humanitarian Reset" award (Dec 2025) and dimming
// the rest. Self-contained (no dependency on app.js) so it can't disturb the main site.
// Not linked from the main nav — reached directly at /cbpf.html.

// The 18 US "Humanitarian Reset" award countries (Dec 2025) = the 21-country two-tranche
// set minus CAR, Lebanon, Venezuela.
const CBPF_AWARD = new Set([
  "BGD", "MMR", "TCD", "COL", "COD", "SLV", "ETH", "GTM", "HTI", "HND",
  "KEN", "MOZ", "NGA", "SSD", "SDN", "SYR", "UGA", "UKR",
]);
// All 29 countries with an OCHA pooled-fund envelope — country-based pooled funds plus
// Regional Humanitarian Pooled Fund (RHPF) envelopes (AP / LAC / WCA / ESAHF).
const CBPF_ALL = new Set([
  "AFG", "BFA", "BGD", "CAF", "COD", "COL", "ETH", "FJI", "GTM", "HND",
  "HTI", "KEN", "LBN", "MLI", "MMR", "MOZ", "NGA", "PAK", "PSE", "SLB",
  "SLV", "SDN", "SOM", "SSD", "SYR", "TCD", "UGA", "UKR", "VEN",
]);
const AWARD_EDGE = "#111111", AWARD_W = 2.2;   // US-award outline (black, bold)
const CBPF_EDGE = "#6a3d9a", CBPF_W = 1.7;     // any-CBPF outline (purple)
const OTHER_EDGE = "#cfcfcf", OTHER_W = 0.5;   // everyone else (thin grey)
const PALE = 0.5;                              // fill-opacity for non-award countries
const groupOf = (iso3) =>
  CBPF_AWARD.has(iso3) ? "award" : (CBPF_ALL.has(iso3) ? "cbpf" : "other");

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
  const fillFor = {};
  for (const [cat, [fill, , hatch]] of Object.entries(STYLE)) {
    if (!hatch) { fillFor[cat] = fill; continue; }
    const id = "cbpf-pat-" + cat;
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
  // Outline legend (the two highlight colours).
  const og = document.createElement("div");
  og.className = "legend-group";
  const ot = document.createElement("span");
  ot.style.fontWeight = "600"; ot.textContent = "Outline:";
  og.appendChild(ot);
  for (const [edge, w, label] of [
    [AWARD_EDGE, 2.5, "US-award country"],
    [CBPF_EDGE, 2, "Other CBPF country"],
  ]) {
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
  const seasonalityOn = () => seasonality.checked;
  const currentTri = () => fc.trimesters[+triSlider.value].key;
  const updateTriLabel = () => {
    const t = fc.trimesters[+triSlider.value];
    curTriKey = t.key;
    triLabel.textContent = `${t.key} (${t.label})`;
  };

  // Map (plate carrée; flush fit with a temporary zoomSnap 0).
  const map = L.map("map", {
    crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
    attributionControl: false, zoomControl: false,
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
    map.options.zoomSnap = 0;
    map.fitBounds(viewBounds, { padding: [0, 0] });
    map.options.zoomSnap = 1;
  }
  fitMap();
  map.setMaxBounds(viewBounds.pad(0.15));

  // ── adm0 layer: all forecasts shown; award + CBPF countries outlined ──────────────
  const catOf = (f, tri, rainyOn) => classify((fc.data[f.properties.iso3] || {})[tri], rainyOn);
  const tooltipHtml = (f) => {
    const rec = (fc.data[f.properties.iso3] || {})[currentTri()];
    const cat = catOf(f, currentTri(), seasonalityOn());
    const g = groupOf(f.properties.iso3);
    const tag = g === "award" ? " · US award" : g === "cbpf" ? " · CBPF" : "";
    let extra = "";
    if (rec && rec.rp != null) {
      extra = `<div>Return period: ${fmtRp(rec.rp)} yr</div><div>Correlation: ${fmtR(rec.r)}</div>`;
    } else if (rec) {
      extra = `<div>Correlation: ${fmtR(rec.r)}</div>`;
    }
    return `<div class="name">${f.properties.name}${tag}</div>` +
      `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>` + extra;
  };
  // Fill opacity: pooled-fund countries (award or CBPF) full; everyone else paler.
  const fillOpacityFor = (iso3) => (groupOf(iso3) === "other" ? PALE : 1);
  // Outline depends on the two toggles. Award countries are also CBPFs, so when the award
  // outline is hidden they fall back to the CBPF outline (if that's shown).
  const outlineFor = (iso3) => {
    const g = groupOf(iso3);
    if (g === "award" && awardToggle.checked) return [AWARD_EDGE, AWARD_W];
    if (g !== "other" && cbpfToggle.checked) return [CBPF_EDGE, CBPF_W];
    return [OTHER_EDGE, OTHER_W];
  };
  const admLayer = L.geoJSON(geo, {
    style: (f) => ({
      weight: OTHER_W, color: OTHER_EDGE, opacity: 1,
      fillColor: "#ffffff", fillOpacity: fillOpacityFor(f.properties.iso3),
    }),
    onEachFeature: (f, layer) => layer.bindTooltip(() => tooltipHtml(f), { sticky: true }),
  }).addTo(map);

  function renderAdm() {
    const tri = currentTri(), rainyOn = seasonalityOn();
    admLayer.eachLayer((layer) => {
      const el = layer._path;
      if (!el) return;
      const iso3 = layer.feature.properties.iso3;
      el.setAttribute("fill", fillFor[catOf(layer.feature, tri, rainyOn)]);
      el.setAttribute("fill-opacity", fillOpacityFor(iso3));
    });
  }
  function applyOutlines() {
    admLayer.eachLayer((layer) => {
      const el = layer._path;
      if (!el) return;
      const [stroke, w] = outlineFor(layer.feature.properties.iso3);
      el.setAttribute("stroke", stroke);
      el.setAttribute("stroke-width", w);
    });
    // Raise CBPF, then award, so their borders sit above neighbouring countries.
    admLayer.eachLayer((l) => {
      if (cbpfToggle.checked && groupOf(l.feature.properties.iso3) !== "other") l.bringToFront();
    });
    admLayer.eachLayer((l) => {
      if (awardToggle.checked && groupOf(l.feature.properties.iso3) === "award") l.bringToFront();
    });
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
  awardToggle.addEventListener("change", applyOutlines);
  cbpfToggle.addEventListener("change", applyOutlines);
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
