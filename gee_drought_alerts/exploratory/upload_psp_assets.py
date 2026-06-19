"""
Upload PSP raster + shapefile to GCS staging, then ingest into Earth Engine.
============================================================================

Inputs (must exist locally, produced by ``build_psp_raster.py``)
----------------------------------------------------------------
- ``temp/psp_admin1_mask.tif`` — 48-band uint8 raster, EPSG:4326 @ 0.05°
- ``temp/psp_admin1/psp_admin1.{shp,shx,dbf,prj,cpg}`` — shapefile bundle

Process
-------
1. Zip the shapefile bundle (GEE table ingestion accepts a single zip).
2. Upload raster + shapefile zip to ``gs://ee_general_bucket/asap_psp/``.
3. Trigger ``ee.data.startIngestion`` for the raster asset.
4. Trigger ``ee.data.startTableIngestion`` for the FC asset.
5. Poll until both tasks complete; print task ids and final state.

Idempotent re-runs
------------------
If an asset already exists at the target path the script aborts before
ingestion — manually delete (``earthengine rm <asset>``) and re-run if
you want to replace it. GCS uploads always overwrite (cheap, harmless).
"""

from __future__ import annotations

import logging
import time
import uuid
import zipfile
from pathlib import Path

import ee
from google.cloud import storage

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GEE_PROJECT = "ee-zackarno"
GCS_BUCKET = "ee_general_bucket"
GCS_PREFIX = "asap_psp"

LOCAL_TIF = Path("temp/psp_admin1_mask.tif")
LOCAL_SHP_DIR = Path("temp/psp_admin1")
LOCAL_SHP_ZIP = Path("temp/psp_admin1.zip")  # built on demand

GCS_TIF_BLOB = f"{GCS_PREFIX}/psp_admin1_mask.tif"
GCS_SHP_BLOB = f"{GCS_PREFIX}/psp_admin1.zip"

ASSET_IMAGE = f"projects/{GEE_PROJECT}/assets/asap_psp_adm1_mask"
ASSET_TABLE = f"projects/{GEE_PROJECT}/assets/asap_psp_adm1_fc"

POLL_SECONDS = 15

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def zip_shapefile(shp_dir: Path, out_zip: Path) -> None:
    """Zip all shapefile siblings into a single archive at the zip root."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(shp_dir.iterdir()):
            if f.is_file():
                zf.write(f, arcname=f.name)
    size_mb = out_zip.stat().st_size / 1e6
    log.info("Zipped %s → %s (%.1f MB)", shp_dir, out_zip, size_mb)


def upload_to_gcs(local: Path, bucket_name: str, blob_name: str) -> str:
    """Upload a local file to gs://<bucket>/<blob>. Returns the gs:// URI."""
    client = storage.Client(project=GEE_PROJECT)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    log.info("Uploading %s → gs://%s/%s (%.1f MB)",
             local, bucket_name, blob_name, local.stat().st_size / 1e6)
    t0 = time.time()
    blob.upload_from_filename(str(local))
    log.info("  done in %.1fs", time.time() - t0)
    return f"gs://{bucket_name}/{blob_name}"


def asset_exists(asset_id: str) -> bool:
    try:
        ee.data.getAsset(asset_id)
        return True
    except ee.EEException:
        return False


def start_image_ingestion(asset_id: str, gcs_uri: str) -> str:
    """Trigger raster ingestion via the v2 manifest. Returns the task id."""
    manifest = {
        "name": asset_id,
        "tilesets": [{"sources": [{"uris": [gcs_uri]}]}],
        # Binary masks → MODE pyramid keeps the dominant value when downsampling.
        "pyramiding_policy": "MODE",
    }
    request_id = ee.data.newTaskId()[0]
    op = ee.data.startIngestion(request_id, manifest)
    return op["id"]


def start_table_ingestion(asset_id: str, gcs_uri: str) -> str:
    """Trigger table ingestion from a shapefile zip. Returns the task id."""
    manifest = {
        "name": asset_id,
        "sources": [{"uris": [gcs_uri]}],
    }
    request_id = ee.data.newTaskId()[0]
    op = ee.data.startTableIngestion(request_id, manifest)
    return op["id"]


def wait_for_task(task_id: str, label: str) -> str:
    """Poll a task until it leaves the PENDING/RUNNING states. Returns final state."""
    log.info("Polling task %s (%s)…", task_id, label)
    while True:
        statuses = ee.data.getTaskStatus([task_id])
        state = statuses[0]["state"]
        if state in ("COMPLETED", "FAILED", "CANCELLED"):
            log.info("  %s → %s", label, state)
            if state != "COMPLETED":
                log.error("  error: %s", statuses[0].get("error_message", "(none)"))
            return state
        log.info("  %s: %s", label, state)
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Initializing Earth Engine (project=%s)…", GEE_PROJECT)
    ee.Initialize(project=GEE_PROJECT)

    # Pre-flight: refuse to overwrite existing assets.
    for asset in (ASSET_IMAGE, ASSET_TABLE):
        if asset_exists(asset):
            raise SystemExit(
                f"Asset already exists: {asset}\n"
                f"Delete it first (`earthengine rm {asset}`) and re-run."
            )

    # Stage files in GCS.
    zip_shapefile(LOCAL_SHP_DIR, LOCAL_SHP_ZIP)
    tif_uri = upload_to_gcs(LOCAL_TIF, GCS_BUCKET, GCS_TIF_BLOB)
    shp_uri = upload_to_gcs(LOCAL_SHP_ZIP, GCS_BUCKET, GCS_SHP_BLOB)

    # Trigger ingestion.
    log.info("Starting raster ingestion → %s", ASSET_IMAGE)
    image_task = start_image_ingestion(ASSET_IMAGE, tif_uri)
    log.info("  task id: %s", image_task)

    log.info("Starting table ingestion → %s", ASSET_TABLE)
    table_task = start_table_ingestion(ASSET_TABLE, shp_uri)
    log.info("  task id: %s", table_task)

    # Poll to completion.
    image_state = wait_for_task(image_task, "raster")
    table_state = wait_for_task(table_task, "table")

    log.info("Done. raster=%s table=%s", image_state, table_state)
    if image_state == "COMPLETED":
        log.info("  Image asset: %s", ASSET_IMAGE)
    if table_state == "COMPLETED":
        log.info("  Table asset: %s", ASSET_TABLE)


if __name__ == "__main__":
    main()
