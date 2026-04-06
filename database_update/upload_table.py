"""
upload_table.py
===============
Simple interface for uploading a custom DataFrame to a staging table.

The staging trigger handles all the pipeline logic:
  - clears the staging table before insert
  - flushes data to the corresponding _latest and history tables
  - skips if the timestamp is already fully present in history

Usage:
    from upload_table import upload_table

    upload_table("gfms", df)
    upload_table("stage_glofas", df, expected_rows=2500)
    upload_table("final_alert", df, failed_key="20240101_final_alert")

Short names and full stage table names are both accepted.
"""

import pandas as pd

from db_upload_utils.db_utils import upsert_dataframe
from db_upload_utils.update_db_gfms import _count_nonzero_gfms
from db_upload_utils.update_db_dfo  import _count_nonzero_dfo

# Map short names to (stage_table, count_fn).
# count_fn mirrors the zero-row filter applied by the history sync trigger
# so the expected row count matches what actually lands in history.
STAGE_TABLES = {
    "gfms":         ("stage_gfms",         _count_nonzero_gfms),
    "hwrf":         ("stage_hwrf",          len),
    "viirs":        ("stage_viirs",         len),
    "dfo":          ("stage_dfo",           _count_nonzero_dfo),
    "glofas":       ("stage_glofas",        len),
    "final_alert":  ("stage_final_alert",   len),
    "mom_gfms":     ("stage_mom_gfms",      len),
    "mom_hwrf":     ("stage_mom_hwrf",      len),
    "mom_dfo":      ("stage_mom_dfo",       len),
    "mom_viirs":    ("stage_mom_viirs",     len),
}


def upload_table(table: str, df: pd.DataFrame) -> None:
    """Upload a DataFrame to a staging table.

    Parameters
    ----------
    table:
        Stage table name ("stage_gfms") or short name ("gfms").
    df:
        DataFrame whose columns match the target stage table.
        Extra columns are silently ignored; missing columns are left NULL.
    """
    if table in STAGE_TABLES:
        stage_table, count_fn = STAGE_TABLES[table]
    else:
        stage_table, count_fn = table, len

    ts = df["timestamp"].iloc[0]
    ts_str = ts.strftime("%Y%m%d%H") if hasattr(ts, "strftime") else str(ts)
    failed_key = f"{ts_str}_{stage_table}"
    expected_rows = count_fn(df)

    upsert_dataframe(stage_table, df, expected_rows=expected_rows, failed_key=failed_key)
