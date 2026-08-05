// Forecast × HNRP tab: ADM1 drought forecast vs HNRP severity/targeted caseloads.
// Three linked views — an ADM1 choropleth (same classification as the Map tab), a
// scatter (x = % of total population in severity 4+, y = targeted as % of total
// population, bubble area = total population, fill/hatch = forecast category ×
// skill), and the ranked table. Reuses app.js globals: STYLE, classify, catBase,
// CAT_LABEL, T, buildPatterns.
(async function () {
  // The tab shows every country at its finest available admin level — the level
  // the HNRP itself publishes, where each unit carries one severity class. The
  // fixed levels remain reachable as an unlisted ?adm=1|2|3 escape hatch (no
  // picker: switching would reload the page with that level's payload).
  const ADM_FILES = {
    low: ["data/hnrp_drought_low.json", "data/hnrp_low.geojson"],
    1: ["data/hnrp_drought.json", "data/hnrp_adm1.geojson"],
    2: ["data/hnrp_drought_adm2.json", "data/hnrp_adm2.geojson"],
    3: ["data/hnrp_drought_adm3.json", "data/hnrp_adm3.geojson"],
  };
  let ADM = new URLSearchParams(location.search).get("adm") ?? "low";
  if (!(ADM in ADM_FILES)) ADM = "low";
  const ADM_LABEL = { low: "lowest available level", 1: "admin 1", 2: "admin 2", 3: "admin 3" };
  let data, geo, world;
  try {
    // no-cache = revalidate: the admin-level switch is a plain navigation, which
    // otherwise serves stale payloads straight from HTTP cache mid-session.
    const fj = (f) => fetch(f, { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)));
    world = await fj("data/countries.geojson");
    try {
      [data, geo] = await Promise.all(ADM_FILES[ADM].map(fj));
    } catch {
      if (ADM !== "1") { // payload not built yet — fall back rather than a blank tab
        ADM = "1";
        [data, geo] = await Promise.all(ADM_FILES[1].map(fj));
      } else { throw new Error("no data"); }
    }
  } catch {
    return; // data files not built yet — leave the tab empty
  }
  // (the heading this used to retitle belonged to the removed scatter; the table
  // below keeps its own static "Matching areas, ranked")
  // Parent admin-1 name qualifies adm2 units (district names repeat across regions).
  const dispName = (r) => (r.parent ? `${r.name ?? r.pcode} (${r.parent})` : (r.name ?? r.pcode));
  // Every control survives a reload (and makes links shareable with their
  // settings): state is carried in the URL query string.
  const CTLS = {
    skill: "hnrp-skill", rp: "hnrp-rp", tri: "hnrp-tri",
    sev: "hnrp-sev-type", lvl: "hnrp-sev-lvl", ipcp: "hnrp-ipc-period",
    country: "hnrp-country",
  };
  function stateURL() {
    const u = new URL(location.href);
    if (ADM === "low") u.searchParams.delete("adm");
    else u.searchParams.set("adm", String(ADM));
    for (const [k, id] of Object.entries(CTLS)) {
      const el = document.getElementById(id);
      if (el && el.value) u.searchParams.set(k, el.value);
      else u.searchParams.delete(k); // deselected (e.g. country "") must clear too
    }
    u.searchParams.delete("dro"); // retired "show only drought signals" filter
    u.hash = "hnrp";
    return u;
  }
  function syncURL() {
    // Only mirror state while the HNRP tab owns the URL. This script runs on
    // EVERY page load, and stateURL() stamps #hnrp — unconditional mirroring
    // rewrote the address bar out from under whichever tab was actually open,
    // so the next hard refresh landed on HNRP instead.
    if (location.hash.replace("#", "") !== "hnrp") return;
    history.replaceState(null, "", stateURL().toString());
  }
  function restoreControls() {
    const q = new URLSearchParams(location.search);
    for (const [k, id] of Object.entries(CTLS)) {
      const v = q.get(k), el = document.getElementById(id);
      // Only restore values that exist in the select (the country list differs
      // between admin levels; an absent option silently stays at the default).
      if (v != null && el && [...el.options].some((o) => o.value === v)) el.value = v;
    }
  }

  const skillSel = document.getElementById("hnrp-skill");
  const rpSel = document.getElementById("hnrp-rp");
  const srcTypeSel = document.getElementById("hnrp-sev-type");
  const srcLvlSel = document.getElementById("hnrp-sev-lvl");
  const srcLvlWrap = document.getElementById("hnrp-sev-lvl-wrap");
  const ipcPeriodWrap = document.getElementById("hnrp-ipc-period-wrap");
  const ipcPeriodSel = document.getElementById("hnrp-ipc-period");
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

  // Plan cycle varies by country (e.g. Guatemala's latest is the 2025 HNRP) — surface
  // the year everywhere caseload figures appear.
  const planYrOf = (r) => {
    const ys = [r.ref_year, r.sev_year, r.pbs_yr].filter((y) => y != null);
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
  // PiN/targeted are always the plan's INTERSECTORAL figures (per-sector series
  // remain in the payload's r.sec should a sector view ever return).
  const pinOf = (r) => r.pin ?? null;
  const tgtOf = (r) => r.targeted ?? null;
  // Plan-cycle year of a targeted figure that fell back to an OLDER cycle than the
  // unit's PiN (none published in the current one) — flagged wherever it appears.
  const tgtYrOf = (r) => r.tgt_year ?? null;
  const tgtFlag = (r) => (tgtYrOf(r) ? ` (${tgtYrOf(r)} plan)` : "");
  const secTag = () => "";

  // Units with no PiN/severity/targeted are IPC-only (outside any HNRP's analysis) —
  // shown only in IPC mode, where surfacing needs the plan does NOT capture is the point.
  const inHnrp = (r) => r.sev_total > 0 || r.pin != null || r.targeted != null
    || r.sec != null || r.pbs_tot != null;

  // ── Severity source: the plan's PiN (default) or IPC/CH phase N+ ────────────
  // PiN mode is the plain plan figure per area — one PiN and one severity class
  // per unit, as the plan publishes them. (The PiN-by-Severity distribution
  // behind that class is no longer split out: pockets of higher need inside a
  // unit are ignored, the unit takes the class holding its PiN.)
  // IPC rows carry a list of analysis periods (current / projections, each with a
  // validity window). Rather than a per-country menu of overlapping rounds, one
  // global choice: "Now" = the most recent estimate covering the issuance month
  // (a 'current' analysis if one covers it, else the newest projection that does);
  // "Forecast window" = the most recent projection overlapping the 6-month
  // forecast horizon. IPC and JIAF use different analysed-population bases and
  // scopes, so shares are not comparable across the two sources.
  const ipcMode = () => srcTypeSel.value === "ipc";
  const pinMode = () => srcTypeSel.value === "pin";
  const lvl = () => +srcLvlSel.value;
  const ym = (s) => { const [y, m] = s.split("-").map(Number); return y * 12 + m - 1; };
  const NOW_YM = data.issued_year * 12 + data.issued_month - 1; // anchor: forecast issuance
  function ipcComboOf(r, mode = ipcPeriodSel.value) {
    const list = r.ipc;
    if (!list || !list.length) return null;
    const covers = (c) => ym(c.s) <= NOW_YM && ym(c.e) >= NOW_YM;
    const newest = (arr) => arr.sort((a, b) => ym(b.s) - ym(a.s))[0];
    if (mode === "fwd") {
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
    if (pinMode()) return pinOf(r);
    const c = ipcComboOf(r);
    if (!c) return null;
    return c.p.slice(lvl() - 1).reduce((a, b) => a + (b ?? 0), 0);
  }
  // Denominator: each source's own analysed population. PiN has none of its own —
  // shares use the plan's JIAF analysed population (same plan, same admin unit).
  const sevTotOf = (r) => (ipcMode() ? (ipcComboOf(r)?.tot ?? null) : r.sev_total);
  const lvlTag = () => (lvl() === 5 ? "5" : lvl() + "+");
  const sevLabel = () => (pinMode() ? `PiN${secTag()}` : `IPC ${lvlTag()}`);

  // Exercise (analysis) month + validity window of an IPC combo — spelled out
  // everywhere a figure from it appears, since periods differ by country.
  const fmtYM = (s) => {
    if (!s) return "?";
    const [y, m] = s.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1)).toLocaleString("en", { month: "short", timeZone: "UTC" }) + " " + y;
  };
  const comboDesc = (c) => `${c.t}, exercise ${fmtYM(c.a)}, valid ${c.label}` +
    (c.d ? ` — downscaled from admin-${c.d} by population share` : "");

  const IPC_OPT_BASE = { now: "Now", fwd: "Forecast window" };
  function updateIpcPeriodUI() {
    ipcPeriodWrap.hidden = !ipcMode();
    srcLvlWrap.hidden = pinMode(); // PiN is a headline total, no severity level
    if (!ipcMode()) return;
    // The dropdown options ALWAYS state the exercise (analysis) month and validity
    // window of the numbers each choice resolves to: the concrete analysis when a
    // country is selected, the cross-country range otherwise (periods differ by
    // country — the exact one per area is in its tooltip).
    const perCountry = (mode) => {
      const per = new Map();
      for (const r of data.rows) {
        if (!r.ipc || per.has(r.iso3)) continue;
        if (countrySel.value && r.country !== countrySel.value) continue;
        const c = ipcComboOf(r, mode);
        if (c) per.set(r.iso3, c);
      }
      return [...per.values()];
    };
    for (const opt of ipcPeriodSel.options) {
      const cs = perCountry(opt.value);
      if (!cs.length) {
        opt.textContent = IPC_OPT_BASE[opt.value];
        continue;
      }
      if (countrySel.value) {
        opt.textContent = `${IPC_OPT_BASE[opt.value]} — ${comboDesc(cs[0])}`;
        continue;
      }
      // "YYYY-MM" strings compare lexicographically = chronologically.
      const lo = (k) => cs.reduce((a, c) => (c[k] && (!a || c[k] < a) ? c[k] : a), null);
      const hi = (k) => cs.reduce((a, c) => (c[k] && (!a || c[k] > a) ? c[k] : a), null);
      opt.textContent = `${IPC_OPT_BASE[opt.value]} — exercises ` +
        `${fmtYM(lo("a"))}–${fmtYM(hi("a"))}, valid ${fmtYM(lo("s"))}–${fmtYM(hi("e"))}`;
    }
    // A projection that is ALSO the latest estimate for now makes the two choices
    // identical — offering both would imply different data, so hide the redundant one.
    const fwdOpt = ipcPeriodSel.querySelector('option[value="fwd"]');
    if (countrySel.value) {
      const [cn] = perCountry("now"), [cf] = perCountry("fwd");
      fwdOpt.hidden = !!(cn && cf && cn.t === cf.t && cn.s === cf.s && cn.e === cf.e);
      if (fwdOpt.hidden && ipcPeriodSel.value === "fwd") ipcPeriodSel.value = "now";
    } else {
      fwdOpt.hidden = false;
    }
  }

  // ── Valid-season selection ───────────────────────────────────────────────────
  // "Worst drought" modes: each unit shows its worst qualifying drought trimester —
  // by default among forecast seasons only (lead ≥ 0), since the worst signal is
  // often a season already in progress; "incl. in-season" also scans leads −2/−1.
  // (Fallback: the default lead-1 trimester.) An explicit trimester shows THAT
  // season's forecast for every unit. Slot keys are the compact trimester codes (MJJ).
  const triSel = document.getElementById("hnrp-tri");
  const inScope = (r) =>
    (!countrySel.value || r.country === countrySel.value) && (ipcMode() || inHnrp(r));
  const fbSlot = (r) => (r.fb_pct == null ? null
    : { key: r.fb_tri, lead: 1, rp: r.fb_rp, pct: r.fb_pct, r: r.fb_r, rainy: !!r.fb_rainy });
  // Worst qualifying drought among the unit's valid trimesters, under the CURRENT
  // skill threshold (unlike the export's precomputed slot, which is r_mod-gated).
  function worstSlot(r, inclInSeason) {
    const rMin = skillSel.value === "high" ? T.r_high : T.r_mod;
    let best = null;
    for (const [key, t] of Object.entries(r.tris ?? {})) {
      if (!inclInSeason && t.lead < 0) continue;
      if (t.pct == null || t.pct >= 50 || !t.rainy || t.rp == null) continue;
      if (t.r == null || t.r < rMin) continue;
      if (!best || t.rp > best.rp) best = { key, lead: t.lead, rp: t.rp, pct: t.pct, r: t.r, rainy: true };
    }
    return best;
  }
  function rawSlotOf(r) {
    if (triSel.value === "auto" || triSel.value === "auto-in") {
      const w = worstSlot(r, triSel.value === "auto-in");
      if (w) return { ...w, worst: true };
      return fbSlot(r);
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
  // Display slot: the qualifying drought slot, else the same season's real
  // category — flood/normal/low-skill/off-season, like the Map tab. Narrowing
  // the view to drought signals is the legend's job (pin "strongly below" /
  // "below normal"), not a separate filter.
  // slotOfAny ignores the HNRP/IPC scope gate; slotOf applies it (map and table
  // stay scoped to the selected mode).
  function slotOfAny(r) {
    const s = rawSlotOf(r);
    if (!s) return null;
    if (isDrought(s)) return s;
    if (s.worst) {
      // auto mode: the worst-drought slot failed the filters — display the default
      // lead-1 trimester instead (the slot shown for every non-drought unit).
      return fbSlot(r);
    }
    return s;
  }
  function slotOf(r) {
    return inScope(r) ? slotOfAny(r) : null;
  }
  // Style for HNRP units with nothing to display (no forecast for the selected
  // season): distinct from both the world background and the classified categories.
  const HNRP_MUTED = { fill: "#e9eeee", edge: "#c4d0d1" };
  function catOf(r) {
    const s = r && slotOf(r);
    return s ? classify({ pct: s.pct, r: s.r, rainy: s.rainy }, false) : null;
  }

  // ── ADM1 choropleth ──────────────────────────────────────────────────────────
  const map = L.map("hnrp-map", {
    crs: L.CRS.EPSG4326, attributionControl: false, maxZoom: 8,
    // Fractional zoom: fitBounds otherwise rounds DOWN to a whole zoom level,
    // leaving dead space on the E/W edges of the default (data-bounds) view.
    zoomSnap: 0.25,
  });
  // Bottom-left: the sticky filter bar overlays the top of the viewport, and a
  // top-left zoom control slides under it as the page scrolls, eating its clicks.
  map.zoomControl.setPosition("bottomleft");
  // Global view: per-admin figures at world zoom are noise. One line per country —
  // name, plan year, and which datasets we hold — until the country is clicked open.
  const countryTipCache = new Map();
  function countryTip(country) {
    if (!countryTipCache.has(country)) {
      const rs = data.rows.filter((x) => x.country === country);
      const has = (k) => rs.some((x) => x[k] != null);
      const ds = [];
      if (has("pct") || has("fb_pct")) ds.push("SEAS5 forecast");
      if (has("pin")) ds.push("PiN");
      if (has("targeted")) ds.push("targeted");
      if (has("pb") || has("pba")) ds.push("PiN by severity");
      if (has("ipc")) ds.push("IPC");
      const py = planYrByCountry.get(country);
      countryTipCache.set(country,
        `<div class="name">${country}</div>` +
        `<div>${py ? `Plan data ${py} · ` : ""}${ds.join(", ")}</div>` +
        `<div class="cat" style="color:#9db1b3">Click to explore</div>`);
    }
    return countryTipCache.get(country);
  }
  const tipHtml = (f) => {
    const p = f.properties, r = byPcode.get(p.pcode);
    if (!countrySel.value) return r ? countryTip(r.country)
      : `<div class="name">${p.name ?? p.pcode}</div>`;
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
      rows += `<div>${sevLabel()}: ${fmtN(sevValOf(r))}${c ? ` (${comboDesc(c)})` : ""}</div>`;
    }
    if (tgtOf(r) != null) rows += `<div>Targeted${secTag()}: ${fmtN(tgtOf(r))}${tgtFlag(r)}</div>`;
    const py = planYrOf(r);
    if (py) rows += `<div>Plan data: ${py}</div>`;
    if (ADM === "low") {
      const cls = sevClassOf(r);
      if (cls) {
        rows += `<div>Severity class: <span style="display:inline-block;width:10px;` +
          `height:10px;background:${sevColors()[cls - 1]};border:1px solid #9db1b3;` +
          `vertical-align:baseline"></span> ${sevClassLabels()[cls - 1]}</div>`;
      }
    }
    // Membership must be per-unit, never assumed from scope: in IPC mode most of a
    // country's states can be in view yet outside its HNRP (Nigeria covers only
    // Borno/Adamawa/Yobe).
    const member = inHnrp(r);
    if (!member) rows += `<div style="color:#9db1b3">Not in an HNRP</div>`;
    const catLine = cat
      ? `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>`
      : `<div class="cat" style="color:#9db1b3">${member ? "In HNRP" : "IPC-covered, not in an HNRP"}` +
        ` — no forecast data</div>`;
    return `<div class="name">${dispName(r)}</div>` + catLine + rows;
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
    onEachFeature: (f, l) => {
      l.bindTooltip(() => tipHtml(f), { sticky: true });
      // Click-to-focus: clicking a unit selects its country; once one is selected,
      // clicking anywhere outside it (another country, open sea) returns to the
      // global view — the map click handler below, unless this stops the event.
      l.on("click", (e) => {
        const c = byPcode.get(f.properties.pcode)?.country;
        if (!countrySel.value && c) {
          L.DomEvent.stopPropagation(e);
          countrySel.value = c;
          countrySel.dispatchEvent(new Event("change"));
        } else if (c && c === countrySel.value) {
          L.DomEvent.stopPropagation(e); // clicks inside the focus country keep it
        }
        // Focused + clicked another (whited-out) country: fall through — it reads
        // as backdrop, so the map handler returns to the global view.
      });
    },
  }).addTo(map);
  map.on("click", () => {
    if (!countrySel.value) return;
    countrySel.value = "";
    countrySel.dispatchEvent(new Event("change"));
  });
  // Country borders above the admin mosaic — without them the admins of
  // neighbouring countries blend into one surface. Same world-layer source as
  // the selected-country outline (edge misalignments vs the COD adm1 polygons
  // are cosmetic).
  const dataIsos = new Set(data.rows.map((r) => r.iso3).filter(Boolean));
  const bordersLayer = L.geoJSON(
    { type: "FeatureCollection",
      features: world.features.filter((f) => dataIsos.has(f.properties.iso3)) },
    { interactive: false,
      style: { color: "#1d2021", weight: 1, opacity: 0.65, fill: false } },
  ).addTo(map);
  map.fitBounds(layer.getBounds(), { animate: false });

  // ── Inset forecast-category rings (lowest view) ──────────────────────────────
  // Same clip-path trick as the CBPF page: clip each path to its own shape and
  // double the stroke width so only the inner half renders — an outline fully
  // inside the unit. A second, narrower "gap" ring in the unit's fill colour is
  // drawn on top, standing the category ring off the shared boundary.
  const RING_W = 2.8; // visible ring width, px (flush: neighbours touch)
  const SVGNS = "http://www.w3.org/2000/svg";
  const ringCat = L.geoJSON(geo, {
    filter: (f) => /Polygon/.test(f.geometry.type),
    interactive: false,
    style: () => ({ weight: 0, fill: false, opacity: 1 }),
  }).addTo(map);
  const clipPaths = {};
  function setClipped(l, pcode, stroke, w, dash) {
    const el = l._path;
    const svg = el && el.ownerSVGElement;
    if (!svg) return;
    if (!stroke) {
      el.setAttribute("stroke", "none");
      el.removeAttribute("clip-path");
      return;
    }
    let cp = clipPaths[pcode];
    if (!cp) {
      let defs = svg.querySelector("defs.hnrp-clips");
      if (!defs) {
        defs = document.createElementNS(SVGNS, "defs");
        defs.setAttribute("class", "hnrp-clips");
        svg.appendChild(defs);
      }
      const clip = document.createElementNS(SVGNS, "clipPath");
      clip.setAttribute("id", "hnrp-clip-" + pcode);
      cp = document.createElementNS(SVGNS, "path");
      clip.appendChild(cp);
      defs.appendChild(clip);
      clipPaths[pcode] = cp;
    }
    cp.setAttribute("d", el.getAttribute("d") || "");
    el.setAttribute("clip-path", `url(#hnrp-clip-${pcode})`);
    el.setAttribute("stroke", stroke);
    el.setAttribute("stroke-width", String(w * 2)); // clipped: visible half == w
    if (dash) el.setAttribute("stroke-dasharray", dash);
    else el.removeAttribute("stroke-dasharray");
  }
  // Clip geometry must track the projected paths after every zoom/move.
  function syncClips() {
    ringCat.eachLayer((l) => {
      const cp = clipPaths[l.feature.properties.pcode];
      if (cp && l._path) cp.setAttribute("d", l._path.getAttribute("d") || "");
    });
  }
  map.on("zoomend moveend", () => { syncClips(); });
  bordersLayer.bringToFront(); // country borders sit above the inset rings

  // Selected-country outline: admin-1 borders alone make the country edge hard to
  // see. Drawn from the world layer (different source than the COD adm1 polygons,
  // so tiny misalignments at the edge are cosmetic only).
  let outlineLayer = null, outlineFor = null;
  function renderOutline() {
    const c = countrySel.value;
    if (c === outlineFor) return; // no layer churn unless the selection changed —
    outlineFor = c;               // add/remove mid-zoom-animation can wedge Leaflet
    if (outlineLayer) { map.removeLayer(outlineLayer); outlineLayer = null; }
    if (!c) return;
    const iso3 = data.rows.find((r) => r.country === c)?.iso3;
    const f = world.features.find((f) => f.properties.iso3 === iso3);
    if (!f) return;
    outlineLayer = L.geoJSON(f, {
      interactive: false,
      style: { color: "#1d2021", weight: 2.6, fill: false },
    }).addTo(map);
  }
  // Per-admin trimester codes, shown as permanent centered labels when a single
  // country is selected (readable at that zoom; the world view relies on hover).
  const triLabels = L.layerGroup().addTo(map);
  function renderTriLabels() {
    triLabels.clearLayers();
    const sel = countrySel.value;
    if (!sel) return;
    if (!triSel.value.startsWith("auto")) return; // one explicit season — labels are noise
    // At adm2 a country can have 1,000+ units (Colombia) — label soup. Cap it.
    const nShown = data.rows.filter((r) => r.country === sel && slotOf(r)).length;
    if (nShown > 150) return;
    layer.eachLayer((l) => {
      const r = byPcode.get(l.feature.properties.pcode);
      if (!r || r.country !== sel) return;
      const s = slotOf(r);
      if (!s) return;
      // Markers, not standalone tooltips: DivOverlay tooltips mis-anchor after
      // interrupted/fractional zoom animations (labels drifting west, wedged
      // zooms); markers track the view exactly.
      const dimmed = isDimmed(catOf(r), ADM === "low" ? sevClassOf(r) : null);
      triLabels.addLayer(L.marker(l.getBounds().getCenter(), {
        interactive: false, keyboard: false, opacity: dimmed ? 0.15 : 1,
        icon: L.divIcon({
          className: "tri-map-label-wrap", iconSize: null,
          html: `<span class="tri-map-label">${s.key}</span>`,
        }),
      }));
    });
  }
  // The unit's severity class (lowest view only — the finest level is where the
  // one-class-per-unit classification is native):
  // PiN mode = the class holding the unit's PiN (a single class for most plans);
  // IPC mode = the IPC area-classification rule (highest phase reaching ≥20%
  // of the analysed population).
  function sevClassOf(r) {
    if (ipcMode()) {
      const c = ipcComboOf(r);
      if (!c || !c.tot) return null;
      let cum = 0;
      for (let i = 4; i >= 0; i--) {
        cum += c.p[i] ?? 0;
        if (cum / c.tot >= 0.2) return i + 1;
      }
      return 1;
    }
    if (!r.pb) return null;
    let best = 0, bi = null;
    r.pb.forEach((v, i) => { if ((v ?? 0) >= best && (v ?? 0) > 0) { best = v; bi = i + 1; } });
    return bi;
  }
  // How a legend entry, per dimension, decides whether a unit matches. Skill
  // follows the Map tab's rule (app.js setHighlight): the "roughly normal"
  // categories carry their skill in the category name, not a suffix.
  const HL_MATCH = {
    cat: (cat, cls, v) => !!cat && (catBase(cat) === v
      || (v === "none" && (cat === "high_none" || cat === "mid_none"))),
    skill: (cat, cls, v) => !!cat && (v === "skill_high"
      ? cat.endsWith("_high") || cat === "high_none"
      : cat.endsWith("_mod") || cat === "mid_none"),
    cls: (cat, cls, v) => cls != null && String(cls) === v,
  };
  function isDimmed(cat, cls) {
    // OR within a dimension, AND across them: "strongly below" × "class 4" is
    // the intersection. A dimension with nothing active constrains nothing.
    return HL_DIMS.some((dim) => {
      const act = activeOf(dim);
      return act.size > 0 && ![...act].some((v) => HL_MATCH[dim](cat, cls, v));
    });
  }
  function renderMap() {
    renderOutline();
    renderTriLabels();
    const sel = countrySel.value;
    layer.eachLayer((l) => {
      const el = l._path;
      if (!el) return;
      const r = byPcode.get(l.feature.properties.pcode);
      // Country filter: everything else blends into the backdrop WITHOUT a hover —
      // "no qualifying drought signal" would be a lie about units that are merely
      // filtered out of view.
      const offCountry = sel && (!r || r.country !== sel);
      if (offCountry && l.getTooltip()) l.unbindTooltip();
      if (!offCountry && !l.getTooltip()) {
        l.bindTooltip(() => tipHtml(l.feature), { sticky: true });
      }
      if (offCountry || !r || (!ipcMode() && !inHnrp(r))) {
        // Out of scope for the current mode: blend into the world backdrop —
        // and clear any ring left from a previous render, or other countries'
        // category outlines linger when one country is selected.
        el.setAttribute("fill", "#f7f9f9");
        el.setAttribute("stroke", "#d9dedf");
        el.setAttribute("stroke-width", 0.5);
        el.removeAttribute("stroke-dasharray");
        // Backdrop units are never dimmed — but this branch used to leave the
        // opacity a legend highlight had set, so units that dropped out of scope
        // while an entry was hovered stayed ghosted long after it was released.
        el.setAttribute("fill-opacity", "1");
        el.setAttribute("stroke-opacity", "1");
        ringInfo.set(l.feature.properties.pcode, null);
        return;
      }
      const cat = catOf(r);
      const cls = ADM === "low" ? sevClassOf(r) : null;
      let fill;
      if (ADM === "low") {
        // Lowest view: the BODY is ALWAYS severity (muted when the unit has no
        // class — never the forecast category, which lives on the inset ring).
        // cat==null (no forecast for the selected season) stays fully muted.
        fill = !cat ? HNRP_MUTED.fill : cls ? sevColors()[cls - 1] : HNRP_MUTED.fill;
        el.setAttribute("fill", fill);
        el.setAttribute("stroke", "#000000"); // true admin boundary
        el.setAttribute("stroke-width", 0.4);
        el.setAttribute("stroke-dasharray", "");
      } else {
        fill = cat ? fillOf(cat) : HNRP_MUTED.fill;
        el.setAttribute("fill", fill);
        el.setAttribute("stroke", cat ? STYLE[cat][1] : HNRP_MUTED.edge);
        el.setAttribute("stroke-width", 0.6);
        el.setAttribute("stroke-dasharray", "");
      }
      // Legend hover: dim everything that doesn't match the hovered forecast
      // category or severity class (same interaction as the main Map tab).
      const dim = isDimmed(cat, cls);
      el.setAttribute("fill-opacity", dim ? "0.12" : "1");
      el.setAttribute("stroke-opacity", dim ? "0.2" : "1");
      // low_skill's STYLE colour is white — as a ring that reads as a hole;
      // no category ring at all is the honest encoding for "no usable skill".
      ringInfo.set(l.feature.properties.pcode,
        ADM === "low" && cat && cat !== "low_skill" && !offCountry
          ? { cat, fill, dim, dash: cat.endsWith("_mod") ? "4 7" : null } : null);
    });
    renderRings();
  }
  // Inset category rings + their standoff gap, driven by the main pass above.
  const ringInfo = new Map();
  function renderRings() {
    ringCat.eachLayer((l) => {
      const info = ringInfo.get(l.feature.properties.pcode);
      setClipped(l, l.feature.properties.pcode,
        info ? STYLE[info.cat][0] : null, RING_W, info && info.dash);
      if (info && l._path) l._path.setAttribute("stroke-opacity", info.dim ? "0.2" : "1");
    });
  }
  // ── Legend (main-page style: titled strips, hover = highlight on the map) ────
  // Three independent dimensions — forecast category, skill, severity class —
  // each its own strip. The active highlight per dimension is its PINNED
  // (clicked) entries plus, transiently, the hovered one; entries OR within a
  // dimension and AND across them. Non-matching legend entries pale to mirror
  // the map dim, and the clear button stands up the moment anything is pinned —
  // otherwise a click made three controls ago silently shrinks the highlight.
  const HL_DIMS = ["cat", "skill", "cls"];
  const pinned = { cat: new Set(), skill: new Set(), cls: new Set() };
  let hover = null; // { dim, val } while the pointer is on a legend entry
  const activeOf = (dim) => (hover && hover.dim === dim
    ? new Set(pinned[dim]).add(hover.val) : pinned[dim]);
  const anyPinned = () => HL_DIMS.some((d) => pinned[d].size > 0);
  let clearChip = null;
  function refreshLegendDim() {
    document.querySelectorAll("#hnrp-legend .ls-seg[data-hl-dim]").forEach((seg) => {
      const { hlDim: dim, hlVal: v } = seg.dataset;
      const act = activeOf(dim);
      seg.style.opacity = act.size && !act.has(v) ? "0.3" : "1";
      seg.classList.toggle("sel", pinned[dim].has(v));
    });
    // Fixed label, and hidden by visibility rather than [hidden]: the button
    // keeps its slot on the legend's last line whether or not anything is
    // pinned, so nothing reflows as entries are added and dropped.
    if (clearChip) clearChip.classList.toggle("on", anyPinned());
  }
  function clearPins() {
    if (!anyPinned()) return;
    for (const d of HL_DIMS) pinned[d].clear();
    refreshLegendDim();
    renderMap();
  }
  function buildHnrpLegend() {
    const root = document.getElementById("hnrp-legend");
    root.innerHTML = "";
    const wire = (el, dim, val) => {
      val = String(val);
      el.dataset.hlDim = dim; el.dataset.hlVal = val;
      const paint = () => { refreshLegendDim(); renderMap(); };
      el.addEventListener("mouseenter", () => { hover = { dim, val }; paint(); });
      el.addEventListener("mouseleave", () => { hover = null; paint(); });
      el.addEventListener("click", () => {
        const set = pinned[dim];
        set.has(val) ? set.delete(val) : set.add(val);
        paint();
      });
    };
    function strip(title, segs, segWidth, rowClass = "") {
      const block = document.createElement("div");
      block.className = "legend-block";
      const t = document.createElement("span");
      t.className = "lb-title"; t.textContent = title;
      const row = document.createElement("div");
      row.className = "legend-strip interactive" + (rowClass ? " " + rowClass : "");
      for (const sg of segs) {
        const seg = document.createElement("span");
        seg.className = "ls-seg"; seg.style.width = segWidth + "px";
        const cell = document.createElement("span");
        cell.className = "ls-cell"; cell.style.background = sg.fill;
        if (sg.hatch) cell.style.backgroundImage = hatchBg(sg.hatch);
        if (sg.border) cell.style.boxShadow = "inset 0 0 0 1px #c4d0d1";
        // Skill swatches quote the map's own encoding: at the lowest level the
        // category is a boundary line (solid = high skill, dashed = moderate),
        // at the fixed admin levels it is the fill (plain vs hatched).
        if (sg.outline) cell.style.border = `2px ${sg.outline} #1d2021`;
        if (sg.ramp != null) cell.dataset.ramp = sg.ramp;
        const lbl = document.createElement("span");
        lbl.className = "ls-lbl"; lbl.textContent = sg.label;
        seg.append(cell, lbl);
        wire(seg, sg.dim, sg.val);
        row.appendChild(seg);
      }
      block.append(t, row);
      root.appendChild(block);
      return block;
    }
    strip(ADM === "low" ? "Forecast category (boundary line)"
                        : "Forecast category (fill)", [
      { fill: "#7f5619", label: "strongly below", dim: "cat", val: "drought_vsev" },
      { fill: "#dda555", label: "below normal", dim: "cat", val: "drought_sev" },
      { fill: "#e2e8e8", label: "normal", border: true, dim: "cat", val: "none" },
      { fill: "#74a1e8", label: "above normal", dim: "cat", val: "flood_sev" },
      { fill: "#134ead", label: "strongly above", dim: "cat", val: "flood_vsev" },
    ], 86);
    strip("\u00a0", [
      { fill: "#b1c1c2", label: "off season", dim: "cat", val: "off_season" },
    ], 86);
    // Skill is how each category above is DRAWN, so these swatches carry no
    // colour of their own — white boxes wearing the map's own distinction.
    strip(ADM === "low" ? "Skill (line style)" : "Skill (fill style)", [
      { fill: "#ffffff", outline: "solid", label: "high skill",
        dim: "skill", val: "skill_high" },
      ADM === "low"
        ? { fill: "#ffffff", outline: "dashed", label: "moderate skill",
            dim: "skill", val: "skill_mod" }
        : { fill: "#ffffff", outline: "solid", hatch: "grey", label: "moderate skill",
            dim: "skill", val: "skill_mod" },
    ], 84, "boxes");
    // Both sources classify every unit (PiN by the class holding its PiN, IPC by
    // the area rule), so the class strip belongs to the lowest view outright.
    if (ADM === "low") {
      strip("Severity class (fill)", [1, 2, 3, 4, 5].map((c) => ({
        fill: sevColors()[c - 1], ramp: c - 1, label: String(c),
        border: c <= 2, dim: "cls", val: c,
      })), 44);
    }
    // Pins outlive every other control change, so there is always one click
    // back to the unfiltered map.
    clearChip = document.createElement("button");
    clearChip.type = "button";
    clearChip.id = "hnrp-legend-clear";
    clearChip.textContent = "Clear all filters";
    clearChip.addEventListener("click", clearPins);
    root.appendChild(clearChip);
    // Backstop: a pointer that leaves the legend without the segment's own
    // mouseleave firing (fast exit, re-render under the cursor, tab switch)
    // would otherwise leave the hover highlight stuck on.
    root.addEventListener("mouseleave", () => {
      if (!hover) return;
      hover = null;
      refreshLegendDim();
      renderMap();
    });
  }
  // (built at the end of setup — it reads sevColors(), declared further down)

  // ── Scatter (REMOVED 2026-08) ────────────────────────────────────────────────
  // The severity-vs-targeted scatter was dropped as more confusing than useful.
  // Full implementation + revival notes: docs/dev-notes/hnrp-scatter.md
  // (last live at commit d7140e9, docs/hnrp.js renderScatter/scatterRows/popOf).

  const NS = SVGNS; // SVG namespace for the bar chart's elements

  // ── Severity-breakdown bars (per admin, when a country is selected) ──────────
  // Population by JIAF class 1–5 (stacked), a tick for the targeted population, and
  // the unit's forecast category as a swatch beside its name. Severity uses the
  // IPC/CH-convention colours — a domain-standard scale this audience reads at a
  // glance, and far more separable than a single-hue ramp.
  // Two class ramps: IPC keeps the IPC convention; intersectoral (PbS/JIAF)
  // uses the blue severity ramp from humanitarianaction.info's severity
  // choropleths (Datawrapper stops on the Global HNO plan pages).
  const IPC_COLORS = ["#cdfacd", "#fae61e", "#e67800", "#c80000", "#640000"];
  const JIAF_COLORS = ["#e9f2fb", "#d4e5f7", "#82b5e9", "#418fde", "#1f69b3"];
  const sevColors = () => (ipcMode() ? IPC_COLORS : JIAF_COLORS);
  const JIAF_LABELS = ["1 — minimal", "2 — stress", "3 — severe", "4 — extreme", "5 — catastrophic"];
  const IPC_LABELS = ["1 — minimal", "2 — stressed", "3 — crisis", "4 — emergency", "5 — catastrophe"];
  const sevClassLabels = () => (ipcMode() ? IPC_LABELS : JIAF_LABELS);
  // Class breakdown for the bars — IPC mode only: the selected period's phases.
  // A PiN bar is one figure for the unit, coloured by the unit's own class.
  const PIN_COLOR = "#9db1b3"; // classless units (plans publishing no PbS)
  const segsOf = (r) => (pinMode() ? null : (ipcComboOf(r)?.p ?? null));
  const barsWrap = document.getElementById("hnrp-bars-wrap");
  const barsHint = document.getElementById("hnrp-bars-hint");
  const barsSvg = document.getElementById("hnrp-bars");
  const barsTitle = document.getElementById("hnrp-bars-title");
  const barsLegend = document.getElementById("hnrp-bars-legend");
  // Classes 1–2 dwarf 3–5 in populous areas and drown the signal — plot 3+ by
  // default, with a checkbox to bring the full distribution back.
  const barsFullEl = document.getElementById("hnrp-bars-full");
  const barC0 = () => (barsFullEl.checked ? 0 : 2);
  const fmtSI = (v) => (v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
    : v >= 1e3 ? Math.round(v / 1e3) + "k" : String(Math.round(v)));

  function renderBarsLegend() {
    // In PiN mode the bar IS the severity colour, so the ramp legend applies to
    // both modes — only the class range differs (IPC stacks from barC0()).
    barsLegend.innerHTML =
      sevColors().map((c, i) => ((!pinMode() && i < barC0()) ? ""
        : `<span><i style="background:${c}"></i> ${sevClassLabels()[i]}</span>`)).join("") +
      (pinMode() ? `<span><i style="background:${PIN_COLOR}"></i> no class published</span>` : "") +
      `<span><i class="tick"></i> targeted</span>`;
  }

  // Bar sorting: click a column header. Values sort high→low on first click,
  // names A→Z; clicking the active header flips it. Forecast order puts the
  // worst droughts on top.
  const CAT_ORDER = ["drought_vsev_high", "drought_vsev_mod", "drought_sev_high",
    "drought_sev_mod", "flood_vsev_high", "flood_vsev_mod", "flood_sev_high",
    "flood_sev_mod", "high_none", "mid_none", "low_skill", "off_season"];
  const catRank = (r) => {
    const i = CAT_ORDER.indexOf(catOf(r) ?? "");
    return i === -1 ? CAT_ORDER.length : i;
  };
  // key -> [comparator in its DEFAULT direction, sorts a text column?]
  const BAR_SORTS = {
    forecast: [(a, b) => catRank(a) - catRank(b) || (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0), false],
    severity: [(a, b) => (sevClassOf(b) ?? 0) - (sevClassOf(a) ?? 0)
      || (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0), false],
    name: [(a, b) => String(a.name ?? a.pcode).localeCompare(String(b.name ?? b.pcode)), true],
    value: [(a, b) => (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0), false],
    targeted: [(a, b) => (tgtOf(b) ?? 0) - (tgtOf(a) ?? 0), false],
  };
  let barSort = "value", barSortFlip = false;

  function renderBars() {
    renderBarsLegend();
    barsFullEl.closest("label").style.display = pinMode() ? "none" : "";
    const country = countrySel.value;
    const [cmp, isText] = BAR_SORTS[barSort] ?? BAR_SORTS.value;
    const rows = country
      ? data.rows.filter((r) => r.country === country
            && (pinMode() ? sevValOf(r) != null : sevTotOf(r) > 0 && segsOf(r)))
          .sort(barSortFlip ? (a, b) => cmp(b, a) : cmp)
      : [];
    barsWrap.hidden = rows.length === 0;
    barsHint.hidden = rows.length > 0;
    if (!rows.length) return;
    if (pinMode()) {
      barsTitle.textContent = `${country} — PiN${secTag()} and severity class, ` +
        `per ${ADM_LABEL[ADM]} (plan data ${planYrOf(rows[0]) ?? "–"}` +
        `${rows[0].pba ? ", class from the area classification" : ""})`;
    } else {
      const c = ipcComboOf(rows[0]);
      barsTitle.textContent = `${country} — population by IPC/CH phase, per ${ADM_LABEL[ADM]}` +
        (c ? ` (${comboDesc(c)})` : "");
    }

    const W = barsSvg.parentElement.clientWidth || 900;
    // Left gutter holds three labelled columns: forecast swatch, severity
    // square, admin name — each header wide enough to read and click.
    const COL = { cat: 2, sev: 108, name: 168 };
    const ROW = 26, M = { l: 330, r: 24, t: 26, b: 34 };
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
    // Scale to what is actually plotted: the sum of the SHOWN classes (3+ by
    // default; the PiN total in PiN mode) and the targeted tick.
    const shownSum = (r) => (pinMode() ? (sevValOf(r) ?? 0)
      : (segsOf(r) ?? []).slice(barC0()).reduce((a, b) => a + (b ?? 0), 0));
    const xmax = Math.max(...rows.map((r) => Math.max(shownSum(r), tgtOf(r) ?? 0))) * 1.04;
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
      .textContent = pinMode() ? `People in Need${secTag()}`
        : barC0() ? "People (analysed population in classes 3–5)"
        : "People (analysed population by severity class)";

    // Clickable column headers, in place of a sort dropdown: each names the
    // column beneath it and sorts by it. "targeted" rides along in the value
    // header — it has no column of its own, only the tick on each bar.
    function header(x, key, label, anchor = "start") {
      const t = g("text", { x, y: M.t - 10, "font-size": 11, "text-anchor": anchor,
                            fill: barSort === key ? "#1d2021" : "#555",
                            "font-weight": barSort === key ? 600 : 400 });
      // ↓ always means "descending" — which is the DEFAULT for values (high
      // first) and the flipped state for names (Z→A), as in the table below.
      const desc = BAR_SORTS[key][1] ? barSortFlip : !barSortFlip;
      t.textContent = label + (barSort === key ? (desc ? " ↓" : " ↑") : "");
      t.style.cursor = "pointer";
      t.addEventListener("click", () => {
        if (barSort === key) barSortFlip = !barSortFlip;
        else { barSort = key; barSortFlip = false; }
        renderBars();
      });
      return t;
    }
    header(COL.cat, "forecast", "Forecast category");
    header(COL.sev, "severity", "Severity");
    header(COL.name, "name", "Admin name");
    header(M.l, "value", pinMode() ? "PiN" : `${sevLabel()} pop`);
    // Fixed offset, not a measured one: getComputedTextLength() returns 0 while
    // the panel is hidden, which would stack the two headers on top of each other.
    header(M.l + 96, "targeted", "targeted");
    g("line", { x1: 0, x2: W - M.r, y1: M.t - 4, y2: M.t - 4, stroke: "#e2e8e8" });

    rows.forEach((r, i) => {
      const y = M.t + i * ROW;
      const cat = catOf(r);
      const cls = ADM === "low" ? sevClassOf(r) : null;
      // Forecast-category swatch, severity square, admin name.
      g("rect", { x: COL.cat, y: y + ROW / 2 - 7, width: 13, height: 13,
                  fill: cat ? fillOf(cat) : HNRP_MUTED.fill,
                  stroke: cat ? STYLE[cat][1] : HNRP_MUTED.edge, "stroke-width": 1 });
      if (cls) {
        const sq = g("rect", { x: COL.sev, y: y + ROW / 2 - 7, width: 13, height: 13,
                               fill: sevColors()[cls - 1], stroke: "#9db1b3",
                               "stroke-width": 0.6 });
        const ti = document.createElementNS(NS, "title");
        ti.textContent = `severity class ${cls} — ${sevClassLabels()[cls - 1]}`;
        sq.appendChild(ti);
      }
      const nm0 = r.name ?? r.pcode;
      const maxLen = 24;
      const name = nm0.length > maxLen ? nm0.slice(0, maxLen - 1) + "…" : nm0;
      g("text", { x: COL.name, y: y + ROW / 2 + 4, "font-size": 11, fill: "#333" }).textContent = name;

      // PiN mode: one bar per unit, carrying the unit's own severity colour (the
      // same encoding as the map's fill). IPC mode: the phase distribution,
      // stacked, with a white spacer between segments.
      if (pinMode()) {
        const v = sevValOf(r) ?? 0;
        if (v > 0) {
          const seg = g("rect", { x: X(0), y: y + 4, width: Math.max(X(v) - X(0), 0.5),
                                  height: ROW - 9,
                                  fill: cls ? sevColors()[cls - 1] : PIN_COLOR,
                                  stroke: "#9db1b3", "stroke-width": 0.6 });
          const title = document.createElementNS(NS, "title");
          title.textContent = `${r.name ?? r.pcode} — PiN${secTag()}: ${fmtN(v)}` +
            (cls ? ` · severity class ${cls} (${sevClassLabels()[cls - 1]})` : "");
          seg.appendChild(title);
        }
      } else {
        const segs = segsOf(r) ?? [];
        let acc = 0;
        for (let c = barC0(); c < 5; c++) {
          const v = segs[c] ?? 0;
          if (v <= 0) continue;
          const seg = g("rect", { x: X(acc), y: y + 4, width: Math.max(X(acc + v) - X(acc) - 1, 0.5),
                                  height: ROW - 9, fill: sevColors()[c] });
          const title = document.createElementNS(NS, "title");
          title.textContent = `${r.name ?? r.pcode} — IPC phase ${c + 1}: ${fmtN(v)}`;
          seg.appendChild(title);
          acc += v;
        }
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
  // Rebuilt per render: in PiN mode the severity column IS the PiN column, so
  // carrying both would print the same number twice.
  const colsNow = () => [
    { key: "country", label: "Country", num: false },
    { key: "name", label: ADM === "low" ? "Admin unit" : `Admin ${ADM}`, num: false },
    { key: "_plan_yr", label: "Plan", num: true },
    ...(ADM === "low" ? [{ key: "sclass", label: "Class", num: true }] : []),
    ...(pinMode() ? [] : [{ key: "sev4", label: "Severity 4+ pop", num: true }]),
    { key: "pin", label: "PiN", num: true },
    { key: "targeted", label: "Targeted", num: true },
    { key: "tri_label", label: "Season", num: false },
    { key: "rp", label: "Drought RP (yr)", num: true },
    { key: "pct", label: "Percentile", num: true },
    { key: "r", label: "Skill (r)", num: true },
  ];
  let sortKey = "pin", sortDesc = true;

  function renderTable() {
    const COLS = colsNow();
    // The active column can vanish with a source switch — fall back rather than
    // sort by a key no column offers.
    if (!COLS.some((c) => c.key === sortKey)) { sortKey = "pin"; sortDesc = true; }
    thead.innerHTML = "";
    const trh = document.createElement("tr");
    for (const c of COLS) {
      const th = document.createElement("th");
      // PiN/Targeted columns follow the caseload selector.
      let label = c.label + ((c.key === "pin" || c.key === "targeted") ? secTag() : "");
      if (c.key === "sev4") label = `${sevLabel()} pop`;
      if (c.key === "pin") {
        th.title = "People in Need — the plan's total intersectoral PiN. " +
          "A headline planning figure, not a severity band: " +
          "it is not broken down by JIAF class or IPC phase.";
      }
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
      : sortKey === "sclass" ? sevClassOf(row)
      : sortKey === "sev4" ? sevValOf(row)
      : sortKey === "pin" ? pinOf(row)
      : sortKey === "targeted" ? tgtOf(row)
      : sortKey in SLOT_KEYS ? slotOf(row)?.[SLOT_KEYS[sortKey]]
      : row[sortKey]);
    const rs = data.rows.filter(passes).sort((a, b) => {
      const x = kv(a), y = kv(b);
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sortDesc ? -cmp : cmp;
    });
    tbody.innerHTML = "";
    emptyEl.hidden = rs.length > 0;
    // DOM guard for adm2 (potentially thousands of qualifying rows): render the
    // top 500 under the current sort, and say so.
    const capped = rs.length > 500;
    for (const r of rs.slice(0, 500)) {
      const tr = document.createElement("tr");
      const s = slotOf(r);
      // Pale wash of the forecast-category colour, tying rows to the map/scatter.
      const cat = catOf(r);
      if (cat) tr.style.background = STYLE[cat][0] + "21";
      const skillCls = s && s.r >= T.r_high ? "skill-high" : "skill-mod";
      tr.innerHTML =
        `<td>${r.country ?? r.iso3}</td>` +
        `<td>${dispName(r)}</td>` +
        `<td class="num">${planYrOf(r) ?? "–"}</td>` +
        (ADM === "low" ? (() => {
          const cls = sevClassOf(r);
          return `<td class="num">${cls
            ? `<span class="cls-chip" style="background:${sevColors()[cls - 1]}"></span>${cls}`
            : "–"}</td>`;
        })() : "") +
        (pinMode() ? "" : `<td class="num">${fmtN(sevValOf(r))}</td>`) +
        `<td class="num">${fmtN(pinOf(r))}</td>` +
        `<td class="num">${fmtN(tgtOf(r))}${tgtYrOf(r)
          ? `<span class="stale-flag" title="targeted from the ${tgtYrOf(r)} plan cycle — none published in the current one">*</span>` : ""}</td>` +
        `<td>${s ? s.key : "–"}${s && s.lead < 0 ? ' <span class="in-season-tag">· in season</span>' : ""}</td>` +
        `<td class="num">${fmt(s?.rp, 1)}</td>` +
        `<td class="num">${fmt(s?.pct, 1)}</td>` +
        `<td class="num ${skillCls}">${fmt(s?.r, 2)}</td>`;
      tbody.appendChild(tr);
    }
    if (capped) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td colspan="${COLS.length}" style="color:var(--muted);font-style:italic">` +
        `Showing the top 500 of ${rs.length.toLocaleString("en-US")} matching areas — ` +
        `narrow with the filters or sort to bring others into view.</td>`;
      tbody.appendChild(tr);
    }
  }

  function fitCountry(animate = true) {
    const c = countrySel.value;
    let bounds = null;
    layer.eachLayer((l) => {
      const r = byPcode.get(l.feature.properties.pcode);
      if (c && (!r || r.country !== c)) return;
      bounds = bounds ? bounds.extend(l.getBounds()) : L.latLngBounds(l.getBounds());
    });
    // map.stop() first: an animated fit interrupted mid-flight wedges Leaflet's
    // zoom animation and every later fit silently no-ops (observed: Afghanistan
    // "not zooming"). Stopping any in-flight animation before starting the next
    // keeps the smooth zoom safe — and because the wedge keeps finding new ways
    // to happen, self-heal: if the view hasn't arrived once the animation should
    // have finished, force the fit without animation.
    if (!bounds) return;
    map.stop();
    map.fitBounds(bounds, { padding: [10, 10], animate });
    if (animate) {
      // The world view CONTAINS every country's centre, so a containment check
      // can't detect a wedged animation (observed: Benin never zooming while
      // the check passed). Compare against the target zoom instead.
      const want = bounds;
      const tz = map.getBoundsZoom(want, false, L.point(10, 10));
      setTimeout(() => {
        if (countrySel.value !== c) return; // selection moved on — don't fight it
        if (Math.abs(map.getZoom() - tz) > 0.5
            || !map.getBounds().contains(want.getCenter())) {
          map.stop();
          map.fitBounds(want, { padding: [10, 10], animate: false });
        }
      }, 700);
    }
  }
  // Valid-season selector options: auto + each valid trimester at this issuance.
  for (const t of data.trimesters ?? []) {
    const o = document.createElement("option");
    o.value = t; o.textContent = t;
    triSel.appendChild(o);
  }

  function renderAll() {
    syncURL(); // keep the URL an exact mirror of the controls at all times
    updateIpcPeriodUI();
    // Legend severity ramp follows the active source (IPC vs intersectoral blue).
    document.querySelectorAll("#hnrp-legend [data-ramp]").forEach((el) => {
      el.style.background = sevColors()[+el.dataset.ramp];
    });
    refreshLegendDim();
    renderMap(); renderBars(); renderTable();
  }
  for (const el of [skillSel, rpSel, srcTypeSel, srcLvlSel, triSel, ipcPeriodSel]) {
    el.addEventListener("change", renderAll);
  }
  barsFullEl.addEventListener("change", renderBars);
  // finally: whatever happens during re-render, the zoom step must still run.
  countrySel.addEventListener("change", () => {
    try { renderAll(); } finally { fitCountry(); }
  });

  // Hidden-panel sizing: (re)fit when the tab becomes visible.
  window.tabShown = window.tabShown || {};
  window.tabShown.hnrp = () => {
    map.invalidateSize();
    fitCountry(false); // instant on reveal — the panel just appeared, nothing to glide from
    renderAll(); // paths may mount after the panel becomes visible — restyle then
  };
  // Escape drops every pinned legend entry — the keyboard route out of a
  // highlight, for when the chip itself has scrolled out of view.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("tab-hnrp").hidden) clearPins();
  });
  buildHnrpLegend(); // safe here: every const it reads is initialised by now
  restoreControls(); // after options are populated, before the first render
  renderAll();
  if (countrySel.value) fitCountry(false); // restored country: land on it directly
  requestAnimationFrame(renderMap); // catch paths that mounted after the first pass
})();
