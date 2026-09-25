# SEAS5 precipitation alerts — static site

A self-contained static page (no backend) showing the **most recent SEAS5 seasonal precipitation
forecast** as a zoomable world map, with a **Country/Pixel** resolution toggle, a valid-trimester
selector, and a rainy-season toggle. Deployable to GitHub Pages.

## Tabs
- **Map** — forecast browser (`app.js`): Country/Pixel toggle, issued year/month menus
  (browse the whole 1981–present record at country level; pixel locks to the latest issuance),
  valid-trimester slider.
- **Skill map** — global hindcast skill (`skillmap.js`): Country/Pixel toggle, valid-trimester +
  leadtime selectors. Shows skill (Pearson r) alone, categorical at the 0.3/0.5 cutoffs.
- **Skill by country** — per-country leadtime×trimester skill heatmap + climatology (`skill.js`).

Valid trimesters run from leadtime **−2 to 4**. Negative leads are **in-season (mixed)
trimesters** — the issuance falls inside the trimester, so the elapsed 1–2 months come from ERA5
observations and only the rest from SEAS5 (each forecast month bias-corrected per calendar month in
log space before blending; see `src/skill.py:aggregate_mixed_trimester`). Their skill is naturally
much higher. **Pixel layers cover leads 0–4 / fully-forecast trimesters only** — the in-season
combos are country-level; the JS shows a note instead of a broken overlay.
- **Methodology** — static prose.

Both maps share `mapframe.js`: an in-frame title card (top-left) that spells out the valid
trimester (`Jul–Aug–Sep 2026 (JAS)`), a small `Source: ECMWF SEAS5 and ERA5` line
(bottom-right), and **zoom-aware dots for small countries** — a monitored country whose
on-screen footprint is smaller than about a dot gets one at its largest polygon's centroid,
recomputed on every zoom so the dots disappear once the country itself is legible (dots that
would overlap a larger country's dot are culled until you zoom in). The /cma mirror sets
`window.SITE_MODEL_LABEL` / `window.SITE_SOURCE` before loading it so the title and source
name CMME instead. When editing these scripts bump the `?v=` on the script/style tags in
`index.html` and `cma/index.html`.

There are also **unlisted** standalone pages sharing `cbpf.js` — the country forecast map (all
countries shown) that outlines membership sets, with non-member countries paler, per-set show/hide
toggles, and dashed/interleaved outlines where a country is in several sets:
- `cbpf.html` — US "Humanitarian Reset" award (red) + OCHA pooled-fund/RHPF (orange).
- `cerf.html` — the same plus the two CERF anticipatory-action El Niño sets, framework (blue) and
  non-framework (green), on by default.

`cbpf.js` adapts to whichever `show-*` toggles exist on the page; the set lists are the `CBPF_AWARD`,
`CBPF_ALL`, `CERF_FW`, `CERF_NF` constants at the top of the file. Not linked from the nav; reached directly at `/cbpf.html`. Reuses
`data/forecasts/` (no extra export); the country lists are the `CBPF_AWARD` and `CBPF_ALL` sets at
the top of `cbpf.js`.

## Unlisted CMA (CMME) mirror — `/cma/`

`cma/index.html` is a full mirror of the main site (Map, Skill map, Skill by country,
Methodology) driven by **CMA CMME** forecasts instead of SEAS5, at **country level only** —
no pixel layers, no cbpf/cerf pages. Not linked from the nav; shared by direct link only
(`<meta name="robots" content="noindex">`).

It reuses the SAME `app.js` / `skillmap.js` / `skill.js` untouched: those scripts fetch
`data/…` relative to the page, so the page ships its own `cma/data/` (forecast issuances +
`skill_matrix.json`, leads 1–4) and points `window.SITE_GEO` at the main site's
`countries.geojson` to avoid duplicating the geometry. The raster `meta.json` fetches 404 →
the scripts fall back to country-only and the page has no Country/Pixel toggles.

CMME specifics: ensemble-mean precipitation at 1°, monthly leads 1–6 (the issue month itself
is not forecast → trimester leads 1–4, no in-season trimesters), hindcast inits 1991–2020 +
realtime from Aug 2025 (nothing in between).

**Password protection.** The repo and Pages site are public, so every JSON under `cma/data/`
is **AES-256-GCM encrypted** by the exporter; `cma/index.html` shows a password prompt,
derives the key in-browser (PBKDF2, WebCrypto) and decrypts through a fetch shim — the shared
JS is untouched. The password is NOT stored in the repo (ask the CHD data science team);
rotating it = re-running the export with a new one. This is a share-by-link courtesy gate,
not hard security: the derived stats are brute-forceable offline by a determined attacker.

Rebuild with:

```bash
uv run python pipeline/compute_skill_cma.py                        # CMME blob → adm0 skill parquets (processed/cma/)
CMA_SITE_PASSWORD=... uv run python pipeline/export_cma_site.py    # → docs/cma/data/  (--rebuild to rewrite history)
```

## Files
- `index.html`, `app.js`, `skillmap.js`, `skill.js`, `style.css` — the page (loads Leaflet from a
  CDN; queries nothing else).
- `data/forecasts/` — one small JSON per issuance (`{year}-{month}.json`) with every country's
  forecast for that issuance (`pct` percentile, `r` correlation, `rp` directional return period,
  `rainy` flag), plus `index.json` (available years/months + latest). The Map tab fetches the
  selected issuance on demand. Built by `pipeline/export_history_site.py`.
- `data/forecast.json` — latest issuance only (legacy; superseded by `data/forecasts/`).
- `data/skill_matrix.json` — per country: leadtime×trimester Pearson-r matrix, monthly + trimester
  ERA5 climatology, rainy flags, thresholds. Feeds the Skill-map adm0 layer and the Skill-by-country
  heatmap.
- `data/countries.geojson` — simplified country boundaries (choropleth + basemap outlines).
- `raster/data/` — forecast pixel layer: category-code PNGs per trimester (masked / all) + `meta.json`
  (`pipeline/export_raster_site.py`).
- `raster/skill/` — skill pixel layer: baked RGBA PNGs per trimester × leadtime (`{TRI}_L{lead}.png`)
  + `meta.json` (`pipeline/export_skill_raster_site.py`).

The Country layers read `data/`; the Pixel layers read `raster/`. The categorisation and colours are
a faithful port of the marimo app's map logic (`analysis/prob_alerts.py`).

## Where the data comes from

`data/`, `raster/data/` and `cma/data/` are **generated and not in git** (~100 MB, rewritten every
issuance). The Databricks job **"SEAS5 Skill Monthly Refresh"** (`databricks.yml`, 7th of the
month 03:00 UTC, entrypoint `databricks/dispatch.py`) builds them and uploads them as a versioned
bundle to the dev blob (`projects/ds-seas5-skill/site/<YYYY-MM>/`, `pipeline/sync_site_data.py`);
the Pages deploy downloads that bundle into the site artifact. GitHub Actions never touches the
database — the Postgres servers are only reachable from the Databricks workspace.

To work on the site locally, fetch the published bundle first:

```bash
uv run python pipeline/sync_site_data.py download            # latest bundle -> data/, raster/data/, cma/data/
uv run python pipeline/sync_site_data.py download --tag 2026-08   # an older issuance
uv run python pipeline/sync_site_data.py list
```

`raster/skill/` (the skill pixel layer, `pipeline/export_skill_raster_site.py`) IS committed: skill is
a fixed hindcast statistic, so it only changes when the cube is fully recomputed.

## The monthly refresh (what the job does)

One run, one issuance (`issued` parameter, default = the current month), tasks in this order —
each is one of the ordinary scripts below, unchanged; `databricks/dispatch.py` only composes them:

| task | runs | notes |
|---|---|---|
| `skill_adm0` | `pipeline/compute_skill.py` | ~30 min; prod DB → dev blob parquets |
| `skill_adm1` | `pipeline/compute_skill_adm1.py --workers 6` | ~45 min |
| `skill_adm2` | `pipeline/compute_skill_adm2.py --workers 6` | ~1.5 h (5,131 units) |
| `clim_signal` | `compute_monthly_clim.py` + `build_hdx_signal_inputs.py`, levels 0/1/2 | HDX-signal inputs |
| `raster` | `compute_skill_raster.py --issued-months <M> --merge` | in parallel with the above; only the new month is recomputed and merged into the blob cubes |
| `site` | previous bundle → `export_static_site`, `export_history_site`, `export_hnrp_drought` ×6, `export_plan_caseloads`, `export_raster_site`, CMA mirror when a new CMME file exists → **`verify_site_data.py --expect <YYYY-MM> --strict-hnrp [--strict-raster]`** → `sync_site_data.py upload` | the vintage gate fails the run before anything is published |
| `enso_slides` | `analysis/png_enso_slides.py` + `sync_enso_slides.py upload` | **opt-in** (`enso=run`): ~700 MB of renders, and the ERA5 slicing is slow without the teleconnections cache — still run by hand for now |
| `publish` | dispatches the Pages deploy workflow | needs the dsci secret `GH_SEAS5_PAGES_TOKEN`; the deploy's own 10:00 UTC cron is the fallback |

The CMA mirror needs the dsci secret `CMA_SITE_PASSWORD` (the payloads are encrypted); without it
the previous `/cma/` payloads carry over and the log says so.

```bash
databricks bundle deploy -t prod -p DEFAULT                      # after merging a change to main
databricks bundle run seas5_monthly_refresh -t prod -p DEFAULT   # a full run now
databricks bundle run seas5_monthly_refresh -t prod -p DEFAULT --params issued=2026-09,cma=force,enso=run
databricks bundle run seas5_monthly_refresh -t dev  -p DEFAULT   # dry run on your interactive cluster
```

A single failed task is re-run from the Workflows UI ("Repair run"); the `site` task can be
repeated on its own once the compute is on the blob.

Notes:
- **History is frozen.** `export_history_site.py` only writes the new issuance's file (past files'
  in-sample percentiles drift trivially each month). Use `--rebuild` to regenerate all of
  `data/forecasts/` — needed after a methodology change (last done July 2026, adding the
  in-season trimesters) — then upload a new bundle.
- **Vintage gate.** `pipeline/verify_site_data.py` fails the run when any payload does not show the
  issuance the run is for (`--expect`), when the in-season trimesters are missing (ERA5's elapsed
  month not landed yet — rerun later), when the HNRP or raster payloads lag, or when the two country
  exporters disagree. The deploy re-runs it (without `--expect`) on the downloaded bundle.
- **Still by hand** after an issuance: the ENSO slides (above), the CMA password/token secrets
  the first time, and the Uganda page (`compute_uga_district_stats.py`, `compute_skill_uga_adm2.py`,
  `analysis/uganda_hnrp.qmd` — a narrative written around one issuance; do not re-render blindly).
  `pipeline/audit_site_coverage.py` checks that every selectable country renders.

## GitHub Pages
The app is live at **https://ocha-dap.github.io/ds-seas5-skill/app/** — under `/app/`, not at the
root. The root serves the landing page in `pages/`, which links to the app and to the country
analyses.

Pages is built by the **`Deploy pages site to GitHub Pages` workflow**, not served from a branch:
it rsyncs `pages/` to the site root and `docs/` to `/app/`. Do NOT repoint Pages at a branch — a
`source[branch]` change would bypass the workflow and serve `docs/` at the root again, breaking
every `/app/` and `/uganda/` link.

Old links to the root still work: `pages/index.html` forwards a recognised app hash or query
(`/#hnrp`, `/?country=...`) to `/app/`, and `pages/cma/index.html` redirects `/cma/` to
`/app/cma/`.

## Local preview
```bash
uv run python pipeline/sync_site_data.py download   # once, or after each issuance
python -m http.server -d docs 8000                  # then open http://localhost:8000
```
