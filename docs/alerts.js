// Country alerts tab (#alerts, unlisted): one summary line per country × hazard —
// the worst SEAS5 signal among the COUNTRY-LEVEL trimesters (data/forecast.json,
// the exact stats behind the Map tab's country view — NOT aggregated from admin
// units, which flags a country on one extreme state), with the country's HNRP
// plan-headline PiN for scale. Pure table, no map.
(async function () {
  let fc, planCl, world;
  try {
    [fc, planCl, world] = await Promise.all([
      fetch("data/forecast.json", { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : Promise.reject(r))),
      // Country PiN comes from the HPC plan HEADLINES (export_plan_caseloads.py):
      // the admin-level mirror lags the plan cycle (2025 while 2026 plans are
      // out). Optional — PiN shows "–" if this file is missing.
      fetch("data/plan_caseloads.json", { cache: "no-cache" })
        .then((r) => (r.ok ? r.json() : { plans: {} }))
        .then((d) => d.plans ?? {})
        .catch(() => ({})),
      fetch("data/countries.geojson") // fallback display names for non-HNRP countries
        .then((r) => (r.ok ? r.json() : { features: [] }))
        .catch(() => ({ features: [] })),
    ]);
  } catch {
    return; // data files not built yet — leave the tab empty
  }
  const TH = fc.thresholds;
  const vsevM = 100 / TH.vsev_rp, sevM = 100 / TH.sev_rp;
  const geoName = new Map(world.features.map((f) => [f.properties.iso3, f.properties.name]));
  const nameOf = (iso3) => planCl[iso3]?.plan_name ?? geoName.get(iso3) ?? iso3;

  // Signed lead from the issue month (matches src/skill.py trimester_lead):
  // MJJ issued July -> -2 (in season), JAS -> 0, NDJ -> 4.
  const TRI_START = { JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6,
                      JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 12 };
  const leadOf = (tri) => {
    const o = (TRI_START[tri] - fc.issued_month + 12) % 12;
    return o >= 10 ? o - 12 : o;
  };

  const skillSel = document.getElementById("alerts-skill");
  const sevSel = document.getElementById("alerts-sev");
  const belowChk = document.getElementById("alerts-below");
  const aboveChk = document.getElementById("alerts-above");
  const rainyChk = document.getElementById("alerts-rainy");
  const hnrpChk = document.getElementById("alerts-hnrp");
  const oldChk = document.getElementById("alerts-old");

  // Worst signal per country × hazard under the CURRENT filters (the filters
  // change which trimesters qualify, so "worst" must be recomputed — under
  // high-skill-only a country's best may fall to a weaker-severity trimester).
  // "Worst" ranks severity band, then skill band; among ties a season still
  // ahead beats one already in progress (in-season trimesters are shown only
  // when nothing else qualifies at that tier), then raw return period.
  function beats(a, b) {
    if (a.sev !== b.sev) return a.sev > b.sev;
    if (a.skill !== b.skill) return a.skill > b.skill;
    const af = a.lead >= 0, bf = b.lead >= 0;
    if (af !== bf) return af;
    return a.rp > b.rp;
  }
  function bestSignals() {
    const rMin = skillSel.value === "high" ? TH.r_high : TH.r_mod;
    const highOnly = sevSel.value === "high";
    const best = new Map(); // "iso3|hazard" -> candidate
    for (const [iso3, tris] of Object.entries(fc.data)) {
      for (const [tri, t] of Object.entries(tris)) {
        if (t.pct == null || t.r == null || t.r < rMin) continue;
        if (rainyChk.checked && !t.rainy) continue;
        const high = t.pct <= vsevM || t.pct >= 100 - vsevM;
        const modr = !high && (t.pct <= sevM || t.pct >= 100 - sevM);
        if (!high && (highOnly || !modr)) continue;
        const hazard = t.pct < 50 ? "drought" : "flood";
        const cand = { sev: high ? 2 : 1, skill: t.r >= TH.r_high ? 2 : 1,
                       rp: t.rp ?? 0, r: t.r, tri, lead: leadOf(tri) };
        const k = iso3 + "|" + hazard;
        const cur = best.get(k);
        if (!cur || beats(cand, cur)) best.set(k, cand);
      }
    }
    return best;
  }

  // Severity chips wear the map's category labels and palette (dark = strong).
  const SEV_CHIP = {
    drought: { 2: ["Strongly below normal", "#7f5619", "#ffffff"],
               1: ["Below normal", "#dda555", "#3d2b0d"] },
    flood: { 2: ["Strongly above normal", "#134ead", "#ffffff"],
             1: ["Above normal", "#74a1e8", "#0d2c5c"] },
  };
  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  // Compact figures for the bar cells, GHO-dashboard style (21.9M, 640k).
  const fmtM = (v) => (v == null ? "–"
    : v >= 995e4 ? `${(v / 1e6).toFixed(0)}M`
    : v >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
    : v >= 1e3 ? `${Math.round(v / 1e3)}k` : String(Math.round(v)));
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  // In-cell horizontal bar (as on the GHO operations dashboard): fill scaled to
  // `frac` of the track, value printed right of the track.
  const barCell = (frac, text, color, title = "") =>
    `<td class="num" title="${esc(title)}"><span class="cellbar">` +
    `<span class="bar-track"><span class="bar-fill" style="width:${(100 * Math.max(0, Math.min(1, frac))).toFixed(1)}%;` +
    `background:${color}"></span></span><span class="bar-val">${text}</span></span></td>`;

  // Latest plan year across the headline caseloads: rows on an OLDER plan get a
  // visible year mark so mixed vintages are never silent. Plans older than the
  // previous cycle (e.g. Ethiopia's 2024 HRP) are hidden entirely unless the
  // "include older plans" box is ticked.
  const latestPlanYr = Math.max(0, ...Object.values(planCl).map((c) => c.plan_year));
  const oldestPlanYr = Math.min(latestPlanYr, ...Object.values(planCl).map((c) => c.plan_year));
  const oldYrEl = document.getElementById("alerts-old-yr");
  if (oldYrEl) oldYrEl.textContent = String(oldestPlanYr);

  document.getElementById("alerts-title").textContent =
    `SEAS5 precipitation alerts — forecast issued ${fc.issued_label}, ` +
    `worst country-level signal (in-season included)`;
  const thead = document.querySelector("#alerts-table thead");
  thead.innerHTML = `<tr><th>Country</th><th>Forecast severity</th><th>Valid trimester</th>` +
    `<th>Forecast skill</th><th class="num">PiN</th><th class="num">PiN (% of pop.)</th></tr>`;
  const tbody = document.querySelector("#alerts-table tbody");

  function render() {
    const best = bestSignals();
    const rows = [...best.entries()].map(([k, c]) => {
      const [iso3, hazard] = k.split("|");
      // * = the same country ALSO has a qualifying signal in the opposite
      // direction (under the current skill/severity filters, regardless of
      // which direction checkboxes are ticked).
      const opp = best.has(iso3 + "|" + (hazard === "drought" ? "flood" : "drought"));
      let cl = planCl[iso3];
      // Older-than-last-cycle plans don't count as "having a plan" unless opted in.
      if (cl && cl.plan_year < latestPlanYr - 1 && !oldChk.checked) cl = undefined;
      return { iso3, hazard, opp, ...c, cl, pinShow: cl ? cl.pin : null };
    }).filter((r) =>
      (r.hazard === "drought" ? belowChk.checked : aboveChk.checked)
      && (!hnrpChk.checked || r.cl));
    rows.sort((a, b) => b.sev - a.sev || b.skill - a.skill
      || (b.pinShow ?? -1) - (a.pinShow ?? -1) || a.iso3.localeCompare(b.iso3));

    tbody.innerHTML = "";
    const maxPin = Math.max(1, ...rows.map((r) => r.pinShow ?? 0)); // bar scale
    for (const r of rows) {
      const [label, bg, ink] = SEV_CHIP[r.hazard][r.sev];
      const detail = `Country-mean forecast percentile ` +
        `— RP ${r.rp.toFixed(1)} yr, r ${r.r.toFixed(2)}`;
      const frac = r.cl && r.cl.pop ? r.cl.pin / r.cl.pop : null;
      const pinTip = r.cl
        ? `${fmtN(r.cl.pin)} — ${r.cl.plan_name} ${r.cl.plan_year} plan headline (HPC API)` : "";
      const yrTag = r.cl && r.cl.plan_year < latestPlanYr
        ? ` <span class="in-season-tag">${r.cl.plan_year}</span>` : "";
      const tr = document.createElement("tr");
      tr.insertAdjacentHTML("beforeend",
        `<td>${esc(nameOf(r.iso3))}${r.opp ? "*" : ""}</td>` +
        `<td><span class="sev-chip" style="background:${bg};color:${ink}" title="${esc(detail)}">` +
        `${label}</span></td>` +
        `<td>${r.tri}${r.lead < 0 ? ` <span class="in-season-tag">in season</span>` : ""}</td>` +
        `<td class="${r.skill === 2 ? "skill-hi" : "skill-mod"}">${r.skill === 2 ? "High" : "Moderate"}</td>` +
        (r.pinShow == null ? `<td class="num">–</td>`
          : barCell(r.pinShow / maxPin, fmtM(r.pinShow) + yrTag, "#418fde", pinTip)) +
        (frac == null ? `<td class="num">–</td>`
          : barCell(frac, `${(100 * frac).toFixed(1)}%`, "#ef7a93")));
      tbody.appendChild(tr);
    }
    document.getElementById("alerts-empty").hidden = rows.length > 0;
    document.getElementById("alerts-star-note").hidden = !rows.some((r) => r.opp);
  }

  for (const el of [skillSel, sevSel, belowChk, aboveChk, rainyChk, hnrpChk, oldChk]) {
    el.addEventListener("change", render);
  }
  render();
})();
