# ds-seas5-skill

Skill and anomaly analysis of **ECMWF SEAS5** seasonal precipitation forecasts against **ERA5**
reanalysis, at both the country (admin-0) and native pixel (0.4° grid) level. For each issue
month × target trimester the forecast is aggregated, normalised, detrended, scored for skill
(temporal Pearson correlation vs ERA5), and located within its own hindcast distribution to flag
below-/above-normal seasons where the forecast is skilful.

## Apps

- **Full interactive app** (marimo — all controls, historical browsing, country detail):
  https://chd-ds-seas5-viz-development-gsf5fhhfakdubagv.eastus2-01.azurewebsites.net/
- **Static map** (latest forecast, defaults only; Country/Pixel toggle, zoomable):
  https://ocha-dap.github.io/ds-seas5-skill/
- **CMA CMME mirror** (same method on CMA's multi-model ensemble, country level only;
  unlisted — direct link only): https://ocha-dap.github.io/ds-seas5-skill/cma/

## Layout

- `src/` — the method: `skill.py` (country level), `skill_raster.py` (per-pixel), `season.py`
  (the in-season / rainy-trimester rule), `hdx_signal.py` (the HDX signal's unit condition and
  country roll-up), `constants.py`.
- `databricks.yml` + `databricks/dispatch.py` — the **monthly refresh job** (Databricks Asset Bundle,
  7th 03:00 UTC): runs every batch job below for the new issuance, verifies the site payloads and
  publishes them as a bundle to the dev blob for the Pages deploy (see `docs/README.md`).
- `pipeline/` — batch jobs: `compute_skill.py` / `compute_skill_adm1.py` / `compute_skill_adm2.py` /
  `compute_skill_raster.py` (compute stats to blob at admin-0/1/2 and per pixel),
  `compute_monthly_clim.py` (ERA5 monthly climatology per admin level),
  `build_hdx_signal_inputs.py` (one tidy table per admin level for the HDX signal —
  see [`docs/dev-notes/hdx-signal-data.md`](docs/dev-notes/hdx-signal-data.md)),
  `export_static_site.py` / `export_raster_site.py` (build the static-site data in `docs/`),
  `compute_skill_cma.py` / `export_cma_site.py` (the same, for the CMA CMME mirror at `docs/cma/`).
- `analysis/` — the marimo app (`prob_alerts.py`) and other exploratory notebooks.
- `docs/` — the static GitHub Pages site (see [`docs/README.md`](docs/README.md)).

## Setup

Uses [`uv`](https://docs.astral.sh/uv/) for environment management:

```bash
uv sync
uv run marimo edit analysis/prob_alerts.py   # full app locally
```

Blob/DB access goes through `ocha-stratus`. Input SEAS5/ERA5 rasters are read from the **prod**
blob; derived outputs are written to **dev**. The Postgres servers are reachable only from the
Databricks workspace (private endpoints), so the DB-reading pipelines run there
(`databricks bundle run seas5_monthly_refresh -t dev`); the generated site data is fetched with
`uv run python pipeline/sync_site_data.py download`.
