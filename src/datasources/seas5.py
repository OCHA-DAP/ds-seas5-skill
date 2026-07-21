import ocha_stratus as stratus
import pandas as pd


def load_seas5(pcode: str) -> pd.DataFrame:
    query = """
    SELECT *
    FROM public.seas5
    WHERE pcode = %s
    """
    engine = stratus.get_engine("prod")
    with engine.connect() as conn:
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
