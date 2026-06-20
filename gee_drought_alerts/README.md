# gee_drought_alerts — quick start

Pixel-level SEAS5 seasonal **drought/flood alert** pipeline (Google Earth Engine +
Quarto), vendored from the deprecated `ds-eo-rasters`. Background, design rationale,
and open next steps are in **`VENDORED.md`**.

## Setup (one-time)

```bash
# from the repo root (ds-seas5-skill/)
uv sync                      # installs everything incl. the GEE deps
uv run earthengine authenticate   # once per machine; uses the ee-zackarno project
```

The GEE assets it reads (`ee-zackarno/seas5_monthly`, `asap_psp_adm1_mask/_fc`) are
remote and public-read — nothing to download.

## Run (from inside this directory)

```bash
cd gee_drought_alerts

# the alert pipeline -> data/seas5_{admin,adm1}_drought_alerts_2026-06.parquet
uv run python exploratory/seas5_admin_drought_alerts.py

# skill sensitivity sweep + ERA5 hindcast backtest (~40 min GEE each)
uv run python exploratory/skill_sweep.py
uv run python exploratory/backtest_datagen.py
uv run python exploratory/backtest_verify.py

# admin boundaries for the maps (ASAP adm1/adm0 + Natural Earth basemap)
uv run python exploratory/export_admin_boundaries.py

# the documentation book (renders from local data/, no GEE needed)
cd book && QUARTO_PYTHON=../../.venv/bin/python quarto render
```

## Data

`data/` is **gitignored** — present locally for offline work, regenerable from the
scripts above. Only code is tracked.

## What's where

- `exploratory/seas5_admin_drought_alerts.py` — the pipeline (masks → aggregate →
  Weibull RP / z / extent → fire-and-escalate tiers; adm0 + adm1).
- `exploratory/backtest_*.py`, `skill_sweep.py`, `sweep_analysis.py` — verification.
- `exploratory/export_admin_boundaries.py` — map geometry.
- `exploratory/{build_psp_raster,scrape_asap_psp_adm1*,upload_psp_assets}.py` — build
  the ASAP in-season mask (historical; assets already deployed).
- `app/seas5_era5_corr_app.js` — the deployed GEE Code Editor visualizer.
- `book/` — Quarto book (method · skill sensitivity · threshold selection · alert config).
