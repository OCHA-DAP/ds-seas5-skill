# Vendored: GEE pixel-level SEAS5 drought/flood alert pipeline

Imported from the `ds-eo-rasters` repo (which we are deprecating). This is a
self-contained block: run everything from **inside this directory**
(`gee_drought_alerts/`) — the scripts use paths relative to it (`exploratory/…`,
`data/…`; the book reads `../data/…`).

## Why this is a *different stack* (not a drop-in to `src/`)

This repo's existing pipeline reads SEAS5/ERA5 **pre-aggregated to admin level** from
the team DB via `ocha-stratus`. **This vendored code does the load-bearing work at the
PIXEL level in Google Earth Engine** and only collapses to admin units at the very
end:

- a **skill mask** — per-pixel Spearman correlation of SEAS5 vs ERA5-Land;
- an **in-season (PSP) mask** — ASAP precipitation-sensitive period;
- a **frozen footprint** (skill ∧ in-season), applied identically across all years;
- **per-pixel anomaly** for the extent metric (dry area / % of footprint);
- **area-weighted masked aggregation** to admin (adm1, rolled up to adm0).

None of this can be reconstructed from admin-level series — that is the whole reason
it is GEE/raster. Integration is therefore a *decision*, not a merge: either expose
the GEE-derived admin outputs to this repo's alerts/static-site, or port the
pixel-level masking onto a raster source here. It is **not** wired into
`src/datasources`.

## Layout

| path | what |
|---|---|
| `exploratory/seas5_admin_drought_alerts.py` | the pipeline: masks → aggregate → Weibull RP / z / extent → tiers (adm0 + adm1) |
| `exploratory/backtest_datagen.py`, `backtest_verify.py` | hindcast verification vs ERA5 (POD/FAR; chose the skill gate) |
| `exploratory/skill_sweep.py`, `sweep_analysis.py` | skill-threshold sensitivity sweep |
| `exploratory/export_admin_boundaries.py` | export ASAP adm1/adm0 polygons + Natural Earth basemap |
| `exploratory/build_psp_raster.py`, `scrape_asap_psp_adm1*.py`, `upload_psp_assets.py` | build/ingest the ASAP PSP in-season mask |
| `exploratory/*.qmd` | narrative notebooks (incl. SEAS5→GEE ingest) |
| `app/seas5_era5_corr_app.js` | the deployed GEE Code Editor visualizer |
| `book/` | Quarto **book** (method · skill sensitivity · threshold selection · alert config) |
| `data/` | **gitignored** — present locally, regenerable from the scripts |
| `CLAUDE.md` | project notes + GEE pitfalls (from the source repo) |

## Deployed GEE assets it depends on (project `ee-zackarno`)

- `projects/ee-zackarno/assets/seas5_monthly` — SEAS5 ImageCollection (public-read)
- `projects/ee-zackarno/assets/asap_psp_adm1_mask` / `asap_psp_adm1_fc` — PSP mask + FC
- ERA5-Land: `ECMWF/ERA5_LAND/MONTHLY_AGGR` (Google-hosted)

## Extra dependencies (not in this repo's `pyproject.toml`)

The vendored code needs, beyond what `ds-seas5-skill` already pins:
`earthengine-api`, `geemap`, `rioxarray`, `pyarrow` (parquet), `google-cloud-storage`,
`python-dateutil`, and for the Quarto book `jupyter`/`nbclient`. The ingest/PSP
scripts also use `cfgrib`, `zarr`. Merge these into `pyproject.toml` deliberately when
integrating (left untouched here to avoid clobbering the lockfile).

## Reproduce

- `python exploratory/seas5_admin_drought_alerts.py` → the canonical adm0+adm1 alert
  tables (issued June 2026, skill gate r≥0.20).
- `python exploratory/skill_sweep.py` then `backtest_datagen.py` + `backtest_verify.py`
  → the sensitivity sweep and the ERA5 hindcast verification.
- `cd book && quarto render` → the documentation book (reads the regenerated `../data`).

## Open next steps (carried over)

- ~~Per-year dry-area backtest to calibrate the alert escalation thresholds.~~ **Done**
  — `backtest_verify.py` §D scores each escalation arm as a precision lever against
  observed severe drought (ERA5 dry RP ≥ 5) over the fired universe. The extent arms
  were too loose (`dry_frac ≥ 0.50` was ~implied by firing; `dry_area ≥ 100k` mostly
  escalated large countries), inverting the split. Retuned to `dry_frac ≥ 0.80` /
  `dry_area ≥ 300k` (`z ≤ −1.5` unchanged — the strongest arm), restoring a watch-heavy
  ~2:1 climatological split. Input bands: `backtest_datagen.py --dry`.
- Test skill cutoffs **below 0.20** and a **regional** (not global) gate.
- **adm1** return-period choropleth (geometry already exported).
- **Continuous skill-weighting** instead of a hard skill gate.
