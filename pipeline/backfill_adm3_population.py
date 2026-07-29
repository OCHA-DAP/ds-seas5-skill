"""One-off: WorldPop total population per ADM3 unit -> dev blob parquet.

The population mirrors stop at admin-2 (HAPI truncates COD-PS; the HNO 'all'
baseline publishes none at admin-3), so the tab's adm3 view has no denominator.
WorldPop 1km UN-adjusted 2020 rasters, zonally summed over the same COD adm3
shapefiles the site's geometry uses (spatial join — no pcode reconciliation),
fill it: ~950 units across BFA/MMR/SYR.

Run:  uv run python pipeline/backfill_adm3_population.py
Writes ds-seas5-skill/processed/pop_adm3_worldpop.parquet (dev blob).
"""
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import rasterio
import rasterio.mask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.constants import PROJECT_PREFIX  # noqa: E402

ADM3_ISO3S = ["BFA", "MMR", "SYR"]
WP_URL = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/"
          "2020/{ISO}/{iso}_ppp_2020_1km_Aggregated_UNadj.tif")
OUT_BLOB = f"{PROJECT_PREFIX}/processed/pop_adm3_worldpop.parquet"


def main() -> None:
    rows = []
    with tempfile.TemporaryDirectory() as td:
        for iso3 in ADM3_ISO3S:
            tif = Path(td) / f"{iso3}.tif"
            url = WP_URL.format(ISO=iso3, iso=iso3.lower())
            print(f"{iso3}: downloading WorldPop raster...")
            urllib.request.urlretrieve(url, tif)
            g = stratus.load_shp_from_blob(
                f"{iso3.lower()}_shp.zip", shapefile=f"{iso3.lower()}_adm3.shp",
                stage="prod", container_name="polygon",
            )
            ncol = next(c for c in g.columns
                        if c.upper() in ("ADM3_EN", "ADM3_FR", "ADM3_ES"))
            with rasterio.open(tif) as src:
                if g.crs != src.crs:
                    g = g.to_crs(src.crs)
                for _, r in g.iterrows():
                    try:
                        arr, _ = rasterio.mask.mask(
                            src, [r.geometry], crop=True, nodata=np.nan, filled=True)
                        pop = float(np.nansum(arr))
                    except ValueError:  # geometry outside raster bounds
                        pop = float("nan")
                    rows.append({"pcode": r["ADM3_PCODE"], "iso3": iso3,
                                 "name": r[ncol],
                                 "population": round(pop) if pop == pop else None,
                                 "pop_year": 2020})
            n = sum(1 for x in rows if x["iso3"] == iso3 and x["population"])
            tot = sum(x["population"] or 0 for x in rows if x["iso3"] == iso3)
            print(f"  {iso3}: {n} units, total {tot/1e6:.1f}M")
    df = pd.DataFrame(rows)
    stratus.upload_parquet_to_blob(df, OUT_BLOB, stage="dev")
    print(f"Wrote {OUT_BLOB} ({len(df)} rows)")


if __name__ == "__main__":
    main()
