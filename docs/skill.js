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
  // ML leaves room for the J/F overhang bars PLUS an axis-label gutter left of them
  // (the y ticks and the heatmap lead labels share that gutter).
  const ML = 106, MR = 156, MT = 22, MB = 40;
  const CELL_W = 60, CELL_H = 40;
  const BAR_H_M = 78, MON_LBL_H = 15, TRI_LBL_H = 18, SEC_GAP = 16;

  function render(host, meta, country) {
    const tris = meta.trimesters;          // 12, calendar order
    const leads = meta.leads;              // [-2..4]; negative = in-season (mixed) issues
    const nC = tris.length, nR = leads.length;
    const plotW = nC * CELL_W;

    const RMOD = (meta.thresholds && meta.thresholds.r_mod) || 0.3;
    const RHIGH = (meta.thresholds && meta.thresholds.r_high) || 0.5;
    // HDX brand tokens; negative is a PALE error-scale brown so it doesn't shout.
    const CATS = [
      { min: RHIGH, color: [30, 121, 95], label: `≥ ${RHIGH.toFixed(2)}  high` },
      { min: RMOD, color: [125, 193, 173], label: `${RMOD.toFixed(2)}–${RHIGH.toFixed(2)}  moderate` },
      { min: 0, color: [212, 234, 228], label: `0–${RMOD.toFixed(2)}  low` },
      { min: -Infinity, color: [243, 218, 215], label: `< 0  negative` },
    ];
    const catFor = (r) => CATS.find((c) => r >= c.min) || CATS[CATS.length - 1];
    const RAINY = "#0e3b82";  // rainy-season outline + label colour (HDX primary-7)

    // Rainfall scale for the monthly chart (|| 1 guards the all-zero desert case —
    // a zero max would make the y-axis tick step 0 and loop forever).
    const monVals = country.clim_monthly.filter((v) => v != null);
    const climMax = (monVals.length ? Math.max(...monVals) : 1) || 1;

    const colX = (j) => ML + j * CELL_W;
    const colMid = (j) => ML + (j + 0.5) * CELL_W;

    // ---- vertical cursor ----
    let y = MT;
    const W = ML + plotW + MR;
    const svg = el("svg", {
      viewBox: "0 0 0 0", width: "100%",
      preserveAspectRatio: "xMidYMid meet", class: "skill-svg",
    });

    // 1) Monthly climatology — each month centred on the trimester whose MIDDLE month it
    // is. That shifts the cycle so it brackets the trimester axis: Jan (JFM's first month)
    // overhangs to the left of column 0 and Feb (DJF's last month) overhangs to the right
    // of the last column, the run reading J F M A M J J A S O N D J F.
    // (The trimester bar chart was dropped: the heatmap's labels + rainy outlines carry
    // that information.)
    const monBars = [];
    for (let j = -1; j <= nC; j++) {
      const m = ((j + 13) % 12) + 1; // middle month of the trimester at column j
      monBars.push({ col: j, v: country.clim_monthly[m - 1], label: MON[m], letter: MON1[m - 1] });
    }
    svg.appendChild(el("text", { x: ML, y: y - 6, class: "skill-axttl" }, "ERA5 monthly rainfall (mm/day)"));
    const base = y + BAR_H_M;

    // y-axis with gridlines: a "nice" step (1/2/5 progression) giving ~4 ticks. The
    // labels sit in the gutter LEFT of the overhanging Jan bar (col −1), and gridlines
    // span the full bar run (J … F), so nothing overlaps the January column.
    const axX = ML - CELL_W - 8;   // shared axis-label gutter (also the lead labels)
    const rawStep = climMax / 4;
    const pow = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const step = [1, 2, 5, 10].map((k) => k * pow).find((s) => s >= rawStep) || rawStep;
    for (let v = 0; v <= climMax + 1e-9; v += step) {
      const gy = base - (v / climMax) * BAR_H_M;
      svg.appendChild(el("line", {
        x1: ML - CELL_W, y1: gy, x2: ML + plotW + CELL_W, y2: gy,
        stroke: v === 0 ? "#c4d0d1" : "#ebeff0",
      }));
      svg.appendChild(el("text", {
        x: axX, y: gy + 3.5, class: "skill-tick", "text-anchor": "end",
      }, step >= 1 ? String(Math.round(v)) : v.toFixed(1)));
    }
    monBars.forEach((b) => {
      if (b.v == null) return;
      const h = (b.v / climMax) * BAR_H_M;
      const bar = el("rect", {
        x: colX(b.col) + 6, y: base - h, width: CELL_W - 12, height: h, fill: "#74a1e8",
      });
      bar.appendChild(el("title", {}, `${b.label}: ${b.v.toFixed(2)} mm/day`));
      svg.appendChild(bar);
    });
    y = base;
    monBars.forEach((b) => {
      svg.appendChild(el("text", {
        x: colMid(b.col), y: y + 11, class: "skill-tick", "text-anchor": "middle",
      }, b.letter));
    });
    y += MON_LBL_H + SEC_GAP;

    // 2) Trimester labels (column headers for the heatmap; blue+bold = rainy season)
    const yTriLbl = y;
    tris.forEach((t, j) => {
      svg.appendChild(el("text", {
        x: colMid(j), y: yTriLbl + 14, class: "skill-trilbl", "text-anchor": "middle",
        "font-weight": country.rainy[j] ? "700" : "400",
        fill: country.rainy[j] ? RAINY : "#333",
      }, t.key));
    });
    const yHeat = yTriLbl + TRI_LBL_H;

    // 4) Heatmap (categorical Pearson r): rows = leadtime, cols = trimester.
    // Negative leads = in-season issues (1–2 months already observed, rest forecast).
    leads.forEach((lead, i) => {
      const ry = yHeat + i * CELL_H;
      svg.appendChild(el("text", {
        x: ML - CELL_W - 8, y: ry + CELL_H / 2 + 4, class: "skill-tick", "text-anchor": "end",
      }, String(lead).replace("-", "−")));
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
        const leadTxt = lead < 0
          ? `issued in-season, ${-lead} of 3 months observed`
          : `leadtime ${lead} mo`;
        cell.appendChild(el("title", {},
          `${country.name} — ${t.label}\nIssued ${MON[im]}, ${leadTxt}\nr = ${r.toFixed(2)}`));
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

    // Separator under the in-season (negative-lead) rows: a thicker white gap.
    const sepIdx = leads.findIndex((l) => l >= 0);
    if (sepIdx > 0) {
      const sy = yHeat + sepIdx * CELL_H;
      svg.appendChild(el("line", {
        x1: ML, y1: sy, x2: ML + plotW, y2: sy, stroke: "#fff", "stroke-width": 5,
      }));
    }

    // Rainy-season outline: ONE contiguous rounded box per run of adjacent rainy
    // trimesters (not per-column, which doubled the borders between neighbours).
    let runStart = null;
    for (let j = 0; j <= nC; j++) {
      const rainy = j < nC && country.rainy[j];
      if (rainy && runStart == null) runStart = j;
      if (!rainy && runStart != null) {
        svg.appendChild(el("rect", {
          x: colX(runStart) + 1, y: yHeat + 1,
          width: (j - runStart) * CELL_W - 2, height: nR * CELL_H - 2,
          rx: 4, fill: "none", stroke: RAINY, "stroke-width": 2.5,
        }));
        runStart = null;
      }
    }

    // Axis titles
    const H = yHeat + nR * CELL_H + MB;
    svg.appendChild(el("text", {
      x: ML + plotW / 2, y: H - 10, class: "skill-axttl", "text-anchor": "middle",
    }, "Valid trimester  (blue = rainy season, ≥ 15% of annual rainfall)"));
    svg.appendChild(el("text", {
      x: 14, y: yHeat + (nR * CELL_H) / 2, class: "skill-axttl", "text-anchor": "middle",
      transform: `rotate(-90 14 ${yHeat + (nR * CELL_H) / 2})`,
    }, "Leadtime (months ahead; − = in-season)"));

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
    const lyRainy = yHeat + 4 + CATS.length * 22 + 6;
    svg.appendChild(el("rect", {
      x: lx, y: lyRainy, width: 15, height: 15, rx: 3,
      fill: "none", stroke: RAINY, "stroke-width": 2,
    }));
    svg.appendChild(el("text", {
      x: lx + 21, y: lyRainy + 12, class: "skill-tick",
    }, "Rainy season"));
    svg.appendChild(el("text", {
      x: lx, y: lyRainy + 34, class: "skill-tick",
    }, "Cell text = issue month"));

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
