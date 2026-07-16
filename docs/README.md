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

## Rebuild the data
Generated from the processed skill stats (blob) + ERA5 climatology (DB) and the per-pixel skill cube.
Re-run when a new forecast lands (≈monthly):

```bash
uv run python pipeline/export_static_site.py        # data/forecast.json, skill_matrix.json, countries.geojson
uv run python pipeline/export_history_site.py       # data/forecasts/  (adds only the NEW issuance; see below)
uv run python pipeline/export_raster_site.py        # raster/data/  (forecast pixels)
uv run python pipeline/export_skill_raster_site.py  # raster/skill/ (skill pixels)
```

Commit the result.

Notes:
- **Compute first.** These exports read the processed stats. When a new SEAS5 issuance lands, first
  refresh those: `pipeline/compute_skill.py` (country → blob parquets) and
  `pipeline/compute_skill_raster.py` (per-pixel cube → blob + `/tmp`). The raster compute wants its
  full ~16 GB — close memory-heavy apps first, or run just the latest issuance with
  `--issued-months <N> --no-upload` to refresh only the forecast-pixel layer.
- **History is frozen.** `export_history_site.py` only writes the new issuance's file (past files'
  in-sample percentiles drift trivially each month; freezing keeps the repo from bloating). Use
  `--rebuild` to regenerate all of `data/forecasts/` — needed after a methodology change (last
  done July 2026, adding the in-season trimesters).
- **Skill pixels are stable.** Skill is a fixed hindcast statistic, so `raster/skill/` only changes
  when the cube is fully recomputed; it can usually be skipped between forecasts.

## GitHub Pages
Live at **https://ocha-dap.github.io/ds-seas5-skill/**.

Configured under **Settings → Pages → Deploy from a branch**. It is currently served from the
`prob-rp-alerts` branch `/docs` (so it could go live before the PR merges). **After this PR merges
to `main`, repoint Pages to `main` / `/docs`** (the feature branch will likely be deleted):

```bash
gh api -X PUT repos/OCHA-DAP/ds-seas5-skill/pages -f "source[branch]=main" -f "source[path]=/docs"
```

## Local preview
```bash
python -m http.server -d docs 8000   # then open http://localhost:8000
```

## TODO: automate monthly refresh (future)
Add a scheduled GitHub Action (e.g. monthly) that runs `pipeline/export_static_site.py` and commits
the updated `data/`. It needs the blob + DB credentials as repo secrets (the same env
`ocha-stratus` uses for `stage="dev"` blob and the `prod` DB engine). Until then, refresh manually.
