# ds-seas5-skill

Skill and anomaly analysis of **ECMWF SEAS5** seasonal precipitation forecasts against **ERA5**
reanalysis, at both the country (admin-0) and native pixel (0.4° grid) level. For each issue
month × target trimester the forecast is aggregated, normalised, detrended, scored for skill
(temporal Pearson correlation vs ERA5), and located within its own hindcast distribution to flag
below-/above-normal seasons where the forecast is skilful.

## Apps

- **Full interactive app** (marimo — all controls, historical browsing, country detail):
  https://chd-ds-seas5-viz-skill-ewcwhteggec2gege.eastus2-01.azurewebsites.net/
- **Static map** (latest forecast, defaults only; Country/Pixel toggle, zoomable):
  https://ocha-dap.github.io/ds-seas5-skill/

## Layout

- `src/` — the method: `skill.py` (country level), `skill_raster.py` (per-pixel), `constants.py`.
- `pipeline/` — batch jobs: `compute_skill.py` / `compute_skill_raster.py` (compute stats to blob),
  `export_static_site.py` / `export_raster_site.py` (build the static-site data in `docs/`).
- `analysis/` — the marimo app (`prob_alerts.py`) and other exploratory notebooks.
- `docs/` — the static GitHub Pages site (see [`docs/README.md`](docs/README.md)).

## Setup

Uses [`uv`](https://docs.astral.sh/uv/) for environment management:

```bash
uv sync
uv run marimo edit analysis/prob_alerts.py   # full app locally
```

Blob/DB access goes through `ocha-stratus`. Input SEAS5/ERA5 rasters are read from the **prod**
blob; derived outputs are written to **dev**.
