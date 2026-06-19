"""
Backtest verification: choose the skill threshold by drought-detection skill.
=============================================================================

Reads the SEAS5-forecast + ERA5-observed seasonal aggregates from
backtest_datagen.py, rolls adm1 -> adm0, and scores forecast dry-tail events
against ERA5 observed dry-tail events (leave-one-out) across:
    skill threshold  x  event return period {3, 5, 7}.

Both forecast and observed use the SAME Weibull dry-RP rule, so the comparison is
rank-based and unaffected by SEAS5's units. Misses are weighted heavily in the
read-out (POD prioritised) because a missed drought is the costly error.

Run: .venv/bin/python exploratory/backtest_verify.py
"""
import glob
import os
from collections import defaultdict

import numpy as np
import pandas as pd

EVENT_RPS = [3, 5, 7]
INSEASON_MIN, EVALUABLE_MIN = 0.10, 0.25
AREA_FLOOR_KM2 = 25000                  # OR-gate: enough trusted ground by area (see pipeline)
SCORE_YEARS = list(range(1993, 2026))   # ERA5 obs available through 2025
SCEN = sorted(glob.glob('data/backtest/adm1_r*.parquet'))


def tag_to_r(path):
    t = os.path.basename(path).split('_')[1].split('.')[0]   # 'r30'
    return int(t[1:]) / 100.0


def rollup_adm0(df):
    """adm1 -> adm0 per season: area-weighted (by combined masked area) SEAS5 & ERA5
    series, plus area-weighted coverage. Returns {(name0, season): dict}."""
    syrs = [c for c in df.columns if c.startswith('s') and c[1:].isdigit()]
    eyrs = [c for c in df.columns if c.startswith('e') and c[1:].isdigit()]
    out = {}
    for (name0, season), g in df.groupby(['name0', 'season']):
        A = g['area_m2'].fillna(0).to_numpy()
        Atot = A.sum() or 1.0
        w = (g['cov_combined'].fillna(0).to_numpy()) * A          # combined masked area
        W = w.sum()
        cov_in = float((g['cov_inseason'].fillna(0).to_numpy() * A).sum() / Atot)
        cov_cb = float(w.sum() / Atot)
        combined_km2 = float(w.sum() / 1e6)        # absolute in-season+skilled area

        def wseries(cols, prefix):
            s = {}
            for c in cols:
                vals = g[c].to_numpy(dtype=float)
                mask = ~np.isnan(vals) & (w > 0)
                s[int(c[1:])] = float((vals[mask] * w[mask]).sum() / w[mask].sum()) if mask.any() and w[mask].sum() > 0 else None
            return s
        out[(name0, season)] = dict(
            cov_inseason=cov_in, cov_combined=cov_cb, combined_area_km2=combined_km2,
            seas5=wseries(syrs, 's'), era5=wseries(eyrs, 'e'))
    return out


def is_dry(series, year, rp):
    """Weibull dry-RP rule (matches the alert pipeline), leave-one-out."""
    cur = series.get(year)
    hist = [v for y, v in series.items() if y != year and v is not None and y in SCORE_YEARS]
    if cur is None or len(hist) < 15:
        return None
    N = len(hist)
    K = sum(1 for v in hist if v <= cur)
    return (N + 1) / (K + 1) >= rp


def contingency(rolled, rp):
    a = b = c = d = 0           # hits, false alarms, misses, correct negatives
    n_units = 0
    for (name0, season), u in rolled.items():
        # same OR-gate as the pipeline: enough trusted ground by fraction OR area
        if u['cov_inseason'] < INSEASON_MIN:
            continue
        if u['cov_combined'] < EVALUABLE_MIN and u['combined_area_km2'] < AREA_FLOOR_KM2:
            continue
        n_units += 1
        for y in SCORE_YEARS:
            f = is_dry(u['seas5'], y, rp)
            o = is_dry(u['era5'], y, rp)
            if f is None or o is None:
                continue
            if f and o:
                a += 1
            elif f and not o:
                b += 1
            elif (not f) and o:
                c += 1
            else:
                d += 1
    return a, b, c, d, n_units


def z_of(series, year):
    """Standardized anomaly of `year` vs the leave-one-out climatology."""
    cur = series.get(year)
    hist = [v for y, v in series.items() if y != year and v is not None and y in SCORE_YEARS]
    if cur is None or len(hist) < 15:
        return None
    mean = sum(hist) / len(hist)
    sd = (sum((v - mean) ** 2 for v in hist) / len(hist)) ** 0.5
    return (cur - mean) / sd if sd > 0 else None


def is_alert(series, year, rp, z_gate):
    """Forecast fires only if BOTH the dry rank AND the magnitude clear the bar —
    i.e. the actual alert rule, not just the raw dry-tail event."""
    dry = is_dry(series, year, rp)
    z = z_of(series, year)
    if dry is None or z is None:
        return None
    return dry and (z <= z_gate)


def evaluable(u):
    return (u['cov_inseason'] >= INSEASON_MIN and
            (u['cov_combined'] >= EVALUABLE_MIN or u['combined_area_km2'] >= AREA_FLOOR_KM2))


def observed_truth(rolled, rp):
    """Observed dry events per (unit, year) over the addressable universe (units
    evaluable at the loosest gate). ERA5 truth, fixed across thresholds."""
    truth = {}
    for k, u in rolled.items():
        if not evaluable(u):
            continue
        ev = {y: is_dry(u['era5'], y, rp) for y in SCORE_YEARS}
        truth[k] = {y: o for y, o in ev.items() if o is not None}
    return truth


def contingency_system(rolled_T, truth, rp, z_gate=None):
    """Fixed-universe: a unit not evaluable at threshold T never fires, so its
    observed droughts become misses — this is what penalises a strict gate for
    excluding addressable area (the cost-of-miss view). With z_gate set, the
    forecast fires on the actual rank-AND-z alert rather than the raw dry event."""
    a = b = c = d = 0
    active = 0
    for k, obs in truth.items():
        uT = rolled_T.get(k)
        is_active = uT is not None and evaluable(uT)
        active += int(is_active)
        for y, o in obs.items():
            if is_active:
                f = is_alert(uT['seas5'], y, rp, z_gate) if z_gate is not None else is_dry(uT['seas5'], y, rp)
            else:
                f = False
            if f is None:
                f = False
            if f and o:
                a += 1
            elif f and not o:
                b += 1
            elif (not f) and o:
                c += 1
            else:
                d += 1
    return a, b, c, d, active


def scores(a, b, c, d):
    pod = a / (a + c) if (a + c) else float('nan')           # hit rate (1 - miss rate)
    far = b / (a + b) if (a + b) else float('nan')           # false-alarm ratio
    pofd = b / (b + d) if (b + d) else float('nan')
    bias = (a + b) / (a + c) if (a + c) else float('nan')
    denom = (a + c) * (c + d) + (a + b) * (b + d)
    hss = 2 * (a * d - b * c) / denom if denom else float('nan')   # Heidke
    pss = pod - pofd                                                # Peirce
    return dict(POD=pod, FAR=far, bias=bias, HSS=hss, PSS=pss)


def main():
    if not SCEN:
        print('no backtest parquets yet in data/backtest/'); return
    rolled_by_r = {tag_to_r(p): rollup_adm0(pd.read_parquet(p)) for p in SCEN}
    loosest = min(rolled_by_r)                      # truth universe = loosest gate
    truth = {rp: observed_truth(rolled_by_r[loosest], rp) for rp in EVENT_RPS}

    Z_GATE = -1.0    # the rank-AND-z alert (watch level) for the precision test
    cond, syst, alrt = [], [], []
    for r, rolled in sorted(rolled_by_r.items()):
        for rp in EVENT_RPS:
            a, b, c, d, n = contingency(rolled, rp)
            cond.append(dict(r=r, event_rp=rp, n_units=n,
                             **{k: round(v, 3) for k, v in scores(a, b, c, d).items()}))
            a, b, c, d, act = contingency_system(rolled, truth[rp], rp)
            sc = scores(a, b, c, d)
            syst.append(dict(r=r, event_rp=rp, active_units=act, hits=a, misses=c, false_alarms=b,
                             POD=round(sc['POD'], 3), FAR=round(sc['FAR'], 3), PSS=round(sc['PSS'], 3)))
            a, b, c, d, _ = contingency_system(rolled, truth[rp], rp, z_gate=Z_GATE)
            sc = scores(a, b, c, d)
            alrt.append(dict(r=r, event_rp=rp, hits=a, misses=c, false_alarms=b,
                             POD=round(sc['POD'], 3), FAR=round(sc['FAR'], 3), PSS=round(sc['PSS'], 3)))

    pd.set_option('display.width', 160)
    cond_df, syst_df, alrt_df = pd.DataFrame(cond), pd.DataFrame(syst), pd.DataFrame(alrt)
    print('── (A) CONDITIONAL skill: among units evaluable at each gate (per-alert quality) ──')
    print(cond_df.to_string(index=False))
    print('\n── (B) SYSTEM skill (raw dry event): fixed universe; excluded units never fire,')
    print('       so their droughts become misses (cost-of-miss / coverage view) ──')
    print(syst_df.to_string(index=False))
    print('\n── (C) SYSTEM skill, rank-AND-z ALERT (z<=%.1f): the actual alert rule, same universe.' % Z_GATE)
    print('       Compare to (B) to see what the z-gate buys: lower FAR at the cost of POD? ──')
    print(alrt_df.to_string(index=False))
    print('\nRead: (A) favours a strict gate (per-alert reliability); (B) favours a loose gate')
    print('(catch all addressable droughts). (C) shows whether the alert criteria, not the skill')
    print('gate, can carry precision — letting us keep the gate loose for recall.')
    cond_df.to_parquet('data/backtest/verification_conditional.parquet', index=False)
    syst_df.to_parquet('data/backtest/verification_system.parquet', index=False)
    alrt_df.to_parquet('data/backtest/verification_alert.parquet', index=False)
    print('\nwrote data/backtest/verification_{conditional,system,alert}.parquet')


if __name__ == '__main__':
    main()
