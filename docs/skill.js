// "Skill" tab: per-country SEAS5 forecast skill explorer.
//
// Stacked, column-aligned in one SVG:
//   1. ERA5 monthly climatology (12 month bars — the rainfall seasonality)
//   2. ERA5 trimester climatology (12 trimester bars; rainy season highlighted)
//   3. Pearson-r skill heatmap: x = valid trimester, y = leadtime (months from the
//      issue month to the trimester's first month, increasing down). Each cell shows
//      the issuing month + r. Colours are categorical at the skill cutoffs.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // First (calendar-start) month of each trimester — to label the issuing month in cells.
  const TRI_START = {
    JFM: 1, FMA: 2, MAM: 3, AMJ: 4, MJJ: 5, JJA: 6,
    JAS: 7, ASO: 8, SON: 9, OND: 10, NDJ: 11, DJF: 12,
  };
  const MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const MON1 = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

  const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  const textOn = (c) => (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] < 150 ? "#fff" : "#222");

  function el(tag, attrs, text) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  // Layout (internal SVG units; the <svg> scales to its container via viewBox).
  const ML = 70, MR = 132, MT = 22, MB = 40;
  const CELL_W = 60, CELL_H = 40;
  const BAR_H_M = 60, BAR_H_T = 70, MON_LBL_H = 15, TRI_LBL_H = 18, SEC_GAP = 16;

  function render(host, meta, country) {
    const tris = meta.trimesters;          // 12, calendar order
    const leads = meta.leads;              // [0..6]
    const nC = tris.length, nR = leads.length;
    const plotW = nC * CELL_W;

    const RMOD = (meta.thresholds && meta.thresholds.r_mod) || 0.3;
    const RHIGH = (meta.thresholds && meta.thresholds.r_high) || 0.5;
    const CATS = [
      { min: RHIGH, color: [26, 152, 80], label: `≥ ${RHIGH.toFixed(2)}  high` },
      { min: RMOD, color: [166, 217, 106], label: `${RMOD.toFixed(2)}–${RHIGH.toFixed(2)}  moderate` },
      { min: 0, color: [254, 224, 139], label: `0–${RMOD.toFixed(2)}  low` },
      { min: -Infinity, color: [229, 115, 115], label: `< 0  negative` },
    ];
    const catFor = (r) => CATS.find((c) => r >= c.min) || CATS[CATS.length - 1];

    // Shared rainfall scale across both climatology charts (monthly is the finer signal).
    const monVals = country.clim_monthly.filter((v) => v != null);
    const climMax = monVals.length ? Math.max(...monVals) : 1;

    const colX = (j) => ML + j * CELL_W;
    const colMid = (j) => ML + (j + 0.5) * CELL_W;

    // ---- vertical cursor ----
    let y = MT;
    const W = ML + plotW + MR;
    const svg = el("svg", {
      viewBox: "0 0 0 0", width: "100%",
      preserveAspectRatio: "xMidYMid meet", class: "skill-svg",
    });

    // helper: a climatology bar chart from explicit bar specs [{col, v, label, rainy}].
    function climChart(bars, barH, opts) {
      svg.appendChild(el("text", { x: ML, y: y - 6, class: "skill-axttl" }, opts.title));
      const base = y + barH;
      svg.appendChild(el("line", { x1: ML, y1: base, x2: ML + plotW, y2: base, stroke: "#bbb" }));
      bars.forEach((b) => {
        if (b.v == null) return;
        const h = (b.v / climMax) * barH;
        const bar = el("rect", {
          x: colX(b.col) + opts.pad, y: base - h, width: CELL_W - 2 * opts.pad, height: h,
          fill: b.rainy ? "#4a78c4" : opts.fill,
          stroke: b.rainy ? "#1f4e9b" : "none", "stroke-width": b.rainy ? 1 : 0,
        });
        bar.appendChild(el("title", {}, `${b.label}: ${b.v.toFixed(2)} mm/day`));
        svg.appendChild(bar);
      });
      y = base;
    }

    // 1) Monthly climatology — each month centred on the trimester whose MIDDLE month it
    // is. That shifts the cycle so it brackets the trimester axis: Jan (JFM's first month)
    // overhangs to the left of column 0 and Feb (DJF's last month) overhangs to the right
    // of the last column, the run reading J F M A M J J A S O N D J F.
    const monBars = [];
    for (let j = -1; j <= nC; j++) {
      const m = ((j + 13) % 12) + 1; // middle month of the trimester at column j
      monBars.push({ col: j, v: country.clim_monthly[m - 1], label: MON[m], letter: MON1[m - 1] });
    }
    climChart(monBars, BAR_H_M, { title: "ERA5 monthly rainfall (mm/day)", fill: "#9bb3d4", pad: 6 });
    monBars.forEach((b) => {
      svg.appendChild(el("text", {
        x: colMid(b.col), y: y + 11, class: "skill-tick", "text-anchor": "middle",
      }, b.letter));
    });
    y += MON_LBL_H + SEC_GAP;

    // 2) Trimester climatology
    const triBars = country.clim.map((v, j) => ({
      col: j, v, label: tris[j].label, rainy: country.rainy[j],
    }));
    climChart(triBars, BAR_H_T, { title: "ERA5 trimester rainfall (mm/day)", fill: "#cdd6e2", pad: 8 });

    // 3) Trimester labels (shared column headers between the bars and the heatmap)
    const yTriLbl = y;
    tris.forEach((t, j) => {
      svg.appendChild(el("text", {
        x: colMid(j), y: yTriLbl + 14, class: "skill-trilbl", "text-anchor": "middle",
        "font-weight": country.rainy[j] ? "700" : "400",
        fill: country.rainy[j] ? "#1f4e9b" : "#333",
      }, t.key));
    });
    const yHeat = yTriLbl + TRI_LBL_H;

    // 4) Heatmap (categorical Pearson r): rows = leadtime, cols = trimester
    leads.forEach((lead, i) => {
      const ry = yHeat + i * CELL_H;
      svg.appendChild(el("text", {
        x: ML - 10, y: ry + CELL_H / 2 + 4, class: "skill-tick", "text-anchor": "end",
      }, String(lead)));
      tris.forEach((t, j) => {
        const x = colX(j);
        const r = country.r[i][j];
        const im = ((TRI_START[t.key] - lead - 1 + 144) % 12) + 1;
        if (r == null) {
          svg.appendChild(el("rect", {
            x, y: ry, width: CELL_W, height: CELL_H, fill: "#f0f0f0",
            stroke: "#fff", "stroke-width": 1,
          }));
          return;
        }
        const cat = catFor(r);
        const tcol = textOn(cat.color);
        const cell = el("rect", {
          x, y: ry, width: CELL_W, height: CELL_H, fill: css(cat.color),
          stroke: "#fff", "stroke-width": 1,
        });
        cell.appendChild(el("title", {},
          `${country.name} — ${t.label}\nIssued ${MON[im]}, leadtime ${lead} mo\nr = ${r.toFixed(2)}`));
        svg.appendChild(cell);
        svg.appendChild(el("text", {
          x: x + CELL_W / 2, y: ry + 15, class: "skill-cellmo", "text-anchor": "middle",
          fill: tcol, "fill-opacity": "0.75",
        }, MON[im]));
        svg.appendChild(el("text", {
          x: x + CELL_W / 2, y: ry + 30, class: "skill-cellv", "text-anchor": "middle", fill: tcol,
        }, r.toFixed(2)));
      });
    });

    // Rainy-season column outlines over the whole heatmap.
    tris.forEach((t, j) => {
      if (!country.rainy[j]) return;
      svg.appendChild(el("rect", {
        x: colX(j), y: yHeat, width: CELL_W, height: nR * CELL_H,
        fill: "none", stroke: "#1f4e9b", "stroke-width": 2,
      }));
    });

    // Axis titles
    const H = yHeat + nR * CELL_H + MB;
    svg.appendChild(el("text", {
      x: ML + plotW / 2, y: H - 10, class: "skill-axttl", "text-anchor": "middle",
    }, "Valid trimester  (blue = rainy season, ≥ 15% of annual rainfall)"));
    svg.appendChild(el("text", {
      x: 14, y: yHeat + (nR * CELL_H) / 2, class: "skill-axttl", "text-anchor": "middle",
      transform: `rotate(-90 14 ${yHeat + (nR * CELL_H) / 2})`,
    }, "Leadtime (months ahead)"));

    // Categorical legend (right of the heatmap)
    const lx = ML + plotW + 16;
    svg.appendChild(el("text", { x: lx, y: yHeat - 6, class: "skill-tick" }, "Skill (r)"));
    CATS.forEach((c, k) => {
      const ly = yHeat + 4 + k * 22;
      svg.appendChild(el("rect", {
        x: lx, y: ly, width: 15, height: 15, fill: css(c.color), stroke: "#999",
      }));
      svg.appendChild(el("text", {
        x: lx + 21, y: ly + 12, class: "skill-tick",
      }, c.label));
    });

    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    host.replaceChildren(svg);
  }

  fetch("data/skill_matrix.json").then((r) => r.json()).then((meta) => {
    const sel = document.getElementById("skill-country");
    const host = document.getElementById("skill-chart");
    if (!sel || !host) return;
    const isos = Object.keys(meta.countries); // already sorted by name
    for (const iso of isos) {
      const o = document.createElement("option");
      o.value = iso; o.textContent = meta.countries[iso].name;
      sel.appendChild(o);
    }
    const def = ["KEN", "ETH", "SOM"].find((c) => meta.countries[c]) || isos[0];
    sel.value = def;
    const draw = () => render(host, meta, meta.countries[sel.value]);
    sel.addEventListener("change", draw);
    draw();
  }).catch((e) => {
    const host = document.getElementById("skill-chart");
    if (host) host.textContent = "Could not load skill data.";
    console.error(e);
  });
})();
