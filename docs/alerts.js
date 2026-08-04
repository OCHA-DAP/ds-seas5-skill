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

  // Worst signal per country × hazard. "Worst" ranks severity band first, then
  // skill band, then raw return period — mirroring how the alert table is read
  // (High/High rows above High/Moderate above Moderate/Moderate).
  const best = new Map(); // "country|hazard" -> candidate
  const agg = new Map();  // country -> { pin, pop } summed over adm1 units
  const vsevM = 100 / TH.vsev_rp, sevM = 100 / TH.sev_rp;
  for (const r of data.rows) {
    // pin = full PiN sum; the % denominator is coverage-matched (units carrying
    // BOTH pin and pop) — population is missing for many adm1 units (Somalia has
    // it on 6 of 18), and an all-units ratio overstates wildly (147% of pop).
    const a = agg.get(r.country) ?? { pin: null, pairPin: 0, pairPop: 0 };
    if (r.pin != null) a.pin = (a.pin ?? 0) + r.pin;
    if (r.pin != null && r.pop != null) { a.pairPin += r.pin; a.pairPop += r.pop; }
    agg.set(r.country, a);
    for (const [tri, t] of Object.entries(r.tris ?? {})) {
      if (t.pct == null || t.r == null || !t.rainy || t.r < TH.r_mod) continue;
      const high = t.pct <= vsevM || t.pct >= 100 - vsevM;
      const modr = !high && (t.pct <= sevM || t.pct >= 100 - sevM);
      if (!high && !modr) continue;
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

  const rows = [...best.entries()].map(([k, c]) => {
    const [country, hazard] = k.split("|");
    return { country, hazard, ...c, ...agg.get(country) };
  });
  rows.sort((a, b) => (a.hazard !== b.hazard ? (a.hazard === "drought" ? -1 : 1)
    : b.sev - a.sev || b.skill - a.skill || (b.pin ?? -1) - (a.pin ?? -1)));

  document.getElementById("alerts-title").textContent =
    `SEAS5 precipitation alerts × HNRP countries — forecast issued ${data.issued_label}, ` +
    `worst signal per country (in-season included)`;

  // Chip colours = the map's category palette (dark = High severity).
  const CHIP = {
    drought: { 2: ["#7f5619", "#ffffff"], 1: ["#dda555", "#3d2b0d"] },
    flood: { 2: ["#134ead", "#ffffff"], 1: ["#74a1e8", "#0d2c5c"] },
  };
  const fmtN = (v) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");

  const thead = document.querySelector("#alerts-table thead");
  thead.innerHTML = `<tr><th>Hazard</th><th>Country</th><th>Forecast category</th>` +
    `<th>Forecast skill</th><th class="num">PiN</th><th class="num">PiN (% of pop.)</th></tr>`;
  const tbody = document.querySelector("#alerts-table tbody");
  tbody.innerHTML = "";
  const HAZ_LABEL = { drought: "Drought", flood: "Flooding" };
  let prevHaz = null;
  for (const r of rows) {
    const tr = document.createElement("tr");
    if (r.hazard !== prevHaz) {
      const n = rows.filter((x) => x.hazard === r.hazard).length;
      const td = document.createElement("td");
      td.className = "hazard"; td.rowSpan = n; td.textContent = HAZ_LABEL[r.hazard];
      tr.appendChild(td);
      prevHaz = r.hazard;
    }
    const [bg, ink] = CHIP[r.hazard][r.sev];
    const detail = `${r.tri}${r.lead < 0 ? " (in season)" : ""} — RP ${r.rp.toFixed(1)} yr, ` +
      `r ${r.r.toFixed(2)} — worst unit: ${r.unit}`;
    const pct = r.pin != null && r.pairPop ? `${((100 * r.pairPin) / r.pairPop).toFixed(1)}%` : "–";
    // "Sudan (the)", "Mali (le)": HPC plan names carry the article — drop it.
    const cname = r.country.replace(/\s*\((the|la|le|el|los|las)\)$/i, "");
    tr.insertAdjacentHTML("beforeend",
      `<td>${esc(cname)}</td>` +
      `<td><span class="sev-chip" style="background:${bg};color:${ink}" title="${esc(detail)}">` +
      `${r.sev === 2 ? "High" : "Moderate"}</span></td>` +
      `<td class="${r.skill === 2 ? "skill-hi" : "skill-mod"}">${r.skill === 2 ? "High" : "Moderate"}</td>` +
      `<td class="num">${fmtN(r.pin)}</td>` +
      `<td class="num">${pct}</td>`);
    tbody.appendChild(tr);
  }
})();
