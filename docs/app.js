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

let T = { sev_rp: 3, vsev_rp: 10, r_mod: 0.25, r_high: 0.5 };

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

// SVG diagonal/cross stripe patterns for hatched categories; returns fill ref per category.
function buildPatterns(svg) {
  const defs = svg.append("defs");
  const fillFor = {};
  for (const [cat, [fill, , hatch]] of Object.entries(STYLE)) {
    if (!hatch) { fillFor[cat] = fill; continue; }
    const id = "pat-" + cat;
    const stroke = hatch === "white" ? "rgba(255,255,255,0.7)"
                 : hatch === "grey" ? "#CCCCCC" : "#BBBBBB";
    const p = defs.append("pattern")
      .attr("id", id).attr("patternUnits", "userSpaceOnUse")
      .attr("width", 5).attr("height", 5);
    p.append("rect").attr("width", 5).attr("height", 5).attr("fill", fill);
    // Full-length lines + rotate(45) give continuous straight "/" stripes (no tapering);
    // the crosshatch adds the perpendicular set.
    p.attr("patternTransform", "rotate(45)");
    const sw = hatch === "cross" ? 1.1 : 1.4;
    p.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 0).attr("y2", 5)
      .attr("stroke", stroke).attr("stroke-width", sw);
    if (hatch === "cross") {
      p.append("line").attr("x1", 0).attr("y1", 0).attr("x2", 5).attr("y2", 0)
        .attr("stroke", stroke).attr("stroke-width", sw);
    }
    fillFor[cat] = `url(#${id})`;
  }
  return fillFor;
}

const fmtR = (v) => v == null ? "—" : v.toFixed(2);
const fmtRp = (v) => v == null ? "—" : v.toFixed(1);

Promise.all([
  d3.json("data/forecast.json"),
  d3.json("data/countries.geojson"),
]).then(([fc, geo]) => {
  T = fc.thresholds;
  document.getElementById("subtitle").textContent =
    `Most recent forecast — issued ${fc.issued_label}`;
  document.getElementById("issued-label").textContent = fc.issued_label;

  const triSel = d3.select("#trimester");
  triSel.selectAll("option").data(fc.trimesters).join("option")
    .attr("value", (d) => d.key)
    .text((d) => `${d.key}  (${d.label})`);
  triSel.property("value", fc.default_trimester);

  const svg = d3.select("#map");
  const width = 1100, height = 560;
  svg.attr("viewBox", `0 0 ${width} ${height}`);
  const fillFor = buildPatterns(svg);

  const monitored = { type: "FeatureCollection",
    features: geo.features.filter((f) => fc.data[f.properties.iso3]) };
  // Fit to the full (clipped) world for stable framing, not the monitored subset.
  const projection = d3.geoEquirectangular().fitExtent([[6, 6], [width - 6, height - 6]], geo);
  const path = d3.geoPath(projection);

  const gCountries = svg.append("g");
  const gDots = svg.append("g");
  const tooltip = d3.select("#tooltip");

  // small-country centroid dots so islands stay visible
  const smallFeatures = monitored.features.filter((f) => path.area(f) < 60);

  const mapWrap = document.getElementById("map-wrap");
  const currentTri = () => triSel.property("value");
  const seasonalityOn = () => d3.select("#seasonality").property("checked");
  const catOf = (f, tri, rainyOn) => {
    const iso3 = f.properties.iso3;
    if (!fc.data[iso3]) return "unmonitored";
    return classify(fc.data[iso3][tri], rainyOn);
  };

  // Shared hover tooltip for both country fills and small-country dots.
  function showTooltip(event, f) {
    const iso3 = f.properties.iso3;
    const rec = (fc.data[iso3] || {})[currentTri()];
    const cat = catOf(f, currentTri(), seasonalityOn());
    const [mx, my] = d3.pointer(event, mapWrap);
    let rpLine = "";
    if (rec && rec.rp != null) {
      rpLine = `<div>Return period: ${fmtRp(rec.rp)} yr</div><div>Correlation: ${fmtR(rec.r)}</div>`;
    } else if (rec) {
      rpLine = `<div>Correlation: ${fmtR(rec.r)}</div>`;
    }
    tooltip.classed("hidden", false)
      .style("left", (mx + 14) + "px")
      .style("top", (my + 12) + "px")
      .html(
        `<div class="name">${f.properties.name}</div>` +
        `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>` +
        rpLine
      );
  }
  const hideTooltip = () => tooltip.classed("hidden", true);

  gCountries.selectAll("path").data(geo.features).join("path")
    .attr("class", "country")
    .attr("d", path)
    .attr("stroke-width", 0.4)
    .on("mousemove", showTooltip)
    .on("mouseleave", hideTooltip);

  function render() {
    const tri = currentTri(), rainyOn = seasonalityOn();
    gCountries.selectAll("path")
      .attr("fill", (f) => fillFor[catOf(f, tri, rainyOn)])
      .attr("stroke", (f) => STYLE[catOf(f, tri, rainyOn)][1]);
    gDots.selectAll("circle").data(smallFeatures).join("circle")
      .attr("class", "dot")
      .attr("transform", (f) => `translate(${path.centroid(f)})`)
      .attr("r", 3.2)
      .attr("stroke", (f) => STYLE[catOf(f, tri, rainyOn)][1])
      .attr("stroke-width", 0.6)
      .attr("fill", (f) => fillFor[catOf(f, tri, rainyOn)])
      .on("mousemove", showTooltip)
      .on("mouseleave", hideTooltip);
  }

  triSel.on("change", render);
  d3.select("#seasonality").on("change", render);
  render();
  buildLegend();
});

// Tabs (Map / Methodology) with #hash deep-linking.
(function setupTabs() {
  const buttons = document.querySelectorAll(".tab");
  function show(name) {
    buttons.forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
    document.querySelectorAll(".tab-panel").forEach((p) => {
      p.hidden = p.id !== "tab-" + name;
    });
  }
  buttons.forEach((b) => b.addEventListener("click", () => {
    show(b.dataset.tab);
    history.replaceState(null, "", "#" + b.dataset.tab);
  }));
  const initial = location.hash.replace("#", "");
  if (initial === "methods" || initial === "map") show(initial);
})();

// CSS hatch fill for legend swatches — crosshatch for "cross", single stripe otherwise.
function hatchBg(hatch) {
  const color = hatch === "white" ? "rgba(255,255,255,0.7)" : hatch === "grey" ? "#CCC" : "#BBB";
  // 135deg → "/" stripes, matching the map's SVG hatch direction.
  const stripe = (deg) => `repeating-linear-gradient(${deg}deg, ${color} 0 1.5px, transparent 1.5px 5px)`;
  return hatch === "cross" ? `${stripe(45)}, ${stripe(135)}` : stripe(135);
}

function buildLegend() {
  const groups = [
    ["Forecast (high skill)", ["drought_vsev_high", "drought_sev_high", "high_none", "flood_sev_high", "flood_vsev_high"]],
    ["Other", ["mid_none", "low_skill", "off_season", "no_data"]],
  ];
  const root = d3.select("#legend");
  for (const [title, cats] of groups) {
    const g = root.append("div").attr("class", "legend-group");
    g.append("span").style("font-weight", "600").text(title + ":");
    for (const cat of cats) {
      const [fill, edge, hatch] = STYLE[cat];
      const item = g.append("span").attr("class", "legend-item");
      const sw = item.append("span").attr("class", "swatch")
        .style("background", fill).style("border-color", edge);
      if (hatch) sw.style("background-image", hatchBg(hatch));
      item.append("span").text(CAT_LABEL[catBase(cat)] || cat);
    }
  }
}
