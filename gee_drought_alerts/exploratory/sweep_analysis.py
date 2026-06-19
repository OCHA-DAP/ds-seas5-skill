"""Analyze the skill-threshold sweep: coverage vs cutoff, and alert robustness."""
import pandas as pd

THRESHOLDS = [0.20, 0.25, 0.30, 0.35]
TAGS = {r: 'r%02d' % int(round(r * 100)) for r in THRESHOLDS}
adm0 = {r: pd.read_parquet('data/sweep/adm0_%s.parquet' % t) for r, t in TAGS.items()}

# 1. coverage / tier counts per threshold
print('── per-threshold summary (adm0) ──')
print('%5s %10s %12s %7s %7s' % ('r', 'evaluable', 'not_eval', 'alert', 'watch'))
for r in THRESHOLDS:
    d = adm0[r]
    ev = d[d.state == 'evaluable']
    print('%5.2f %10d %12d %7d %7d' % (
        r, len(ev), (d.state == 'not_evaluable_low_skill').sum(),
        (ev.tier == 'alert').sum(), (ev.tier == 'watch').sum()))

# 2. alert robustness across thresholds
key = ['admin', 'season']
piv = None
for r in THRESHOLDS:
    s = adm0[r].set_index(key)['tier'].rename(TAGS[r])
    piv = s.to_frame() if piv is None else piv.join(s, how='outer')
piv = piv.fillna('not_eval')

alert_cols = list(TAGS.values())
is_alert = (piv[alert_cols] == 'alert')
ever_alert = piv[is_alert.any(axis=1)].copy()
ever_alert['n_thresh_alert'] = is_alert.loc[ever_alert.index].sum(axis=1)

robust = ever_alert[ever_alert[TAGS[0.35]] == 'alert']      # alert even at strictest gate
marginal = ever_alert[(ever_alert[TAGS[0.35]] != 'alert')]  # only emerges when relaxed

print('\n── alert robustness ──')
print('total (admin x season) ever flagged alert in sweep:', len(ever_alert))
print('  robust  (alert at r>=0.35, strictest):', len(robust))
print('  marginal(only at relaxed r<0.35)     :', len(marginal))
print('\n# alerts by how many thresholds they fire at:')
print(ever_alert.n_thresh_alert.value_counts().sort_index().to_string())

# 3. what does relaxing surface? newly-evaluable-and-alert at r=0.20 that were
#    not even evaluable at r=0.35 — credible signal or noise?
d20, d35 = adm0[0.20].set_index(key), adm0[0.35].set_index(key)
newly = d20[(d20.tier == 'alert') & (d35.reindex(d20.index).state != 'evaluable')]
print('\n── alerts at r=0.20 that were NOT evaluable at r=0.35 (surfaced by relaxing) ──')
print('count:', len(newly))
if len(newly):
    print(newly.sort_values('z')[['z', 'dry_rp', 'cov_combined', 'dry_frac_footprint']]
          .head(25).to_string())
