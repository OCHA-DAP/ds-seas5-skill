# The removed HNRP scatter plot (and how to bring it back)

Removed 2026-08 from the Forecast × HNRP tab: a square severity-vs-targeted
scatter judged more confusing than useful. This note records what it was and
how to revive it.

## What it showed
One bubble per admin unit: **x = targeted as % of the unit's population,
y = population in the selected severity band as % of the same population**,
bubble area = that population base, fill/outline = forecast category × skill
(same encoding as the map). Axes fixed 0–100%. A dashed 45° line with rotated
half-plane labels: because both axes divided by the SAME per-unit population
base, position compared absolute headcounts — above the line, more people in
the severity band than the plan targets (a possible coverage gap); below it,
targeting exceeds it. The 6 largest bases were direct-labelled; trimester
codes were printed inside bubbles large enough; units missing a figure for an
axis sat at 0% (origin if both); in the Lowest view a corner note said which
admin level a selected country was shown at.

## The population-base machinery (still in the payloads)
The per-unit denominator survives in the exported rows and is the part worth
keeping alive: `pop`/`pop_year`/`pop_src` = layered base — COD-PS baseline
(ds-population-mirror) where present and trusted (1.3× analysed distrust
guard; PAK excluded for upstream mis-p-coding), else the plan's HNO/JIAF
baseline total (needs_admin population_status='all'), else WorldPop 2020 at
adm3 (`pipeline/backfill_adm3_population.py`); client fallback was
max(IPC analysed, JIAF analysed). See the git history of the "Reading the
scatter" footnote section for the full caveat text.

## To revive
`git show d7140e9:docs/hnrp.js` — the last commit with the scatter live.
Restore: `scatterRows()`, `popOf()`, `popSrcOf()`, `renderScatter()` and its
call in `renderAll()`, plus the `#hnrp-scatter-wrap`/`#hnrp-tip` markup and
the "Reading the scatter" footnote from the same commit's `docs/index.html`.
CSS (`#hnrp-scatter-*`) was left in `docs/style.css`.
