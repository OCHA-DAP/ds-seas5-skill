"""
Build PSP wide-format FeatureCollection + multi-band raster from the v2 scrape.
================================================================================

Inputs
------
- ``temp/asap1_psp_v2.parquet`` — long-format PSP summary from the v2 scraper
  (one row per (asap1_id, season_id) with psp_sos_month, psp_eos_month, season,
  land_use).
- ``temp/gaul1_asap_v05/gaul1_asap_v05/gaul1_asap.shp`` — ASAP-modified GAUL1
  admin1 boundaries, keyed by ``asap1_id``.

Outputs (in ``temp/``)
----------------------
- ``psp_admin1.geojson`` — 1 row per admin1, 48 boolean columns
  (``crop_s1_m01``..``range_s2_m12``) plus identifiers and geometry.
  Source for the ee.FeatureCollection asset.
- ``psp_admin1_mask.tif`` — 48-band uint8 raster at 0.05° EPSG:4326. Source
  for the ee.Image asset. GEE downsamples cleanly to SEAS5's coarser grid
  at render time.

Band / column naming
--------------------
Raster band descriptions use full names: ``{land_use}_s{1|2}_m{01..12}``
(``crop_s1_m01`` etc.) — 4 × 12 = 48 bands.

Shapefile column names use a shortened form because shapefile attribute
names are capped at 10 chars: ``c1_M01``..``r2_M12`` where ``c|r`` is
crop|rangeland and the digit after is the season. Mapping is round-trip
deterministic via ``raster_band_name()`` / ``shp_col_name()``.

Usage notes
-----------
For an admin with only one season (the common monomodal case), the s2 bands
are all 0. The 3 admins where ASAP labels the single crop season as "season=2"
(10081, 1381, 1811) populate s2 bands, not s1 — preserves ASAP's labeling.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_bounds

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PSP_PATH = Path("temp/asap1_psp_v2.parquet")
SHP_PATH = Path("temp/gaul1_asap_v05/gaul1_asap_v05/gaul1_asap.shp")

OUT_FC = Path("temp/psp_admin1/psp_admin1.shp")
OUT_TIF = Path("temp/psp_admin1_mask.tif")

# 0.05° (~5km at equator). Fine enough for accurate adm0-level % stats and
# for visual clarity; GEE downsamples to SEAS5's coarser grid at render.
RESOLUTION_DEG = 0.05

LAND_USES = ["crop", "rangeland"]
SEASONS = ["1", "2"]
MONTHS = list(range(1, 13))

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Month-of-year expansion (handles wrap-around)
# ---------------------------------------------------------------------------

def months_in_psp(sos: int, eos: int) -> set[int]:
    """
    Months covered by an inclusive [sos, eos] PSP window.

    Handles wrap-around: e.g., sos=11, eos=2 → {11, 12, 1, 2}.
    """
    if sos <= eos:
        return set(range(sos, eos + 1))
    return set(range(sos, 13)) | set(range(1, eos + 1))


# ---------------------------------------------------------------------------
# Long → wide expansion
# ---------------------------------------------------------------------------

def raster_band_name(land_use: str, season: str, month: int) -> str:
    """Full readable name, used in GeoTIFF band descriptions (no length limit)."""
    short = "crop" if land_use == "crop" else "range"
    return f"{short}_s{season}_m{month:02d}"


def shp_col_name(land_use: str, season: str, month: int) -> str:
    """Shortened name for shapefile attributes (10-char cap)."""
    prefix = "c" if land_use == "crop" else "r"
    return f"{prefix}{season}_M{month:02d}"


ALL_BAND_NAMES: list[str] = [
    raster_band_name(lu, s, m)
    for lu in LAND_USES
    for s in SEASONS
    for m in MONTHS
]

ALL_SHP_COLS: list[str] = [
    shp_col_name(lu, s, m)
    for lu in LAND_USES
    for s in SEASONS
    for m in MONTHS
]

# Round-trip mapping: long ↔ short.
BAND_TO_SHP: dict[str, str] = dict(zip(ALL_BAND_NAMES, ALL_SHP_COLS))


def build_wide(psp: pd.DataFrame) -> pd.DataFrame:
    """
    Expand long PSP rows into a wide table: 1 row per asap1_id, 48 bool cols.

    Each row in ``psp`` contributes 12 monthly booleans to one (land_use, season)
    block. Multiple rows for the same (admin, land_use, season) would OR — but
    after the v2 dedupe by season_id there's at most one row per such tuple.
    """
    # Initialize wide frame indexed by asap1_id, all False.
    asap_ids = pd.unique(psp["asap1_id"]).astype(int)
    wide = pd.DataFrame(
        0,
        index=pd.Index(asap_ids, name="asap1_id"),
        columns=ALL_BAND_NAMES,
        dtype=np.uint8,
    )

    for row in psp.itertuples(index=False):
        aid = int(row.asap1_id)
        months = months_in_psp(row.psp_sos_month, row.psp_eos_month)
        for m in months:
            col = raster_band_name(row.land_use, row.season, m)
            wide.at[aid, col] = 1

    return wide


# ---------------------------------------------------------------------------
# Rasterization
# ---------------------------------------------------------------------------

def rasterize_admin_ids(
    gdf: gpd.GeoDataFrame,
    resolution_deg: float,
) -> tuple[np.ndarray, rasterio.Affine]:
    """
    Rasterize admin1 polygons to a global EPSG:4326 grid where each pixel value
    is the asap1_id of the containing admin (0 = no admin).

    Done once; band lookups happen via numpy indexing afterwards.
    """
    width = int(round(360 / resolution_deg))
    height = int(round(180 / resolution_deg))
    transform = from_bounds(-180, -90, 180, 90, width, height)

    log.info("Rasterizing %d admin polygons to %dx%d grid (%.3f°)",
             len(gdf), width, height, resolution_deg)

    shapes = (
        (geom, int(aid))
        for geom, aid in zip(gdf.geometry, gdf["asap1_id"])
        if geom is not None and not geom.is_empty
    )
    id_grid = rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=True,  # keep small admins visible
    )
    return id_grid, transform


def build_band_stack(
    wide: pd.DataFrame,
    id_grid: np.ndarray,
) -> np.ndarray:
    """
    For each of the 48 bands, build a 2D mask via vectorized lookup from id_grid.

    Lookup table indexed by asap1_id (sparse but ids fit comfortably in an
    int32 dense array). Pixels with id=0 → 0 in every band.
    """
    max_id = int(max(wide.index.max(), id_grid.max()))
    n_bands = len(ALL_BAND_NAMES)

    log.info("Building %d-band stack (max asap1_id=%d)", n_bands, max_id)

    stack = np.zeros((n_bands, *id_grid.shape), dtype=np.uint8)
    for i, band in enumerate(ALL_BAND_NAMES):
        lookup = np.zeros(max_id + 1, dtype=np.uint8)
        lookup[wide.index.values] = wide[band].values
        stack[i] = lookup[id_grid]
    return stack


def write_geotiff(
    stack: np.ndarray,
    transform: rasterio.Affine,
    out_path: Path,
) -> None:
    """Write multi-band uint8 GeoTIFF with band descriptions."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_bands, height, width = stack.shape
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": n_bands,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": transform,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
    }
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(stack)
        for i, name in enumerate(ALL_BAND_NAMES, start=1):
            dst.set_band_description(i, name)
    log.info("Wrote %d-band GeoTIFF to %s (%.1f MB)",
             n_bands, out_path, out_path.stat().st_size / 1e6)


# ---------------------------------------------------------------------------
# FC export
# ---------------------------------------------------------------------------

KEEP_GAUL_COLS = ["asap1_id", "name1", "asap0_id", "name0", "an_crop", "an_range"]


def write_shapefile_fc(
    gdf: gpd.GeoDataFrame,
    wide: pd.DataFrame,
    out_path: Path,
) -> None:
    """
    Join admin geometry to wide PSP, write zipped shapefile.

    Shapefile is GEE's most reliable upload format for FeatureCollections.
    Column names are mapped from long band names to ≤10-char shortened form
    (see ``BAND_TO_SHP``) for shapefile compatibility.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep = [c for c in KEEP_GAUL_COLS if c in gdf.columns]
    base = gdf[keep + ["geometry"]].copy()
    base["asap1_id"] = base["asap1_id"].astype(int)

    # Rename wide cols from long → short before join.
    wide_short = wide.rename(columns=BAND_TO_SHP)
    joined = base.merge(
        wide_short.reset_index(),
        on="asap1_id",
        how="left",
    )
    # Admins with no PSP data (the 248 filtered out by the scraper): fill 0.
    joined[ALL_SHP_COLS] = joined[ALL_SHP_COLS].fillna(0).astype(np.uint8)

    log.info("Writing shapefile with %d admins, %d cols to %s",
             len(joined), len(joined.columns), out_path)
    # geopandas auto-removes existing .shp/.shx/.dbf/.prj/.cpg when overwriting.
    joined.to_file(out_path, driver="ESRI Shapefile")
    # Total size = .shp + .shx + .dbf + .prj
    total = sum(f.stat().st_size for f in out_path.parent.iterdir() if f.is_file())
    log.info("Wrote shapefile (%.1f MB total across .shp/.shx/.dbf/.prj)",
             total / 1e6)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Loading PSP parquet: %s", PSP_PATH)
    psp = pd.read_parquet(PSP_PATH)
    # Source has asap1_id, season as strings; cast.
    psp["asap1_id"] = psp["asap1_id"].astype(int)
    psp["psp_sos_month"] = psp["psp_sos_month"].astype(int)
    psp["psp_eos_month"] = psp["psp_eos_month"].astype(int)
    log.info("PSP rows: %d, unique admins: %d", len(psp), psp["asap1_id"].nunique())

    log.info("Loading GAUL1 shapefile: %s", SHP_PATH)
    gdf = gpd.read_file(SHP_PATH)
    log.info("GAUL1 admins: %d (total in shapefile)", len(gdf))

    wide = build_wide(psp)
    log.info("Wide table: %d admins × %d bands", *wide.shape)
    log.info("Per-band totals (first 6 bands):")
    for b in ALL_BAND_NAMES[:6]:
        log.info("  %-16s in-season admins: %d", b, int(wide[b].sum()))

    id_grid, transform = rasterize_admin_ids(gdf, RESOLUTION_DEG)
    stack = build_band_stack(wide, id_grid)
    write_geotiff(stack, transform, OUT_TIF)

    write_shapefile_fc(gdf, wide, OUT_FC)

    log.info("Done.")


if __name__ == "__main__":
    main()
