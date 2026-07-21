import ocha_stratus as stratus
import pandas as pd


def load_era5(pcode: str) -> pd.DataFrame:
    query = """
    SELECT *
    FROM public.era5
    WHERE pcode = %s
    """
    engine = stratus.get_engine("prod")
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params=(pcode,),
            parse_dates=["valid_date"],
        )
    # See load_seas5: all-NULL mean -> object dtype -> ufunc crashes; drop NULL rows.
    df["mean"] = pd.to_numeric(df["mean"], errors="coerce")
    return df.dropna(subset=["mean"]).reset_index(drop=True)
