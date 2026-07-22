// Forecast × HNRP tab: ranked table of admin-1 units where a bad drought forecast
// (rainy-season, ≥ moderate skill, latest issuance) coincides with a large
// intersectoral PiN / targeted caseload (ds-hnrp-mirror). Plain JS, no deps.
(async function () {
  let data;
  try {
    data = await (await fetch("data/hnrp_drought.json")).json();
  } catch {
    return; // data file not built yet — leave the tab empty
  }

  const skillSel = document.getElementById("hnrp-skill");
  const rpSel = document.getElementById("hnrp-rp");
  const countrySel = document.getElementById("hnrp-country");
  const issuedEl = document.getElementById("hnrp-issued");
  const thead = document.querySelector("#hnrp-table thead");
  const tbody = document.querySelector("#hnrp-table tbody");
  const emptyEl = document.getElementById("hnrp-empty");
  const R_HIGH = data.thresholds.r_high;

  issuedEl.textContent = `Forecast issued ${data.issued_label}.`;

  // Country filter options.
  const countries = [...new Set(data.rows.map((r) => r.country).filter(Boolean))].sort();
  for (const c of countries) {
    const o = document.createElement("option");
    o.value = c; o.textContent = c;
    countrySel.appendChild(o);
  }

  const COLS = [
    { key: "country", label: "Country", num: false },
    { key: "name", label: "Admin 1", num: false },
    { key: "pin", label: "PiN", num: true },
    { key: "targeted", label: "Targeted", num: true },
    { key: "tri_label", label: "Season", num: false },
    { key: "rp", label: "Drought RP (yr)", num: true },
    { key: "pct", label: "Percentile", num: true },
    { key: "r", label: "Skill (r)", num: true },
  ];
  // Default: worst-first within the RP filter, biggest caseload as tiebreak → rank by
  // PiN among qualifying rows (the ask: highest caseload with the worst forecast).
  let sortKey = "pin", sortDesc = true;

  const fmtN = (v) => (v == null ? "–" : v.toLocaleString("en-US"));
  const fmt = (v, d) => (v == null ? "–" : Number(v).toFixed(d));

  function rows() {
    const minRp = +rpSel.value;
    const needHigh = skillSel.value === "high";
    const country = countrySel.value;
    return data.rows.filter((r) => {
      if (country && r.country !== country) return false;
      if (r.rp == null) return false; // no qualifying drought slot
      if (r.rp < minRp) return false;
      if (needHigh && r.r < R_HIGH) return false;
      return true;
    });
  }

  function render() {
    thead.innerHTML = "";
    const trh = document.createElement("tr");
    for (const c of COLS) {
      const th = document.createElement("th");
      th.textContent = c.label;
      th.className = (c.num ? "num" : "") + (c.key === sortKey ? " sorted" : "");
      if (c.key === sortKey) th.textContent += sortDesc ? " ↓" : " ↑";
      th.addEventListener("click", () => {
        if (sortKey === c.key) sortDesc = !sortDesc;
        else { sortKey = c.key; sortDesc = c.num; }
        render();
      });
      trh.appendChild(th);
    }
    thead.appendChild(trh);

    const rs = rows().sort((a, b) => {
      const x = a[sortKey], y = b[sortKey];
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      return sortDesc ? -cmp : cmp;
    });

    tbody.innerHTML = "";
    emptyEl.hidden = rs.length > 0;
    for (const r of rs) {
      const tr = document.createElement("tr");
      const skillCls = r.r >= R_HIGH ? "skill-high" : "skill-mod";
      const inSeason = r.lead != null && r.lead < 0;
      tr.innerHTML =
        `<td>${r.country ?? r.iso3}</td>` +
        `<td>${r.name ?? r.pcode}</td>` +
        `<td class="num">${fmtN(r.pin)}</td>` +
        `<td class="num">${fmtN(r.targeted)}</td>` +
        `<td>${r.tri_label ?? "–"}${inSeason ? ' <span class="in-season-tag">· in season</span>' : ""}</td>` +
        `<td class="num">${fmt(r.rp, 1)}</td>` +
        `<td class="num">${fmt(r.pct, 1)}</td>` +
        `<td class="num ${skillCls}">${fmt(r.r, 2)}</td>`;
      tbody.appendChild(tr);
    }
  }

  for (const el of [skillSel, rpSel, countrySel]) el.addEventListener("change", render);
  render();
})();
