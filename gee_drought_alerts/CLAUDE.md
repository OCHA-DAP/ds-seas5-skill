# ds-eo-rasters

Earth observation raster experiments. Currently centered on a Google Earth Engine
app that visualizes SEAS5 seasonal precipitation forecasts — skill against
ERA5-Land plus per-year percentile/return-period anomaly views.

## Tooling

- **Python**: managed by `uv`, pinned to 3.12.4 (`.python-version`).
- **Run scripts**: `.venv/bin/python <script>` or `uv run <script>`.
- **Add deps**: `uv add <package>` (writes to `pyproject.toml` + `uv.lock`).
- Core deps: `ocha-stratus` (Azure blob access), `earthengine-api`, `geemap`, `rioxarray`.

## Repo layout

- `app/` — deliverables. The GEE JavaScript app `seas5_era5_corr_app.js` lives here.
- `artefacts/` — scratch / experiments / one-off scripts. **gitignored.** Nothing
  here is load-bearing; treat it as a throwaway working directory.
- `exploratory/` — Quarto (`.qmd`) notebooks for narrative exploration. Tracked.
- `pyproject.toml`, `uv.lock` — Python env.

## Workflow conventions

- **Validate GEE computations in Python before porting to JS.** The Code Editor
  has slow iteration; the same `ee` operations are far cheaper to debug via the
  `earthengine-api`. Pattern: drop a `artefacts/*_test.py` that proves the math
  on a known case, then translate to JS once confirmed.
- **Test heavy pipelines on one item first.** Before ingesting hundreds of files,
  do the full chain (load → temp file → GCS → GEE asset) on a single one.
- **Branch per app version**: `seas5-app-vN`. `main` holds the project scaffold
  only; deployed app versions live on their own branches so prior states are
  always recoverable.
- **Commit messages**: descriptive imperative subject (~70 char), multi-line body
  explaining *why* when non-obvious. No AI attribution.

## Deployed assets

| Resource | Location |
|---|---|
| GEE project | `ee-zackarno` |
| SEAS5 ImageCollection | `projects/ee-zackarno/assets/seas5_monthly` (public-readable) |
| GCS staging bucket | `gs://ee_general_bucket` |
| ERA5-Land monthly | `ECMWF/ERA5_LAND/MONTHLY_AGGR` (Google-hosted, public) |

## SEAS5 collection notes

- 3815 images (1981–2026, monthly precipitation ensemble means).
- Each image has `date_issued`, `date_valid`, `leadtime` as **string** properties.
  Leadtimes are 0–6 months; one image per (issued month, leadtime) per year.
- Filter via stored properties for performance:
  - `ee.Filter.inList('leadtime', ['1','2','3'])`
  - `ee.Filter.stringContains('date_issued', '-05-')` (issued month = May)
  - `ee.Filter.calendarRange(year, year, 'year')` on `system:time_start`
    (which equals the *valid* date)
- Avoid `.map()`-derived month/year properties — they re-evaluate per tile and
  collapse interactive performance. Stored properties only.

## Pipeline summary (historical)

SEAS5 COGs were ingested from Azure Blob (`raster` container,
`seas5/monthly/processed/`) using `ocha-stratus`:
blob → temp GeoTIFF → GCS staging → `ee.data.startIngestion`. The full ingest
script lived in `artefacts/ingest_full.py` (since rotated out, but reconstructable
from the v1 commit history).

## Known GEE pitfalls

- `ee.Reducer.spearmansCorrelation()` returns a fully-null `p-value` band
  in tile rendering even when the correlation band is fine. Don't rely on its
  p-values — derive a threshold on `|r|` instead.
- `ui.Button` `backgroundColor` only paints the wrapper element, not the button
  face. For colored buttons, use `color` on the text and accept the default fill.
- For partial layer transparency, `image.updateMask(fractional)` gets binarized
  at tile level. Bake fades into the visualized RGB instead, or just rely on
  pale low-end palette colors.
