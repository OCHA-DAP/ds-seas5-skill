"use strict";

// Shared in-frame furniture for the Map and Skill-map Leaflet maps (also reused by the
// /cma mirror, which sets window.SITE_MODEL_LABEL / SITE_SOURCE before loading these):
//   • a title/subtitle card in the top-left corner (the valid trimester spelled out),
//   • a small data-source line in the bottom-right corner,
//   • zoom-aware dots for countries too small to see at the current zoom.
window.MapFrame = (function () {
  const MODEL = window.SITE_MODEL_LABEL || "ECMWF SEAS5";
  const SOURCE = window.SITE_SOURCE || "ECMWF SEAS5 and ERA5";

  // Title card (top-left). Returns { setTitle(text), setSub(html) }.
  function addTitle(map, title) {
    const ctl = L.control({ position: "topleft" });
    let tEl, sEl;
    ctl.onAdd = () => {
      const div = L.DomUtil.create("div", "map-title");
      tEl = L.DomUtil.create("div", "mt-title", div);
      sEl = L.DomUtil.create("div", "mt-sub", div);
      tEl.textContent = title;
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      return div;
    };
    ctl.addTo(map);
    return {
      setTitle: (t) => { tEl.textContent = t; },
      setSub: (html) => { sEl.innerHTML = html; },
    };
  }

  // Source line (bottom-right).
  function addSource(map) {
    const ctl = L.control({ position: "bottomright" });
    ctl.onAdd = () => {
      const div = L.DomUtil.create("div", "map-source");
      div.textContent = `Source: ${SOURCE}`;
      return div;
    };
    ctl.addTo(map);
  }

  // Valid-trimester text for a title: "Jul–Aug–Sep 2026 (JAS)"; the year straddles
  // when the trimester crosses New Year ("Nov–Dec–Jan 2026/27").
  const TRI_START = { JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6,
    JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 12 };
  function validText(tri, issuedYear, issuedMonth) {
    let years = "";
    if (issuedYear != null && issuedMonth != null) {
      // Signed lead (−2..4 → offset from the issue month), then the start year.
      const o = (TRI_START[tri.key] - issuedMonth + 12) % 12;
      const lead = o <= 6 ? o : o - 12;
      const startAbs = issuedYear * 12 + (issuedMonth - 1) + lead;
      const y0 = Math.floor(startAbs / 12), y1 = Math.floor((startAbs + 2) / 12);
      years = " " + (y1 === y0 ? String(y0) : `${y0}/${String(y1).slice(2)}`);
    }
    return `${tri.label}${years} (${tri.key})`;
  }

  // ── Small-country dots ─────────────────────────────────────────────────────────
  // A country whose on-screen footprint is smaller than about a dot gets a dot at its
  // largest polygon's centroid. Recomputed on every zoom: dots vanish once the country
  // itself is legible. Dots that would overlap another (larger country's) dot at this
  // zoom are culled so archipelago chains don't smear — zooming in reveals them.
  const DOT_R = 5;
  const MIN_SIDE_PX = 2 * DOT_R;  // sqrt(screen area) below this → dot

  function ringArea(ring) {
    let a = 0;
    for (let i = 0; i < ring.length - 1; i++) a += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    return a / 2;
  }
  function ringCentroid(ring) {
    let cx = 0, cy = 0, a = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const f = ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
      cx += (ring[i][0] + ring[i + 1][0]) * f; cy += (ring[i][1] + ring[i + 1][1]) * f; a += f;
    }
    if (Math.abs(a) < 1e-12) return [ring[0][1], ring[0][0]];
    return [cy / (3 * a), cx / (3 * a)];  // [lat, lon]
  }
  // Per feature: total area in deg² (plate carrée ⇒ screen area = deg² × (px/deg)²) and
  // the centroid of the largest piece (antimeridian-split pieces stay separate).
  function measure(feature) {
    const g = feature.geometry;
    const polys = g.type === "MultiPolygon" ? g.coordinates : [g.coordinates];
    let total = 0, best = -1, pt = null;
    for (const poly of polys) {
      const a = Math.abs(ringArea(poly[0]));
      total += a;
      if (a > best) { best = a; pt = ringCentroid(poly[0]); }
    }
    return { area: total, pt };
  }

  // opts: { features, hasData(iso3) → bool, tooltip(feature) → html, colour(feature) →
  // { fill, stroke, dim } }. Returns { render(), clear() }. render() (re)places and
  // recolours the dots for the current zoom; call it after every data change and it
  // also runs on zoomend. clear() removes all dots (e.g. Pixel mode).
  function dots(map, opts) {
    const items = opts.features.filter((f) => f.properties.iso3)
      .map((f) => Object.assign({ feature: f, iso3: f.properties.iso3, marker: null }, measure(f)))
      .filter((it) => it.pt && isFinite(it.pt[0]) && isFinite(it.pt[1]))
      .sort((a, b) => b.area - a.area);  // larger countries win the space
    let active = false;

    function pxPerDeg() {
      const p0 = map.latLngToLayerPoint([0, 0]), p1 = map.latLngToLayerPoint([0, 1]);
      return Math.abs(p1.x - p0.x);
    }
    function render() {
      active = true;
      const k = pxPerDeg();
      const placed = [];
      for (const it of items) {
        const side = Math.sqrt(it.area) * k;
        let show = side < MIN_SIDE_PX && opts.hasData(it.iso3);
        let p = null;
        if (show) {
          p = map.latLngToLayerPoint(it.pt);
          show = !placed.some((q) => Math.hypot(q.x - p.x, q.y - p.y) < 2 * DOT_R + 1);
        }
        if (!show) {
          if (it.marker && map.hasLayer(it.marker)) map.removeLayer(it.marker);
          continue;
        }
        placed.push(p);
        if (!it.marker) {
          it.marker = L.circleMarker(it.pt, {
            radius: DOT_R, weight: 1, fillOpacity: 1, opacity: 1, className: "small-country-dot",
          });
          it.marker.bindTooltip(() => opts.tooltip(it.feature), { sticky: true });
        }
        if (!map.hasLayer(it.marker)) it.marker.addTo(map);
        const c = opts.colour(it.feature);
        const el = it.marker._path;
        if (!el) continue;
        el.setAttribute("fill", c.fill);
        el.setAttribute("stroke", c.stroke || "#5a5a5a");
        el.setAttribute("fill-opacity", c.dim ? "0.12" : "1");
        el.setAttribute("stroke-opacity", c.dim ? "0.25" : "1");
      }
    }
    function clear() {
      active = false;
      for (const it of items) if (it.marker && map.hasLayer(it.marker)) map.removeLayer(it.marker);
    }
    map.on("zoomend viewreset resize", () => { if (active) render(); });
    return { render, clear };
  }

  return { MODEL, SOURCE, addTitle, addSource, validText, dots };
})();
