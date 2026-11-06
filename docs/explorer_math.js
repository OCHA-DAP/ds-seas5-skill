// Pure maths for the Admin explorer tab (explorer.js). No DOM: it also runs under
// Node for pipeline/check_explorer_detrend.py, which checks `detrend` and
// `detrendObserved` against the pipeline's own detrended series
// (paired_yearly_detrended*.parquet).
(function (root) {
  "use strict";

  // Minimum overlap years for the pipeline to detrend at all (src/constants.py MIN_YEARS).
  const MIN_YEARS = 10;

  // Integer-encoded log1p(mm/day) -> log1p(mm/day); nulls stay null.
  function decode(arr, scale) {
    return arr.map((v) => (v == null ? null : v / scale));
  }

  // Ordinary least squares y = a*x + b.
  function linfit(xs, ys) {
    const n = xs.length;
    let mx = 0, my = 0;
    for (let i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
    mx /= n; my /= n;
    let sxy = 0, sxx = 0;
    for (let i = 0; i < n; i++) {
      sxy += (xs[i] - mx) * (ys[i] - my);
      sxx += (xs[i] - mx) * (xs[i] - mx);
    }
    const a = sxx > 0 ? sxy / sxx : 0;
    return { a, b: my - a * mx, meanY: my };
  }

  // The pipeline's detrending (src/skill.py run_all_combinations, detrend=True), on one
  // (issue month, trimester) series in log space. Fit years = the overlap (forecast AND
  // observed present). Each series loses its own fitted line and gets the overlap mean
  // back: x_dt = x - (a*year + b) + mean(x over the overlap). The forecast fit is applied
  // to every forecast year (the current one too); the obs fit to every observed year.
  // Fewer than MIN_YEARS overlap years: the pipeline leaves both series as they are.
  function detrend(y0, fc, obs) {
    const yrs = [], f = [], o = [];
    for (let i = 0; i < fc.length; i++) {
      if (fc[i] != null && obs[i] != null) { yrs.push(y0 + i); f.push(fc[i]); o.push(obs[i]); }
    }
    if (yrs.length < MIN_YEARS) return { fc: fc.slice(), obs: obs.slice() };
    const ff = linfit(yrs, f), fo = linfit(yrs, o);
    return {
      fc: fc.map((v, i) => (v == null ? null : v - (ff.a * (y0 + i) + ff.b) + ff.meanY)),
      obs: obs.map((v, i) => (v == null ? null : v - (fo.a * (y0 + i) + fo.b) + fo.meanY)),
    };
  }

  // The fully observed trimester (lead -3: ERA5 only, no forecast), as
  // src/skill.py observed_trimester_stats detrends it: the newest season with a value
  // is out of sample; the line is fitted to the EARLIER seasons and every season loses
  // it and gets the earlier seasons' mean back. Fewer than MIN_YEARS earlier seasons:
  // left as is. `fc` is all null for this trimester and is passed through.
  function detrendObserved(y0, fc, obs) {
    let last = -1;
    for (let i = 0; i < obs.length; i++) if (obs[i] != null) last = i;
    const yrs = [], o = [];
    for (let i = 0; i < last; i++) {
      if (obs[i] != null) { yrs.push(y0 + i); o.push(obs[i]); }
    }
    if (last < 0 || yrs.length < MIN_YEARS) return { fc: fc.slice(), obs: obs.slice() };
    const fo = linfit(yrs, o);
    return {
      fc: fc.slice(),
      obs: obs.map((v, i) => (v == null ? null : v - (fo.a * (y0 + i) + fo.b) + fo.meanY)),
    };
  }

  function pearson(xs, ys) {
    const n = xs.length;
    if (n < 3) return null;
    let mx = 0, my = 0;
    for (let i = 0; i < n; i++) { mx += xs[i]; my += ys[i]; }
    mx /= n; my /= n;
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) {
      const dx = xs[i] - mx, dy = ys[i] - my;
      sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
    }
    return sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : null;
  }

  // pandas Series.quantile default (linear interpolation, "type 7").
  function quantile(values, q) {
    const s = values.slice().sort((a, b) => a - b);
    if (!s.length) return null;
    const h = (s.length - 1) * q, lo = Math.floor(h), hi = Math.ceil(h);
    return s[lo] + (h - lo) * (s[hi] - s[lo]);
  }

  const api = { MIN_YEARS, decode, linfit, detrend, detrendObserved, pearson, quantile };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ExplorerMath = api;
})(typeof window !== "undefined" ? window : globalThis);
