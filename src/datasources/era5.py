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
        return pd.read_sql(
            query,
            conn,
            params=(pcode,),
            parse_dates=["valid_date"],
        )
