from functools import lru_cache

import ocha_stratus as stratus
import pandas as pd


@lru_cache(maxsize=1)
def _engine():
    # stratus.get_engine builds a NEW pooled engine per call and never disposes it —
    # under parallel per-pcode loads that leaks connections until the server's slots
    # are exhausted. One cached engine per process keeps it to ~1 connection each.
    return stratus.get_engine("prod")


def load_seas5(pcode: str) -> pd.DataFrame:
    query = """
    SELECT *
    FROM public.seas5
    WHERE pcode = %s
    """
    with _engine().connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params=(pcode,),
            parse_dates=["valid_date", "issued_date"],
        )
    # An all-NULL mean column (tiny polygons that miss every raster cell, e.g. CV02)
    # comes back as object dtype and breaks numpy ufuncs downstream; NULL rows carry
    # no information for any consumer, so coerce and drop them.
    df["mean"] = pd.to_numeric(df["mean"], errors="coerce")
    return df.dropna(subset=["mean"]).reset_index(drop=True)
