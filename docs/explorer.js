// Admin explorer tab (#explorer, unlisted): the static port of the old Azure marimo
// app's per-country section (analysis/prob_alerts.py), extended to admin 1 and admin 2
// the way the ds-seas5-viz explorer was. For one unit and the LATEST issuance:
//   1. forecast percentile and skill per valid trimester, raw next to detrended;
//   2. the hindcast scatter (SEAS5 vs ERA5, one point per year) for one trimester;
//   3. the forecast and observed series over the record, raw and detrended, with the
//      raw series' linear trend;
//   4. the unit's skill for every issue month (the Skill-by-country heatmap, reused
//      from skill.js) + its monthly ERA5 climatology.
// Data: data/explorer/ (pipeline/export_explorer_site.py). Unit files are gzipped
// JSON; detrended series are re-derived here (explorer_math.js `detrend`, checked
// against the pipeline by pipeline/check_explorer_detrend.py). Every r, percentile
// and return period shown is the pipeline's, never recomputed in the browser.
(function () {
  "use strict";
  const M = window.ExplorerMath;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const BASE = "data/explorer/";

  // Series: violet = raw, aqua = detrended (validated pair, dataviz palette slots 7/3).
  const COL = { raw: "#6250d6", dt: "#1baf7a" };
  const VER_LABEL = { raw: "Raw", dt: "Detrended" };
  const INK = "#101212", INK2 = "#3f4748", MUTED = "#5e6a6b";
  const GRID = "#ebeff0", AXIS = "#c4d0d1", HILITE = "#f5f7f7";
  // The Map tab's drier/wetter colours, as washes behind the percentile bars.
  const DRY2 = "#7f5619", DRY = "#dda555", WET = "#74a1e8", WET2 = "#134ead";
  const RAINY = "#0e3b82";
  const MON = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const $ = (id) => document.getElementById(id);
  const fmt = (v, d = 2) => (v == null || !isFinite(v) ? "–" : v.toFixed(d));
  const minus = (s) => String(s).replace("-", "−");

  function el(tag, attrs, text) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function tip(node, text) { node.appendChild(el("title", {}, text)); return node; }

  // "Nice" axis ticks (1/2/5 steps) over [lo, hi].
  function ticks(lo, hi, n = 5) {
    const span = hi - lo || 1;
    const raw = span / n, pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 5, 10].map((k) => k * pow).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) {
      out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
    }
    return { out, step };
  }
  const tickLbl = (v, step) => (step >= 1 ? String(Math.round(v)) : v.toFixed(step >= 0.1 ? 1 : 2));

  // ── data loading ────────────────────────────────────────────────────────────
  const cache = new Map();
  function getJSON(url) {
    if (!cache.has(url)) {
      cache.set(url, (async () => {
        const res = await fetch(url, { cache: "no-cache" });
        if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
        const buf = new Uint8Array(await res.arrayBuffer());
        // Gzip magic: inflate here. A server that already sent Content-Encoding: gzip
        // hands over plain JSON, which parses directly.
        if (buf[0] === 0x1f && buf[1] === 0x8b) {
          const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
          return JSON.parse(await new Response(stream).text());
        }
        return JSON.parse(new TextDecoder().decode(buf));
      })());
      cache.get(url).catch(() => cache.delete(url)); // let a failed fetch retry
    }
    return cache.get(url);
  }

  let IDX = null;             // index.json
  const S = { iso3: null, adm1: "", adm2: "", tri: null, ver: "dt" };
  let country = null;         // <ISO3>.json.gz
  let adm2File = null;        // adm2/<ADM1>.json.gz (or null)
  let unit = null, unitName = "";

  // ── per-trimester derived series ───────────────────────────────────────────
  // For one unit × trimester: decoded raw + detrended series (log space), the
  // historical (overlap) years and the current forecast, all in mm/day.
  function series(t) {
    const fcR = M.decode(t.fc, IDX.scale), obsR = M.decode(t.obs, IDX.scale);
    const d = M.detrend(t.y0, fcR, obsR);
    const out = { y0: t.y0, cur: t.cur, n: t.fc.length };
    for (const [k, fc, obs] of [["raw", fcR, obsR], ["dt", d.fc, d.obs]]) {
      const hist = [];
      for (let i = 0; i < fc.length; i++) {
        const yr = t.y0 + i;
        if (fc[i] != null && obs[i] != null && yr !== t.cur) {
          hist.push({ yr, f: Math.expm1(fc[i]), o: Math.expm1(obs[i]), fl: fc[i], ol: obs[i] });
        }
      }
      const curF = t.cur != null && fc[t.cur - t.y0] != null ? Math.expm1(fc[t.cur - t.y0]) : null;
      // Normal = expm1(mean log obs over the overlap): the same "HNRP-identical"
      // normal the Forecast × HNRP tab uses.
      const normal = hist.length
        ? Math.expm1(hist.reduce((s, h) => s + h.ol, 0) / hist.length) : null;
      out[k] = {
        hist, curF, normal,
        fcAll: fc.map((v) => (v == null ? null : Math.expm1(v))),
        obsAll: obs.map((v) => (v == null ? null : Math.expm1(v))),
      };
    }
    return out;
  }

  // The Map tab's categorisation (app.js classify) for one stats record.
  function category(st, rainy) {
    if (!st || st.r == null || st.pct == null) return "No current forecast";
    const T = IDX.thresholds;
    const vsev = 100 / T.vsev_rp, sev = 100 / T.sev_rp, p = st.pct;
    if (st.r < T.r_mod) return "Low skill";
    const sk = st.r >= T.r_high ? "high skill" : "moderate skill";
    let c = "Roughly normal";
    if (p <= vsev) c = "Strongly below normal";
    else if (p <= sev) c = "Below normal";
    else if (p >= 100 - vsev) c = "Strongly above normal";
    else if (p >= 100 - sev) c = "Above normal";
    return `${c} (${sk})${rainy ? "" : ", outside rainy season"}`;
  }

  const triMeta = (key) => IDX.trimesters.find((t) => t.key === key);
  const leadTxt = (lead) => (lead < 0 ? `in season, ${-lead} of 3 months observed` : `lead ${lead}`);
  const isRainy = (key) => unit.rainy[IDX.all_trimesters.findIndex((t) => t.key === key)];

  // ── 1. outlook: percentile + skill per trimester, raw vs detrended ─────────
  function drawOutlook() {
    const host = $("ex-outlook");
    const tris = IDX.trimesters.filter((t) => unit.tri[t.key]);
    if (!tris.length) { host.textContent = "No forecast data for this unit."; return; }
    const ML = 64, MR = 150, GW = 92, BW = 22, GAP = 2;
    const W = ML + tris.length * GW + MR;
    const P1 = { top: 30, h: 170 }, P2 = { top: 30 + 170 + 74, h: 120 };
    const H = P2.top + P2.h + 26;
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "ex-svg", role: "img",
      "aria-label": "Forecast percentile and skill per trimester, raw and detrended" });
    const gx = (j) => ML + j * GW;

    // Selected trimester column + click targets (drawn first, under the marks).
    tris.forEach((t, j) => {
      if (t.key === S.tri) {
        svg.appendChild(el("rect", { x: gx(j) + 4, y: P1.top - 26, width: GW - 8,
          height: P2.top + P2.h - P1.top + 46, rx: 4, fill: HILITE }));
      }
    });

    // Panel 1 — percentile, baseline 50; washes = the Map's below/above-normal bands.
    const T = IDX.thresholds, vsev = 100 / T.vsev_rp, sev = 100 / T.sev_rp;
    const y1 = (p) => P1.top + (1 - p / 100) * P1.h;
    const bands = [
      [0, vsev, DRY2, "Strongly below normal"], [vsev, sev, DRY, "Below normal"],
      [100 - sev, 100 - vsev, WET, "Above normal"], [100 - vsev, 100, WET2, "Strongly above normal"],
    ];
    const x0 = ML, x1 = ML + tris.length * GW;
    bands.forEach(([a, b, c, lbl]) => {
      svg.appendChild(el("rect", { x: x0, y: y1(b), width: x1 - x0, height: y1(a) - y1(b),
        fill: c, "fill-opacity": 0.13 }));
      svg.appendChild(el("text", { x: x1 + 8, y: (y1(a) + y1(b)) / 2 + 4, class: "ex-muted" }, lbl));
    });
    [0, sev, 50, 100 - sev, 100].forEach((p) => {
      svg.appendChild(el("line", { x1: x0, x2: x1, y1: y1(p), y2: y1(p),
        stroke: p === 50 ? AXIS : GRID }));
      svg.appendChild(el("text", { x: ML - 8, y: y1(p) + 4, "text-anchor": "end", class: "ex-muted" },
        String(Math.round(p))));
    });
    svg.appendChild(el("text", { x: ML - 54, y: P1.top - 12, class: "ex-axttl" },
      "Forecast percentile (below 50 = drier than usual)"));

    // Panel 2 — Pearson r, baseline 0, cutoffs at the moderate/high skill thresholds.
    const rMin = Math.min(-0.2, ...tris.flatMap((t) => {
      const v = unit.tri[t.key]; return [v.raw?.r ?? 0, v.dt?.r ?? 0];
    }));
    const lo = Math.floor(rMin * 10) / 10;
    const y2 = (r) => P2.top + (1 - (r - lo) / (1 - lo)) * P2.h;
    const rt = [lo, 0, T.r_mod, T.r_high, 1].filter((v, i, a) => a.indexOf(v) === i);
    rt.forEach((r) => {
      svg.appendChild(el("line", { x1: x0, x2: x1, y1: y2(r), y2: y2(r), stroke: r === 0 ? AXIS : GRID }));
      svg.appendChild(el("text", { x: ML - 8, y: y2(r) + 4, "text-anchor": "end", class: "ex-muted" },
        minus(r.toFixed(1))));
    });
    svg.appendChild(el("text", { x: x1 + 8, y: y2(T.r_high) + 4, class: "ex-muted" },
      `High skill (r ≥ ${T.r_high})`));
    svg.appendChild(el("text", { x: x1 + 8, y: y2(T.r_mod) + 4, class: "ex-muted" },
      `Moderate (r ≥ ${T.r_mod})`));
    svg.appendChild(el("text", { x: ML - 54, y: P2.top - 12, class: "ex-axttl" },
      "Skill: correlation of forecast with observed, 1981–present (r)"));

    // Bars: 4px rounded data-end, square at the baseline (path), 2px surface gap.
    function bar(x, base, end, color) {
      if (end == null || !isFinite(end)) return;
      const up = end < base, h = Math.abs(end - base);
      if (h < 0.5) {
        svg.appendChild(el("rect", { x, y: base - 1, width: BW, height: 2, fill: color }));
        return;
      }
      const r = Math.min(4, h, BW / 2);
      const d = up
        ? `M${x},${base}V${end + r}Q${x},${end} ${x + r},${end}H${x + BW - r}Q${x + BW},${end} ${x + BW},${end + r}V${base}Z`
        : `M${x},${base}V${end - r}Q${x},${end} ${x + r},${end}H${x + BW - r}Q${x + BW},${end} ${x + BW},${end - r}V${base}Z`;
      svg.appendChild(el("path", { d, fill: color }));
    }

    const hits = [];
    tris.forEach((t, j) => {
      const v = unit.tri[t.key], s = series(v);
      const cx = gx(j) + GW / 2;
      const xs = { dt: cx - BW - GAP / 2, raw: cx + GAP / 2 };
      const rainy = isRainy(t.key);
      const lines = [];
      for (const k of ["dt", "raw"]) {
        const st = v[k];
        bar(xs[k], y1(50), st?.pct == null ? null : y1(st.pct), COL[k]);
        bar(xs[k], y2(0), st?.r == null ? null : y2(st.r), COL[k]);
        const rp = st?.pct == null ? "" : st.pct < 50
          ? `, dry return period ${fmt(st.rp_dry, 1)} yr` : `, wet return period ${fmt(st.rp_wet, 1)} yr`;
        lines.push(`${VER_LABEL[k]}: ` + (st?.pct == null ? "no current forecast"
          : `${fmt(st.pct, 0)}th percentile, ${fmt(s[k].curF)} mm/day vs normal ${fmt(s[k].normal)}${rp}`) +
          `; skill r ${fmt(st?.r)}`);
      }
      if (v.cur == null) {
        svg.appendChild(el("text", { x: cx, y: y1(50) - 6, "text-anchor": "middle", class: "ex-muted" },
          "no forecast"));
      }
      // Column labels: trimester (bold blue = rainy season), then lead.
      svg.appendChild(el("text", { x: cx, y: P1.top + P1.h + 20, "text-anchor": "middle",
        class: "ex-trilbl", "font-weight": rainy ? 700 : 400, fill: rainy ? RAINY : INK }, t.key));
      svg.appendChild(el("text", { x: cx, y: P1.top + P1.h + 35, "text-anchor": "middle", class: "ex-muted" },
        t.lead < 0 ? "in season" : `lead ${t.lead}`));
      // Hit target: the whole column (bigger than the marks, on top of them) — one
      // tooltip with both versions; click selects the trimester for the charts below.
      const hit = el("rect", { x: gx(j) + 4, y: P1.top - 26, width: GW - 8,
        height: P2.top + P2.h - P1.top + 46, fill: "transparent", class: "ex-hit" });
      tip(hit, `${t.label} ${t.season_year} (${leadTxt(t.lead)})` +
        `${rainy ? ", rainy season" : ""}\n${lines.join("\n")}\nClick to show this trimester below`);
      hit.addEventListener("click", () => { S.tri = t.key; update(); });
      hits.push(hit);
    });
    hits.forEach((h) => svg.appendChild(h));
    host.replaceChildren(svg);
  }

  // ── table view of the same numbers ─────────────────────────────────────────
  function drawTable() {
    const tb = $("ex-table");
    const head = "<thead><tr><th>Trimester</th><th>Lead</th>" +
      "<th class='num'>Forecast mm/day<br>detrended</th><th class='num'>Forecast mm/day<br>raw</th>" +
      "<th class='num'>Normal<br>mm/day</th>" +
      "<th class='num'>Percentile<br>detrended</th><th class='num'>Percentile<br>raw</th>" +
      "<th class='num'>Skill r<br>detrended</th><th class='num'>Skill r<br>raw</th>" +
      "<th>Map category (detrended)</th></tr></thead>";
    const rows = IDX.trimesters.filter((t) => unit.tri[t.key]).map((t) => {
      const v = unit.tri[t.key], s = series(v), rainy = isRainy(t.key);
      const cls = t.key === S.tri ? " class='sel'" : "";
      return `<tr${cls}><td><span class="${rainy ? "ex-rainy" : ""}">${t.key}</span> ${t.season_year}</td>` +
        `<td>${t.lead < 0 ? "in season" : t.lead}</td>` +
        `<td class='num'>${fmt(s.dt.curF)}</td><td class='num'>${fmt(s.raw.curF)}</td>` +
        `<td class='num'>${fmt(s.raw.normal)}</td>` +
        `<td class='num'>${fmt(v.dt?.pct, 0)}</td><td class='num'>${fmt(v.raw?.pct, 0)}</td>` +
        `<td class='num'>${fmt(v.dt?.r)}</td><td class='num'>${fmt(v.raw?.r)}</td>` +
        `<td>${category(v.dt, rainy)}</td></tr>`;
    }).join("");
    tb.innerHTML = head + `<tbody>${rows}</tbody>`;
  }

  // ── 2. scatter: forecast vs observed, one point per year ───────────────────
  function drawScatter() {
    const host = $("ex-scatter"), stats = $("ex-scatter-stats");
    const t = triMeta(S.tri), v = unit.tri[S.tri];
    if (!t || !v) { host.textContent = "No data for this trimester."; stats.textContent = ""; return; }
    const s = series(v)[S.ver], hist = s.hist;
    if (hist.length < 3) { host.textContent = "Too few years to plot."; stats.textContent = ""; return; }
    const W = 600, H = 540, ML = 64, MR = 24, MT = 18, MB = 52;
    // Axes on a log(1 + mm/day) scale — the scale the forecast is matched, detrended and
    // correlated on — labelled in mm/day. It also spreads the dry years, which a linear
    // axis piles into one corner wherever a few very wet years set the range.
    const L = Math.log1p;
    const xs = hist.map((h) => L(h.f)).concat(s.curF != null ? [L(s.curF)] : []);
    const ys = hist.map((h) => L(h.o));
    const pad = (a, b) => [a - 0.06 * (b - a || 1), b + 0.06 * (b - a || 1)];
    const [xl, xh] = pad(Math.min(...xs), Math.max(...xs));
    const [yl, yh] = pad(Math.min(...ys), Math.max(...ys));
    const X = (mm) => ML + ((L(mm) - xl) / (xh - xl)) * (W - ML - MR);
    const Y = (mm) => MT + (1 - (L(mm) - yl) / (yh - yl)) * (H - MT - MB);
    const xMin = Math.expm1(xl), xMax = Math.expm1(xh), yMin = Math.expm1(yl), yMax = Math.expm1(yh);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "ex-svg ex-scatter-svg", role: "img",
      "aria-label": `Forecast against observed rainfall, ${t.label}, one point per year` });

    // Terciles of the plotted years (pandas-style quantiles, in mm/day as the old app).
    // Shade only the two corners where forecast and observation fell in the same third.
    const q = (arr, pr) => M.quantile(arr, pr);
    const fx = hist.map((h) => h.f), oy = hist.map((h) => h.o);
    const tl = { f: q(fx, 1 / 3), o: q(oy, 1 / 3) }, tu = { f: q(fx, 2 / 3), o: q(oy, 2 / 3) };
    const rect = (x0, y0, x1, y1, c) => svg.appendChild(el("rect", { x: Math.min(x0, x1),
      y: Math.min(y0, y1), width: Math.abs(x1 - x0), height: Math.abs(y1 - y0), fill: c, "fill-opacity": 0.16 }));
    rect(X(xMin), Y(yMin), X(tl.f), Y(tl.o), DRY);
    rect(X(tu.f), Y(tu.o), X(xMax), Y(yMax), WET);

    // Grid (log-spaced mm ticks, thinned so labels never touch) + frame.
    const CAND = [0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 7, 10, 15, 20, 30, 50];
    const logTicks = (lo, hi, pos) => {
      const out = [];
      for (const c of CAND) {
        if (c < lo || c > hi) continue;
        if (!out.length || Math.abs(pos(c) - pos(out.at(-1))) >= 34) out.push(c);
      }
      return out;
    };
    const lbl = (v2) => (v2 >= 1 ? String(v2) : v2 === 0 ? "0" : v2.toFixed(v2 < 0.1 ? 2 : v2 === 0.75 ? 2 : 1));
    logTicks(xMin, xMax, X).forEach((v2) => {
      svg.appendChild(el("line", { x1: X(v2), x2: X(v2), y1: MT, y2: H - MB, stroke: GRID }));
      svg.appendChild(el("text", { x: X(v2), y: H - MB + 16, "text-anchor": "middle", class: "ex-muted" }, lbl(v2)));
    });
    logTicks(yMin, yMax, Y).forEach((v2) => {
      svg.appendChild(el("line", { x1: ML, x2: W - MR, y1: Y(v2), y2: Y(v2), stroke: GRID }));
      svg.appendChild(el("text", { x: ML - 8, y: Y(v2) + 4, "text-anchor": "end", class: "ex-muted" }, lbl(v2)));
    });
    svg.appendChild(el("rect", { x: ML, y: MT, width: W - ML - MR, height: H - MT - MB,
      fill: "none", stroke: AXIS }));
    // 1:1 line (forecast = observed); straight on matching log axes.
    const d0 = Math.max(xMin, yMin), d1 = Math.min(xMax, yMax);
    if (d1 > d0) {
      svg.appendChild(el("line", { x1: X(d0), y1: Y(d0), x2: X(d1), y2: Y(d1), stroke: AXIS }));
    }
    // Tercile boundaries: dashed reference lines (not gridlines), labelled at the frame.
    const ref = { stroke: "#9db1b3", "stroke-dasharray": "3 3" };
    [tl.f, tu.f].forEach((v2) => svg.appendChild(el("line", { x1: X(v2), x2: X(v2), y1: MT, y2: H - MB, ...ref })));
    [tl.o, tu.o].forEach((v2) => svg.appendChild(el("line", { x1: ML, x2: W - MR, y1: Y(v2), y2: Y(v2), ...ref })));
    svg.appendChild(el("text", { x: X(tl.f) - 4, y: H - MB - 6, "text-anchor": "end", class: "ex-muted ex-small" },
      "lower third"));
    svg.appendChild(el("text", { x: X(tu.f) + 4, y: H - MB - 6, class: "ex-muted ex-small" }, "upper third"));
    svg.appendChild(el("text", { x: W - MR - 4, y: Y(tl.o) + 12, "text-anchor": "end", class: "ex-muted ex-small" },
      "lower third"));
    svg.appendChild(el("text", { x: ML + 4, y: Y(tu.o) - 5, class: "ex-muted ex-small" }, "upper third"));

    // This issuance's forecast: a vertical reference line (no observation yet), labelled
    // at the top where the dry-year cluster is least likely to be.
    if (s.curF != null) {
      const cx = X(s.curF);
      svg.appendChild(el("line", { x1: cx, x2: cx, y1: MT, y2: H - MB, stroke: INK,
        "stroke-width": 1.5, "stroke-dasharray": "5 4" }));
      svg.appendChild(el("text", { x: cx - 5, y: MT + 6, class: "ex-cur", "text-anchor": "end",
        transform: `rotate(-90 ${cx - 5} ${MT + 6})` }, `${t.season_year} forecast: ${fmt(s.curF)} mm/day`));
    }
    // Years as the markers (as the old apps drew them), each with a hover target.
    hist.forEach((h) => {
      const g = el("g", {});
      g.appendChild(el("circle", { cx: X(h.f), cy: Y(h.o), r: 9, fill: "transparent" }));
      g.appendChild(el("text", { x: X(h.f), y: Y(h.o) + 3.5, "text-anchor": "middle", class: "ex-yr" },
        String(h.yr)));
      tip(g, `${h.yr}: forecast ${fmt(h.f)} mm/day, observed ${fmt(h.o)} mm/day`);
      svg.appendChild(g);
    });
    svg.appendChild(el("text", { x: (ML + W - MR) / 2, y: H - 12, "text-anchor": "middle", class: "ex-axttl" },
      `SEAS5 forecast issued ${MON[IDX.issued_month]}, matched to ERA5 (mm/day, log scale)`));
    svg.appendChild(el("text", { x: 16, y: (MT + H - MB) / 2, "text-anchor": "middle", class: "ex-axttl",
      transform: `rotate(-90 16 ${(MT + H - MB) / 2})` }, "ERA5 observed (mm/day, log scale)"));
    host.replaceChildren(svg);

    // Hit rates on the plotted values (as the old app computed them), r from the pipeline.
    const st = v[S.ver];
    const dry = hist.filter((h) => h.o < tl.o), wet = hist.filter((h) => h.o > tu.o);
    const dryHit = dry.filter((h) => h.f < tl.f).length, wetHit = wet.filter((h) => h.f > tu.f).length;
    const pctTxt = (a, b) => (b ? ` (${Math.round((100 * a) / b)}%)` : "");
    stats.innerHTML =
      `<strong>${VER_LABEL[S.ver]}</strong> · correlation r = <strong>${fmt(st?.r)}</strong>` +
      ` over ${st?.n ?? hist.length} years · ` +
      `dry years (observed in the lower tercile) also forecast in the lower tercile: ` +
      `<strong>${dryHit} of ${dry.length}</strong>${pctTxt(dryHit, dry.length)} · ` +
      `wet years forecast in the upper tercile: <strong>${wetHit} of ${wet.length}</strong>` +
      `${pctTxt(wetHit, wet.length)} · chance alone would give about 1 in 3.`;
  }

  // ── 3. the record: forecast and observed series, raw vs detrended ──────────
  function drawTrend() {
    const host = $("ex-trend");
    const t = triMeta(S.tri), v = unit.tri[S.tri];
    if (!t || !v) { host.textContent = ""; return; }
    const s = series(v);
    const panels = [
      ["fc", "SEAS5 forecast, matched to ERA5 (mm/day)", s.raw.fcAll, s.dt.fcAll],
      ["obs", "ERA5 observed (mm/day)", s.raw.obsAll, s.dt.obsAll],
    ];
    const W = 560, H = 230, ML = 52, MR = 14, MT = 34, MB = 30;
    const svgs = panels.map(([key, title, raw, dt]) => {
      const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "ex-svg", role: "img", "aria-label": title });
      const vals = raw.concat(dt).filter((x) => x != null);
      if (!vals.length) return svg;
      const yr0 = s.y0, yr1 = s.y0 + s.n - 1;
      const lo = Math.min(...vals), hi = Math.max(...vals), pad = 0.08 * (hi - lo || 1);
      const X = (yr) => ML + ((yr - yr0) / Math.max(1, yr1 - yr0)) * (W - ML - MR);
      const Y = (x) => MT + (1 - (x - (lo - pad)) / (hi - lo + 2 * pad)) * (H - MT - MB);
      const ty = ticks(lo - pad, hi + pad, 4);
      ty.out.forEach((x) => {
        svg.appendChild(el("line", { x1: ML, x2: W - MR, y1: Y(x), y2: Y(x), stroke: GRID }));
        svg.appendChild(el("text", { x: ML - 6, y: Y(x) + 4, "text-anchor": "end", class: "ex-muted" },
          tickLbl(x, ty.step)));
      });
      for (let yr = Math.ceil(yr0 / 10) * 10; yr <= yr1; yr += 10) {
        svg.appendChild(el("text", { x: X(yr), y: H - MB + 16, "text-anchor": "middle", class: "ex-muted" },
          String(yr)));
      }
      svg.appendChild(el("text", { x: ML, y: 14, class: "ex-axttl" }, title));
      // Raw-series trend (exported: mm/day per decade + p), drawn over the fitted years.
      const tr = v.trend?.[key];
      if (tr) {
        // The exporter's fit years: forecast = overlap years (observed too), not this
        // issuance; observed = every observed year.
        const pts = raw.map((x, i) => [s.y0 + i, x])
          .filter(([yr, x], i) => x != null &&
            (key === "obs" || (s.raw.obsAll[i] != null && yr !== v.cur)));
        const mx = pts.reduce((a, p) => a + p[0], 0) / pts.length;
        const my = pts.reduce((a, p) => a + p[1], 0) / pts.length;
        const slope = tr[0] / 10, ya = my + slope * (pts[0][0] - mx), yb = my + slope * (pts.at(-1)[0] - mx);
        svg.appendChild(el("line", { x1: X(pts[0][0]), y1: Y(ya), x2: X(pts.at(-1)[0]), y2: Y(yb),
          stroke: COL.raw, "stroke-width": 2, "stroke-linecap": "round" }));
        svg.appendChild(el("text", { x: W - MR, y: 14, "text-anchor": "end", class: "ex-muted" },
          `raw trend ${tr[0] >= 0 ? "+" : "−"}${Math.abs(tr[0]).toFixed(2)} mm/day per decade ` +
          `(p ${tr[1] < 0.001 ? "< 0.001" : "= " + tr[1].toFixed(3)})`));
      }
      // Detrended drawn larger and first, raw on top: where detrending changes nothing
      // the raw dot sits inside an aqua halo instead of hiding it.
      for (const [k, arr, rad] of [["dt", dt, 5.5], ["raw", raw, 3.5]]) {
        arr.forEach((x, i) => {
          if (x == null) return;
          const yr = s.y0 + i, isCur = yr === v.cur;
          const c = el("circle", { cx: X(yr), cy: Y(x), r: rad, fill: isCur ? "#fff" : COL[k],
            stroke: isCur ? COL[k] : "#fff", "stroke-width": isCur ? 2 : 1.5 });
          tip(c, `${yr}${isCur ? " (this forecast)" : ""} — ${VER_LABEL[k]}: ${fmt(x)} mm/day`);
          svg.appendChild(c);
        });
      }
      return svg;
    });
    host.replaceChildren(...svgs);
  }

  // ── 4. skill for every issue month (skill.js heatmap) ──────────────────────
  function drawSkill() {
    const host = $("ex-skill");
    if (typeof window.renderSkillChart !== "function") { host.textContent = ""; return; }
    const r = (S.ver === "raw" ? unit.r_raw : unit.r_dt)
      .map((row) => row.map((x) => (x == null ? null : x / 100)));
    window.renderSkillChart(host, { trimesters: IDX.all_trimesters, leads: IDX.leads,
      thresholds: IDX.thresholds }, { name: unitName, clim_monthly: unit.clim_monthly,
      rainy: unit.rainy, r });
  }

  // ── selectors ──────────────────────────────────────────────────────────────
  function fillSelect(sel, opts, value) {
    sel.replaceChildren(...opts.map(([v, label]) => {
      const o = document.createElement("option"); o.value = v; o.textContent = label; return o;
    }));
    if (value != null && opts.some(([v]) => v === value)) sel.value = value;
  }

  async function selectCountry(iso3) {
    S.iso3 = iso3;
    country = await getJSON(`${BASE}${iso3}.json.gz`);
    if (S.iso3 !== iso3) return false; // superseded by a later pick
    const a1 = country.adm1;
    fillSelect($("ex-adm1"), [["", a1.length ? "Whole country" : "No admin 1 data"]]
      .concat(a1.map(([p, n]) => [p, n])), S.adm1);
    $("ex-adm1").disabled = !a1.length;
    if (!a1.some(([p]) => p === S.adm1)) S.adm1 = "";
    return selectAdm1(S.adm1);
  }

  async function selectAdm1(pcode) {
    S.adm1 = pcode;
    const entry = country.adm1.find(([p]) => p === pcode);
    adm2File = null;
    if (entry && entry[2]) {
      adm2File = await getJSON(`${BASE}adm2/${pcode}.json.gz`);
      if (S.adm1 !== pcode) return false;
    }
    const sel = $("ex-adm2");
    if (adm2File) {
      fillSelect(sel, [["", "Whole admin 1"]].concat(
        Object.entries(adm2File.units).map(([p, u]) => [p, u.name])), S.adm2);
      sel.disabled = false;
      if (!(S.adm2 in adm2File.units)) S.adm2 = "";
    } else {
      const why = !pcode ? "Pick an admin 1 first"
        : IDX.countries.find((c) => c.iso3 === S.iso3)?.adm2 ? "No admin 2 data here"
          : "Not available for this country";
      fillSelect(sel, [["", why]], "");
      sel.disabled = true;
      S.adm2 = "";
    }
    return true;
  }

  function resolveUnit() {
    const cName = country.name;
    if (S.adm2 && adm2File) {
      unit = adm2File.units[S.adm2];
      const a1 = country.adm1.find(([p]) => p === S.adm1)?.[1] ?? "";
      unitName = `${unit.name}, ${a1}, ${cName}`;
      return "Admin 2";
    }
    if (S.adm1) {
      unit = country.units[S.adm1];
      unitName = `${unit.name}, ${cName}`;
      return "Admin 1";
    }
    unit = country.units[country.pcode];
    unitName = cName;
    return "Country";
  }

  function syncURL() {
    if (location.hash.replace("#", "") !== "explorer") return;
    const q = new URLSearchParams(location.search);
    q.set("ex", [S.iso3, S.adm1, S.adm2].filter(Boolean).join("."));
    q.set("ex_tri", S.tri);
    q.set("ex_v", S.ver);
    history.replaceState(null, "", `?${q}#explorer`);
  }

  function update() {
    const level = resolveUnit();
    const avail = IDX.trimesters.filter((t) => unit.tri[t.key]).map((t) => t.key);
    if (!avail.includes(S.tri)) {
      S.tri = avail.includes(IDX.default_trimester) ? IDX.default_trimester : avail[0];
    }
    fillSelect($("ex-tri"), IDX.trimesters.filter((t) => avail.includes(t.key))
      .map((t) => [t.key, `${t.label} ${t.season_year} (${leadTxt(t.lead)})`]), S.tri);
    document.querySelectorAll("#ex-ver .seg-btn").forEach((b) =>
      b.classList.toggle("active", b.dataset.v === S.ver));
    $("ex-title").textContent = `${unitName} — SEAS5 issued ${IDX.issued_label}`;
    $("ex-level").textContent = level;
    $("ex-scatter-h").textContent =
      `${triMeta(S.tri)?.label ?? ""}: forecast against observed, one point per year`;
    $("ex-trend-h").textContent = `${triMeta(S.tri)?.label ?? ""}: the record, raw and detrended`;
    if (!avail.length) {
      ["ex-outlook", "ex-scatter", "ex-trend"].forEach((id) => { $(id).textContent = "No data for this unit."; });
      $("ex-table").innerHTML = ""; $("ex-scatter-stats").textContent = "";
    } else {
      drawOutlook(); drawTable(); drawScatter(); drawTrend();
    }
    drawSkill();
    syncURL();
  }

  async function run(step) {
    $("ex-status").textContent = "Loading…";
    try {
      if ((await step()) !== false) update();
      $("ex-status").textContent = "";
    } catch (e) {
      console.error(e);
      $("ex-status").textContent = "Could not load this unit's data.";
    }
  }

  let started = false;
  async function init() {
    if (started) return;
    started = true;
    try {
      IDX = await getJSON(`${BASE}index.json`);
    } catch {
      $("ex-title").textContent = "Admin explorer — data not built for this issuance yet";
      return; // payload not in this bundle yet: leave the tab empty
    }
    const q = new URLSearchParams(location.search);
    const [qIso, qA1, qA2] = (q.get("ex") || "").split(".");
    const isos = IDX.countries.map((c) => c.iso3);
    S.iso3 = isos.includes(qIso) ? qIso : (["KEN", "ETH", "SOM"].find((c) => isos.includes(c)) || isos[0]);
    S.adm1 = qA1 || ""; S.adm2 = qA2 || "";
    S.tri = q.get("ex_tri") || IDX.default_trimester;
    S.ver = q.get("ex_v") === "raw" ? "raw" : "dt";
    fillSelect($("ex-country"), IDX.countries.map((c) => [c.iso3, c.name]), S.iso3);

    $("ex-country").addEventListener("change", (e) => {
      S.adm1 = ""; S.adm2 = ""; run(() => selectCountry(e.target.value));
    });
    $("ex-adm1").addEventListener("change", (e) => { S.adm2 = ""; run(() => selectAdm1(e.target.value)); });
    $("ex-adm2").addEventListener("change", (e) => { S.adm2 = e.target.value; update(); });
    $("ex-tri").addEventListener("change", (e) => { S.tri = e.target.value; update(); });
    document.querySelectorAll("#ex-ver .seg-btn").forEach((b) => b.addEventListener("click", () => {
      S.ver = b.dataset.v; update();
    }));
    run(() => selectCountry(S.iso3));
  }

  window.tabShown = window.tabShown || {};
  window.tabShown.explorer = init;
  // Opened straight on #explorer: app.js showed the tab before this script registered
  // its callback, so start here.
  const panel = $("tab-explorer");
  if (panel && !panel.hidden) init();
})();
