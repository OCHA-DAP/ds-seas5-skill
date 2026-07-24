// "Skill map" tab: global SEAS5 hindcast skill (Pearson r) as a map, like the forecast
// map but showing skill alone. Pick a valid trimester and a leadtime; toggle Country
// (adm0 choropleth from skill_matrix.json) vs Pixel (baked RGBA overlays on the native
// SEAS5 grid). Colours are categorical at the skill cutoffs.

(function () {
  const OUTLINE_W = 1.1;
  const NODATA = "#f5f7f7";  // matches the forecast adm0 "unmonitored" fill
  const OFF_SEASON = "#b1c1c2";

  // HDX brand ramp (data.humdata.org v2 tokens); negative = a pale error-scale tint.
  function catsFor(thr) {
    const RMOD = (thr && thr.r_mod) || 0.3;
    const RHIGH = (thr && thr.r_high) || 0.5;
    return [
      { min: RHIGH, color: "#1e795f", label: `High (r ≥ ${RHIGH.toFixed(2)})` },
      { min: RMOD, color: "#7dc1ad", label: `Moderate (${RMOD.toFixed(2)}–${RHIGH.toFixed(2)})` },
      { min: 0, color: "#bee0d6", label: `Low (0–${RMOD.toFixed(2)})` },
      { min: -Infinity, color: "#f3dad7", label: "Negative (< 0)" },
    ];
  }

  Promise.all([
    fetch("data/skill_matrix.json").then((r) => r.json()),
    // window.SITE_GEO lets sibling pages (e.g. /cma) share the main site's geometry.
    fetch(window.SITE_GEO || "data/countries.geojson").then((r) => r.json()),
    fetch("raster/skill/meta.json").then((r) => r.json()).catch(() => null),
  ]).then(([sm, geo, rmeta]) => {
    const tris = sm.trimesters;                 // 12, calendar order
    const leads = sm.leads;                      // [-2..4]; negative = in-season issues
    const CATS = catsFor(sm.thresholds);
    const catFor = (r) => (r == null ? null : CATS.find((c) => r >= c.min) || CATS[CATS.length - 1]);
    const triIdx = (key) => tris.findIndex((t) => t.key === key);
    const MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const TRI_START = { JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6,
      JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 12 };

    // ── Controls (sliders) ──────────────────────────────────────────────────────
    const triSlider = document.getElementById("skillmap-tri");
    const leadSlider = document.getElementById("skillmap-lead");
    const triLbl = document.getElementById("skillmap-tri-label");
    const leadLbl = document.getElementById("skillmap-lead-label");
    triSlider.max = tris.length - 1;
    triSlider.value = Math.max(0, triIdx((rmeta && rmeta.default_trimester) || "JAS"));
    // Issued-month slider runs opposite to leadtime: left = earliest issue (longest
    // lead), right = latest issue. Negative leads = issued in-season (months already
    // observed blended in). Position p → lead maxLead−p.
    const maxLead = leads[leads.length - 1];
    const minLead = leads[0];
    leadSlider.min = 0;
    leadSlider.max = maxLead - minLead;
    const defaultLead = (rmeta && rmeta.default_lead) != null ? rmeta.default_lead : 1;
    leadSlider.value = String(maxLead - defaultLead);
    const seasonality = document.getElementById("skillmap-seasonality");
    const seasonalityOn = () => seasonality.checked;
    const isRainy = (iso3) => {
      const c = sm.countries[iso3];
      return !!(c && c.rainy[triIdx(curTri())]);
    };
    const curTri = () => tris[+triSlider.value].key;
    const curLead = () => maxLead - (+leadSlider.value);
    const issuedMonth = () => ((TRI_START[curTri()] - curLead() - 1 + 144) % 12) + 1;
    const leadText = (l) => l === 0 ? "same month"
      : l < 0 ? `${-l} mo into season` : `${l}-month lead`;
    function updateLabels() {
      const t = tris[+triSlider.value];
      triLbl.textContent = `${t.key} (${t.label})`;
      const l = curLead();
      leadSlider.classList.toggle("in-season", l < 0);
      leadLbl.innerHTML = l < 0
        ? `${MON[issuedMonth()]} <span class="in-season-tag">(${leadText(l)})</span>`
        : `${MON[issuedMonth()]} (${leadText(l)})`;
    }

    // ── Map ───────────────────────────────────────────────────────────────────
    const map = L.map("skill-map", {
      crs: L.CRS.EPSG4326, minZoom: 1, maxZoom: 8,
      attributionControl: false, zoomControl: false, maxBoundsViscosity: 1.0,
    });
    L.control.zoom({ position: "topleft" }).addTo(map);
    const outlineLayer = L.geoJSON(geo, {
      interactive: false,
      style: { color: "#5a5a5a", weight: OUTLINE_W, fillOpacity: 0, opacity: 0.95 },
    });
    const worldBounds = outlineLayer.getBounds();
    const LAT_PAD = 8;
    const viewBounds = L.latLngBounds(
      [worldBounds.getSouth() - LAT_PAD, worldBounds.getWest()],
      [worldBounds.getNorth() + LAT_PAD, worldBounds.getEast()]);
    const aspect = (viewBounds.getEast() - viewBounds.getWest()) /
                   (viewBounds.getNorth() - viewBounds.getSouth());
    document.getElementById("skill-map").style.aspectRatio = String(aspect);
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
    window.tabShown.skillmap = fitMap;

    // ── Country (adm0) layer ────────────────────────────────────────────────────
    const rOf = (iso3) => {
      const c = sm.countries[iso3];
      if (!c) return undefined;
      const li = leads.indexOf(curLead()), ti = triIdx(curTri());
      return li < 0 || ti < 0 ? undefined : c.r[li][ti];
    };
    const admTooltip = (f) => {
      const iso3 = f.properties.iso3;
      const r = rOf(iso3);
      const off = !seasonalityOn() && !isRainy(iso3);
      let line;
      if (r == null) {
        line = `<div class="cat">Not monitored</div>`;
      } else if (off) {
        line = `<div class="cat" style="color:#999">Outside rainy season</div>` +
          `<div>r = ${r.toFixed(2)}</div>`;
      } else {
        const cat = catFor(r);
        line = `<div class="cat" style="color:${cat.color}">${cat.label.split(" (")[0]}</div>` +
          `<div>r = ${r.toFixed(2)}</div>`;
      }
      return `<div class="name">${f.properties.name}</div>` +
        `<div>Issued ${MON[issuedMonth()]} · ${curTri()} · ${leadText(curLead())}</div>${line}`;
    };
    const admLayer = L.geoJSON(geo, {
      style: () => ({ weight: OUTLINE_W, color: "#5a5a5a", fillOpacity: 1, opacity: 1 }),
      onEachFeature: (f, layer) => layer.bindTooltip(() => admTooltip(f), { sticky: true }),
    });
    function renderAdm() {
      const rainyOn = seasonalityOn();
      admLayer.eachLayer((layer) => {
        const iso3 = layer.feature.properties.iso3;
        const r = rOf(iso3);
        const el = layer._path;
        if (!el) return;
        let fill;
        if (r == null) fill = NODATA;
        else if (!rainyOn && !isRainy(iso3)) fill = OFF_SEASON;
        else fill = catFor(r).color;
        el.setAttribute("fill", fill);
        el.setAttribute("fill-opacity", "1");
      });
    }

    // ── Pixel layer (baked RGBA overlay + off-season cover) ──────────────────────
    const pixUrl = () => `raster/skill/${curTri()}_L${curLead()}.png`;
    const maskUrl = () => `raster/skill/mask_${curTri()}.png`;
    const hasMask = !!(rmeta && rmeta.has_mask);
    // Baked skill PNGs exist only for the leads in rmeta.leads (−2..4 incl. in-season);
    // anything else falls back to the note.
    const pixLeads = (rmeta && rmeta.leads) || [];
    const pixNote = document.getElementById("skillmap-pixel-note");
    let overlay = null, cover = null;
    function showPixel() {
      if (!rmeta) return;
      if (!pixLeads.includes(curLead())) {
        if (pixNote) pixNote.hidden = false;
        if (overlay && map.hasLayer(overlay)) map.removeLayer(overlay);
        if (cover && map.hasLayer(cover)) map.removeLayer(cover);
        return;
      }
      if (pixNote) pixNote.hidden = true;
      if (!overlay) {
        overlay = L.imageOverlay(pixUrl(), rmeta.bounds, {
          opacity: 1, className: "skill-overlay", interactive: false,
        });
      } else {
        overlay.setUrl(pixUrl());
      }
      if (!map.hasLayer(overlay)) overlay.addTo(map);
      // Off-season grey cover on top of the skill image when the rainy mask is on.
      if (hasMask && !seasonalityOn()) {
        if (!cover) {
          cover = L.imageOverlay(maskUrl(), rmeta.bounds, {
            opacity: 1, className: "skill-overlay", interactive: false,
          });
        } else {
          cover.setUrl(maskUrl());
        }
        if (!map.hasLayer(cover)) cover.addTo(map);
        cover.bringToFront();
      } else if (cover && map.hasLayer(cover)) {
        map.removeLayer(cover);
      }
      outlineLayer.bringToFront();
    }

    // ── Mode toggle ─────────────────────────────────────────────────────────────
    let mode = "country";
    function applyMode() {
      if (mode === "country") {
        if (overlay) map.removeLayer(overlay);
        if (cover) map.removeLayer(cover);
        if (pixNote) pixNote.hidden = true;
        map.removeLayer(outlineLayer);
        admLayer.addTo(map);
        renderAdm();
      } else {
        map.removeLayer(admLayer);
        outlineLayer.addTo(map);
        showPixel();
      }
    }
    function refresh() {
      if (mode === "country") renderAdm(); else showPixel();
    }

    const viewBtns = document.querySelectorAll("#skillmap-toggle .seg-btn");
    viewBtns.forEach((b) => b.addEventListener("click", () => {
      if (b.dataset.view === mode || (b.dataset.view === "pixel" && !rmeta)) return;
      mode = b.dataset.view;
      viewBtns.forEach((x) => x.classList.toggle("active", x.dataset.view === mode));
      applyMode();
    }));
    if (!rmeta) {
      const px = document.querySelector('#skillmap-toggle [data-view="pixel"]');
      if (px) { px.disabled = true; px.title = "Pixel layer not built"; }
    }
    const onSlide = () => { updateLabels(); refresh(); };
    triSlider.addEventListener("input", onSlide);
    leadSlider.addEventListener("input", onSlide);
    seasonality.addEventListener("change", refresh);

    // ── Legend: contiguous ramp strip (ordered scale, negative → high) ───────────
    const legend = document.getElementById("skill-map-legend");
    if (legend) {
      legend.innerHTML = "";
      const block = document.createElement("div");
      block.className = "legend-block";
      const t = document.createElement("span");
      t.className = "lb-title"; t.textContent = "Skill (Pearson r)";
      const row = document.createElement("div");
      row.className = "legend-strip";
      for (const c of [...CATS].reverse()) {   // ascending: negative → low → moderate → high
        const seg = document.createElement("span");
        seg.className = "ls-seg"; seg.style.width = "108px";
        const cell = document.createElement("span");
        cell.className = "ls-cell"; cell.style.background = c.color;
        const lbl = document.createElement("span");
        lbl.className = "ls-lbl"; lbl.textContent = c.label;
        seg.append(cell, lbl);
        row.appendChild(seg);
      }
      block.append(t, row);
      legend.appendChild(block);

      const g = document.createElement("div");
      g.className = "legend-group";
      g.style.paddingTop = "18px";
      for (const it of [{ color: OFF_SEASON, label: "Outside rainy season" },
                        { color: NODATA, label: "Not monitored" }]) {
        const span = document.createElement("span");
        span.className = "legend-item";
        const sw = document.createElement("span");
        sw.className = "swatch"; sw.style.background = it.color;
        const lbl = document.createElement("span");
        lbl.textContent = it.label;
        span.append(sw, lbl);
        g.appendChild(span);
      }
      legend.appendChild(g);
    }

    updateLabels();
    applyMode();
  }).catch((e) => {
    const host = document.getElementById("skill-map");
    if (host) host.textContent = "Could not load skill map data.";
    console.error(e);
  });
})();
