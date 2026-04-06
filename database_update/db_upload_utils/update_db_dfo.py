"""
Push latest DFO data to stage_dfo in the database.

For each new file: download, build a DataFrame with all rows (including zeros),
and insert via upsert_dataframe which handles all type conversion against the
live DB schema.  Zero-row filtering for the history table is handled entirely
by the fn_dfo_sync DB trigger — no pre-filtering here.
"""

import csv
import io

import pandas as pd
import psycopg2

from .db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    list_server_files, parse_timestamp_day, upsert_dataframe,
)

STAGE_TABLE   = "stage_dfo"
HISTORY_TABLE = "summary_dfo"
BASE_URL      = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"


def get_timestamp(filename):
    return filename[4:].replace(".csv", "")


def extract_df(content, timestamp):
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.insert(0, "timestamp", timestamp)
    return df


def _count_nonzero_dfo(df):
    """Mirror the zero-row filter in fn_dfo_sync: keep rows where any flood area is non-zero."""
    import pandas as pd
    cols = [
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area",
    ]
    present = [c for c in cols if c in df.columns]
    if not present:
        return len(df)
    numeric = df[present].apply(pd.to_numeric, errors="coerce").fillna(0)
    nonzero_count = int(numeric.ne(0).any(axis=1).sum())
    return nonzero_count if nonzero_count > 0 else 1  # fallback: trigger writes 1 row


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, HISTORY_TABLE)
        print(f"  {len(processed)} timestamps in {HISTORY_TABLE}")

        print("Fetching file list from server...")
        all_files = list_server_files(BASE_URL, r'href="(DFO_\w+\.csv)"')
        new_files = [f for f in all_files
                     if parse_timestamp_day(get_timestamp(f)) not in processed]
        print(f"  {len(all_files)} files on server, {len(processed)} already in DB, "
              f"{len(new_files)} to process\n")

        if not new_files:
            print("Nothing to do.")
            return

        total_rows = 0
        for i, filename in enumerate(new_files, 1):
            timestamp = get_timestamp(filename)
            try:
                content = download_text(BASE_URL + filename)
                df      = extract_df(content, timestamp)
                if not df.empty:
                    upsert_dataframe(STAGE_TABLE, df, conn=conn)
                total_rows += len(df)
                print(f"  [{i}/{len(new_files)}] {filename} -> {len(df)} rows inserted")
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

        print(f"\nDone. {total_rows:,} rows inserted into {STAGE_TABLE}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
