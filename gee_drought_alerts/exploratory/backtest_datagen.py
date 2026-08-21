"""
Backtest data generation: SEAS5 forecast + ERA5 observed seasonal aggregates.
=============================================================================

For each skill threshold, season, adm1 unit, and year, emit BOTH:
  - the SEAS5 ensemble-mean seasonal aggregate (the forecast), and
  - the ERA5-Land seasonal aggregate (observed truth),
over the SAME frozen in-season+skilled footprint.

This is the input to backtest_verify.py, which scores forecast dry-tail events
against observed dry-tail events (leave-one-out) to choose the skill threshold by
drought-detection skill rather than a significance cutoff.

Reuses the pipeline's masks/helpers (import) so the footprint logic is identical.
Output: data/backtest/adm1_r{tag}.parquet (one per threshold), adm1 resolution;
verify.py rolls up to adm0. Paged reduceRegions to stay under the response limit.

  python exploratory/backtest_datagen.py         # aggregates, 4 thresholds (~40 min)
  python exploratory/backtest_datagen.py --dry   # per-year dry-area bands, r20 only (~10 min)

The `--dry` mode adds the input for the ESCALATION-threshold backtest: per adm1
unit, season, and year, the fraction of the unit that the SEAS5 forecast places in
per-pixel drought (pixel-z <= PIX_DRY_Z within the frozen footprint) — i.e. the same
`dry01` extent metric the alert pipeline reports for 2026, but for every historical
year so the watch->alert escalation thresholds can be calibrated. Written to a
SEPARATE file (adm1_dry_r20.parquet) so the aggregate parquets are left untouched.
Only the production skill gate (r=0.20) is generated: escalation is a downstream
lever, so it is calibrated at the fixed gate rather than across all thresholds.
"""
import importlib.util
import os
import sys

import ee
import pandas as pd

THRESHOLDS = [0.20, 0.25, 0.30, 0.35]
DRY_THRESH = 0.20                # escalation is downstream of the fixed skill gate
OUTDIR = 'data/backtest'
CHUNK = 600                      # features per reduceRegions page
os.makedirs(OUTDIR, exist_ok=True)

# import the pipeline module to reuse masks, season helpers, and globals
spec = importlib.util.spec_from_file_location(
    'alerts', 'exploratory/seas5_admin_drought_alerts.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SEAS5_YEARS = m.ALL_YEARS                 # 1993-2026 (incl. live forecast year)
ERA5_YEARS = m.CLIM_YEARS                 # 1993-2025 (no 2026 obs yet)


def season_base(months):
    lts = [str((vm - m.ISSUED_MONTH + 12) % 12) for vm in months]
    return (m.SEAS5.filter(ee.Filter.inList('leadtime', lts))
            .filter(ee.Filter.stringContains('date_issued', '-%02d-' % m.ISSUED_MONTH))
            .select('precip'))


def combined_mask(base, months, r_thresh):
    paired = ee.ImageCollection([
        m.seas5_season(base, months, y).rename('seas5')
         .addBands(m.era5_season(months, y).rename('era5'))
        for y in m.CLIM_YEARS])
    corr = paired.reduce(ee.Reducer.spearmansCorrelation()).select('correlation')
    skill = corr.gte(r_thresh)
    psp = m.psp_mask_for(months)
    return skill.unmask(0).And(psp.unmask(0)), psp, skill


def reduce_paged(img, keep):
    n = m.admin.size().getInfo()
    lst = m.admin.toList(n)
    feats = []
    for off in range(0, n, CHUNK):
        sub = ee.FeatureCollection(lst.slice(off, off + CHUNK))
        fc = (img.reduceRegions(collection=sub, reducer=ee.Reducer.mean(), scale=m.SCALE)
              .select(keep, None, False))
        feats += fc.getInfo()['features']
    return feats


def main():
    keep_static = [m.ADM0_NAME, m.ADM0_ID, m.ADM1_ID, 'name1', 'area_m2',
                   'cov_inseason', 'cov_skill', 'cov_combined']
    for r in THRESHOLDS:
        tag = 'r%02d' % int(round(r * 100))
        rows = []
        for skey, months in m.SEASONS.items():
            lts = [(vm - m.ISSUED_MONTH + 12) % 12 for vm in months]
            if any(lt > 6 for lt in lts):
                continue
            base = season_base(months)
            combined, psp, skill = combined_mask(base, months, r)
            sbands = [m.seas5_season(base, months, y).updateMask(combined).rename('s%d' % y)
                      for y in SEAS5_YEARS]
            ebands = [m.era5_season(months, y).updateMask(combined).rename('e%d' % y)
                      for y in ERA5_YEARS]
            cov = [psp.unmask(0).rename('cov_inseason'),
                   skill.unmask(0).rename('cov_skill'),
                   combined.rename('cov_combined')]
            img = ee.Image.cat(sbands + ebands + cov)
            keep = keep_static + ['s%d' % y for y in SEAS5_YEARS] + ['e%d' % y for y in ERA5_YEARS]
            print('  %s %s: reducing (paged)…' % (tag, skey))
            for f in reduce_paged(img, keep):
                p = dict(f['properties'])
                p['season'] = skey
                rows.append(p)
        out = '%s/adm1_%s.parquet' % (OUTDIR, tag)
        pd.DataFrame(rows).to_parquet(out, index=False)
        print('wrote %d adm1 rows -> %s' % (len(rows), out))


def dry_fraction_bands(base, months, combined):
    """Per-year 'dry fraction of unit' bands, one per climatology year.

    For each year, the SEAS5 seasonal field is standardized against its own
    per-pixel climatology LEAVE-ONE-OUT and flagged dry where z <= PIX_DRY_Z, then
    intersected with the frozen footprint. reduceRegions(mean) of the unmasked-0
    band = the fraction of the WHOLE unit that is in-footprint AND dry — exactly the
    pipeline's `dry01` reduction (dry_area = frac * unit_area; dry_frac = dry_area /
    combined_area on rollup).

    Leave-one-out (not production's full-climatology std) is deliberate: the pipeline
    applies this rule to 2026, which sits OUTSIDE the 1993-2025 climatology, so its
    dry_frac is out-of-sample. Scoring each backtest year out-of-sample too keeps the
    calibration distribution comparable; an in-sample std would deflate each year's z
    and inflate dry_frac. LOO mean/var are derived from the sum / sum-of-squares
    images (cheap) rather than 33 separate reductions.
    """
    yrs = list(m.CLIM_YEARS)
    N = len(yrs)
    fields = {y: m.seas5_season(base, months, y) for y in yrs}
    S = ee.ImageCollection(list(fields.values())).sum()
    SS = ee.ImageCollection([x.pow(2) for x in fields.values()]).sum()
    bands = []
    for y in yrs:
        x = fields[y]
        loo_mean = S.subtract(x).divide(N - 1)
        loo_var = SS.subtract(x.pow(2)).divide(N - 1).subtract(loo_mean.pow(2))
        z = x.subtract(loo_mean).divide(loo_var.sqrt())
        dry01 = z.lte(m.PIX_DRY_Z).And(combined).unmask(0).rename('sdry%d' % y)
        bands.append(dry01)
    return bands


def gen_dry():
    """Emit per-year SEAS5 dry-fraction bands at the production skill gate (r20)."""
    r = DRY_THRESH
    keep_static = [m.ADM0_NAME, m.ADM0_ID, m.ADM1_ID, 'name1', 'area_m2', 'cov_combined']
    dry_years = list(m.CLIM_YEARS)
    rows = []
    for skey, months in m.SEASONS.items():
        lts = [(vm - m.ISSUED_MONTH + 12) % 12 for vm in months]
        if any(lt > 6 for lt in lts):
            continue
        base = season_base(months)
        combined, psp, skill = combined_mask(base, months, r)
        bands = dry_fraction_bands(base, months, combined)
        img = ee.Image.cat([combined.rename('cov_combined')] + bands)
        keep = keep_static + ['sdry%d' % y for y in dry_years]
        print('  dry %s: reducing (paged)…' % skey)
        for f in reduce_paged(img, keep):
            p = dict(f['properties'])
            p['season'] = skey
            rows.append(p)
    out = '%s/adm1_dry_r%02d.parquet' % (OUTDIR, int(round(r * 100)))
    pd.DataFrame(rows).to_parquet(out, index=False)
    print('wrote %d adm1 dry rows -> %s' % (len(rows), out))


if __name__ == '__main__':
    if '--dry' in sys.argv:
        gen_dry()
    else:
        main()
