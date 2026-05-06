import numpy as np
import pandas as pd


def detrend_column(
    df: pd.DataFrame,
    col: str,
    index_col: str = "valid_date",
    min_index=None,
    max_index=None,
) -> pd.DataFrame:
    """Remove a linear trend from a column, preserving the historical mean.

    Fits the trend on rows within [min_index, max_index] (defaults to full range),
    then applies it to all rows — including out-of-sample points.  The result is
    stored in a new column ``{col}_detrended``.

    Works with integer and datetime index columns.
    """
    df_sorted = df.sort_values(index_col).copy()
    df_model = df_sorted.copy()
    if min_index is not None:
        df_model = df_model[df_model[index_col] >= min_index]
    if max_index is not None:
        df_model = df_model[df_model[index_col] <= max_index]

    x = df_model[index_col].values.astype(float)
    y = df_model[col].values.astype(float)

    A = np.column_stack([x, np.ones(len(x))])
    a, b = np.linalg.lstsq(A, y, rcond=None)[0]

    x_all = df_sorted[index_col].values.astype(float)
    df_sorted[f"{col}_detrended"] = df_sorted[col].values.astype(float) - (a * x_all + b) + y.mean()
    return df_sorted
