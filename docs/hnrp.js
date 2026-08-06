// Forecast × HNRP tab: drought forecast vs HNRP severity/targeted caseloads, at
// each country's lowest published admin level. Two linked views — a choropleth
// (severity class as the fill, forecast category as the boundary line) and, once
// a country is selected, per-area PiN bars — driven by one interactive legend.
// Reuses app.js globals: STYLE, classify, catBase, CAT_LABEL, T, buildPatterns.
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
  // Parent admin-1 name qualifies adm2 units (district names repeat across regions).
  const dispName = (r) => (r.parent ? `${r.name ?? r.pcode} (${r.parent})` : (r.name ?? r.pcode));
  // Every control survives a reload (and makes links shareable with their
  // settings): state is carried in the URL query string.
  const CTLS = {
    skill: "hnrp-skill", rp: "hnrp-rp", tri: "hnrp-tri",
    sev: "hnrp-sev-type", lvl: "hnrp-sev-lvl", ipcp: "hnrp-ipc-period",
    country: "hnrp-country", yr: "hnrp-plan-yr",
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

  // buildPatterns() returns the complete cat -> fill map (solid hex or pattern url);
  // app.js already registered the pattern defs, duplicate ids resolve to the first.
  const fillFor = buildPatterns();
  const fillOf = (cat) => fillFor[cat];
  const byPcode = new Map(data.rows.map((r) => [r.pcode, r]));

  // Plan cycle varies by country (e.g. Guatemala's latest is the 2025 HNRP) — surface
  // the year everywhere caseload figures appear.
  const sevYrOf = (r) => {
    const ys = [r.sev_year, r.pbs_yr].filter((y) => y != null);
    return ys.length ? Math.max(...ys) : null;
  };
  // ── Plan year ────────────────────────────────────────────────────────────────
  // Each unit carries its caseloads per cycle in r.cyc = {"2026": [pin, targeted]},
  // and the selector says which one to read. Nothing is ever blended: a unit with
  // no figures for the chosen year shows dashes rather than an older cycle's.
  const planYrSel = document.getElementById("hnrp-plan-yr");
  const cycYears = [...new Set(data.rows.flatMap((r) => Object.keys(r.cyc ?? {})))]
    .map(Number).sort((a, b) => b - a);
  for (const y of cycYears) {
    const o = document.createElement("option");
    o.value = String(y);
    o.textContent = String(y);
    planYrSel.appendChild(o);
  }
  const planYr = () => planYrSel.value || String(cycYears[0] ?? "");
  const cycOf = (r) => (r.cyc ?? {})[planYr()] ?? null;
  // Cycles that publish a PiN but no targets anywhere — the 2026 HNRPs, whose
  // subnational figures come from the JIAF needs analysis (PiN by severity, no
  // targeting). Worth saying out loud rather than leaving a column of dashes.
  const yearsWithTgt = new Set();
  for (const r of data.rows) {
    for (const [y, v] of Object.entries(r.cyc ?? {})) if (v[1] != null) yearsWithTgt.add(y);
  }
  issuedEl.textContent = `Forecast issued ${data.issued_label}.`;

  const countries = [...new Set(data.rows.map((r) => r.country).filter(Boolean))].sort();
  for (const c of countries) {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    countrySel.appendChild(o);
  }

  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const fmt = (v, d) => (v == null ? "–" : Number(v).toFixed(d));
  const pctOf = (num, den) => (num == null || !den ? null : (100 * num) / den);
  // PiN/targeted are always the plan's INTERSECTORAL figures, for the selected
  // plan year (per-sector series remain in r.sec should a sector view return).
  const pinOf = (r) => cycOf(r)?.[0] ?? null;
  const tgtOf = (r) => cycOf(r)?.[1] ?? null;
  const secTag = () => "";

  // Units with no PiN/severity/targeted are IPC-only (outside any HNRP's analysis) —
  // shown only in IPC mode, where surfacing needs the plan does NOT capture is the point.
  const inHnrp = (r) => r.sev_total > 0 || r.cyc != null || r.targeted != null
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
  // Scope is the country filter, nothing more. Areas outside the plan's
  // admin-level analysis still carry a forecast, and the forecast is the point
  // of the tab — they show it, on a muted body that says "no severity here".
  const inScope = (r) => !countrySel.value || r.country === countrySel.value;
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
  // Display slot: the qualifying drought slot, else the same season's real
  // category — flood/normal/low-skill/off-season, like the Map tab. Narrowing
  // the view to drought signals is the legend's job (pin "strongly below" /
  // "below normal"), not a separate filter.
  // slotOfAny ignores the HNRP/IPC scope gate; slotOf applies it (map and bars
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
  function countryTip(country, hint = "Click to explore") {
    const key = `${country}|${hint}`;
    if (!countryTipCache.has(key)) {
      const rs = data.rows.filter((x) => x.country === country);
      const has = (k) => rs.some((x) => x[k] != null);
      const ds = [];
      if (has("pct") || has("fb_pct")) ds.push("SEAS5 forecast");
      if (has("pin")) ds.push("PiN");
      if (has("targeted")) ds.push("targeted");
      if (has("pb") || has("pba")) ds.push("PiN by severity");
      if (has("ipc")) ds.push("IPC");
      const py = planYrByCountry.get(country);
      countryTipCache.set(key,
        `<div class="name">${country}</div>` +
        `<div>${py ? `Plan data ${py} · ` : ""}${ds.join(", ")}</div>` +
        `<div class="cat" style="color:#9db1b3">${hint}</div>`);
    }
    return countryTipCache.get(key);
  }
  const tipHtml = (f) => {
    const p = f.properties, r = byPcode.get(p.pcode);
    const sel = countrySel.value;
    if (!sel) return r ? countryTip(r.country)
      : `<div class="name">${p.name ?? p.pcode}</div>`;
    // Outside the selected country: the country line, never a per-unit readout —
    // those areas are drawn as backdrop, and their figures are not on screen.
    // (Answering with something is also what keeps the tooltip BOUND: Leaflet
    // 1.9.4 leaks a focus listener on every bindTooltip that unbindTooltip never
    // removes, so binding once per layer and never touching it again is the only
    // way to keep those listeners from piling up.)
    if (r && r.country !== sel) {
      return countryTip(r.country, "Click to return to the world view");
    }
    // No row at all = a polygon the payload doesn't cover; nothing to say but
    // its name. (Areas that ARE in the payload but outside the plan's
    // admin-level analysis still get the full readout — their forecast is real,
    // and the "Not in an HNRP" line below is what qualifies it.)
    if (!r) return `<div class="name">${p.name ?? p.pcode}</div>`;
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
    if (tgtOf(r) != null) rows += `<div>Targeted${secTag()}: ${fmtN(tgtOf(r))}</div>`;
    if (cycOf(r)) rows += `<div>HNRP ${planYr()}</div>`;
    if (sevYrOf(r) && sevYrOf(r) !== +planYr()) {
      rows += `<div style="color:#9db1b3">severity analysis ${sevYrOf(r)}</div>`;
    }
    if (ADM === "low") {
      const cls = sevClassOf(r);
      if (cls) {
        rows += `<div><span style="display:inline-block;width:10px;` +
          `height:10px;background:${sevColors()[cls - 1]};border:1px solid #9db1b3;` +
          `vertical-align:baseline"></span> ${sevClassDesc(r)}</div>`;
      } else if (pinMode() && pinOf(r) != null) {
        rows += `<div style="color:#9db1b3">No severity published for this area</div>`;
      }
    }
    // Membership must be per-unit, never assumed from scope: in IPC mode most of a
    // country's states can be in view yet outside its HNRP (Nigeria covers only
    // Borno/Adamawa/Yobe).
    const member = inHnrp(r);
    if (!member) rows += `<div style="color:#9db1b3">Not in an HNRP</div>`;
    const catLine = cat
      ? `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>`
      : `<div class="cat" style="color:#9db1b3">No forecast for the selected season</div>`;
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
    // At adm2 a country can have 1,000+ units (Colombia) — label soup. Cap it,
    // counting only what would actually be drawn.
    const nShown = data.rows.filter((r) => r.country === sel && slotOf(r)
      && catOf(r) !== "low_skill").length;
    if (nShown > 150) return;
    layer.eachLayer((l) => {
      const r = byPcode.get(l.feature.properties.pcode);
      if (!r || r.country !== sel) return;
      const s = slotOf(r);
      if (!s) return;
      // No usable skill = no alert, so which season it would have been is noise.
      if (catOf(r) === "low_skill") return;
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
  // one-class-per-unit classification is native), and where it came from:
  //   pin      — the class holding the unit's PiN (its PbS distribution)
  //   area     — the plan's area classification, where its PbS had no usable
  //              classes (the export flags these as pba)
  //   analysis — the severity analysis's own population-by-class for the unit,
  //              read here rather than in the export: the export's area-class
  //              fallback is keyed on units present in the PbS table, so units
  //              missing from it entirely fell through with no class at all
  //              (DR Congo, Mozambique, Nigeria, Mali, Niger, Haiti — 466 units)
  //   ipc      — the IPC area rule (highest phase reaching ≥20% of the analysed
  //              population)
  // The dominant class is the one holding the most people; `split` carries the
  // rest, for the rare unit whose caseload spans more than one.
  const dominant = (arr) => {
    let best = 0, bi = null;
    (arr ?? []).forEach((v, i) => { if ((v ?? 0) >= best && (v ?? 0) > 0) { best = v; bi = i + 1; } });
    return bi;
  };
  const spread = (arr) => (arr ?? []).map((v, i) => [i + 1, v ?? 0]).filter(([, v]) => v > 0);
  function sevClassInfo(r) {
    if (!r) return { cls: null };
    if (ipcMode()) {
      const c = ipcComboOf(r);
      if (!c || !c.tot) return { cls: null };
      let cum = 0;
      for (let i = 4; i >= 0; i--) {
        cum += c.p[i] ?? 0;
        if (cum / c.tot >= 0.2) return { cls: i + 1, src: "ipc", split: spread(c.p) };
      }
      return { cls: 1, src: "ipc", split: spread(c.p) };
    }
    const fromPin = dominant(r.pb);
    if (fromPin) return { cls: fromPin, src: r.pba ? "area" : "pin", split: spread(r.pb) };
    const sev = [r.s1, r.s2, r.s3, r.s4, r.s5];
    const fromAnalysis = dominant(sev);
    if (fromAnalysis) return { cls: fromAnalysis, src: "analysis", split: spread(sev) };
    return { cls: null };
  }
  const sevClassOf = (r) => sevClassInfo(r).cls;
  // Says the class and, where it matters, how it was arrived at.
  const CLS_SRC_NOTE = {
    area: " (from the plan's area classification)",
    analysis: " (from the severity analysis, not the PiN split)",
  };
  function sevClassDesc(r) {
    const { cls, src, split } = sevClassInfo(r);
    if (!cls) return null;
    let s = `${sevClassTitle()} ${cls} — ${sevClassWord(cls)}${CLS_SRC_NOTE[src] ?? ""}`;
    // A caseload spanning classes is rare (one unit in the current payload) but
    // it is exactly the case where a single class would mislead.
    if (split && split.length > 1) {
      s += ` · also ${split.filter(([c]) => c !== cls)
        .map(([c, v]) => `${c}: ${fmtSI(v)}`).join(", ")}`;
    }
    return s;
  }
  // How a legend entry, per dimension, decides whether a unit matches. Skill
  // follows the Map tab's rule (app.js setHighlight): the "roughly normal"
  // categories carry their skill in the category name, not a suffix.
  const HL_MATCH = {
    cat: (cat, cls, v) => !!cat && (catBase(cat) === v
      || (v === "none" && (cat === "high_none" || cat === "mid_none"))),
    skill: (cat, cls, v) => !!cat && (
      v === "skill_high" ? cat.endsWith("_high") || cat === "high_none"
      : v === "skill_mod" ? cat.endsWith("_mod") || cat === "mid_none"
      : cat === "low_skill"),
    cls: (cat, cls, v) => cls != null && String(cls) === v,
  };
  // OR within a dimension, AND across them: "strongly below" × "class 4" is the
  // intersection. A dimension with nothing selected constrains nothing.
  function noMatch(cat, cls, setOf) {
    return HL_DIMS.some((dim) => {
      const act = setOf(dim);
      return act.size > 0 && ![...act].some((v) => HL_MATCH[dim](cat, cls, v));
    });
  }
  // Hover is transient — it only pales. A pin is a filter: the bar chart drops
  // non-matching rows outright (the map keeps them, dimmed, since holes in a
  // choropleth read as missing data rather than as filtered out).
  const isDimmed = (cat, cls) => noMatch(cat, cls, activeOf);
  const isFilteredOut = (cat, cls) => noMatch(cat, cls, (dim) => pinned[dim]);
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
      if (offCountry || !r) {
        // Out of scope: blend into the world backdrop —
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
    renderBars(); // the filter dropped rows; they come back
  }
  function buildHnrpLegend() {
    const root = document.getElementById("hnrp-legend");
    root.innerHTML = "";
    const wire = (el, dim, val) => {
      val = String(val);
      el.dataset.hlDim = dim; el.dataset.hlVal = val;
      // Hover only repaints (bars pale in place); a pin changes which rows the
      // bar chart holds at all, so it rebuilds the chart.
      const paint = () => { refreshLegendDim(); renderMap(); paintBarDim(); };
      el.addEventListener("mouseenter", () => { hover = { dim, val }; paint(); });
      el.addEventListener("mouseleave", () => { hover = null; paint(); });
      el.addEventListener("click", () => {
        const set = pinned[dim];
        set.has(val) ? set.delete(val) : set.add(val);
        refreshLegendDim(); renderMap(); renderBars();
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
        // "no line drawn" — just enough edge to see where the box is
        if (sg.faint) cell.style.border = "1px solid #dfe6e6";
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
    // Below the skill floor (r < 0.30) no category is drawn at all: at the
    // lowest level that means no boundary line, hence a swatch with no outline.
    strip(ADM === "low" ? "Skill (line style)" : "Skill (fill style)", ADM === "low"
      ? [{ fill: "#ffffff", outline: "solid", label: "high skill",
           dim: "skill", val: "skill_high" },
         { fill: "#ffffff", outline: "dashed", label: "moderate skill",
           dim: "skill", val: "skill_mod" },
         { fill: "#ffffff", faint: true, label: "no skill",
           dim: "skill", val: "low_skill" }]
      : [{ fill: "#ffffff", outline: "solid", label: "high skill",
           dim: "skill", val: "skill_high" },
         { fill: "#ffffff", outline: "solid", hatch: "grey", label: "moderate skill",
           dim: "skill", val: "skill_mod" },
         { fill: "#ffffff", outline: "solid", hatch: "cross", label: "no skill",
           dim: "skill", val: "low_skill" }],
      84, "boxes");
    // Both sources classify every unit (PiN by the class holding its PiN, IPC by
    // the area rule), so the class strip belongs to the lowest view outright.
    if (ADM === "low") {
      // Title and ramp both follow the source — renderAll() keeps them current.
      strip(sevLegendTitle(), [1, 2, 3, 4, 5].map((c) => ({
        fill: sevColors()[c - 1], ramp: c - 1, label: String(c),
        border: c <= 2, dim: "cls", val: c,
      })), 44).querySelector(".lb-title").id = "hnrp-sev-strip-title";
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
      paintBarDim();
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
  // Readable ink for a label sitting on one of the ramp colours.
  const inkOn = (hex) => {
    const n = parseInt(hex.slice(1), 16);
    const lum = 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
    return lum > 150 ? "#1d2021" : "#ffffff";
  };
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
  const noTgtEl = document.getElementById("hnrp-no-tgt");
  const BARS_HINT = barsHint.textContent; // restored whenever it applies again
  // Denominator for the share columns: the LARGEST population figure we hold
  // for the unit — its COD-PS/HNO/WorldPop total, the IPC analysed base, or the
  // plan's JIAF analysed base. Largest, not "total first": where an analysis
  // covers more people than the baseline says live there, the baseline is the
  // stale number, and picking it would overstate every share (it lowers 1,073
  // units' shares to take the bigger base). The same value divides PiN and
  // targeted, so the two are comparable; the tooltip names the one used.
  // Deliberately independent of the IPC period selector — the widest IPC base,
  // not the selected period's — so a PiN share doesn't move when the period does.
  function popBase(r) {
    const yr = r.pop_year ? ` ${r.pop_year}` : "";
    const cands = [
      [r.pop ?? 0, `total population, ${{ HNO: "HNO baseline", WorldPop: "WorldPop" }[r.pop_src]
        ?? "COD-PS"}${yr}`],
      [Math.max(0, ...(r.ipc ?? []).map((c) => c.tot ?? 0)), "IPC analysed population"],
      [r.sev_total ?? 0, "JIAF analysed population"],
    ].sort((a, b) => b[0] - a[0]);
    return cands[0][0] > 0 ? { pop: cands[0][0], src: cands[0][1] } : { pop: null, src: null };
  }
  const popOf = (r) => popBase(r).pop;
  const popSrcOf = (r) => popBase(r).src;
  // Lowest class the IPC bars stack, straight from the Level selector above the
  // chart: 3+ stacks 3–5, 5 stacks 5 alone. (Phases 1–2 dwarf the rest in
  // populous areas and drown the signal, which is why the floor is never 1.)
  const barC0 = () => lvl() - 1;
  // The class shown per unit — only the lowest view classifies one unit at a time.
  const clsOf = (r) => (ADM === "low" ? sevClassOf(r) : null);
  // "4 — extreme" already carries the number; don't print it twice.
  const sevClassWord = (cls) => sevClassLabels()[cls - 1].replace(/^\d+\s*—\s*/, "");
  // The severity encoding is the plan's intersectoral class, or IPC's phase —
  // named in full wherever it is labelled.
  const sevClassTitle = () => (pinMode() ? "Intersectoral severity" : "IPC phase");
  // "IPC phase class" reads badly — the phase IS the class.
  const sevLegendTitle = () => (pinMode() ? "Intersectoral severity class (fill)"
    : "IPC phase (fill)");
  const fmtSI = (v) => (v == null ? "–"
    : v >= 1e6 ? (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
    : v >= 1e3 ? Math.round(v / 1e3) + "k" : String(Math.round(v)));
  // A share, at the precision it deserves: 0.4%, 7.2%, 55%. A caseload larger
  // than every population figure we hold for the area is not a share at all —
  // print the fact, not a spurious 1,656%. The tooltip carries the real ratio.
  const fmtPct = (f) => (f == null ? "–"
    : f > 1 ? ">100%"
    : `${(100 * f).toFixed(f >= 0.1 ? 0 : 1)}%`);

  function renderBarsLegend() {
    // PiN mode needs no ramp key: the class number is written in its swatch, and
    // the bar wears the same colour. IPC mode keeps it — the bar there is a
    // stack of phases, and only a key says which segment is which.
    barsLegend.innerHTML =
      (pinMode() ? `<span><i style="background:${PIN_COLOR}"></i> no severity published</span>`
        : sevColors().map((c, i) => (i < barC0() ? ""
            : `<span><i style="background:${c}"></i> ${sevClassLabels()[i]}</span>`)).join("")) +
      `<span><i class="tick"></i> targeted</span>` +
      `<span>% columns are of the area's population — hover any figure for the` +
      ` count, the share and the population base used</span>`;
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
  // Share of the area's population — null when it has no population base, which
  // sorts last rather than as zero.
  const shareOfPop = (r, v) => {
    const p = popOf(r);
    return p && v != null ? v / p : null;
  };
  // key -> [comparator in its DEFAULT direction, sorts a text column?]
  const numCmp = (val) => (a, b) => (val(b) ?? -1) - (val(a) ?? -1);
  const BAR_SORTS = {
    forecast: [(a, b) => catRank(a) - catRank(b) || (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0), false],
    severity: [(a, b) => (sevClassOf(b) ?? 0) - (sevClassOf(a) ?? 0)
      || (sevValOf(b) ?? 0) - (sevValOf(a) ?? 0), false],
    name: [(a, b) => String(a.name ?? a.pcode).localeCompare(String(b.name ?? b.pcode)), true],
    value: [numCmp((r) => sevValOf(r)), false],
    targeted: [numCmp((r) => tgtOf(r)), false],
    valuePct: [numCmp((r) => shareOfPop(r, sevValOf(r))), false],
    targetedPct: [numCmp((r) => shareOfPop(r, tgtOf(r))), false],
  };
  let barSort = "value", barSortFlip = false;

  function renderBars() {
    renderBarsLegend();
    const country = countrySel.value;
    const [cmp, isText] = BAR_SORTS[barSort] ?? BAR_SORTS.value;
    const rows = country
      ? data.rows.filter((r) => r.country === country
            && (pinMode() ? sevValOf(r) != null : sevTotOf(r) > 0 && segsOf(r))
            && !isFilteredOut(catOf(r), clsOf(r)))
          .sort(barSortFlip ? (a, b) => cmp(b, a) : cmp)
      : [];
    barsWrap.hidden = rows.length === 0;
    barsHint.hidden = rows.length > 0;
    // "Pick a country" is the wrong prompt when a country IS picked and the
    // legend filters emptied the chart.
    barsHint.textContent = country && anyPinned()
      ? "No areas in this country match the pinned legend filters."
      : BARS_HINT;
    if (!rows.length) { resetBarRows(); return; }
    // Name the level these rows actually sit at, not "lowest available" — the
    // whole point of the lowest view is that it differs by country, and a
    // caseload reads differently at admin 2 than at admin 3.
    const lvls = [...new Set(rows.map((r) => r.lvl).filter((v) => v != null))].sort();
    const lvlTxt = lvls.length ? `admin ${lvls.join("–")}` : ADM_LABEL[ADM];
    // One year in the title: which HNRP these figures are from. What each column
    // holds is written on the column.
    if (pinMode()) {
      barsTitle.textContent = `${country} — HNRP ${planYr()}, per ${lvlTxt}`;
    } else {
      const c = ipcComboOf(rows[0]);
      barsTitle.textContent = `${country} — population by IPC/CH phase, per ${lvlTxt}` +
        (c ? ` (${comboDesc(c)})` : "");
    }
    // A cycle can publish needs without targets — say so once, above the chart,
    // rather than leaving a column of dashes to be read as missing data.
    noTgtEl.hidden = yearsWithTgt.has(planYr());
    noTgtEl.textContent = `No targeted figures published for the HNRP ${planYr()} cycle yet` +
      ` — its subnational figures come from the needs analysis, which carries PiN only.` +
      (cycYears.some((y) => yearsWithTgt.has(String(y)))
        ? ` Pick an earlier plan year to see targeting.` : "");

    const W = barsSvg.parentElement.clientWidth || 900;
    // Left gutter holds three labelled columns: forecast swatch, severity
    // square, admin name. Column starts are set by the HEADER widths, not the
    // 13px swatches — "Intersectoral severity" is the widest thing here.
    const COL = { cat: 2, sev: 108, name: 226 };
    const ROW = 26, M = { l: 392, r: 24, t: 26, b: 34 };
    // Caseload columns, GHO-dashboard style (as on the Country alerts tab): a
    // fixed-width track = the area's whole population, filled to the share this
    // caseload takes, with the headcount printed beside it. Fixed tracks, not one
    // scale across rows: the question here is "how much of this area", and the
    // absolute figure is right there in the label.
    // Four numeric columns on the right — headcount and share, for the caseload
    // and for targeted — each sortable. The bar takes whatever is left.
    const NUMW = 72, NGAP = 4, NUMS = 4;
    const NUMBLOCK = NUMS * NUMW + (NUMS - 1) * NGAP;
    const numRight = (i) => W - M.r - NUMBLOCK + i * (NUMW + NGAP) + NUMW;
    const barRight = Math.max(M.l + 80, W - M.r - NUMBLOCK - 18);
    // Two layers: chrome (grid, headers, axis) is redrawn every render; rows
    // persist between renders, keyed by pcode, so a legend pin can slide and
    // fade them rather than blink the chart.
    if (!rowsG) {
      chromeG = document.createElementNS(NS, "g");
      rowsG = document.createElementNS(NS, "g");
      barsSvg.append(chromeG, rowsG);
    }
    // Nothing to animate BETWEEN countries or sources — those swap the whole
    // population of the chart, and cross-fading two unrelated sets reads as noise.
    const barsKeyNow = `${country}|${srcTypeSel.value}|${ADM}`;
    if (barsKeyNow !== barsKey) { barsKey = barsKeyNow; resetBarRows(); }
    chromeG.innerHTML = "";
    // Hold the taller box while rows are on their way out, so they fade in view
    // instead of being clipped; settle to the exact height once they are gone.
    const H = M.t + M.b + rows.length * ROW;
    const Hshow = Math.max(H, prevBarsH);
    prevBarsH = H;
    barsSvg.setAttribute("viewBox", `0 0 ${W} ${Hshow}`);
    barsSvg.style.height = Hshow + "px";
    clearTimeout(barsSettle);
    if (Hshow !== H) barsSettle = setTimeout(renderBars, EXIT_MS + 20);
    const g = (tag, attrs, parent = chromeG) => {
      const el = document.createElementNS(NS, tag);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      parent.appendChild(el);
      return el;
    };
    // One absolute scale across the rows, so bar length compares headcounts
    // between areas; the share of each area's own population is the numbers
    // column's job.
    const shownSum = (r) => (pinMode() ? (sevValOf(r) ?? 0)
      : (segsOf(r) ?? []).slice(barC0()).reduce((a, b) => a + (b ?? 0), 0));
    const xmax = Math.max(...rows.map((r) => Math.max(shownSum(r), tgtOf(r) ?? 0)), 1) * 1.04;
    const X = (v) => M.l + (v / xmax) * (barRight - M.l);

    // x grid: 4 round ticks.
    const step = Math.pow(10, Math.floor(Math.log10(xmax / 4)));
    const tick = Math.ceil(xmax / 4 / step) * step;
    for (let v = 0; v <= xmax; v += tick) {
      g("line", { x1: X(v), x2: X(v), y1: M.t, y2: H - M.b, stroke: "#eef1f1" });
      g("text", { x: X(v), y: H - M.b + 16, "text-anchor": "middle", "font-size": 10, fill: "#888" })
        .textContent = fmtSI(v);
    }
    g("text", { x: (M.l + barRight) / 2, y: H - 4, "text-anchor": "middle",
                "font-size": 11, fill: "#555" })
      .textContent = pinMode() ? `People in Need${secTag()}`
        : `People (analysed population in IPC ${lvlTag()})`;

    // Clickable column headers, in place of a sort dropdown: each names the
    // column beneath it and sorts by it (by the headcount, not the share — the
    // bars are shares, but "which area has the largest caseload" is the question
    // a click on a caseload header is asking).
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
    header(COL.sev, "severity", sevClassTitle());
    header(COL.name, "name", "Admin name");
    // The bar itself is not a sort target — the four numeric columns are, one
    // per quantity it draws (headcount and share, caseload and targeted).
    g("text", { x: M.l, y: M.t - 10, "font-size": 11, fill: "#555" }).textContent =
      pinMode() ? "PiN (bar) · targeted (tick)"
        : `IPC phases (bar) · targeted (tick)`;
    const NUM_COLS = [
      ["value", pinMode() ? "PiN" : sevLabel()],
      ["targeted", "Targeted"],
      ["valuePct", `${pinMode() ? "PiN" : sevLabel()} %`],
      ["targetedPct", "Targeted %"],
    ];
    NUM_COLS.forEach(([key, label], i) => header(numRight(i), key, label, "end"));
    g("line", { x1: 0, x2: W - M.r, y1: M.t - 4, y2: M.t - 4, stroke: "#e2e8e8" });

    barRows.length = 0;
    const alive = new Set();
    rows.forEach((r, i) => {
      const y = 0; // rows are positioned by their group's transform, not by y
      const cat = catOf(r);
      const cls = clsOf(r);
      // One group per row: a legend hover pales whole rows without rebuilding,
      // and a row kept across renders animates from wherever it was.
      alive.add(r.pcode);
      let row = rowEls.get(r.pcode);
      const entering = !row;
      const top = M.t + i * ROW;
      if (entering) {
        row = document.createElementNS(NS, "g");
        row.setAttribute("class", "bar-row");
        // Position it BEFORE it enters the document: an element appended with
        // its transform already set has no previous value to transition from,
        // so it lands in place instead of sliding in from the origin — and no
        // transition has to be suppressed and restored to achieve that.
        row.style.transform = `translateY(${top}px)`;
        rowsG.appendChild(row);
        rowEls.set(r.pcode, row);
        // A one-shot animation, not a class the next frame takes off — and only
        // while the page is actually being painted. A hidden tab freezes its
        // animation timeline at t=0, which would hold every new row at the
        // opacity the fade starts from: a chart of invisible rows until the
        // window comes forward. Nothing about a fade is worth that risk.
        if (document.visibilityState === "visible") {
          row.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 180, easing: "ease" });
        }
      } else {
        row.classList.remove("leaving"); // caught on its way out — bring it back
        row.innerHTML = "";
        row.style.transform = `translateY(${top}px)`; // slides, per the CSS transition
      }
      barRows.push({ el: row, cat, cls });
      const gr = (tag, attrs) => g(tag, attrs, row);
      const titled = (el, text) => {
        const t = document.createElementNS(NS, "title");
        t.textContent = text;
        el.appendChild(t);
        return el;
      };
      // Forecast-category swatch, severity square, admin name.
      const sw = gr("rect", { x: COL.cat, y: y + ROW / 2 - 7, width: 13, height: 13,
                              fill: cat ? fillOf(cat) : HNRP_MUTED.fill,
                              stroke: cat ? STYLE[cat][1] : HNRP_MUTED.edge, "stroke-width": 1 });
      const sl = slotOf(r);
      titled(sw, cat
        ? `${CAT_LABEL[catBase(cat)] || cat}` +
          (sl ? ` · ${sl.key}${sl.lead < 0 ? " (in season)" : ""} — RP ${fmt(sl.rp, 1)} yr, ` +
                `percentile ${fmt(sl.pct, 1)}, skill r ${fmt(sl.r, 2)}` : "")
        : "No forecast for the selected season");
      // Which season the category refers to, beside its swatch — omitted below
      // the skill floor, where there is no alert for a season to qualify.
      if (sl && cat && cat !== "low_skill") {
        gr("text", { x: COL.cat + 20, y: y + ROW / 2 + 4, "font-size": 10,
                     fill: sl.lead < 0 ? "#1d2021" : "#6b7a7b",
                     "font-weight": sl.lead < 0 ? 600 : 400 })
          .textContent = sl.key;
      }
      if (cls) {
        // The class number goes IN the swatch: five steps of one ramp are hard
        // to tell apart, and the colour is then a cue rather than the only key.
        titled(gr("rect", { x: COL.sev, y: y + ROW / 2 - 8, width: 15, height: 15,
                            rx: 2, fill: sevColors()[cls - 1], stroke: "#9db1b3",
                            "stroke-width": 0.6 }),
          sevClassDesc(r));
        gr("text", { x: COL.sev + 7.5, y: y + ROW / 2 + 4, "text-anchor": "middle",
                     "font-size": 10, "font-weight": 600, "pointer-events": "none",
                     fill: inkOn(sevColors()[cls - 1]) }).textContent = String(cls);
      }
      const nm0 = r.name ?? r.pcode;
      const maxLen = 24;
      const nameEl = gr("text", { x: COL.name, y: y + ROW / 2 + 4, "font-size": 11, fill: "#333" });
      // textContent first: it would wipe a <title> child appended before it.
      nameEl.textContent = nm0.length > maxLen ? nm0.slice(0, maxLen - 1) + "…" : nm0;
      titled(nameEl, dispName(r));

      // ── Bar (headcount, shared scale) + targeted tick ────────────────────
      const pop = popOf(r), popSrc = popSrcOf(r);
      const share = (v) => (pop && v != null ? v / pop : null);
      const pctTxt = (v) => {
        const f = share(v);
        if (f == null) return "share unknown — no population base";
        // Over 100%: give the real ratio here, and say why it can happen —
        // people counted who are not in the baseline (displacement), or a
        // caseload and a population figure that do not describe the same area.
        const exact = `${(100 * f).toFixed(f >= 0.1 ? 0 : 1)}% of ${fmtN(pop)} (${popSrc})`;
        return f > 1 ? `${exact} — caseload exceeds every population figure we hold`
          : exact;
      };
      const val = sevValOf(r), tgt = tgtOf(r);
      if (pinMode()) {
        if (val > 0) {
          titled(gr("rect", { x: X(0), y: y + 4, width: Math.max(X(val) - X(0), 0.5),
                              height: ROW - 9,
                              fill: cls ? sevColors()[cls - 1] : PIN_COLOR,
                              stroke: "#9db1b3", "stroke-width": 0.6 }),
            `${r.name ?? r.pcode} — PiN${secTag()}: ${fmtN(val)} · ${pctTxt(val)}` +
            (cls ? ` · ${sevClassDesc(r)}` : " · no severity published for this area"));
        }
      } else {
        const segs = segsOf(r) ?? [];
        let acc = 0;
        for (let c = barC0(); c < 5; c++) {
          const v = segs[c] ?? 0;
          if (v <= 0) continue;
          titled(gr("rect", { x: X(acc), y: y + 4,
                              width: Math.max(X(acc + v) - X(acc) - 1, 0.5),
                              height: ROW - 9, fill: sevColors()[c] }),
            `${r.name ?? r.pcode} — IPC phase ${c + 1}: ${fmtN(v)} · ${pctTxt(v)}`);
          acc += v;
        }
      }
      if (tgt != null) {
        titled(gr("line", { x1: X(tgt), x2: X(tgt), y1: y + 1, y2: y + ROW - 3,
                            stroke: "#1d2021", "stroke-width": 2 }),
          `${r.name ?? r.pcode} — targeted: ${fmtN(tgt)} · ${pctTxt(tgt)}`);
      }
      // ── The same four quantities as figures, one sortable column each ────
      [[val, fmtSI(val), pctTxt(val)],
       [tgt, tgt == null ? "–" : fmtSI(tgt), pctTxt(tgt)],
       [share(val), fmtPct(share(val)), pctTxt(val)],
       [share(tgt), tgt == null ? "–" : fmtPct(share(tgt)), pctTxt(tgt)],
      ].forEach(([v, text, tip], i) => {
        // ">100%" is a flag, not a figure — mute it like a missing value.
        const flagged = v == null || (i >= 2 && v > 1);
        const t = gr("text", { x: numRight(i), y: y + ROW / 2 + 4, "text-anchor": "end",
                               "font-size": 11, fill: flagged ? "#aab6b7" : "#333",
                               "font-variant-numeric": "tabular-nums" });
        t.textContent = v == null ? "–" : text;
        titled(t, `${r.name ?? r.pcode} — ${tip}`);
      });
    });
    // Rows the filter dropped: fade them where they stand, then drop them for
    // real — unless a later render revives them first.
    for (const [pcode, el] of rowEls) {
      if (alive.has(pcode) || el.classList.contains("leaving")) continue;
      el.classList.add("leaving");
      setTimeout(() => {
        if (!el.classList.contains("leaving")) return; // revived
        el.remove();
        if (rowEls.get(pcode) === el) rowEls.delete(pcode);
      }, EXIT_MS);
    }
    paintBarDim();
  }
  // Legend hover pales matching-out rows without rebuilding the chart (a pin,
  // which changes WHICH rows exist, re-runs renderBars instead).
  const barRows = [];
  const rowEls = new Map(); // pcode -> <g>, including rows mid-exit
  const EXIT_MS = 300;      // must outlast the .bar-row opacity transition
  let chromeG = null, rowsG = null, barsKey = null, barsSettle = 0, prevBarsH = 0;
  function resetBarRows() {
    if (rowsG) rowsG.innerHTML = "";
    rowEls.clear();
    barRows.length = 0;
  }
  function paintBarDim() {
    for (const b of barRows) {
      b.el.setAttribute("opacity", isDimmed(b.cat, b.cls) ? "0.15" : "1");
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
    // Legend severity strip follows the active source (IPC phases vs the plan's
    // own intersectoral classes, on the blue ramp).
    document.querySelectorAll("#hnrp-legend [data-ramp]").forEach((el) => {
      el.style.background = sevColors()[+el.dataset.ramp];
    });
    const sevStripTitle = document.getElementById("hnrp-sev-strip-title");
    if (sevStripTitle) sevStripTitle.textContent = sevLegendTitle();
    refreshLegendDim();
    renderMap(); renderBars();
  }
  for (const el of [skillSel, rpSel, srcTypeSel, srcLvlSel, triSel, ipcPeriodSel, planYrSel]) {
    el.addEventListener("change", renderAll);
  }
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
