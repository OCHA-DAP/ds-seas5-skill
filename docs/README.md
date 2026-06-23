# SEAS5 precipitation alerts — static site

A self-contained static page (no backend) showing the **most recent SEAS5 seasonal precipitation
forecast** as a zoomable world map, with a **Country/Pixel** resolution toggle, a valid-trimester
selector, and a rainy-season toggle. Deployable to GitHub Pages.

## Files
- `index.html`, `app.js`, `style.css` — the page (loads Leaflet from a CDN; queries nothing else).
- `data/forecast.json` — latest forecast per country per valid trimester (`pct` percentile,
  `r` correlation, `rp` directional return period, `rainy` flag) plus the threshold defaults.
- `data/countries.geojson` — simplified country boundaries (choropleth + basemap outlines).
- `raster/data/` — the pixel-level layer: category-code PNGs per trimester (masked / all) plus
  `meta.json`, rendered by `pipeline/export_raster_site.py`.

The Country layer reads the two files in `data/`; the Pixel layer reads `raster/data/`. The
categorisation and colours are a faithful port of the marimo app's map logic
(`analysis/prob_alerts.py`).

## Rebuild the data
The data is generated from the processed skill stats (blob) + ERA5 climatology (DB). Re-run when a
new forecast lands (≈monthly):

```bash
uv run python pipeline/export_static_site.py
```

This overwrites `docs/data/forecast.json` and `docs/data/countries.geojson`. Commit the result.

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
