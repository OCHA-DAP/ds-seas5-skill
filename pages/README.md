# Pages site

**Live:** https://ocha-dap.github.io/ds-seas5-skill/

The landing page plus per-product pages, using the template from
`OCHA-DAP/ds-geospatial-impact-estimates` (HDX v2 tokens, particle hero, cards). There is no
build step: `.github/workflows/deploy-pages.yml` assembles the site on push to `main` as

| Site path | Source |
|---|---|
| `/` | `pages/` as-is (landing, assets, product pages) |
| `/app/` | `docs/` — the SEAS5 alerts app, copied unchanged at deploy time |

so the app stays exactly where it always lived in the repo (`docs/`, refreshed monthly by
`monthly-refresh.yml`) and nothing about it changes except the URL prefix. The site root,
which used to serve the app directly, now serves the landing page.

**Deploy triggers.** Push to `main` touching `pages/**` or `docs/**` — plus a `workflow_run`
on "Monthly data refresh", because that workflow merges its data PR with `GITHUB_TOKEN`, whose
pushes do not fire push-triggered workflows. Without the `workflow_run` trigger the app's data
would silently stop reaching the site.

## Adding a page

Create a directory under `pages/` with an `index.html`, then add a card to `pages/index.html` —
copy an existing `<a class="k">` block and change the href, title, blurb and foot. Nothing on
this site is auth-gated; keep it that way or gate deliberately (see the geospatial repo's
README for the encrypted-page pattern).

## The Uganda analysis

`pages/uganda/index.html` is the **rendered** output of `analysis/uganda_hnrp.qmd`
(self-contained Quarto HTML). Editing the `.qmd` does not update the site — re-render and copy:

```bash
QUARTO_PYTHON=.venv/bin/python quarto render analysis/uganda_hnrp.qmd --to html
cp analysis/uganda_hnrp.html pages/uganda/index.html
```

## The Uganda flood-trigger plan

`pages/uganda-flood-trigger/index.html` is hand-authored HTML (no build step): the
OND 2026 flood-trigger design plan and options, synthesized from five country
documents shared Aug 2026 (DTM affected-by-district xlsx, UHF emerging-risk note,
El Niño impact retrospective, URCS/FAO flood-risk maps, FAO EWS/AA assessment) plus
the `/uganda/` analysis's FloodScan/SEAS5 layers. The source documents are not
committed or republished — the page quotes district-level facts only. Edit the HTML
directly to update.

## The ENSO slides (every country)

`pages/enso/` is a country-selector page (dropdown with English/French country names;
selection and language land in `?country=ISO3&lang=fr`) over per-country slide pairs —
inline SVGs with selectable text plus vector PDFs, EN + FR. **The slides are not in
git**: ~700 MB of `{ISO3}_slide{1,2}[_fr].svg` + `{ISO3}[_fr].pdf` + `countries.json`
live on the dev `projects` blob under `ds-seas5-skill/processed/enso_slides/`, and the
Pages deploy downloads them into the artifact (`pipeline/sync_enso_slides.py download`,
using the repo's `DSCI_AZ_BLOB_DEV_SAS` secret). French country names:
`analysis/country_names_fr.json` (NE `NAME_FR` + UN short names).

To refresh after a new SEAS5 issuance (needs a current `skill_stats_grid_detrended.nc`;
the script asserts its vintage — the slide-1 correlations are cached in
`analysis/_png_enso_cache/corr_*.npz` so this is render-only, ~30 min):

```bash
rm -f /tmp/skill_stats_grid_detrended.nc
uv run python analysis/png_enso_slides.py --country all --force
uv run python pipeline/sync_enso_slides.py upload     # needs the write SAS
gh workflow run deploy-pages.yml
```

Add `--recompute` only if the ERA5 record itself moved (new year of data).
`pages/{png,tls,ner}-enso/` are redirect stubs kept so shared links survive.

## Checking locally

```bash
mkdir -p /tmp/seas5-site && rsync -a --delete pages/ /tmp/seas5-site/ && rsync -a docs/ /tmp/seas5-site/app/
python3 -m http.server -d /tmp/seas5-site 8000
```
