"""
Export admin boundaries for mapping the alert tables.
=====================================================

- ASAP adm1 polygons (the units the pipeline aggregates over) with their exact
  asap0_id / asap1_id / name keys -> data/boundaries/asap_adm1.geojson. These join
  to the alert tables with zero name/iso3 ambiguity. Dissolved to adm0 too.
- Natural Earth 110m countries cached locally as a neutral basemap (same source the
  ds-teleconnections global maps use).

Geometries are simplified (~10 km) — plenty for a global/continental choropleth and
small enough to page through getInfo under the response-size limit.

Run: .venv/bin/python exploratory/export_admin_boundaries.py
"""
import os

import ee
import geopandas as gpd
import requests
from shapely.geometry import shape

OUTDIR = 'data/boundaries'
SIMPLIFY_M = 10000      # ~10 km; mapping resolution, keeps payload small
CHUNK = 150
NE_URL = ('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/'
          'master/geojson/ne_110m_admin_0_countries.geojson')

os.makedirs(OUTDIR, exist_ok=True)
ee.Initialize(project='ee-zackarno')

fc = (ee.FeatureCollection('projects/ee-zackarno/assets/asap_psp_adm1_fc')
      .select(['asap0_id', 'asap1_id', 'name0', 'name1'])
      .map(lambda f: f.setGeometry(f.geometry().simplify(SIMPLIFY_M))))

n = fc.size().getInfo()
lst = fc.toList(n)
feats = []
for off in range(0, n, CHUNK):
    feats += ee.FeatureCollection(lst.slice(off, off + CHUNK)).getInfo()['features']
    print('  fetched %d/%d' % (min(off + CHUNK, n), n))

rows = [f['properties'] for f in feats if f.get('geometry')]
geoms = [shape(f['geometry']) for f in feats if f.get('geometry')]
adm1 = gpd.GeoDataFrame(rows, geometry=geoms, crs='EPSG:4326')
adm1['geometry'] = adm1.geometry.make_valid()   # simplification can self-intersect; repair before dissolve
adm1.to_file('%s/asap_adm1.geojson' % OUTDIR, driver='GeoJSON')
print('wrote %d adm1 polygons -> %s/asap_adm1.geojson' % (len(adm1), OUTDIR))

adm0 = adm1.dissolve(by=['asap0_id', 'name0']).reset_index()[['asap0_id', 'name0', 'geometry']]
adm0.to_file('%s/asap_adm0.geojson' % OUTDIR, driver='GeoJSON')
print('wrote %d adm0 polygons -> %s/asap_adm0.geojson' % (len(adm0), OUTDIR))

ne_path = '%s/naturalearth_admin0.geojson' % OUTDIR
if not os.path.exists(ne_path):
    r = requests.get(NE_URL, timeout=120)
    r.raise_for_status()
    open(ne_path, 'wb').write(r.content)
    print('cached Natural Earth basemap -> %s' % ne_path)
