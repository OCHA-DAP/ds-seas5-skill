"""
SEAS5 seasonal drought alerts aggregated to admin units (Path B).
=================================================================

For a fixed issuance (default: latest = June), aggregate the SEAS5 seasonal
precip forecast to admin units over a FROZEN, time-invariant footprint
(skill mask AND in-season PSP mask), then place the forecast in each unit's
own climatological distribution via Weibull percentile / return period.

Rows = (admin unit x forecastable trimester). From a June issuance the fully
reachable trimesters (leadtimes 0-6, valid Jun-Dec) are JJA, JAS, ASO, SON, OND.
The PSP mask does NOT pick a season; it gates which (unit x season) rows are
"live" (in-season for that unit). A bimodal unit naturally yields two live rows.

Why aggregate-then-classify, and why a frozen mask:
  - The trigger is the return period of the *admin-level* aggregate, which is a
    clean, defensible statistic (vs. averaging per-pixel ranks).
  - Skill (Spearman r vs ERA5-Land) and PSP are both time-invariant. We compute
    ONE combined mask and aggregate the SAME footprint across every climatology
    year and the forecast year. Recomputing the mask per year (or letting the
    forecast year leak into it) would make the percentile/RP meaningless.

Skill and trigger are both rank-based, so SEAS5's units (a ~mm/day rate, unlike
ERA5's accumulated metres) do not affect them. The aggregate is reported as a
relative index, not absolute rainfall.

Admin-agnostic: swap ADMIN_FC / ADMIN_ID_FIELD to move adm0 -> adm1.

Run: .venv/bin/python exploratory/seas5_admin_drought_alerts.py
"""
from __future__ import annotations

import ee
import pandas as pd

# ── config ──────────────────────────────────────────────────────────────────
ISSUED_MONTH = 6                 # June issuance (latest)
FORECAST_YEAR = 2026             # the live forecast valid-year
START_YEAR, END_YEAR = 1993, 2025  # climatology range (app default)
# Skill gate. Chosen by BACKTEST against ERA5 (backtest_verify.py), not significance.
# In the system / cost-of-miss view, a looser gate detects more of all addressable
# droughts (system POD/PSS peak at the loosest tested cutoff) for only small extra
# false alarms; misses are the costly error, so we go loose. 0.20 is the loosest
# tested (testing < 0.20 and a regional gate are flagged next steps). The skill gate
# is the RECALL knob; precision is carried by the alert criteria below.
R_THRESH = 0.20
PSP_LANDUSE = 'either'           # 'crop' | 'range' | 'either'

# Evaluability / season-gating. A fraction alone is unit-size-blind: 25% of Sudan
# is a far larger absolute area than 25% of El Salvador. So a unit is evaluable if
# it has enough trusted ground by EITHER measure — fraction OR absolute area.
INSEASON_MIN = 0.10              # below this PSP coverage => season not relevant here
EVALUABLE_MIN = 0.25             # in-season AND skilled coverage fraction, OR ...
AREA_FLOOR_KM2 = 25000           # ... this much absolute in-season+skilled area (~13 SEAS5
                                 # pixels). Provisional — tune jointly via the backtest.

# Drought tiers (backtest-driven): fire broadly for recall, escalate by magnitude.
# The backtest showed the skill gate + a below-normal (dry RP>=3) event maximises
# detection, and that GATING on z HURTS detection (the ensemble mean's variance is
# damped, so real-but-mild-magnitude dry forecasts get filtered out). So:
#   WATCH  fires on the dry event alone (recall-optimised);
#   ALERT  escalates a watch when it is severe/widespread, where magnitude (z),
#          fractional extent, OR absolute dry area can EACH escalate (OR -> escalation
#          never suppresses a watch, so recall is preserved). Absolute area earns a
#          role here without repeating the z-gate's recall cost.
DROUGHT_WATCH_RP = 3.0           # fire: forecast dry RP >= 3 (below-normal)
ESC_Z = -1.5                     # escalate to ALERT if z <= this, OR ...
ESC_DRY_FRAC = 0.50              # ... >= this share of footprint forecast dry, OR ...
ESC_DRY_AREA_KM2 = 100000        # ... this much absolute dry area. Provisional; tune via backtest.
FLOOD_WATCH_RP = 3.0             # wet-tail analogue — UNVALIDATED weak proxy (see book chapter)

# Areal-extent metric: a pixel counts as "in drought" if its standardized
# anomaly vs its own climatology is <= this. Reported as columns (dry_area_km2,
# dry_frac_footprint); not yet folded into the tier rule.
PIX_DRY_Z = -1.0

# Trimesters reachable from a June issuance (all contiguous, same calendar year).
SEASONS = {
    'JJA': [6, 7, 8],
    'JAS': [7, 8, 9],
    'ASO': [8, 9, 10],
    'SON': [9, 10, 11],
    'OND': [10, 11, 12],
}

# Admin units: aggregate over the ASAP adm1 polygons the PSP is defined on
# (clean geometries; avoids the corrupt-vertex features in FAO GAUL), then roll
# up to adm0 in Python via masked-area weights. adm1 detail is kept for free.
ADMIN_FC = 'projects/ee-zackarno/assets/asap_psp_adm1_fc'
ADM1_ID = 'asap1_id'
ADM0_ID = 'asap0_id'
ADM0_NAME = 'name0'
COUNTRIES = None   # None => global; or a list of name0 for a quick subset

OUT_PATH = 'data/seas5_admin_drought_alerts_%04d-%02d.parquet' % (FORECAST_YEAR, ISSUED_MONTH)
OUT_PATH_ADM1 = 'data/seas5_adm1_drought_alerts_%04d-%02d.parquet' % (FORECAST_YEAR, ISSUED_MONTH)

# ── collections (mirror the app) ──────────────────────────────────────────────
ee.Initialize(project='ee-zackarno')

SEAS5 = (ee.ImageCollection('projects/ee-zackarno/assets/seas5_monthly')
         .filter(ee.Filter.notNull(['date_issued', 'date_valid', 'leadtime'])))
ERA5 = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR').select('total_precipitation_sum')

PSP_BAND_NAMES = ['%s_%s_m%02d' % (lu, s, m)
                  for lu in ('crop', 'range') for s in ('s1', 's2') for m in range(1, 13)]
PSP = ee.Image('projects/ee-zackarno/assets/asap_psp_adm1_mask').rename(PSP_BAND_NAMES)

admin = ee.FeatureCollection(ADMIN_FC)
if COUNTRIES:
    admin = admin.filter(ee.Filter.inList(ADM0_NAME, COUNTRIES))
# attach geodesic area (m^2) for the adm1 -> adm0 area-weighted rollup
admin = admin.map(lambda f: f.set('area_m2', f.geometry().area(1000)))

CLIM_YEARS = list(range(START_YEAR, END_YEAR + 1))
ALL_YEARS = CLIM_YEARS + ([FORECAST_YEAR] if FORECAST_YEAR not in CLIM_YEARS else [])
SCALE = (SEAS5.filter(ee.Filter.inList('leadtime', ['0'])).select('precip')
         .first().projection().nominalScale())

# ── helpers ───────────────────────────────────────────────────────────────────
def seas5_season(base, months, y):
    """Seasonal mean of the pre-filtered SEAS5 base for valid-year y (contiguous months)."""
    f = ee.Filter.And(ee.Filter.calendarRange(min(months), max(months), 'month'),
                      ee.Filter.calendarRange(y, y, 'year'))
    return base.filter(f).mean()


def era5_season(months, y):
    f = ee.Filter.And(ee.Filter.calendarRange(min(months), max(months), 'month'),
                      ee.Filter.calendarRange(y, y, 'year'))
    return ERA5.filter(f).mean()


def psp_mask_for(months):
    prefixes = []
    if PSP_LANDUSE in ('crop', 'either'):
        prefixes += ['crop_s1_', 'crop_s2_']
    if PSP_LANDUSE in ('range', 'either'):
        prefixes += ['range_s1_', 'range_s2_']
    bands = ['%sm%02d' % (p, m) for m in months for p in prefixes]
    return PSP.select(bands).reduce(ee.Reducer.max()).gt(0)


def season_image(months):
    """One image carrying per-year masked aggregates + coverage bands, for one season.

    Year bands are masked to the frozen footprint -> mean = aggregate over it.
    Coverage bands are unmasked 0/1 -> mean = fraction of the whole unit.
    reduceRegions honours each band's own mask, so a single call returns all of it.
    """
    lts = [str((vm - ISSUED_MONTH + 12) % 12) for vm in months]
    if any(int(lt) > 6 for lt in lts):
        return None  # not forecastable from this issuance
    base = (SEAS5.filter(ee.Filter.inList('leadtime', lts))
            .filter(ee.Filter.stringContains('date_issued', '-%02d-' % ISSUED_MONTH))
            .select('precip'))

    # frozen skill mask: Spearman r over the full climatology range
    paired = ee.ImageCollection([
        seas5_season(base, months, y).rename('seas5').addBands(era5_season(months, y).rename('era5'))
        for y in CLIM_YEARS
    ])
    corr = paired.reduce(ee.Reducer.spearmansCorrelation()).select('correlation')
    skill = corr.gte(R_THRESH)
    psp = psp_mask_for(months)
    combined = skill.unmask(0).And(psp.unmask(0))

    year_bands = [seas5_season(base, months, y).updateMask(combined).rename('y%d' % y)
                  for y in ALL_YEARS]

    # Per-pixel standardized anomaly of the forecast vs its own climatology, and
    # a 0/1 "in drought" band within the footprint — for the areal-extent metric.
    clim_ic = ee.ImageCollection([seas5_season(base, months, y) for y in CLIM_YEARS])
    cmean = clim_ic.mean().rename('precip')
    cstd = clim_ic.reduce(ee.Reducer.stdDev()).rename('precip')  # align band name for arithmetic
    pix_z = seas5_season(base, months, FORECAST_YEAR).subtract(cmean).divide(cstd)
    dry01 = pix_z.lte(PIX_DRY_Z).And(combined).unmask(0).rename('dry01')

    cov_bands = [psp.unmask(0).rename('cov_inseason'),
                 skill.unmask(0).rename('cov_skill'),
                 combined.rename('cov_combined'),
                 dry01]
    return ee.Image.cat(year_bands + cov_bands)


def weibull(series, forecast_year):
    """Weibull percentile + dry/wet RP + standardized anomaly of forecast_year
    vs leave-one-out climatology.

    The rank-based RP saturates at N+1 once the forecast leaves the historical
    envelope, so it cannot grade extremes. The standardized anomaly z (current
    minus climatology mean over std) is an unsaturating severity that does — it
    is reported alongside the RP, not folded into the tier logic.
    """
    hist = [v for y, v in series.items()
            if START_YEAR <= y <= END_YEAR and y != forecast_year and v is not None]
    current = series.get(forecast_year)
    if current is None or len(hist) < 5:
        return None
    N = len(hist)
    K = sum(1 for v in hist if v <= current)
    mean = sum(hist) / N
    std = (sum((v - mean) ** 2 for v in hist) / N) ** 0.5
    return dict(N=N, K=K,
                pct=100.0 * (K + 1) / (N + 1),
                rp_dry=(N + 1) / (K + 1),
                rp_wet=(N + 1) / max(N - K, 1),
                z=(current - mean) / std if std > 0 else None)


# ── classification shared by adm0 and adm1 ────────────────────────────────────
def drought_tier(dry_rp, z, dry_frac, dry_area_km2):
    """WATCH on the dry event (recall); ALERT escalates the severe/widespread."""
    if dry_rp is None or dry_rp < DROUGHT_WATCH_RP:
        return 'none'
    escalate = ((z is not None and z <= ESC_Z)
                or (dry_frac is not None and dry_frac >= ESC_DRY_FRAC)
                or (dry_area_km2 is not None and dry_area_km2 >= ESC_DRY_AREA_KM2))
    return 'alert' if escalate else 'watch'


def flood_tier(wet_rp):
    """Wet-tail analogue. UNVALIDATED — seasonal precip is a weak flood proxy."""
    return 'watch' if (wet_rp is not None and wet_rp >= FLOOD_WATCH_RP) else 'none'


def classify(fin, fsk, fcb, series, dry_area, combined_area):
    """Build the metric/tier fields for one admin unit (adm0 or adm1) from its
    coverage fractions, masked precip series, and dry-extent areas. The continuous
    metrics (dry_rp, pct, z) are reported for EVERY evaluable unit — the tiers are a
    threshold layered on top, so a no-threshold return-period map uses the same table."""
    combined_km2 = combined_area / 1e6
    if fin < INSEASON_MIN:
        state = 'not_in_season'
    elif fcb < EVALUABLE_MIN and combined_km2 < AREA_FLOOR_KM2:
        state = 'not_evaluable_low_skill'   # too little trusted ground by fraction OR area
    else:
        state = 'evaluable'
    dry_frac = round(dry_area / combined_area, 3) if combined_area > 0 else None
    dry_area_km2 = round(dry_area / 1e6, 0)
    out = dict(state=state, cov_inseason=round(fin, 3), cov_skill=round(fsk, 3),
               cov_combined=round(fcb, 3), combined_area_km2=round(combined_km2, 0),
               dry_frac_footprint=dry_frac, dry_area_km2=dry_area_km2,
               pct=None, dry_rp=None, wet_rp=None, z=None, agg_index=None,
               drought_tier=None, flood_tier=None)
    if state == 'evaluable':
        wb = weibull(series, FORECAST_YEAR)
        if wb and series.get(FORECAST_YEAR) is not None:
            z = wb['z']
            out.update(agg_index=round(series[FORECAST_YEAR], 4), pct=round(wb['pct'], 0),
                       dry_rp=round(wb['rp_dry'], 1), wet_rp=round(wb['rp_wet'], 1),
                       z=round(z, 2) if z is not None else None,
                       drought_tier=drought_tier(wb['rp_dry'], z, dry_frac, dry_area_km2),
                       flood_tier=flood_tier(wb['rp_wet']))
    return out


def adm1_rows(feats, skey):
    """One row per adm1 unit — the native resolution; adm0 is rolled up from it."""
    rows = []
    for f in feats:
        p = f['properties']
        A = p.get('area_m2') or 0.0
        fin = p.get('cov_inseason') or 0.0
        fsk = p.get('cov_skill') or 0.0
        fcb = p.get('cov_combined') or 0.0
        combined_area = fcb * A
        dry_area = (p.get('dry01') or 0.0) * A
        series = {y: p.get('y%d' % y) for y in ALL_YEARS}
        row = dict(admin0=p.get(ADM0_NAME), admin1=p.get('name1'),
                   asap0_id=p.get(ADM0_ID), asap1_id=p.get(ADM1_ID), season=skey,
                   issued_month=ISSUED_MONTH, forecast_year=FORECAST_YEAR)
        row.update(classify(fin, fsk, fcb, series, dry_area, combined_area))
        rows.append(row)
    return rows


def rollup_to_adm0(feats, skey):
    """Area-weight adm1 reductions up to adm0. Coverage fractions are area-weighted
    by adm1 area; the per-year aggregate and dry extent are weighted by combined
    masked area, making the adm0 value the exact area-weighted mean over the frozen
    footprint. Geodesic adm1 area is a close proxy for pixel area at this scale."""
    from collections import defaultdict
    groups = defaultdict(list)
    for f in feats:
        p = f['properties']
        groups[(p.get(ADM0_NAME), p.get(ADM0_ID))].append(p)

    rows = []
    for (name0, a0), ps in groups.items():
        A = [(p.get('area_m2') or 0.0) for p in ps]
        Atot = sum(A) or 1.0
        fin = sum((p.get('cov_inseason') or 0.0) * a for p, a in zip(ps, A)) / Atot
        fsk = sum((p.get('cov_skill') or 0.0) * a for p, a in zip(ps, A)) / Atot
        fcb = sum((p.get('cov_combined') or 0.0) * a for p, a in zip(ps, A)) / Atot
        w = [(p.get('cov_combined') or 0.0) * a for p, a in zip(ps, A)]  # combined masked area
        combined_area = sum(w)
        dry_area = sum((p.get('dry01') or 0.0) * a for p, a in zip(ps, A))

        series = {}
        for y in ALL_YEARS:
            num = den = 0.0
            for p, wi in zip(ps, w):
                v = p.get('y%d' % y)
                if v is not None and wi > 0:
                    num += v * wi
                    den += wi
            series[y] = num / den if den > 0 else None

        row = dict(admin=name0, asap0_id=a0, season=skey, issued_month=ISSUED_MONTH,
                   forecast_year=FORECAST_YEAR, n_adm1=len(ps))
        row.update(classify(fin, fsk, fcb, series, dry_area, combined_area))
        rows.append(row)
    return rows


# ── run ───────────────────────────────────────────────────────────────────────
def main():
    rows0, rows1 = [], []
    for skey, months in SEASONS.items():
        img = season_image(months)
        if img is None:
            print('  %s: not forecastable from issued month %d — skipped' % (skey, ISSUED_MONTH))
            continue
        print('  %s: reducing over adm1 units…' % skey)
        # Keep only the numeric props and DROP geometry — returning 2368 polygons
        # blows the synchronous response-size limit.
        keep = [ADM0_NAME, ADM0_ID, ADM1_ID, 'name1', 'area_m2',
                'cov_inseason', 'cov_skill', 'cov_combined', 'dry01'] \
            + ['y%d' % y for y in ALL_YEARS]
        fc = (img.reduceRegions(collection=admin, reducer=ee.Reducer.mean(), scale=SCALE)
              .select(keep, None, False))
        feats = fc.getInfo()['features']
        rows0.extend(rollup_to_adm0(feats, skey))   # adm0 (rolled up)
        rows1.extend(adm1_rows(feats, skey))         # adm1 (native resolution)

    pd.DataFrame(rows1).sort_values(['admin0', 'admin1', 'season']).reset_index(drop=True) \
        .to_parquet(OUT_PATH_ADM1, index=False)
    df = pd.DataFrame(rows0).sort_values(['admin', 'season']).reset_index(drop=True)
    df.to_parquet(OUT_PATH, index=False)
    print('\nwrote %d adm0 rows -> %s' % (len(df), OUT_PATH))
    print('wrote %d adm1 rows -> %s' % (len(rows1), OUT_PATH_ADM1))

    live = df[df.state == 'evaluable']
    n_watch = (df.drought_tier == 'watch').sum()
    n_alert = (df.drought_tier == 'alert').sum()
    print('\nevaluable adm0 (admin x season): %d | drought watch: %d | drought alert: %d | flood watch: %d'
          % (len(live), n_watch, n_alert, (df.flood_tier == 'watch').sum()))
    alerts = df[df.drought_tier == 'alert'].sort_values(['dry_area_km2'], ascending=False)
    print('\n── DROUGHT ALERTS (escalated; sorted by dry area) ──')
    print(alerts[['admin', 'season', 'dry_rp', 'z', 'dry_frac_footprint', 'dry_area_km2', 'drought_tier']]
          .head(30).to_string(index=False) if len(alerts) else '  (none)')


if __name__ == '__main__':
    main()
