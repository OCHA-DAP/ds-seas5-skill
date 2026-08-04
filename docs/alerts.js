// Country alerts tab (#alerts, unlisted): one summary line per country × hazard —
// the worst SEAS5 signal across ALL valid rainy-season trimesters (in-season
// included) over the country's admin-1 units, with the country's HNRP PiN for
// scale. Pure table, no map; reads the same adm1 payload as the HNRP tab.
(async function () {
  let data;
  try {
    data = await fetch("data/hnrp_drought.json", { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r)));
  } catch {
    return; // payload not built yet — leave the tab empty
  }
  const TH = data.thresholds; // don't lean on app.js's global T (set async from index.json)
  const vsevM = 100 / TH.vsev_rp, sevM = 100 / TH.sev_rp;

  const skillSel = document.getElementById("alerts-skill");
  const sevSel = document.getElementById("alerts-sev");
  const belowChk = document.getElementById("alerts-below");
  const aboveChk = document.getElementById("alerts-above");
  const hnrpChk = document.getElementById("alerts-hnrp");

  // Country PiN/population, summed over adm1 units, computed once. pin = full
  // PiN sum; the % denominator is coverage-matched (units carrying BOTH pin and
  // pop) — population is missing for many adm1 units (Somalia has it on 6 of
  // 18), and an all-units ratio overstates wildly (147% of pop).
  const agg = new Map();
  for (const r of data.rows) {
    const a = agg.get(r.country) ?? { pin: null, pairPin: 0, pairPop: 0 };
    if (r.pin != null) a.pin = (a.pin ?? 0) + r.pin;
    if (r.pin != null && r.pop != null) { a.pairPin += r.pin; a.pairPop += r.pop; }
    agg.set(r.country, a);
  }

  // Worst signal per country × hazard under the CURRENT skill/severity filters
  // (the filters change which cells qualify, so "worst" must be recomputed —
  // under high-skill-only a country's best may fall to a weaker-severity cell).
  // "Worst" ranks severity band, then skill band, then raw return period.
  function bestSignals() {
    const rMin = skillSel.value === "high" ? TH.r_high : TH.r_mod;
    const highOnly = sevSel.value === "high";
    const best = new Map(); // "country|hazard" -> candidate
    for (const r of data.rows) {
      for (const [tri, t] of Object.entries(r.tris ?? {})) {
        if (t.pct == null || t.r == null || !t.rainy || t.r < rMin) continue;
        const high = t.pct <= vsevM || t.pct >= 100 - vsevM;
        const modr = !high && (t.pct <= sevM || t.pct >= 100 - sevM);
        if (!high && (highOnly || !modr)) continue;
        const hazard = t.pct < 50 ? "drought" : "flood";
        const cand = { sev: high ? 2 : 1, skill: t.r >= TH.r_high ? 2 : 1,
                       rp: t.rp ?? 0, r: t.r, tri, lead: t.lead, unit: r.name };
        const k = r.country + "|" + hazard;
        const cur = best.get(k);
        const better = !cur || cand.sev > cur.sev
          || (cand.sev === cur.sev && (cand.skill > cur.skill
              || (cand.skill === cur.skill && cand.rp > cur.rp)));
        if (better) best.set(k, cand);
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
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

  document.getElementById("alerts-title").textContent =
    `SEAS5 precipitation alerts — forecast issued ${data.issued_label}, ` +
    `worst signal per country (in-season included)`;
  const thead = document.querySelector("#alerts-table thead");
  thead.innerHTML = `<tr><th>Country</th><th>Forecast severity</th><th>Valid trimester</th>` +
    `<th>Forecast skill</th><th class="num">PiN</th><th class="num">PiN (% of pop.)</th></tr>`;
  const tbody = document.querySelector("#alerts-table tbody");

  function render() {
    const best = bestSignals();
    const rows = [...best.entries()].map(([k, c]) => {
      const [country, hazard] = k.split("|");
      // * = the same country ALSO has a qualifying signal in the opposite
      // direction (under the current skill/severity filters, regardless of
      // which direction checkboxes are ticked).
      const opp = best.has(country + "|" + (hazard === "drought" ? "flood" : "drought"));
      return { country, hazard, opp, ...c, ...agg.get(country) };
    }).filter((r) =>
      (r.hazard === "drought" ? belowChk.checked : aboveChk.checked)
      && (!hnrpChk.checked || r.pin != null));
    rows.sort((a, b) => b.sev - a.sev || b.skill - a.skill || (b.pin ?? -1) - (a.pin ?? -1));

    tbody.innerHTML = "";
    for (const r of rows) {
      const [label, bg, ink] = SEV_CHIP[r.hazard][r.sev];
      const detail = `RP ${r.rp.toFixed(1)} yr, r ${r.r.toFixed(2)} — worst unit: ${r.unit}`;
      const pct = r.pin != null && r.pairPop ? `${((100 * r.pairPin) / r.pairPop).toFixed(1)}%` : "–";
      // "Sudan (the)", "Mali (le)": HPC plan names carry the article — drop it.
      const cname = r.country.replace(/\s*\((the|la|le|el|los|las)\)$/i, "");
      const tr = document.createElement("tr");
      tr.insertAdjacentHTML("beforeend",
        `<td>${esc(cname)}${r.opp ? "*" : ""}</td>` +
        `<td><span class="sev-chip" style="background:${bg};color:${ink}" title="${esc(detail)}">` +
        `${label}</span></td>` +
        `<td>${r.tri}${r.lead < 0 ? ` <span class="in-season-tag">in season</span>` : ""}</td>` +
        `<td class="${r.skill === 2 ? "skill-hi" : "skill-mod"}">${r.skill === 2 ? "High" : "Moderate"}</td>` +
        `<td class="num">${fmtN(r.pin)}</td>` +
        `<td class="num">${pct}</td>`);
      tbody.appendChild(tr);
    }
    document.getElementById("alerts-empty").hidden = rows.length > 0;
    document.getElementById("alerts-star-note").hidden = !rows.some((r) => r.opp);
  }

  for (const el of [skillSel, sevSel, belowChk, aboveChk, hnrpChk]) {
    el.addEventListener("change", render);
  }
  render();
})();
