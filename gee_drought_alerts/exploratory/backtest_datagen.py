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

Run: .venv/bin/python exploratory/backtest_datagen.py   (~40 min Earth Engine)
"""
import importlib.util
import os

import ee
import pandas as pd

THRESHOLDS = [0.20, 0.25, 0.30, 0.35]
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


if __name__ == '__main__':
    main()
