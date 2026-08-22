"""Uganda cropping seasons from JRC ASAP, keyed to CODAB districts.

Downloads ASAP's sub-national crop calendar (crop_calendar_gaul1.zip: planting/
growth/harvest dekads per unit × crop) and unit boundaries (gaul1_asap.zip),
keeps Uganda's 10 agro-ecological zones, and assigns each CODAB adm2 district
to its zone by representative point. Karamoja has boundaries but NO calendar
rows — ASAP classifies it 91% rangeland / 4% cropland (pastoral), which is a
finding, not a gap.

Writes (dev blob):
  {PROJECT_PREFIX}/processed/uga/asap_crop_calendar.parquet   zone × crop dekads
  {PROJECT_PREFIX}/processed/uga/asap_district_zones.parquet  adm2 pcode -> zone

Run:  uv run python pipeline/build_uga_crop_calendar.py
"""

import io
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import geopandas as gpd
import ocha_stratus as stratus
import pandas as pd
import requests
from ocha_stratus import codab

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

ASAP = "https://agricultural-production-hotspots.ec.europa.eu/files"
UGA_ASAP0 = 209


def main() -> None:
    cal_zip = requests.get(f"{ASAP}/crop_calendar_gaul1.zip", timeout=120)
    cal_zip.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(cal_zip.content)) as z:
        with z.open("crop_calendar_gaul1.csv") as f:
            cal = pd.read_csv(f, sep=";")
    cal = cal[cal["asap0_id"] == UGA_ASAP0].copy()
    if cal.empty:
        raise RuntimeError("no Uganda rows in ASAP crop calendar — format changed?")

    shp_zip = requests.get(f"{ASAP}/gaul1_asap.zip", timeout=300)
    shp_zip.raise_for_status()
    with TemporaryDirectory() as td:
        Path(td, "gaul1_asap.zip").write_bytes(shp_zip.content)
        zones = gpd.read_file(f"zip://{td}/gaul1_asap.zip!gaul1_asap.shp")
    zones = zones[zones["asap0_id"] == UGA_ASAP0]
    if len(zones) != 10:
        raise RuntimeError(f"expected 10 Uganda ASAP zones, got {len(zones)}")

    # Calendar has the short name; boundaries have both. Attach the full name +
    # land-use context by asap1_id, and keep zones without calendar rows visible.
    cal = cal.merge(
        zones[["asap1_id", "name1", "km2_tot", "km2_crop", "km2_range"]],
        on="asap1_id", how="right",
    ).rename(columns={"name1": "zone"})
    no_cal = cal[cal["crop_name"].isna()]["zone"].tolist()
    print(f"{cal['crop_name'].notna().sum()} calendar rows across "
          f"{cal['zone'].nunique()} zones; zones without crops: {no_cal}")

    cod2 = codab.load_codab_from_blob("uga", admin_level=2).to_crs(zones.crs)
    pts = cod2[["ADM2_PCODE", "ADM2_EN"]].copy()
    pts = gpd.GeoDataFrame(pts, geometry=cod2.representative_point(), crs=zones.crs)
    dz = gpd.sjoin(pts, zones[["asap1_id", "name1", "geometry"]], how="left")
    if dz["name1"].isna().any():
        raise RuntimeError(f"districts outside every ASAP zone: "
                           f"{dz[dz['name1'].isna()]['ADM2_EN'].tolist()}")
    dz = dz[["ADM2_PCODE", "ADM2_EN", "asap1_id", "name1"]].rename(
        columns={"ADM2_PCODE": "pcode", "ADM2_EN": "district", "name1": "zone"})

    cal_out = cal[["asap1_id", "zone", "crop_name", "sos_s", "sos_e", "eos_s", "eos_e",
                   "km2_tot", "km2_crop", "km2_range"]]
    for df, blob in [(cal_out, f"{PROJECT_PREFIX}/processed/uga/asap_crop_calendar.parquet"),
                     (dz, f"{PROJECT_PREFIX}/processed/uga/asap_district_zones.parquet")]:
        print(f"Uploading {len(df)} rows -> {blob} (dev)")
        stratus.upload_parquet_to_blob(df, blob, stage="dev")
    print("Done.")


if __name__ == "__main__":
    main()
