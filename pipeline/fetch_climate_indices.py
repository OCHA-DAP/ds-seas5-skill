"""OND-mean climate indices (DMI for the IOD, ONI for ENSO), 1998-2025.

Fetched once from NOAA PSL and stored on the project blob so the Uganda
analysis renders without a live external dependency and the year
classifications are data-driven rather than remembered:

  DMI (HadISST1.1): https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data
  ONI:              https://psl.noaa.gov/data/correlation/oni.data

Writes {PROJECT_PREFIX}/raw/climate_indices/ond_indices.parquet with columns
year, dmi_ond, oni_ond (OND = mean of the Oct/Nov/Dec monthly values).

Run:  uv run python pipeline/fetch_climate_indices.py
"""

import sys
from pathlib import Path

import ocha_stratus as stratus
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.constants import PROJECT_PREFIX  # noqa: E402

SOURCES = {
    "dmi": "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data",
    "oni": "https://psl.noaa.gov/data/correlation/oni.data",
}
OUT = f"{PROJECT_PREFIX}/raw/climate_indices/ond_indices.parquet"


def parse_psl(text: str) -> pd.DataFrame:
    """PSL timeseries format: header line 'startyear endyear', then one row per
    year with 12 monthly values; trailing metadata lines; large negatives are
    missing-value sentinels."""
    lines = [ln.split() for ln in text.strip().splitlines()]
    y0, y1 = int(lines[0][0]), int(lines[0][1])
    rows = []
    for ln in lines[1:]:
        if len(ln) != 13 or not ln[0].lstrip("-").isdigit():
            break
        yr = int(ln[0])
        if not (y0 <= yr <= y1):
            break
        vals = [float(v) for v in ln[1:]]
        rows.append([yr] + vals)
    df = pd.DataFrame(rows, columns=["year"] + list(range(1, 13))).set_index("year")
    return df.mask(df < -90)  # -99.99 / -9.9 style sentinels


def main() -> None:
    ond = {}
    for name, url in SOURCES.items():
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        df = parse_psl(r.text)
        ond[name] = df[[10, 11, 12]].mean(axis=1)
    out = pd.DataFrame({"dmi_ond": ond["dmi"], "oni_ond": ond["oni"]}).loc[1998:2025]
    out = out.reset_index()
    if out["dmi_ond"].isna().any() or out["oni_ond"].isna().any():
        raise RuntimeError(f"missing OND values in 1998-2025:\n{out[out.isna().any(axis=1)]}")
    print(out.round(2).to_string(index=False))
    stratus.upload_parquet_to_blob(out, OUT, stage="dev")
    print(f"Uploaded {OUT}")


if __name__ == "__main__":
    main()
