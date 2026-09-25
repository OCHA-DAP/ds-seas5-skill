# HDX signal for SEAS5 — data hand-over

*Where every input lives, how it was produced, and how to turn it into the signal. Written
September 2026 for the analyst taking the signal design forward. Blob paths are exact.
Rendered on the Pages site at https://ocha-dap.github.io/ds-seas5-skill/hdx-signal/ (keep the two in step).*

## 1. The signal, as currently specified

Per country, per issuance, per target trimester: **issue a signal when at least a fraction
`frac` of the country's admin-1 units each satisfy all three of**

1. SEAS5 forecasts the trimester at a **return period ≥ `rp` years** (dry *or* wet — two
   separate signals, one per tail);
2. the forecast has **skill ≥ `r_min`** for that unit × issue month × trimester (detrended
   Pearson r of the hindcast against ERA5);
3. the trimester is **in season** for that unit: its share of the unit's annual rainfall is
   ≥ `season_share`.

| Parameter | Starting value | Meaning |
|---|---|---|
| `frac` | **0.60** | fraction of the country's admin-1 units that must qualify |
| `rp` | **5** yr | Weibull return period of the forecast within the unit's own hindcast (dry: `dry_rp`, wet: `wet_rp`) |
| `r_min` | **0.30** | minimum detrended Pearson r (the app's "moderate skill" cut) |
| `season_share` | **0.25** | trimester's share of annual rainfall. 0.25 = "above a flat climatology" (the trimester's mean monthly rain exceeds the annual monthly mean). The app / static site use the looser 0.15. |

All four are free. Every quantity they threshold is a **column** in the input tables below, so
the whole space can be swept without touching the pipelines. Ethiopia (and any other country
with a bimodal or otherwise complicated climatology) can be run at admin-2 instead of admin-1
— the admin-2 table has the same columns.

The starting rule is implemented once, in `src/hdx_signal.py` (`unit_condition`,
`country_signal`); see §6.

## 2. Where the raw numbers come from

Nothing in this work touches rasters. Everything starts from the **zonal statistics** that the
team's `ds-raster-stats` Databricks jobs write to the **prod rasterstats Postgres DB**
(`chd-rasterstats-prod.postgres.database.azure.com`; `ocha_stratus.get_engine("prod")`):

| Table | What | Key columns | Record |
|---|---|---|---|
| `public.seas5` | ECMWF SEAS5 ensemble-mean monthly precipitation, zonal mean per admin unit, one row per issued month × valid month | `pcode, iso3, adm_level, issued_date, valid_date, leadtime (0–6), mean` (+ median/min/max/count/sum/std) | issued 1981-01 → **2026-09** (the 5th of every month; hindcast 1981–2016 + operational) |
| `public.era5` | ERA5 monthly precipitation, zonal mean per admin unit | `pcode, iso3, adm_level, valid_date, mean` | 1981-01 → **2026-08** (lands ~6th of the following month) |
| `public.polygon` | the admin units (COD boundaries from fieldmaps.io): name, level, area, pixel-coverage counts | `pcode, iso3, adm_level, name, seas5_n_intersect_raw_pixels, …` | admin 0/1 for 153 countries; admin 2 for a subset |

Units are **mm/day** (monthly mean rate). `leadtime` 0 = the issue month itself (SEAS5's
horizon is 7 monthly steps). Units smaller than one SEAS5 pixel (~0.4°) get a NULL `mean` and
are dropped downstream (`src/datasources/seas5.py`). The KB page for the upstream job is
`pipelines/raster-stats.md` in `ds-knowledge-base`.

Admin-1 pcodes are the COD ones (`ET01` = Tigray, `NE005` = Tahoua, …). Admin-0 pcodes are
mostly the ISO3 but not always — join on `iso3` at country level, on `pcode` below it.

## 3. What this repo computes from them (the method, briefly)

`src/skill.py:run_all_combinations` is run once per admin unit, for all **12 issue months ×
12 trimesters** = 144 combinations:

1. **Aggregate to trimester means.** SEAS5: the three valid months of one issuance, averaged.
   ERA5: the same three months (complete seasons only). For **in-season trimesters** (issue
   month inside the trimester, leads −1/−2) the months already elapsed at issuance come from
   ERA5 and only the rest from SEAS5, each forecast month bias-corrected per calendar month
   (`aggregate_mixed_trimester`). That is why in-season skill is high by construction.
2. **log1p**, then **normalise** SEAS5 to ERA5's mean/std over the overlap years (so forecast and
   observation live on the same scale).
3. **Detrend** both series (linear fit over the overlap, re-centred) — the *detrended* variant
   is what the site, the HNRP tab and the signal tables use. The raw variant exists in the
   blob too (files without `_detrended`).
4. **Skill** = Pearson r between the normalised forecast and ERA5 over the overlap years
   (`n_years`, typically 45–46; NaN if < 10).
5. **Position of a forecast in its own hindcast distribution**: percentile (`pct`, share of
   hindcast forecasts ≤ it) and two **Weibull return periods**, `dry_rp = (n+1)/rank` with rank
   counted from the dry end and `wet_rp` from the wet end. The hindcast distribution is the
   years with both a forecast and an observation; the live forecast is ranked against it,
   past years are ranked **in-sample**. Both are properties of the *forecast* — they say how
   unusual the forecast is, not how much rain is missing.

Only trimesters at signed lead **−2 … 4** are kept (complete trimesters with at least one
forecast month inside the horizon): 7 trimesters per issuance.

The in-season rule (`src/season.py`) is separate from all of this: the ERA5 monthly climatology
per unit (mean of every January, every February, … over 1981–present), the trimester's share
`3 × mean(3 months) / sum(12 months)`, thresholded.

## 4. Files in the blob

Storage account **`imb0chd0dev`** (the team's *dev* stage), container **`projects`**. Load with
`ocha_stratus.load_parquet_from_blob(path, stage="dev")` — see §5 for the large ones. All
paths below are under **`ds-seas5-skill/processed/`**. Everything was rebuilt for the
**September 2026 issuance** (2026-09-07; Ethiopia admin-2 and the climatologies 2026-09-15; the
signal tables 2026-09-24, with `forecast_mm` / `hist_mean_mm`).

### 4a. Ready-made signal inputs (start here)

| Path | Level | Rows | Units | Countries |
|---|---|---|---|---|
| `hdx_signal/signal_inputs_adm1.parquet` | admin-1 | 8,741,535 | 2,283 | 150 |
| `hdx_signal/signal_inputs_adm1_latest.parquet` | admin-1, current issuance only | 15,935 | 2,283 | 150 |
| `hdx_signal/units_adm1.parquet` | one row per admin-1 unit with a climatology | 2,307 | | 152 |
| `hdx_signal/signal_inputs_adm2.parquet` | admin-2 | 19,704,795 | 5,146 | 24 |
| `hdx_signal/signal_inputs_adm2_latest.parquet` | admin-2, current issuance | 35,920 | 5,146 | 24 |
| `hdx_signal/units_adm2.parquet` | one row per admin-2 unit | 5,146 | | 24 |
| `hdx_signal/signal_inputs.parquet`, `…_latest.parquet`, `hdx_signal/units.parquet` | admin-0 (for comparison) | 576,000 / 1,050 / 152 | 150 | 150 |

One row per **unit × issuance (year, month) × trimester**, 1981 → September 2026, built by
`pipeline/build_hdx_signal_inputs.py` from the three products in 4b:

| Column | Meaning |
|---|---|
| `pcode, iso3, name, adm_level` | the unit (name from `public.polygon`) |
| `issued_year, issued_month` | the SEAS5 issuance (first of the month) |
| `trimester` | `JFM … DJF` (`src/constants.py:TRIMESTERS`) |
| `lead` | signed months from issue month to the trimester's first month, −2 … 4; **negative = in season** (blend of observed + forecast months) |
| `season_year` | the year the trimester is anchored on (wrapping trimesters NDJ/DJF anchor on December's year) |
| `pearson_r, n_years` | detrended hindcast skill of this unit × issue month × trimester; NaN = no skill available |
| `forecast_mean_log, obs_mean_log` | the normalised, detrended trimester means in log1p(mm/day) space; `obs_mean_log` is NaN for the live forecast |
| `in_sample` | True for hindcast years (an observation exists), False for the live forecast |
| `pct` | forecast percentile within the unit's hindcast forecasts (0 = driest ever forecast, 100 = wettest) |
| `dry_rp, wet_rp` | Weibull return period of the forecast from the dry and the wet end; max = n+1 ≈ 47 |
| `forecast_mm` | the row's forecast as a trimester total in mm: `expm1(forecast_mean_log)` (mm/day, normalised to ERA5 and detrended — the value the RP is computed from), clipped at 0, × the trimester's calendar days (`TRIMESTER_DAYS`, non-leap, 89–92; Feb trimesters are 1 day short in leap years, ~1 %) |
| `hist_mean_mm` | the "normal" to read `forecast_mm` against: `expm1` of the **mean of `obs_mean_log`** over every observed year (1981→) of this unit × issue month × trimester, clipped at 0, × the same days. This is `era5_mean` of the skill file, so the number is identical to the "normal" the Forecast × HNRP tab shows next to the same forecast. A log-space mean, not the arithmetic mean of the mm values, which sits above the median for skewed rain and would read a median forecast (`pct` 50) as below normal. Constant across the years of a combo; NaN only where the combo has no hindcast (`pct` / RPs NaN too) |
| `tri_share_annual` | the trimester's share of the unit's annual ERA5 rainfall (climatology) |
| `tri_mean_mm_day` | the trimester's climatological mean, mm/day |
| `in_season_flat` | `tri_share_annual ≥ 0.25` — the starting rule |
| `in_season_app` | `tri_share_annual ≥ 0.15` — what the app / site call "rainy" |

`units_adm*.parquet`: `pcode, iso3, name, adm_level, n_units_in_country, has_skill`. Use
`n_units_in_country` as the denominator for `frac` when a unit drops out for lack of data.

### 4b. The processed products the signal tables are built from

| Path | Level | Content |
|---|---|---|
| `skill_stats_detrended.parquet` / `skill_stats_detrended_adm1.parquet` / `skill_stats_detrended_adm2.parquet` | 0 / 1 / 2 | one row per unit × issue month × trimester: `pearson_r, n_years, rmse, sigma, era5_mean, era5_std, lower_tercile_mm, current_forecast_year, current_forecast_mean, is_predictive, prob_lower_tercile, forecast_rp (dry), flood_rp (wet), prob_rp, forecast_percentile` — the **latest** forecast only. 1.2 / 17 / 36 MB |
| `paired_yearly_detrended.parquet` / `…_adm1.parquet` / `…_adm2.parquet` | 0 / 1 / 2 | one row per unit × issue month × trimester × **year**: `season_year, forecast_mean, obs_mean, hist_prob` (log space). The full hindcast; everything per-year derives from it. 17 / 258 / 562 MB |
| `skill_stats*.parquet`, `paired_yearly*.parquet` (no `_detrended`) | 0 / 1 / 2 | the same, raw (not detrended) variant |
| `monthly_clim.parquet` / `monthly_clim_adm1.parquet` / `monthly_clim_adm2.parquet` | 0 / 1 / 2 | ERA5 monthly climatology: `pcode, iso3, name, adm_level, month, mean_mm_day` (12 rows per unit) |

Producers, in order: `pipeline/compute_skill.py` (admin-0, ~30 min, also run by the monthly
cron), `compute_skill_adm1.py` (~40 min), `compute_skill_adm2.py` (scope = `ADM2_ISO3S`
in that file, ~15 min), `compute_monthly_clim.py --level N`, `build_hdx_signal_inputs.py
--level N`.

Other things under `processed/` (`raster/`, `enso_slides/`, `uga/`, `stories/`, `cma/`,
`forecast_site.parquet`, `pop_adm3_worldpop.parquet`) belong to other products of this repo
and are not inputs here. `rainy_pairs.parquet` and `roc_auc_stats.parquet` (May 2026) are
orphans of an earlier iteration — nothing reads them.

## 5. Loading

```python
import ocha_stratus as stratus

P = "ds-seas5-skill/processed/hdx_signal/"
latest = stratus.load_parquet_from_blob(P + "signal_inputs_adm1_latest.parquet", stage="dev")
units  = stratus.load_parquet_from_blob(P + "units_adm1.parquet", stage="dev")
```

The full-history admin-1 table is ~9 M rows (fine in pandas, ~1.5 GB); the admin-2 one is
~20 M rows — read it through Arrow with column pruning and a country filter rather than
`load_parquet_from_blob`:

```python
import io, pyarrow.parquet as pq
cc = stratus.get_container_client(stage="dev", container_name="projects")
buf = io.BytesIO(cc.download_blob(P + "signal_inputs_adm2.parquet").readall())
eth = pq.read_table(buf, filters=[("iso3", "=", "ETH")],
                    columns=["pcode", "issued_year", "issued_month", "trimester", "lead",
                             "pearson_r", "dry_rp", "wet_rp", "tri_share_annual"]).to_pandas()
```

Credentials: the usual `DSCI_AZ_BLOB_DEV_SAS` (read) in the environment; the DB is not needed
unless you go back to `public.seas5` / `public.era5` yourself.

## 6. Evaluating the signal

```python
from src.hdx_signal import country_signal, unit_condition

dry = country_signal(latest, direction="dry", frac=0.60, rp=5, r_min=0.30, season_share=0.25)
wet = country_signal(latest, direction="wet")            # same defaults
dry[dry["signal"]][["iso3", "trimester", "lead", "n_units", "n_qualifying", "frac_qualifying"]]
```

`country_signal` returns one row per country × issuance × trimester with `n_units`,
`n_qualifying`, `frac_qualifying`, `signal`. Two denominators are offered: `"all"` (every unit
with data — the literal "fraction of the admin-1s") and `"in_season"` (only units for which the
trimester is in season, so a country's arid half cannot dilute its rainy half). Which one the
signal should use is an open design choice. Run it over the full-history table to backtest:
the same call on `signal_inputs_adm1.parquet` gives every issuance since 1981 (hindcast RPs are
in-sample, see §7).

To sweep: the four parameters are plain keyword arguments; the per-row condition is
`unit_condition(df, direction, rp, r_min, season_share)` if you want to build a different
roll-up (e.g. population-weighted).

## 7. Coverage and caveats — read before drawing conclusions

- **Coverage.** The admin-1 signal table has **2,283 units in 150 countries**, of which
  **2,260 have skill**. The skill file itself lists 2,330 units in 152 countries — the 47
  extra are micro-units (Caribbean and Pacific islands, one Botswana town) that miss every
  SEAS5 pixel and so have no forecast at all; Dominica and St Lucia drop out entirely. Every
  other country in `public.polygon` is there except Sint Maarten. 23 units have no ERA5
  rows either, hence no climatology (`tri_share_annual` NaN; they never qualify). Admin-2
  skill exists for **24 countries**:
  the 23 HNRP/IPC-scope countries with admin-2 boundaries in the DB plus **Ethiopia (92
  zones)**, added for this work. Kenya, for instance, has no admin-2 in `public.polygon`;
  extending admin-2 means first getting the boundaries + zonal stats into the rasterstats
  DB, then adding the ISO3 to `ADM2_ISO3S` and running `compute_skill_adm2.py --iso3 XXX`.
- **The September 2026 issuance sits at hindcast extremes** almost everywhere: JAS and ASO
  (in-season, leads −2/−1) come out at percentile 0 / RP ≈ 47 across the whole Sahel and much
  of East Africa. With the starting parameters that fires a dry signal for Niger, Ethiopia and
  most of the Sahel on JAS/ASO. We believe this is at least partly an artefact of the in-season
  blend (an ERA5/SEAS5 mismatch in July–August 2026), not an assessed drought — treat the
  in-season leads of this issuance as suspect and do not quote them publicly. A full-forecast
  lead (0–4) signal is on much firmer ground. Backtests over earlier issuances are unaffected.
- **In-season leads have high skill by construction** (1–2 of the 3 months are observations).
  If the signal is meant to be *anticipatory*, restrict to `lead ≥ 0` or at least report leads
  separately.
- **Hindcast RPs are in-sample**: for 1981–2025 the year's own forecast is part of the
  distribution it is ranked in (the live year is not). A leave-one-out recomputation from
  `paired_yearly_detrended_adm*.parquet` is a 20-line change in
  `build_hdx_signal_inputs.py:position_metrics` if a backtest needs it.
- **Vintage.** The pipelines run after the 5th (SEAS5) and after ERA5's previous month has
  landed. If a compute ran before that month's ERA5 arrived, the in-season trimesters at that
  issue month silently carry the previous year's forecast. `pipeline/verify_site_data.py`
  guards the site; for the tables here, check `issued_year` on the `_latest` file and that the
  in-season rows have `in_sample == False`.
- **RP is not magnitude.** A 47-year dry RP in a marginal season can be a few millimetres.
  `forecast_mm` and `hist_mean_mm` (trimester totals, the same values as the HNRP tab's
  "forecast vs normal") pair the anomaly with an amount; `tri_mean_mm_day` is the raw ERA5
  climatology in mm/day (arithmetic mean, so a little above `hist_mean_mm`).
- **Countries are unequal in unit count** (Niger 8 admin-1 units, DR Congo 26, Ethiopia 13 /
  92 zones). A 60 % threshold means different things across them; `n_units_in_country` is in
  the units table.

## 8. Refreshing after a new issuance

SEAS5 arrives on the 5th; ERA5 for the previous month around the 6th. The Databricks job
"SEAS5 Skill Monthly Refresh" (`databricks.yml`, 6th 13:15 UTC, polling until the raster-stats
tables are current) refreshes **all three admin levels**, the monthly climatology and these
signal tables in one run (tasks `skill_adm0` → `skill_adm1` → `skill_adm2` → `clim_signal`), and
its vintage gate runs `verify_site_data.py --check-signal`, so `signal_inputs{,_latest}.parquet`
and `units.parquet` can never lag the country data. Nothing is manual any more, and laptops can
no longer reach the database. To redo a level by hand, run the same scripts from the workspace
(`databricks bundle run seas5_monthly_refresh -t dev`, or "Repair run" on the task) — the
underlying commands are unchanged:

```bash
uv run python pipeline/compute_skill_adm1.py                 # ~40 min, 8 workers
uv run python pipeline/compute_skill_adm2.py                 # ~15 min (all ADM2_ISO3S)
for L in 0 1 2; do uv run python pipeline/build_hdx_signal_inputs.py --level $L; done
# the job also refreshes the climatology every run (cheap); by hand only if units changed:
for L in 0 1 2; do uv run python pipeline/compute_monthly_clim.py --level $L; done
```

Adding a country at admin-2: add its ISO3 to `ADM2_ISO3S` in `compute_skill_adm2.py`, then
`compute_skill_adm2.py --iso3 XXX` (merges into the existing files), `compute_monthly_clim.py
--level 2`, `build_hdx_signal_inputs.py --level 2`.

## 9. Related, for orientation

- Interactive app (country level, all controls): see the repo README.
- Static site with the admin-1/2/3 **Forecast × HNRP** tab, which uses the same skill files
  and the looser 0.15 in-season rule: https://ocha-dap.github.io/ds-seas5-skill/app/#hnrp
- Method description for readers: the site's Methodology tab; the code is `src/skill.py`.
