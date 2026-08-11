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
    // IPC's own units. The plan view and the IPC view disagree about what a unit
    // IS — DR Congo's plan speaks in 519 zones de santé, its IPC analysis in 26
    // provinces — so each source is drawn on its own geometry rather than one
    // being prorated onto the other. Selected by the Severity source, not ?adm.
    ipc: ["data/hnrp_drought_ipc.json", "data/hnrp_ipc.geojson"],
    1: ["data/hnrp_drought.json", "data/hnrp_adm1.geojson"],
    2: ["data/hnrp_drought_adm2.json", "data/hnrp_adm2.geojson"],
    3: ["data/hnrp_drought_adm3.json", "data/hnrp_adm3.geojson"],
  };
  const QS = new URLSearchParams(location.search);
  let ADM = QS.get("adm") ?? "low";
  if (!(ADM in ADM_FILES) || ADM === "ipc") ADM = "low";
  // Which payload this page load needs. A fixed ?adm= level pins the geometry and
  // keeps both sources on it; the default view follows the source.
  let PAYLOAD = (ADM === "low" && QS.get("sev") === "ipc") ? "ipc" : ADM;
  const ADM_LABEL = { low: "lowest available level", 1: "admin 1", 2: "admin 2", 3: "admin 3" };
  let data, geo, world, countryFc = {};
  try {
    // no-cache = revalidate: the admin-level switch is a plain navigation, which
    // otherwise serves stale payloads straight from HTTP cache mid-session.
    const fj = (f) => fetch(f, { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)));
    world = await fj("data/countries.geojson");
    // Country-level forecast, for the sidebar's country readout. Same per-trimester
    // shape as a row's "tris", so it drops straight into the classifier rather than
    // being averaged up from admin units — an area mean is not a country forecast.
    // Optional: a missing file costs the country its forecast line, nothing else.
    countryFc = await fj("data/forecast.json").then((f) => f.data).catch(() => ({}));
    try {
      [data, geo] = await Promise.all(ADM_FILES[PAYLOAD].map(fj));
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
  let byPcode = new Map(data.rows.map((r) => [r.pcode, r]));
  const mapEl = document.getElementById("hnrp-map");

  // Plan cycle varies by country (e.g. Guatemala's latest is the 2025 HNRP) — surface
  // the year everywhere caseload figures appear.
  // The severity entry for the plan year in force — {tot, pb?, a?} or null.
  const sevcOf = (r) => (r.sevc ?? {})[planYr()] ?? null;
  // ── Plan year ────────────────────────────────────────────────────────────────
  // Each unit carries its caseloads per cycle in r.cyc = {"2026": [pin, targeted]},
  // and the selector says which one to read. Nothing is ever blended: a unit with
  // no figures for the chosen year shows dashes rather than an older cycle's.
  const planYrSel = document.getElementById("hnrp-plan-yr");
  const planYrWrap = document.getElementById("hnrp-plan-yr-wrap");
  // Only cycles that publish a severity distribution are offered. The JIAF PbS
  // starts at 2025, so an older cycle could show a caseload but never a class —
  // a plan year that greys the whole map is worse than one that isn't offered.
  // (2024 is dropped on that rule: it costs 109 units their only cycle and 626
  // their only targeting, Venezuela's included.)
  let cycYears = [];
  // Rebuilt on a source swap — the two payloads need not carry the same cycles.
  function buildPlanYears() {
    const keep = planYrSel.value;
    const sevYears = new Set(data.rows.flatMap((r) => Object.keys(r.sevc ?? {})));
    cycYears = [...new Set(data.rows.flatMap((r) => Object.keys(r.cyc ?? {})))]
      .filter((y) => sevYears.has(y))
      .map(Number).sort((a, b) => b - a);
    planYrSel.innerHTML = "";
    for (const y of cycYears) {
      const o = document.createElement("option");
      o.value = String(y);
      o.textContent = String(y);
      planYrSel.appendChild(o);
    }
    if ([...planYrSel.options].some((x) => x.value === keep)) planYrSel.value = keep;
  }
  buildPlanYears();
  const planYr = () => planYrSel.value || String(cycYears[0] ?? "");
  const cycOf = (r) => (r.cyc ?? {})[planYr()] ?? null;
  // Cycles that publish a PiN but no targets anywhere — the 2026 HNRPs, whose
  // subnational figures come from the JIAF needs analysis (PiN by severity, no
  // targeting). Worth saying out loud rather than leaving a column of dashes.
  let yearsWithTgt = new Set();
  function buildYearsWithTgt() {
    yearsWithTgt = new Set();
    for (const r of data.rows) {
      for (const [y, v] of Object.entries(r.cyc ?? {})) if (v[1] != null) yearsWithTgt.add(y);
      // Response monitoring carries targets for the cycle the needs analysis
      // publishes without them — 2026's subnational targeting exists only there.
      if (r.mon_yr && r.mon?.[1] != null) yearsWithTgt.add(r.mon_yr);
    }
  }
  buildYearsWithTgt();
  issuedEl.textContent = `Forecast issued ${data.issued_label}.`;

  // Rebuilt on a source swap: the two views do not cover the same countries.
  function fillCountries() {
    const keep = countrySel.value;
    for (const o of [...countrySel.querySelectorAll("option")]) if (o.value) o.remove();
    for (const c of [...new Set(data.rows.map((r) => r.country).filter(Boolean))].sort()) {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      countrySel.appendChild(o);
    }
    // A country the other source does not carry falls back to the world view.
    countrySel.value = [...countrySel.options].some((o) => o.value === keep) ? keep : "";
  }
  fillCountries();

  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const fmt = (v, d) => (v == null ? "–" : Number(v).toFixed(d));
  const pctOf = (num, den) => (num == null || !den ? null : (100 * num) / den);
  // PiN/targeted are always the plan's INTERSECTORAL figures, for the selected
  // plan year (per-sector series remain in r.sec should a sector view return).
  const pinOf = (r) => cycOf(r)?.[0] ?? null;
  // Targeted: the needs analysis first, then response monitoring for the cycle
  // it covers. Not a blend of two measures — they are the same one, verified:
  // the monitoring table's national target sums reproduce hpc.plans exactly
  // (AFG 17.48M, SDN 20.42M, YEM 12.00M). It matters because HAPI publishes no
  // subnational 2026 target at all, so without this the current plan year shows
  // a column of dashes. tgtSrcOf names which side answered, for the tooltip.
  const tgtOf = (r) => cycOf(r)?.[1] ?? monLive(r)?.[1] ?? null;
  const tgtSrcOf = (r) => (cycOf(r)?.[1] != null ? "needs analysis"
    : monLive(r)?.[1] != null ? "response monitoring" : null);
  const secTag = () => "";

  // Units with no PiN/severity/targeted are IPC-only (outside any HNRP's analysis) —
  // shown only in IPC mode, where surfacing needs the plan does NOT capture is the point.
  const inHnrp = (r) => r.sev_total > 0 || r.cyc != null || r.targeted != null
    || r.sec != null || r.sevc != null;

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
  // A combo can carry an analysed population with NO phase breakdown, because IPC
  // projections often cover FEWER areas than the countrywide current period and the
  // 'all' row is still carried at full country scope. Sudan's Jan-2026 exercise says
  // so outright: Feb–May 2026 covers all 195 localities, but Jun–Sep 2026 and
  // Oct–Jan 2027 cover 56 — "data was not available for a full nationwide projection
  // analysis". So 135 of 189 units carry a population and no phases for those
  // windows. That is a unit OUTSIDE the projection, not one with nobody in crisis;
  // selected literally it walks the class search down from 5, finds nothing, and
  // paints the area phase 1. Never select such a combo while a real one exists.
  const hasPhases = (c) => c.p.some((v) => (v ?? 0) > 0);
  // ONE PERIOD PER COUNTRY, exactly as ipcinfo.org does it. Its country page draws
  // Current / Projected 1 / Projected 2 as three separate maps, and on a projection
  // map every area outside the projection is left WHITE — Sudan's Jun–Sep 2026 map
  // shows 56 localities and blanks the other 139. Picking per unit instead would
  // paint one map from up to four vintages at once (Sudan did exactly that) under a
  // single title, which no reader could unpick. So the period is chosen for the
  // whole country and a unit with no data for it is simply not classified.
  const periodKey = (c) => `${c.t}|${c.a}|${c.s}|${c.e}`;
  const periodCache = new Map();
  function ipcPeriodOf(iso, mode) {
    const key = `${iso}|${mode}`;
    if (periodCache.has(key)) return periodCache.get(key);
    const covers = (c) => ym(c.s) <= NOW_YM && ym(c.e) >= NOW_YM;
    // Every period the country publishes, deduped, newest exercise first.
    const seen = new Map();
    for (const r of data.rows) {
      if (r.iso3 !== iso) continue;
      for (const c of r.ipc ?? []) if (hasPhases(c) && !seen.has(periodKey(c))) seen.set(periodKey(c), c);
    }
    const list = [...seen.values()];
    // An explicit period picked from the dropdown wins outright — that is the whole
    // point of offering it. A country that does not publish it gets nothing rather
    // than a silent substitution, which is what made the mixed-vintage map possible.
    if (mode !== "now" && mode !== "fwd") {
      const exact = list.find((c) => periodKey(c) === mode) ?? null;
      periodCache.set(key, exact);
      return exact;
    }
    const pick = (arr) => (arr.length
      ? arr.sort((a, b) => (b.a ?? "").localeCompare(a.a ?? "") || ym(b.s) - ym(a.s))[0] : null);
    let chosen = null;
    if (mode === "fwd") {
      chosen = pick(list.filter((c) => c.t !== "current"
        && ym(c.e) >= NOW_YM && ym(c.s) <= NOW_YM + 6));
    }
    chosen = chosen
      || pick(list.filter((c) => c.t === "current" && covers(c)))
      || pick(list.filter(covers))
      // Nothing covers the issuance month: the most recent window that ended.
      || list.reduce((a, b) => (a == null || ym(b.e) > ym(a.e) ? b : a), null);
    periodCache.set(key, chosen);
    return chosen;
  }
  function ipcComboOf(r, mode = ipcPeriodSel.value) {
    const want = ipcPeriodOf(r.iso3, mode);
    if (!want) return null;
    const k = periodKey(want);
    const c = (r.ipc ?? []).find((x) => periodKey(x) === k);
    // Present but unclassified for this period = outside its coverage. Blank, not phase 1.
    return c && hasPhases(c) ? c : null;
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
    (c.d ? ` — downscaled from admin-${c.d} by population share` : "");  // legacy flag: no longer produced

  const IPC_OPT_BASE = { now: "Now", fwd: "Forecast window" };
  // Every period the country publishes, newest exercise first, with how many of its
  // units each one actually classifies. "Now" can legitimately resolve to a period
  // covering very little (Afghanistan's only window over Aug 2026 reaches 9 of 401
  // districts), so the earlier, fuller analyses have to be reachable — otherwise the
  // honest default is also a dead end.
  function ipcPeriodsFor(country) {
    const seen = new Map();
    for (const r of data.rows) {
      if (!r.ipc || (country && r.country !== country)) continue;
      for (const c of r.ipc) {
        if (!hasPhases(c)) continue;
        const k = periodKey(c);
        const e = seen.get(k) ?? { c, units: 0 };
        e.units++;
        seen.set(k, e);
      }
    }
    return [...seen.values()].sort((a, b) =>
      (b.c.a ?? "").localeCompare(a.c.a ?? "") || ym(b.c.s) - ym(a.c.s));
  }
  function updateIpcPeriodUI() {
    ipcPeriodWrap.hidden = !ipcMode();
    srcLvlWrap.hidden = pinMode(); // PiN is a headline total, no severity level
    // Plan year selects an HNRP cycle. It does nothing to IPC figures, so showing
    // it beside the IPC controls just invites the reader to think it does.
    if (planYrWrap) planYrWrap.hidden = ipcMode();
    if (!ipcMode()) return;
    // Explicit periods are only meaningful for one country at a time — across
    // countries the same window belongs to different exercises.
    const keep = ipcPeriodSel.value;
    for (const o of [...ipcPeriodSel.querySelectorAll("option[data-period]")]) o.remove();
    if (countrySel.value) {
      const list = ipcPeriodsFor(countrySel.value);
      const chosen = ipcPeriodOf(
        (data.rows.find((r) => r.country === countrySel.value) || {}).iso3, "now");
      for (const { c, units } of list) {
        const o = document.createElement("option");
        o.value = periodKey(c);
        o.dataset.period = "1";
        o.textContent = `${c.t}, exercise ${fmtYM(c.a)}, valid ${c.label}`
          + ` — ${units} unit${units === 1 ? "" : "s"}`
          + (chosen && periodKey(chosen) === periodKey(c) ? " (current default)" : "");
        ipcPeriodSel.appendChild(o);
      }
      if ([...ipcPeriodSel.options].some((o) => o.value === keep)) ipcPeriodSel.value = keep;
      else if (keep !== "now" && keep !== "fwd") ipcPeriodSel.value = "now";
    } else if (keep !== "now" && keep !== "fwd") {
      ipcPeriodSel.value = "now"; // an explicit period cannot survive "All countries"
    }
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
      if (opt.dataset.period) continue; // explicit periods label themselves
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
  // Ignoring the country filter. Only the map uses this, and only for the faded
  // neighbours: scope decides what we REPORT (tooltips, the chart), but a unit
  // drawn as context should still wear its own colours, and catOf would hand it
  // the same grey as "no forecast for this season".
  function catAnyOf(r) {
    const s = r && slotOfAny(r);
    return s ? classify({ pct: s.pct, r: s.r, rainy: s.rainy }, false) : null;
  }
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
      if (has("cyc")) ds.push("PiN");
      if (rs.some((x) => Object.values(x.cyc ?? {}).some((v) => v[1] != null))) ds.push("targeted");
      if (has("pb") || has("pba")) ds.push("PiN by severity");
      if (has("ipc")) ds.push("IPC");
      // Which plan cycles this country has, since the year is a control now.
      const yrs = [...new Set(rs.flatMap((x) => Object.keys(x.cyc ?? {})))].sort();
      countryTipCache.set(key,
        `<div class="name">${country}</div>` +
        `<div>${yrs.length ? `HNRP ${yrs.join(", ")} · ` : ""}${ds.join(", ")}</div>` +
        `<div class="cat" style="color:#9db1b3">${hint}</div>`);
    }
    return countryTipCache.get(key);
  }
  // ── Response monitoring (targeted / reached) ────────────────────────────────
  // Colours are the GHO monitoring dashboard's own, so our figures are
  // recognisable next to it: the first two are lifted from the published report
  // definition, the third is the tint it draws reach bars in.
  // Four steps of one funnel — need, plan, priority, delivery — so the reds
  // deepen as the caseload narrows. #d15353 stays on the PRIORITY target, which
  // is the figure the dashboard puts its own headline red on ("People targeted
  // (prioritized)"); the broader plan target takes the pale tone above it.
  const MON = {
    pin: "#009dda",       // People in need
    tgt: "#f0a3a3",       // People targeted (the plan's overall target)
    prio: "#d15353",      // People targeted, prioritized
    rea: "#8c3232",       // People reached
    track: "#eef1f1",
  };
  // [PiN, targeted, prioritized target, reached, prioritized reached] for the
  // monitored cycle. Any slot can be null — the source reports unevenly, and a
  // null here means "not reported", never zero.
  const monOf = (r) => (pinMode() ? (r.mon ?? null) : null);
  // Monitoring is only published for the cycle the dashboard is tracking, so it
  // has no business on screen when an earlier plan year is selected.
  const monLive = (r) => (monOf(r) && r.mon_yr === planYr() ? r.mon : null);
  const monMonth = (r) => (data.mon_months ?? {})[r.iso3] ?? null;
  // Concentric arcs, not wedges. The full circle is PiN — that is the "total" —
  // and targeted and reached are drawn as arcs of it on inner bands. A pie of
  // three sectors would have to assume reach ⊆ target ⊆ PiN, and it is not:
  // units report more people reached than they targeted (Kabul: 224,446 against
  // 665,764 targeted, and elsewhere reach exceeds target outright). An arc that
  // can run past its neighbour states that honestly; a wedge cannot.
  // R and the 7px band pitch carry four rings: 30, 23, 16, 9, stroked at 5.
  const R = 30, RING_STROKE = 5, TAU = 2 * Math.PI;
  function arcPath(cx, cy, rad, frac) {
    // A non-finite share draws nothing rather than a path full of NaN, which
    // SVG renders as an invisible element and no error.
    const f = Number.isFinite(frac) ? Math.max(0, Math.min(1, frac)) : 0;
    if (f >= 0.999) {  // a full circle as one arc collapses to a point
      return `M ${cx} ${cy - rad} A ${rad} ${rad} 0 1 1 ${cx - 0.01} ${cy - rad}`;
    }
    const a = -Math.PI / 2 + f * TAU;
    return `M ${cx} ${cy - rad} A ${rad} ${rad} 0 ${f > 0.5 ? 1 : 0} 1 ` +
      `${cx + rad * Math.cos(a)} ${cy + rad * Math.sin(a)}`;
  }
  function monPie(r, agg = null) {
    const m = agg ? agg.mon : monLive(r);
    if (!m) return "";
    const [pin, tgt, prio, rea] = m;
    if (pin == null && tgt == null && prio == null && rea == null) return "";
    // Without a PiN there is no denominator, but there can still be a response:
    // Venezuela reports 582k reached and no needs figure at all. Print the
    // counts and drop the rings rather than dropping the unit — the whole point
    // of this panel is that unreported and zero are different.
    const base = pin || null;
    const frac = (v) => (base == null || v == null ? null : v / base);
    // Four concentric bands, every one a share of PiN so they nest visually and
    // the circle still means "all the need". The FIGURES beside them use the
    // funnel's own denominators, which is a different question — how much of each
    // step survived to the next — and the labels say which is which.
    const band = [[frac(pin), MON.pin, R], [frac(tgt), MON.tgt, R - 7],
                  [frac(prio), MON.prio, R - 14], [frac(rea), MON.rea, R - 21]];
    const arcs = band.map(([frac, col, rad]) =>
      `<path d="${arcPath(R + 3, R + 3, rad, 1)}" fill="none" stroke="${MON.track}"` +
      ` stroke-width="${RING_STROKE}"/>` +
      (frac == null ? ""
        : `<path d="${arcPath(R + 3, R + 3, rad, frac)}" fill="none" stroke="${col}"` +
          ` stroke-width="${RING_STROKE}" stroke-linecap="butt"/>` +
          // Past a full turn the arc has nowhere left to go, and a closed ring
          // would read as exactly 100%. 45 units report reach above their own
          // PiN; hatch those so the ring says "over", and let the figure beside
          // it carry the real share.
          (frac > 1.001
            ? `<path d="${arcPath(R + 3, R + 3, rad, 1)}" fill="none" stroke="#1d2021"` +
              ` stroke-width="${RING_STROKE}" stroke-dasharray="2 4" opacity="0.45"/>` : "")
      )).join("");
    // Each share names its own denominator, because they differ: targeting is
    // read against need, but delivery is read against what was targeted — "17% of
    // PiN" answers a question nobody asked of a response. The PiN line carries no
    // share at all; it IS the denominator.
    const line = (label, v, col, den = null, denLabel = "") => {
      const f = den && v != null ? v / den : null;
      return `<div><span style="display:inline-block;width:8px;height:8px;` +
        `border-radius:50%;background:${col}"></span> ${label}: ` +
        (v == null ? `<span style="color:#9db1b3">not reported</span>`
          : `${fmtN(v)}${f == null ? ""
            : ` · ${Math.round(100 * f)}% of ${denLabel}`}`) + `</div>`;
    };
    const month = agg ? agg.monMonth : monMonth(r);
    const monYr = agg ? agg.monYr : r.mon_yr;
    return `<div style="display:flex;gap:10px;align-items:center;margin-top:4px">` +
      (base == null ? ""
        : `<svg width="${2 * R + 6}" height="${2 * R + 6}" style="flex:none">${arcs}</svg>`) +
      `<div style="font-size:11px;line-height:1.5">` +
      // Each step measured against the one above it, so the four lines read as
      // the funnel they are. A step whose denominator was never reported shows
      // its count and no share — better than quietly borrowing the step above,
      // which would make two different ratios look like one series.
      line("PiN", pin, MON.pin) +
      line("Targeted", tgt, MON.tgt, base, "PiN") +
      line("Prioritized", prio, MON.prio, tgt, "targeted") +
      // Against priority where there is one, else against the plan target. Safe
      // to decide per unit HERE, unlike in a chart column, because the line names
      // its own denominator in words right beside the number.
      // `||`, not `??`: a priority of ZERO is a published figure (Kabul reports
      // one, and 315 of Afghanistan's 401 districts do) but it is not a
      // denominator — dividing by it would drop the share entirely. Fall through
      // to the plan target and say so.
      line("Reached", rea, MON.rea, prio || tgt,
           prio ? "prioritized" : "targeted") +
      `<div class="muted">HNRP ${monYr} response` +
      `${month ? ` · as of ${month}` : ""}</div></div></div>`;
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
      return countryTip(r.country, "Click to switch to this country");
    }
    // No row at all = a polygon the payload doesn't cover. Name it, and where we
    // can tell which country it belongs to, say the click will go there — a
    // tooltip that only names the shape reads as a dead area.
    if (!r) {
      const c = isoCountry.get(p.iso3);
      return `<div class="name">${p.name ?? p.pcode}</div>` +
        (c && c !== sel ? `<div class="cat" style="color:#9db1b3">${c} — click to` +
          ` switch to this country</div>` : "");
    }
    // Inside the selected country the readout lives in the sidebar, which has the
    // room to lay it out; the tooltip would only cover the map with a duplicate.
    return `<div class="name">${dispName(r)}</div>`;
  };
  // ── Detail body, shared by the sidebar's unit and country readouts ───────────
  // One renderer for both, because the country view is the same measurements
  // aggregated — anything that reads differently between the two is a place the
  // aggregate has quietly changed meaning.
  function detailBody(r, agg = null) {
    const cat = agg ? agg.cat : catOf(r);
    const s = agg ? agg.slot : slotOf(r);
    let rows = "";
    const catLine = cat
      ? `<div class="cat" style="color:${STYLE[cat][1]}">${CAT_LABEL[catBase(cat)] || cat}</div>`
      : `<div class="cat muted">No forecast for the selected season</div>`;
    rows += catLine;
    if (s && s.rp != null) {
      rows += `<div><strong>${s.key}</strong>${s.lead < 0 ? " · in season" : ""}` +
        ` — RP ${fmt(s.rp, 1)} yr, r ${fmt(s.r, 2)}</div>`;
    }
    if (agg) {
      rows += `<div class="muted">Country-level forecast, not an average of its` +
        ` areas</div>`;
    }
    const sev = agg ? agg.sevVal : sevValOf(r);
    if (sev != null) {
      const c = ipcMode() ? (agg ? agg.combo : ipcComboOf(r)) : null;
      rows += `<div class="sec"><div class="sec-t">${sevLabel()}</div>` +
        `<div>${fmtN(sev)}${c ? `<div class="muted">${comboDesc(c)}</div>` : ""}</div></div>`;
    }
    const tgt = agg ? agg.tgt : tgtOf(r);
    const live = agg ? agg.mon : monLive(r);
    if (pinMode() && tgt != null && !live) {
      rows += `<div>Targeted${secTag()}: ${fmtN(tgt)}</div>`;
    }
    if (pinMode() && (agg ? agg.hasCycle : cycOf(r))) {
      rows += `<div class="muted">HNRP ${planYr()}</div>`;
    }
    rows += monPie(r, agg);
    if (pinMode() && !agg && r.sevc && !r.sevc[planYr()]) {
      rows += `<div class="muted">not classified in the ${planYr()} cycle</div>`;
    }
    if (agg) {
      rows += agg.clsBreakdown;
    } else {
      // The area's own split across the classes — its IPC population by phase, or
      // the PiN-by-severity distribution behind its plan class. Shown at every
      // admin level, not just the lowest: the breakdown is a property of the
      // unit's analysis, and it was the whole reason to open an area.
      const mix = ipcMode() ? (ipcComboOf(r)?.p ?? null)
        : ((r.sevc ?? {})[planYr()]?.pb ?? null);
      rows += mixHtml(mix);
      if (ADM === "low") {
        const cls = sevClassOf(r);
        if (cls) {
          // Which class the AREA is, and where that came from — a different fact
          // from the split above, which is how its people are distributed.
          rows += `<div><span style="display:inline-block;width:10px;height:10px;` +
            `background:${sevColors()[cls - 1]};border:1px solid #9db1b3;` +
            `vertical-align:baseline"></span> ${sevClassDesc(r)}</div>`;
        } else if (pinMode() && pinOf(r) != null) {
          rows += `<div class="muted">No severity published for this area</div>`;
        }
      }
    }
    if (agg) {
      rows += `<div class="sec"><div class="sec-t">Coverage</div>` +
        `<div>${agg.nUnits} area${agg.nUnits === 1 ? "" : "s"} on the map` +
        `${agg.nWithSev ? `, ${agg.nWithSev} with ${pinMode() ? "a caseload"
          : "an IPC classification"}` : ""}</div>` +
        (agg.pop != null ? `<div class="muted">population ${fmtN(agg.pop)}</div>` : "") +
        `</div>`;
    } else {
      // Membership must be per-unit, never assumed from scope: in IPC mode most of
      // a country's states can be in view yet outside its HNRP (Nigeria covers only
      // Borno/Adamawa/Yobe).
      if (!inHnrp(r)) rows += `<div class="muted">Not in an HNRP</div>`;
      const pop = popOf(r);
      if (pop != null) {
        rows += `<div class="muted">population ${fmtN(pop)} (${popSrcOf(r)})</div>`;
      }
    }
    return rows;
  }
  // ── Country aggregate for the sidebar ───────────────────────────────────────
  // Caseloads sum; the forecast does NOT. A country's forecast comes from its own
  // adm0 series (data/forecast.json — the same one the Map tab draws), because the
  // mean of a country's admin percentiles is not the percentile of the country's
  // rainfall, and averaging skill across areas is meaningless.
  //
  // Every sum is absent-preserving: if no area reports a measure the total is null
  // ("not reported"), never 0. Summing nulls to zero is how a country with no
  // reported reach would come to claim it reached nobody.
  // Population split across the five classes, as counts and shares. Used at both
  // levels: an area's own IPC phase / PbS distribution, and the country's mix
  // summed over its areas. Same renderer, so the two are directly comparable.
  function mixHtml(mix) {
    const total = (mix ?? []).reduce((a, b) => a + (b || 0), 0);
    if (!(total > 0)) return "";
    return `<div class="sec"><div class="sec-t">${sevClassTitle()}</div>` +
      mix.map((v, i) => (!(v > 0) ? "" :
        `<div><span style="display:inline-block;width:10px;height:10px;` +
        `background:${sevColors()[i]};border:1px solid #9db1b3;` +
        `vertical-align:baseline"></span> ${sevClassLabels()[i]} — ${fmtN(v)}` +
        ` <span class="muted">(${Math.round((100 * v) / total)}%)</span></div>`)).join("") +
      `</div>`;
  }
  const aggCache = new Map();
  function countryAgg(country) {
    const key = `${country}|${srcTypeSel.value}|${planYr()}|${ipcPeriodSel.value}|${lvl()}`;
    if (aggCache.has(key)) return aggCache.get(key);
    const rows = data.rows.filter((r) => r.country === country);
    const iso = rows[0]?.iso3;
    const sum = (f) => {
      let any = false, t = 0;
      for (const r of rows) { const v = f(r); if (v != null) { any = true; t += v; } }
      return any ? t : null;
    };
    // The country's own forecast series, run through the same classifier the units
    // use so the category and the wording match exactly.
    //
    // It has to be a STRUCTURALLY COMPLETE row, not just {tris}. forecast.json
    // carries pct/r/rp/rainy per trimester and nothing else, but rawSlotOf reads
    // two things the file does not have: t.lead, to skip in-season trimesters in
    // auto mode, and the fb_* fallback slot it drops to whenever no trimester
    // qualifies as a drought. Without them every country with no drought signal
    // came out null — "No forecast for the selected season" on a country whose
    // forecast we were holding all along. Both are properties of the ISSUANCE, so
    // any unit of the country supplies them; only the values are the country's own.
    const tris = countryFc[iso] ?? null;
    const sample = rows.find((r) => r.tris && r.fb_tri) ?? null;
    let fcRow = null;
    if (tris && sample) {
      const merged = {};
      for (const [k, v] of Object.entries(tris)) {
        merged[k] = { ...v, lead: sample.tris?.[k]?.lead };
      }
      const fb = merged[sample.fb_tri] ?? null;
      fcRow = {
        tris: merged, iso3: iso, country,
        fb_tri: sample.fb_tri, fb_label: sample.fb_label,
        fb_pct: fb?.pct ?? null, fb_r: fb?.r ?? null,
        fb_rp: fb?.rp ?? null, fb_rainy: !!fb?.rainy,
      };
    }
    const slot = fcRow ? slotOfAny(fcRow) : null;
    const cat = slot ? classify({ pct: slot.pct, r: slot.r, rainy: slot.rainy }, false) : null;
    // Severity mix across the country's units, as a share of the caseload rather
    // than a count of areas — one huge class-3 state should not read the same as
    // one tiny one. Built from whichever encoding is on screen.
    const mix = [0, 0, 0, 0, 0];
    let mixAny = false, nWithSev = 0;
    for (const r of rows) {
      if (ipcMode()) {
        const c = ipcComboOf(r);
        if (!c) continue;
        nWithSev++;
        c.p.forEach((v, i) => { if (v) { mix[i] += v; mixAny = true; } });
      } else {
        const v = pinOf(r);
        if (v == null) continue;
        nWithSev++;
        const cls = sevClassOf(r);
        if (cls) { mix[cls - 1] += v; mixAny = true; }
      }
    }
    const clsBreakdown = mixAny ? mixHtml(mix) : "";
    // Monitoring: only the units whose snapshot is the selected cycle contribute,
    // the same rule a unit readout follows.
    const mon = [0, 1, 2, 3, 4].map((i) => sum((r) => monLive(r)?.[i] ?? null));
    const out = {
      country, iso, nUnits: rows.length, nWithSev,
      slot, cat,
      sevVal: sum((r) => sevValOf(r)),
      combo: ipcMode() && iso ? ipcPeriodOf(iso, ipcPeriodSel.value) : null,
      tgt: sum((r) => tgtOf(r)),
      pop: sum((r) => popOf(r)),
      hasCycle: rows.some((r) => cycOf(r)),
      mon: mon.some((v) => v != null) ? mon : null,
      monYr: rows.find((r) => monLive(r))?.mon_yr ?? planYr(),
      monMonth: (data.mon_months ?? {})[iso] ?? null,
      clsBreakdown,
    };
    aggCache.set(key, out);
    return out;
  }

  // ── Detail sidebar ──────────────────────────────────────────────────────────
  // Replaces the per-unit hover tooltip, which had grown to a pie chart plus eight
  // lines and was covering the map it described. Hover fills it, a click pins it.
  const sideEl = document.getElementById("hnrp-side");
  let pinnedPcode = null;   // unit the sidebar is locked to
  let hoverPcode = null;    // unit under the pointer, transient
  function crumb(level, name, onClear, clearLabel) {
    return `<div class="side-crumb"><span class="lbl"><span class="lvl">${level}</span>` +
      `<span class="nm">${name}</span></span>` +
      (onClear ? `<button type="button" class="side-x" data-clear="${onClear}"` +
        ` title="${clearLabel}" aria-label="${clearLabel}">` +
        `<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">` +
        `<path d="M6 6 L18 18 M18 6 L6 18" fill="none" stroke="currentColor"` +
        ` stroke-width="3" stroke-linecap="round"/></svg></button>` : "") + `</div>`;
  }
  function renderSide() {
    const sel = countrySel.value;
    sideEl.hidden = !sel;
    if (!sel) { sideEl.innerHTML = ""; return; }
    // Hover wins over the pin while the pointer is on a unit, so the sidebar can
    // be browsed without losing the pinned one — releasing returns to it.
    const shownPcode = hoverPcode ?? pinnedPcode;
    const r = shownPcode ? byPcode.get(shownPcode) : null;
    const agg = countryAgg(sel);
    let head = crumb("Country", sel, "country", "Back to the world view");
    if (r) {
      // The × clears the PIN, so it only belongs on the pinned unit. Hovering a
      // second area while one is pinned shows the hovered figures, and offering
      // an × there would clear something other than the name it sits beside.
      const isPinned = shownPcode === pinnedPcode;
      head += crumb(r.lvl ? `Admin ${r.lvl}` : "Area", dispName(r),
        isPinned ? "unit" : null, "Back to the country view");
    }
    const body = r ? detailBody(r) : detailBody(null, agg);
    const hint = !r ? "Hover an area for its figures, click to keep them here."
      : shownPcode === pinnedPcode ? "" : "Click to keep this area here.";
    sideEl.innerHTML = `<div class="side-head">${head}</div>` +
      `<div class="side-body">${body}</div>` +
      (hint ? `<div class="side-hint">${hint}</div>` : "");
  }
  sideEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-clear]");
    if (!btn) return;
    if (btn.dataset.clear === "unit") {
      setPinnedUnit(null);
    } else {
      setPinnedUnit(null);
      countrySel.value = "";
      countrySel.dispatchEvent(new Event("change"));
    }
  });
  // The pinned area is marked by tracing its OWN boundary, not by fading its
  // neighbours: a country dimmed to pick out one district loses the comparison
  // that made the map worth looking at. Drawn as its own layer above the mosaic
  // rather than by restyling the unit's path, so it cannot be overdrawn by a
  // neighbour's edge or by the inset forecast ring, and a white casing under the
  // dark line keeps it legible over both the dark end of the ramp and the pale end.
  let pinHalo = null, pinHaloFor = null;
  function renderPinHalo() {
    if (pinnedPcode === pinHaloFor) {
      // Unchanged, but a source swap rebuilds the mosaic and re-fronts the
      // country borders over everything added before it — including this.
      if (pinHalo) pinHalo.eachLayer((l) => l.bringToFront());
      return;
    }
    pinHaloFor = pinnedPcode;
    if (pinHalo) { map.removeLayer(pinHalo); pinHalo = null; }
    if (!pinnedPcode) return;
    // Polygonal features only, the same filter the mosaic uses. Four DR Congo
    // health zones ship with null geometry, and L.geoJSON turns a degenerate
    // feature into a Leaflet MARKER — a pin dropped in the Gulf of Guinea.
    const f = geo.features.find((x) => x.properties?.pcode === pinnedPcode);
    if (!f || !/Polygon/.test(f.geometry?.type ?? "")) return;
    const line = (color, weight) => L.geoJSON(f, {
      interactive: false, style: { color, weight, opacity: 1, fill: false },
    });
    pinHalo = L.layerGroup([line("#fff", 5), line("#1d2021", 2.4)]).addTo(map);
    // Above the country borders, which are themselves brought to the front over
    // the admin mosaic — otherwise a shared edge draws over half the halo.
    pinHalo.eachLayer((l) => l.bringToFront());
  }
  function setPinnedUnit(pcode) {
    if (pinnedPcode === pcode) return;
    pinnedPcode = pcode;
    renderPinHalo();
    renderSide();
  }
  function setHoverUnit(pcode) {
    if (hoverPcode === pcode) return;
    hoverPcode = pcode;
    renderSide();
  }

  // World countries beneath as context (non-interactive, like the Map tab's backdrop).
  L.geoJSON(world, {
    interactive: false,
    style: { color: "#d9dedf", weight: 0.5, fillColor: "#f7f9f9", fillOpacity: 1 },
  }).addTo(map);
  const makeLayer = () => L.geoJSON(geo, {
    filter: (f) => /Polygon/.test(f.geometry.type),
    // Neutral initial style so nothing flashes Leaflet-blue before renderMap runs.
    style: () => ({ weight: 0.6, fillOpacity: 1, color: "#e2e8e8", fillColor: "#f5f7f7" }),
    onEachFeature: (f, l) => {
      l.bindTooltip(() => tipHtml(f), { sticky: true });
      // Click-to-focus. Clicking any unit that has data selects ITS country,
      // whether or not one is already selected — so neighbours are one click
      // apart instead of a round trip out to the world view and back in.
      // Only a click with no country behind it (open sea, a polygon the payload
      // does not cover) falls through to the map handler and deselects.
      l.on("click", (e) => {
        // Resolve through the ROW first, then fall back to the polygon's own
        // iso3. A unit can have no row — the payload and the geometry are built
        // to the same country scope now, but a pcode that fails to reconcile
        // still leaves a shape with nothing behind it, and a click landing on
        // one of those used to do nothing at all (Tanzania: 138 of 170
        // polygons, so four fifths of the country was inert).
        const row = byPcode.get(f.properties.pcode);
        const c = row?.country ?? isoCountry.get(f.properties.iso3);
        if (!c) return;
        L.DomEvent.stopPropagation(e);
        if (c === countrySel.value) {
          // Inside the focus country a click pins the unit to the sidebar, and
          // clicking the pinned one again releases it.
          if (row) setPinnedUnit(pinnedPcode === row.pcode ? null : row.pcode);
          return;
        }
        setPinnedUnit(null);  // a pin belongs to the country it was made in
        countrySel.value = c;
        countrySel.dispatchEvent(new Event("change"));
      });
      // Hover feeds the sidebar. Only inside the selected country: elsewhere the
      // tooltip still answers "which country is this?", which is the question.
      l.on("mouseover", () => {
        const row = byPcode.get(f.properties.pcode);
        if (row && countrySel.value && row.country === countrySel.value) {
          setHoverUnit(row.pcode);
        }
      });
      l.on("mouseout", () => {
        if (hoverPcode === f.properties.pcode) setHoverUnit(null);
      });
    },
  });
  let layer = makeLayer().addTo(map);
  map.on("click", () => {
    // Same click the country hit layer just handled — it selected, we must not undo.
    if (Date.now() - hitClickAt < 100) return;
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
  // ── Whole-country hit layer (world view only) ────────────────────────────────
  // With nothing selected the question the map answers is "which country?", so the
  // hover target is the country, not the admin unit. Hovering the admin mosaic for
  // that meant the tooltip closed and reopened on every boundary crossed — one
  // Leaflet layer per unit, hundreds per country — which reads as flicker. This
  // single transparent polygon per country sits above the mosaic and absorbs the
  // pointer while the world view is up; selecting a country removes it, so the
  // per-admin tooltips underneath work exactly as before.
  let hitClickAt = 0;  // when the country hit layer last handled a click
  const isoCountry = new Map(data.rows.map((r) => [r.iso3, r.country]).filter((x) => x[1]));
  const countryHit = L.geoJSON(
    { type: "FeatureCollection",
      features: world.features.filter((f) => dataIsos.has(f.properties.iso3)) },
    {
      style: { stroke: false, fill: true, fillOpacity: 0, fillColor: "#000" },
      onEachFeature: (f, l) => {
        const c = isoCountry.get(f.properties.iso3);
        if (!c) return;
        l.bindTooltip(() => countryTip(c), { sticky: true });
        l.on("click", (e) => {
          // stopPropagation alone is not enough here. The map's own click handler
          // clears the selection, and by the time it runs the selection we just
          // made is already set — so it reads as "user clicked away" and undoes it,
          // selecting and deselecting in one click. (This worked only while the
          // layer was being REMOVED from the map mid-click, which killed
          // propagation as a side effect.) Stamp the click and let the map handler
          // skip it.
          L.DomEvent.stopPropagation(e);
          hitClickAt = Date.now();
          countrySel.value = c;
          countrySel.dispatchEvent(new Event("change"));
        });
      },
    },
  ).addTo(map);
  // Active only in the world view — but toggled with pointer-events, NEVER by
  // adding and removing the layer. Adding or removing a layer next to an in-flight
  // fitBounds wedges Leaflet's zoom animation and every later fit silently no-ops,
  // which used to wedge fitCountry()'s animated zoom. Deselecting
  // by clicking the map fires the fit and this toggle in the same tick, so a
  // layer-level toggle would sit exactly on that fault line. CSS does not.
  function syncCountryHit() {
    const on = !countrySel.value;
    countryHit.eachLayer((l) => {
      if (l._path) l._path.style.pointerEvents = on ? "" : "none";
    });
  }

  map.fitBounds(layer.getBounds(), { animate: false });

  // ── Inset forecast-category rings (lowest view) ──────────────────────────────
  // Same clip-path trick as the CBPF page: clip each path to its own shape and
  // double the stroke width so only the inner half renders — an outline fully
  // inside the unit. A second, narrower "gap" ring in the unit's fill colour is
  // drawn on top, standing the category ring off the shared boundary.
  const RING_W = 2.8; // visible ring width, px (flush: neighbours touch)
  const SVGNS = "http://www.w3.org/2000/svg";
  const makeRing = () => L.geoJSON(geo, {
    filter: (f) => /Polygon/.test(f.geometry.type),
    interactive: false,
    style: () => ({ weight: 0, fill: false, opacity: 1 }),
  });
  let ringCat = makeRing().addTo(map);
  let clipPaths = {};
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
    // The dismiss button lives in the sidebar now, one per level (country and
    // pinned area). On the map it was a 26px target over a choropleth, easy to
    // miss and easy to mistake for data; in the sidebar it sits beside the name
    // it clears, which says what it does without a tooltip.
  }
  // Per-admin trimester codes, shown as permanent centered labels when a single
  // country is selected (readable at that zoom; the world view relies on hover).
  // A layer whose geometry is missing or degenerate yields empty bounds, and
  // Leaflet reports their centre as (0,0) — Null Island, in the Gulf of Guinea
  // south of Ghana. Anything built from those bounds lands there: a trimester
  // label stranded in the Atlantic, and — worse — a fitBounds stretched from the
  // country to the origin, which looks exactly like "the map didn't zoom".
  // Four DR Congo health zones ship with null geometry today, so this is live,
  // not hypothetical. Both consumers of getBounds() screen for it.
  const usableBounds = (l) => {
    if (typeof l.getBounds !== "function") return null;
    let b;
    try { b = l.getBounds(); } catch { return null; }
    if (!b || !b.isValid()) return null;
    const c = b.getCenter();
    if (!Number.isFinite(c.lat) || !Number.isFinite(c.lng)) return null;
    // An exact (0,0) centre from a zero-area shape is the signature, not a place.
    if (c.lat === 0 && c.lng === 0 && b.getNorth() === b.getSouth()) return null;
    return b;
  };
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
      const lb = usableBounds(l);
      if (!lb) return;
      const dimmed = isDimmed(catOf(r), ADM === "low" ? sevClassOf(r) : null);
      triLabels.addLayer(L.marker(lb.getCenter(), {
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
    // THE CLASS FOLLOWS THE PLAN YEAR. Each cycle carries its own PiN-by-severity
    // distribution in r.sevc, and 29% of units that publish both 2025 and 2026 are
    // classed differently between them — pinning the colour to the newest cycle
    // painted an older year's caseload with the newest year's class. A cycle this
    // unit was not classified in is NOT assessed; it never borrows another year's.
    // The class is carried explicitly ("c"), not inferred from the split: a unit
    // can be assessed and hold no PiN at all — Colombia classifies all 1,122 of its
    // units for 2026 while 672 of them come to zero people in need. "pb" is only
    // the distribution behind the class, for the tooltip.
    const e = sevcOf(r);
    if (e?.c) return { cls: e.c, src: e.a ? "area" : "pin", split: spread(e.pb) };
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
    // "na" is the unclassified bucket — the muted fill on the map. It sits in the
    // cls dimension so it ORs with the numbered classes ("class 4 or not assessed")
    // and still ANDs with forecast category and skill.
    cls: (cat, cls, v) => (v === "na" ? cls == null : cls != null && String(cls) === v),
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
    // A pin cannot survive a change of geometry: the admin-level and source
    // swaps rebuild the payload on different units, and a pcode that is not in
    // the new one would leave the sidebar describing something off the map.
    if (pinnedPcode && !byPcode.has(pinnedPcode)) pinnedPcode = null;
    renderPinHalo();
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
      if (!r) {
        // No row at all: true backdrop, and clear any ring left from a previous
        // render or other countries' category outlines linger.
        el.setAttribute("fill", "#f7f9f9");
        el.setAttribute("stroke", "#d9dedf");
        el.setAttribute("stroke-width", 0.5);
        el.removeAttribute("stroke-dasharray");
        // These are never dimmed — but this branch used to leave the opacity a
        // legend highlight had set, so units that dropped out of scope while an
        // entry was hovered stayed ghosted long after it was released.
        el.setAttribute("fill-opacity", "1");
        el.setAttribute("stroke-opacity", "1");
        ringInfo.set(l.feature.properties.pcode, null);
        return;
      }
      const cat = offCountry ? catAnyOf(r) : catOf(r);
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
      // Two reasons to fade a unit, and they compose. Legend hover dims what does
      // not match the hovered category or class (as on the main Map tab). Country
      // focus fades everything outside the selected country — faded, not blanked:
      // the neighbours keep their real severity colours so the selected country
      // is read in context, and they stay clickable, which is what makes one
      // country reachable from another in a single click.
      const dim = isDimmed(cat, cls);
      el.setAttribute("fill-opacity", dim ? "0.12" : offCountry ? "0.3" : "1");
      el.setAttribute("stroke-opacity", dim ? "0.2" : offCountry ? "0.25" : "1");
      // low_skill's STYLE colour is white — as a ring that reads as a hole;
      // no category ring at all is the honest encoding for "no usable skill".
      ringInfo.set(l.feature.properties.pcode,
        ADM === "low" && cat && cat !== "low_skill"
          ? { cat, fill, dim: dim || offCountry,
              dash: cat.endsWith("_mod") ? "4 7" : null } : null);
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
      // A swatch with no dimension is decoration — nothing to hover, pin or dim by.
      // Without this guard refreshLegendDim() reads pinned[undefined] and throws,
      // taking the whole render with it.
      if (!dim) return;
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
      // The muted fill is a legend entry in its own right, and filterable like the
      // rest: on an IPC projection most of a country can be unclassified (Sudan
      // blanks 140 of 189 units), and "show me only what this cycle did NOT assess"
      // is the question that follows. It lives in the cls dimension as "na", so it
      // ORs with the numbered classes and ANDs with forecast category and skill.
      strip(sevLegendTitle(), [...[1, 2, 3, 4, 5].map((c) => ({
        fill: sevColors()[c - 1], ramp: c - 1, label: String(c),
        border: c <= 2, dim: "cls", val: c,
      })), { fill: HNRP_MUTED.fill, border: true, label: "not assessed",
             dim: "cls", val: "na" }],
        44).querySelector(".lb-title").id = "hnrp-sev-strip-title";
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
  // People reached, from response monitoring, for the selected cycle only.
  const reaOf = (r) => monLive(r)?.[3] ?? null;
  // Does this country report reach at all for the cycle on screen? The column
  // is only worth its width where something fills it, and an all-dash column
  // reads as broken rather than as unreported.
  // The prioritized ("hyper-prioritized") target — a smaller caseload than the
  // plan target, and the one the GHO dashboard headlines. Nationally it runs 0.22
  // of the plan target in Afghanistan and 0.79 in Yemen, so the two are in no
  // sense interchangeable and both are on screen.
  const prioOf = (r) => monLive(r)?.[2] ?? null;
  const anyReached = (rows) => rows.some((r) => reaOf(r) != null);
  const anyPrio = (rows) => rows.some((r) => prioOf(r) != null);
  // Which denominator the reach column should use for this country. A priority of
  // ZERO is a published figure — 1,973 of 3,469 monitored units report one — but
  // it is not something to divide by, so a country where most areas priorititise
  // nothing would get a column of dashes. Use priority only where it actually
  // answers for most of the areas that reported reach; otherwise the plan target
  // does, and the column header says which.
  const prioIsUsefulDen = (rows) => {
    const withReach = rows.filter((r) => reaOf(r) != null);
    if (!withReach.length) return false;
    return withReach.filter((r) => prioOf(r) > 0).length >= 0.5 * withReach.length;
  };
  // Each step of the funnel against the step above it. Null where either side is
  // missing or the denominator is zero — "reached 400 of a priority of 0" is not
  // a percentage, and a missing denominator is not an invitation to borrow the
  // one above: that would make two different ratios look like one series.
  const ratio = (num, den) => (den && num != null ? num / den : null);
  const prioShare = (r) => ratio(prioOf(r), tgtOf(r));
  // Reach is measured against priority where the country publishes one, and
  // against the plan target where it does not (Cameroon, Somalia). Decided ONCE
  // PER COUNTRY and written on the column header, never per unit — a column whose
  // denominator changed row by row would be two ratios wearing one heading.
  let reachDenIsPrio = true;
  const reachDen = (r) => (reachDenIsPrio ? prioOf(r) : tgtOf(r));
  const reachShare = (r) => ratio(reaOf(r), reachDen(r));
  // Deliberately NOT fmtPct, which caps at ">100%": over-delivery against a
  // target is ordinary and interesting (partners report against their own
  // caseloads), and rounding it away to a ceiling hides the very thing worth
  // seeing. A population share over 100% is a data problem; this is not.
  const fmtReachPct = (f) => (f == null ? "–" : `${Math.round(100 * f)}%`);
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

  function renderBarsLegend(anyReachedNow = false, anyPrioNow = false) {
    // PiN mode keys the three bars. It needs no severity ramp: the class number
    // is written in its own swatch in the gutter. IPC mode keys the ramp instead
    // — the bar there is a stack of phases, and only a key says which is which.
    barsLegend.innerHTML =
      (pinMode()
        ? `<span><i style="background:${MON.pin}"></i> PiN</span>` +
          `<span><i style="background:${MON.tgt}"></i> targeted</span>` +
          (anyPrioNow
            ? `<span><i style="background:${MON.prio}"></i> prioritized</span>` : "") +
          (anyReachedNow ? `<span><i style="background:${MON.rea}"></i> reached</span>` : "")
        : sevColors().map((c, i) => (i < barC0() ? ""
            : `<span><i style="background:${c}"></i> ${sevClassLabels()[i]}</span>`)).join("")) +
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
    prioritized: [numCmp((r) => prioOf(r)), false],
    reached: [numCmp((r) => reaOf(r)), false],
    valuePct: [numCmp((r) => shareOfPop(r, sevValOf(r))), false],
    targetedPct: [numCmp((r) => shareOfPop(r, tgtOf(r))), false],
    prioritizedPct: [numCmp((r) => prioShare(r)), false],
    reachedPct: [numCmp((r) => reachShare(r)), false],
  };
  let barSort = "value", barSortFlip = false;

  function renderBars() {
    const country = countrySel.value;
    // Fix the reach denominator BEFORE anything sorts by it, and from the
    // country's whole row set rather than the filtered one — a legend pin should
    // not silently change what a percentage is measured against.
    const allOfCountry = country ? data.rows.filter((r) => r.country === country) : [];
    reachDenIsPrio = pinMode() && prioIsUsefulDen(allOfCountry);
    // Switching to IPC drops the targeted columns; a sort left pointing at one
    // would order the chart by a quantity no longer on screen.
    if (!pinMode() && barSort.startsWith("targeted")) { barSort = "value"; barSortFlip = false; }
    let [cmp, isText] = BAR_SORTS[barSort] ?? BAR_SORTS.value;
    const rows = country
      ? data.rows.filter((r) => r.country === country
            && (pinMode() ? sevValOf(r) != null : sevTotOf(r) > 0 && segsOf(r))
            && !isFilteredOut(catOf(r), clsOf(r)))
          .sort(barSortFlip ? (a, b) => cmp(b, a) : cmp)
      : [];
    // Reached is only a column where the country reports it for this cycle, so
    // a sort left on it after a country or plan-year change has nothing to
    // order by — fall back rather than silently keeping the previous order.
    if (barSort.startsWith("reached") && !(pinMode() && anyReached(rows))) {
      barSort = "value";
      barSortFlip = false;
      [cmp, isText] = BAR_SORTS.value;
      rows.sort(cmp);
    }
    renderBarsLegend(pinMode() && anyReached(rows), pinMode() && anyPrio(rows));
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
    // rather than leaving a column of dashes to be read as missing data. Where
    // the targets come from response monitoring rather than the needs analysis,
    // say THAT instead: the figures are the same measure but a different source
    // and a different vintage, and a reader comparing to HAPI should know.
    const monTgtRows = rows.filter((r) => tgtSrcOf(r) === "response monitoring");
    noTgtEl.hidden = ipcMode() || yearsWithTgt.has(planYr());
    noTgtEl.textContent = `No targeted figures published for the HNRP ${planYr()} cycle yet` +
      ` — its subnational figures come from the needs analysis, which carries PiN only.` +
      (cycYears.some((y) => yearsWithTgt.has(String(y)))
        ? ` Pick an earlier plan year to see targeting.` : "");
    if (noTgtEl.hidden && monTgtRows.length) {
      const months = [...new Set(rows.map(monMonth).filter(Boolean))];
      noTgtEl.hidden = false;
      noTgtEl.textContent =
        `Targeting and reach for ${planYr()} come from OCHA's response monitoring` +
        ` (the needs analysis publishes no subnational target for this cycle)` +
        `${months.length ? `, as last reported in ${months.join(", ")}` : ""}.` +
        ` Reach is what partners attributed to an area — country totals are higher.`;
    }

    const W = barsSvg.parentElement.clientWidth || 900;
    // Left gutter holds three labelled columns: forecast swatch, severity
    // square, admin name. Column starts are set by the HEADER widths, not the
    // 13px swatches — "Intersectoral severity" is the widest thing here.
    const COL = { cat: 2, sev: 108, name: 226 };
    // t leaves room for a two-line header: the label at t-22, its denominator at
    // t-10. Applied whether or not a sub-label is present, so the grid and the
    // first row do not shift as columns come and go.
    const ROW = 26, M = { l: 392, r: 24, t: 40, b: 34 };
    // Caseload columns, GHO-dashboard style (as on the Country alerts tab): a
    // fixed-width track = the area's whole population, filled to the share this
    // caseload takes, with the headcount printed beside it. Fixed tracks, not one
    // scale across rows: the question here is "how much of this area", and the
    // absolute figure is right there in the label.
    // Numeric columns on the right — headcount and share, each sortable; the bar
    // takes whatever is left. TARGETED IS PLAN DATA: it comes from the HNRP cycle
    // for the selected plan year, so it has no business on an IPC chart, where
    // the plan year is not even a visible control. IPC mode shows two columns.
    const showReached = pinMode() && anyReached(rows);
    // The priority COLUMN appears wherever the country publishes the figure at
    // all; only the reach denominator needs it to be usefully non-zero.
    const showPrio = pinMode() && anyPrio(allOfCountry);
    // [key, label, denominator sub-label]. Every share column names what it is a
    // share OF, on a second line: they do NOT share a denominator — caseload and
    // targeting are read against the area's population, delivery against what was
    // targeted. Three columns ending in "%" meaning three different things is
    // exactly the sort of thing a reader is entitled to assume away.
    const NUM_COLS = [
      ["value", pinMode() ? "PiN" : sevLabel()],
      ...(pinMode() ? [["targeted", "Target"]] : []),
      ...(showPrio ? [["prioritized", "Priority", "target"]] : []),
      ...(showReached ? [["reached", "Reached"]] : []),
      ...(showPrio ? [["prioritizedPct", "Priority %", "of target"]] : []),
      ...(showReached ? [["reachedPct", "Reached %",
                          showPrio ? "of priority" : "of target"]] : []),
      ["valuePct", `${pinMode() ? "PiN" : sevLabel()} %`, "of population"],
      ...(pinMode() ? [["targetedPct", "Target %", "of population"]] : []),
    ];
    // Column width falls back from its preferred size until the bar keeps at
    // least MINBAR. A fixed width plus the bar's own minimum meant the two could
    // both be honoured only by overlapping — at 900px the six-column block ran
    // 6px into the bar. Floor at 50px, below which a header no longer fits its
    // own column and the honest answer is that the viewport is too narrow.
    const MINBAR = 80;
    const prefW = NUM_COLS.length > 5 ? 62 : NUM_COLS.length > 4 ? 68 : 72;
    const NGAP = 4;
    const room = W - M.r - M.l - 18 - MINBAR;   // width the columns may take
    const widthFor = (n) => Math.max(50, Math.min(prefW,
      Math.floor((room - (n - 1) * NGAP) / n)));
    const fits = (n) => n * widthFor(n) + (n - 1) * NGAP <= room;
    // Too narrow even at the floor: shed the population-share columns rather than
    // draw the numbers over the bars. Counts and reach-against-target survive —
    // they answer the questions the chart exists for; "% of the area's population"
    // is the one a reader can do in their head from the count and the base.
    for (const key of ["targetedPct", "valuePct"]) {
      if (fits(NUM_COLS.length)) break;
      const i = NUM_COLS.findIndex((c) => c[0] === key);
      if (i < 0) continue;
      NUM_COLS.splice(i, 1);
      if (barSort === key) {
        // Re-sort, not just relabel: the rows were ordered by this column before
        // it was dropped, and leaving them would put the chart in an order no
        // visible header claims.
        barSort = "value";
        barSortFlip = false;
        rows.sort(BAR_SORTS.value[0]);
      }
    }
    const NUMS = NUM_COLS.length;
    const NUMW = widthFor(NUMS);
    const NUMBLOCK = NUMS * NUMW + (NUMS - 1) * NGAP;
    const numRight = (i) => W - M.r - NUMBLOCK + i * (NUMW + NGAP) + NUMW;
    const barRight = Math.max(M.l + MINBAR, W - M.r - NUMBLOCK - 18);
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
    // Reached counts toward the scale: it is not bounded by PiN or by targeted
    // (partners report against their own caseloads), and leaving it out let a
    // reach bar run off the end of the plot area.
    const xmax = Math.max(...rows.map((r) => Math.max(shownSum(r),
      pinMode() ? (tgtOf(r) ?? 0) : 0, pinMode() ? (reaOf(r) ?? 0) : 0)), 1) * 1.04;
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
    function header(x, key, label, anchor = "start", swatch = null, sub = null) {
      // Two lines when the column needs its denominator spelled out; the sub-line
      // is part of the same click target, since a reader aiming at "of target" is
      // aiming at that column.
      const t = g("text", { x, y: M.t - (sub ? 22 : 10), "font-size": 11,
                            "text-anchor": anchor,
                            fill: barSort === key ? "#1d2021" : "#555",
                            "font-weight": barSort === key ? 600 : 400 });
      if (sub) {
        const s = g("text", { x, y: M.t - 10, "font-size": 9, "text-anchor": anchor,
                              fill: "#8b9899" });
        s.textContent = sub;
        s.style.cursor = "pointer";
        s.addEventListener("click", () => {
          if (barSort === key) barSortFlip = !barSortFlip;
          else { barSort = key; barSortFlip = false; }
          renderBars();
        });
      }
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
      // A swatch, not coloured label text: reached is a pale pink that is fine
      // as a 5px bar and illegible as 11px type, and darkening it for the label
      // would stop it matching the bar it names — which is the whole point.
      // Placed by MEASURED text width, so it tracks the sort arrow appearing.
      if (swatch) {
        // getComputedTextLength returns 0 while the tab is display:none, so keep
        // an estimate to fall back on — a swatch a few pixels off beats none.
        const w = t.getComputedTextLength?.() || t.textContent.length * 5.6;
        g("rect", { x: anchor === "end" ? x - w - 9 : x, y: M.t - 16,
                    width: 6, height: 6, rx: 1.5, fill: swatch });
      }
      return t;
    }
    // Which numeric columns name a bar, and in which colour.
    const HEADER_SWATCH = { value: MON.pin, targeted: MON.tgt,
                          prioritized: MON.prio, reached: MON.rea };
    header(COL.cat, "forecast", "Forecast category");
    header(COL.sev, "severity", sevClassTitle());
    header(COL.name, "name", "Admin name");
    // The bar itself is not a sort target — the four numeric columns are, one
    // per quantity it draws (headcount and share, caseload and targeted).
    const capt = g("text", { x: M.l, y: M.t - 10, "font-size": 11, fill: "#555" });
    if (pinMode()) {
      // Each word in its bar's colour, so the caption is its own key. The pale
      // reached pink needs a darker cousin to be readable as type — it names the
      // bar rather than reproducing it, which the swatches above already do.
      const parts = [["PiN", MON.pin], ["targeted", "#c96b6b"],
                     ...(showPrio ? [["prioritized", MON.prio]] : []),
                     ...(showReached ? [["reached", MON.rea]] : [])];
      parts.forEach(([word, col], i) => {
        if (i) {
          const sep = document.createElementNS(NS, "tspan");
          sep.setAttribute("fill", "#9db1b3");
          sep.textContent = " · ";
          capt.appendChild(sep);
        }
        const ts = document.createElementNS(NS, "tspan");
        ts.setAttribute("fill", col);
        ts.setAttribute("font-weight", "600");
        ts.textContent = word;
        capt.appendChild(ts);
      });
      const tail = document.createElementNS(NS, "tspan");
      tail.textContent = " (bars)";
      capt.appendChild(tail);
    } else {
      capt.textContent = "IPC phases (bar)";
    }
    NUM_COLS.forEach(([key, label, sub], i) =>
      header(numRight(i), key, label, "end",
             pinMode() ? HEADER_SWATCH[key] : null, sub));
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
      const val = sevValOf(r), tgt = tgtOf(r), rea = reaOf(r);
      if (pinMode()) {
        // Three bars, one per quantity, in the monitoring palette — not one bar
        // plus a tick plus a foot. PiN, targeted and reached are three
        // measurements of the same area and comparing them is the whole point;
        // a tick could only mark a threshold on someone else's bar, and it could
        // not sit past the end of one, which reach regularly does.
        // Severity has not left the chart — it is the numbered swatch in the
        // gutter, which states the class rather than asking anyone to read it
        // off a five-step ramp.
        const BH = 4, BGAP = 1;           // 4*4 + 3*1 = 19px, inside the 26px row
        const prio = prioOf(r);
        const asOf = `HNRP ${r.mon_yr} response` +
          `${monMonth(r) ? `, as of ${monMonth(r)}` : ""}`;
        const pct = (num, den, what) =>
          (den && num != null ? ` · ${Math.round((100 * num) / den)}% of ${what}` : "");
        const series = [
          ["PiN" + secTag(), val, MON.pin,
           cls ? sevClassDesc(r) : "no severity published for this area"],
          ["targeted", tgt, MON.tgt, tgtSrcOf(r)],
          ["prioritized target", prio, MON.prio, asOf + pct(prio, tgt, "targeted")],
          ["reached", rea, MON.rea,
           asOf + pct(rea, reachDen(r), reachDenIsPrio ? "prioritized" : "targeted")],
        ];
        series.forEach(([label, v, col, note], i) => {
          // A null draws nothing (not reported); a published zero still draws its
          // hairline at the origin, so the two are never the same mark.
          if (v == null) return;
          titled(gr("rect", { x: X(0), y: y + 4 + i * (BH + BGAP),
                              width: Math.max(X(v) - X(0), 0.5), height: BH,
                              fill: col }),
            `${r.name ?? r.pcode} — ${label}: ${fmtN(v)} · ${pctTxt(v)}` +
            (note ? ` · ${note}` : ""));
        });
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
      // ── The same quantities as figures, one sortable column each ─────────
      // Keyed off NUM_COLS so the cells stay aligned with the headers when IPC
      // mode drops the two targeted columns — an index-based list silently
      // shifted the shares under the wrong heading.
      const CELL = {
        value: [val, fmtSI(val), pctTxt(val)],
        targeted: [tgt, tgt == null ? "–" : fmtSI(tgt),
                   tgt == null ? "no target published for this area"
                     : `${pctTxt(tgt)} · ${tgtSrcOf(r)}`],
        // A dash here is "this area reported no response", which is not the same
        // claim as zero people reached — the hover says so in words.
        reached: [rea, rea == null ? "–" : fmtSI(rea),
                  rea == null ? "no response reported for this area"
                    : `${pctTxt(rea)}${tgt ? ` · ${Math.round((100 * rea) / tgt)}% of targeted` : ""}`],
        valuePct: [share(val), fmtPct(share(val)), pctTxt(val)],
        targetedPct: [share(tgt), tgt == null ? "–" : fmtPct(share(tgt)), pctTxt(tgt)],
        prioritized: [prioOf(r), prioOf(r) == null ? "–" : fmtSI(prioOf(r)),
                      prioOf(r) == null
                        ? "no prioritized target published for this area"
                        : `${pctTxt(prioOf(r))}${tgt ? ` · ${Math.round(
                            (100 * prioOf(r)) / tgt)}% of the plan target` : ""}`],
        prioritizedPct: [prioShare(r), prioShare(r) == null ? "–"
                           : fmtReachPct(prioShare(r)),
                         prioShare(r) == null
                           ? "no prioritized target published for this area"
                           : `${fmtN(prioOf(r))} prioritized of ${fmtN(tgt)} targeted`],
        reachedPct: [reachShare(r), fmtReachPct(reachShare(r)),
                     reachShare(r) == null
                       ? (rea == null ? "no response reported for this area"
                          : `no ${reachDenIsPrio ? "prioritized target" : "target"}` +
                            ` published to measure it against`)
                       : `${fmtN(rea)} reached of ${fmtN(reachDen(r))}` +
                         ` ${reachDenIsPrio ? "prioritized" : "targeted"}` +
                         `${reachShare(r) > 1 ? " — more people reached than the"
                           + " caseload, which partners do report" : ""}`],
      };
      NUM_COLS.map(([k]) => [...CELL[k], k]).forEach(([v, text, tip, key], i) => {
        // ">100%" is a flag, not a figure — mute it like a missing value.
        const flagged = v == null || (key.endsWith("Pct") && v > 1);
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

  let fitToken = 0;  // a newer fit always supersedes an older one's backstop
  function fitCountry(animate = true) {
    const c = countrySel.value;
    let bounds = null;
    layer.eachLayer((l) => {
      const r = byPcode.get(l.feature.properties.pcode);
      if (c && (!r || r.country !== c)) return;
      const lb = usableBounds(l);
      if (!lb) return;  // never let a degenerate shape drag the fit to (0,0)
      // COPY, never alias. L.latLngBounds(x) returns x itself when x is already a
      // LatLngBounds, and Polygon.getBounds() hands back the layer's CACHED _bounds
      // — so seeding the accumulator with it made every extend() permanently
      // enlarge that layer's own bounds. One fit over the world view was enough to
      // stretch the first layer it touched to the whole map, for good.
      // That layer is Afghanistan's first unit, because AFG sorts first in the
      // payload, which is why only Afghanistan failed and only after another
      // country had been shown: the world fit in between did the damage. The same
      // corrupted bounds put its trimester label at the world centre — in the
      // Atlantic just south of Ghana.
      bounds = bounds ? bounds.extend(lb)
        : L.latLngBounds(lb.getSouthWest(), lb.getNorthEast());
    });
    if (!bounds) return;
    // Keep the country clear of the sidebar, which overlays the map's right edge
    // — otherwise selecting one fits it neatly underneath the panel describing it.
    // Measured, not assumed: the panel drops below the map on narrow viewports.
    const sideW = sideEl.hidden || sideEl.offsetParent === null ? 0
      : sideEl.getBoundingClientRect().width;
    const PAD = { paddingTopLeft: [10, 10], paddingBottomRight: [10 + sideW, 10] };
    map.stop();
    // A hidden tab gets no requestAnimationFrame, and every animated view change
    // in Leaflet — flyTo and the CSS zoom animation alike — is driven by it. Asking
    // for a glide there leaves the map exactly where it started until the tab is
    // looked at again. Fit instantly instead; there is nobody watching the motion.
    if (!animate || document.visibilityState !== "visible") {
      // animate:false explicitly — the default still animates, and an animation is
      // exactly what cannot finish here.
      map.fitBounds(bounds, { ...PAD, animate: false });
      return;
    }
    // flyToBounds rather than fitBounds({animate:true}): with zoomSnap 0.25 the map
    // runs on fractional zoom, and flyTo drives the view from rAF rather than the
    // CSS zoom animation, which is the more reliable of the two paths here.
    map.flyToBounds(bounds, { ...PAD, duration: 0.35, easeLinearity: 0.4 });
    // Land it anyway if the glide is interrupted — a newer selection always wins.
    const tz = map.getBoundsZoom(bounds, false, L.point(10, 10));
    fitToken += 1;
    const token = fitToken;
    setTimeout(() => {
      if (token !== fitToken || countrySel.value !== c) return;
      if (Math.abs(map.getZoom() - tz) > 0.5
          || !map.getBounds().contains(bounds.getCenter())) {
        map.stop();
        map.fitBounds(bounds, { ...PAD, animate: false });
      }
    }, 600);
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
    syncCountryHit();
    aggCache.clear(); // its key covers the controls, but the payload can swap too
    renderMap(); renderBars(); renderSide();
  }
  for (const el of [skillSel, rpSel, srcLvlSel, triSel, ipcPeriodSel, planYrSel]) {
    el.addEventListener("change", renderAll);
  }
  // Changing the source changes the GEOMETRY, not just the colours: the plan view
  // and the IPC view are drawn on different units. Reload with the other payload —
  // the same plain navigation the ?adm= switch uses, rather than tearing down and
  // rebuilding three Leaflet layers in place. Every control is already in the URL,
  // so the only thing the user loses is the map's pan/zoom.
  // Switching source swaps the SUBNATIONAL geometry — the plan and IPC views are
  // drawn on different units — but everything at country level is the same in both:
  // the world backdrop, the country borders, the hit layer, and the map's own view.
  // So swap the two payload-derived layers in place and leave the rest standing,
  // rather than reloading the page and rebuilding the world from scratch.
  let swapping = false;
  srcTypeSel.addEventListener("change", async () => {
    const want = (ADM === "low" && srcTypeSel.value === "ipc") ? "ipc" : ADM;
    if (want === PAYLOAD || swapping) { renderAll(); return; }
    swapping = true;
    mapEl.classList.add("swapping");
    try {
      const [nd, ng] = await Promise.all(ADM_FILES[want].map(
        (f) => fetch(f, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : Promise.reject(r)))));
      data = nd; geo = ng; PAYLOAD = want;
      byPcode = new Map(data.rows.map((r) => [r.pcode, r]));
      // Rebuild only what the payload draws. Clip defs are keyed by pcode and the
      // old paths are about to be discarded, so drop them with their owner.
      map.removeLayer(layer); map.removeLayer(ringCat);
      document.querySelectorAll("defs.hnrp-clips").forEach((d) => d.remove());
      clipPaths = {};
      layer = makeLayer().addTo(map);
      ringCat = makeRing().addTo(map);
      bordersLayer.bringToFront();   // borders stay above the new mosaic
      fillCountries();
      buildPlanYears();
      buildYearsWithTgt();
      renderAll();
      // The view is already where the user left it; only re-fit if a country is
      // selected, because its units just changed shape.
      if (countrySel.value) fitCountry(false);
      history.replaceState(null, "", stateURL());
    } catch {
      location.href = stateURL();  // fetch failed — fall back to a clean reload
      return;
    } finally {
      swapping = false;
      mapEl.classList.remove("swapping");
    }
  });
  // finally: whatever happens during re-render, the zoom step must still run.
  countrySel.addEventListener("change", () => {
    // A pin belongs to the country it was made in; changing country drops it.
    // Cleared BEFORE the render so the sidebar and the map agree on the first
    // frame rather than briefly describing a unit that is no longer on screen.
    pinnedPcode = null;
    hoverPcode = null;
    try { renderAll(); } finally { fitCountry(); }
  });

  // Hidden-panel sizing: (re)fit when the tab becomes visible.
  window.tabShown = window.tabShown || {};
  window.tabShown.hnrp = () => {
    map.invalidateSize();
    fitCountry(false); // instant on reveal — nothing to glide from
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
