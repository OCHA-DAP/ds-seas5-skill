# SEAS5 precipitation alerts — static site

A self-contained static page (no backend) showing the **most recent SEAS5 seasonal precipitation
forecast** as a zoomable world map, with a **Country/Pixel** resolution toggle, a valid-trimester
selector, and a rainy-season toggle. Deployable to GitHub Pages.

## Tabs
- **Map** — latest forecast (`app.js`): Country/Pixel toggle, valid-trimester slider.
- **Skill map** — global hindcast skill (`skillmap.js`): Country/Pixel toggle, valid-trimester +
  leadtime selectors. Shows skill (Pearson r) alone, categorical at the 0.3/0.5 cutoffs.
- **Skill by country** — per-country leadtime×trimester skill heatmap + climatology (`skill.js`).
- **Methodology** — static prose.

## Files
- `index.html`, `app.js`, `skillmap.js`, `skill.js`, `style.css` — the page (loads Leaflet from a
  CDN; queries nothing else).
- `data/forecast.json` — latest forecast per country per valid trimester (`pct` percentile,
  `r` correlation, `rp` directional return period, `rainy` flag) plus the threshold defaults.
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
uv run python pipeline/export_raster_site.py        # raster/data/  (forecast pixels)
uv run python pipeline/export_skill_raster_site.py  # raster/skill/ (skill pixels)
```

Commit the result. (The skill pixel cube is leadtime-based and only changes when the skill stats are
recomputed, so `export_skill_raster_site.py` rarely needs re-running between forecasts.)

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
