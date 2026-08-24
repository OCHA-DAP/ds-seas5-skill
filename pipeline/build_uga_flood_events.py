"""Case-study rasters for Uganda's worst EM-DAT flood events.

For each selected event (chosen from EM-DAT by deaths/affected to span the two
regimes: wetland-extent floods and slope landslides), downloads the daily
FloodScan SFED COGs over the event window (padded a week each side), takes the
per-pixel maximum, and stores one band per event plus a metadata parquet.

Writes (dev blob):
  {PROJECT_PREFIX}/processed/uga/flood_events_maxsfed.tif   band per event (desc = DisNo)
  {PROJECT_PREFIX}/processed/uga/flood_events.parquet       event metadata + district lists

Run:  uv run python pipeline/build_uga_flood_events.py
"""

import io
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import rasterio
from rasterio.windows import bounds as window_bounds
from rasterio.windows import from_bounds
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

UGA_BOX = (29.0, -2.0, 35.5, 4.7)
PAD_DAYS = 7

# label: short human name; region: DB adm1 pcode whose daily series tells the story;
# districts: CODAB ADM2_EN names parsed from the EM-DAT Location text.
EVENTS = [
    dict(disno="2007-0408-UGA", label="2007 Teso floods", start=date(2007, 8, 15), end=date(2007, 10, 31),
         region="UG2", deaths=29, affected=718045,
         districts=["Amuria", "Bukedea", "Kaberamaido", "Katakwi", "Kumi", "Soroti", "Abim",
                    "Kaabong", "Kotido", "Moroto", "Nakapiripirit", "Bududa", "Bukwo", "Kapchorwa",
                    "Mbale", "Manafwa", "Sironko", "Adjumani", "Arua", "Moyo", "Nebbi", "Yumbe"]),
    dict(disno="2019-0625-UGA", label="Dec 2019 Rwenzori & Elgon floods", start=date(2019, 12, 18), end=date(2019, 12, 18),
         region="UG4", deaths=65, affected=65250,
         districts=["Kasese", "Bundibugyo", "Ntoroko", "Mbale", "Sironko", "Bududa"]),
    dict(disno="2024-0883-UGA", label="Nov 2024 Mt Elgon landslides", start=date(2024, 11, 27), end=date(2024, 11, 28),
         region="UG2", deaths=141, affected=30022,
         districts=["Bulambuli", "Mbale", "Sironko", "Kapchorwa", "Kween", "Bukwo", "Bududa"]),
    dict(disno="2010-0084-UGA", label="Mar 2010 Bududa landslide", start=date(2010, 2, 25), end=date(2010, 3, 1),
         region="UG2", deaths=388, affected=12795,
         districts=["Butaleja", "Bududa", "Budaka", "Bukwo", "Kapchorwa", "Katakwi", "Manafwa",
                    "Mbale", "Pallisa", "Sironko"]),
]

OUT_TIF = f"{PROJECT_PREFIX}/processed/uga/flood_events_maxsfed.tif"
OUT_PARQUET = f"{PROJECT_PREFIX}/processed/uga/flood_events.parquet"


def _read_sfed(d: date):
    name = f"floodscan/daily/v5/processed/aer_area_300s_v{d:%Y-%m-%d}_v05r01.tif"
    for attempt in range(3):
        try:
            data = stratus.load_blob_data(name, container_name="raster", stage="prod")
            with rasterio.open(io.BytesIO(data)) as ds:
                w = (from_bounds(*UGA_BOX, ds.transform)
                     .round_offsets(op="floor").round_lengths(op="ceil"))
                return ds.read(1, window=w), window_bounds(w, ds.transform), ds.crs.to_wkt()
        except Exception:  # noqa: BLE001 — retry transient blob errors, re-raise at the end
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def main() -> None:
    bands, bounds_ref, wkt = [], None, None
    with ThreadPoolExecutor(max_workers=12) as pool:
        for ev in tqdm(EVENTS, desc="events"):
            days = pd.date_range(ev["start"] - timedelta(days=PAD_DAYS),
                                 ev["end"] + timedelta(days=PAD_DAYS)).date
            results = list(pool.map(_read_sfed, days))
            arrs = [r[0] for r in results]
            if bounds_ref is None:
                bounds_ref, wkt = results[0][1], results[0][2]
            bands.append(np.nanmax(np.stack(arrs), axis=0).astype("float32"))

    xmin, ymin, xmax, ymax = bounds_ref
    transform = rasterio.transform.from_bounds(xmin, ymin, xmax, ymax,
                                               bands[0].shape[1], bands[0].shape[0])
    profile = dict(driver="GTiff", width=bands[0].shape[1], height=bands[0].shape[0],
                   count=len(bands), dtype="float32", crs=wkt, transform=transform,
                   compress="deflate", nodata=np.nan)
    buf = io.BytesIO()
    with rasterio.open(buf, "w", **profile) as dst:
        for i, (band, ev) in enumerate(zip(bands, EVENTS), 1):
            dst.write(band, i)
            dst.set_band_description(i, ev["disno"])
    stratus.upload_blob_data(buf.getvalue(), OUT_TIF, stage="dev")

    meta = pd.DataFrame([{**{k: v for k, v in ev.items() if k != "districts"},
                          "districts": ";".join(ev["districts"])} for ev in EVENTS])
    stratus.upload_parquet_to_blob(meta, OUT_PARQUET, stage="dev")
    print(f"Uploaded {OUT_TIF} ({len(bands)} bands) and {OUT_PARQUET}")


if __name__ == "__main__":
    main()
