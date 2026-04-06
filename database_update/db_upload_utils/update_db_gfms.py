"""
Push latest GFMS data to stage_gfms in the database.

For each new file: download, build a DataFrame with all rows (including zeros),
and insert via upsert_dataframe which handles all type conversion against the
live DB schema.  Zero-row filtering for the history table is handled entirely
by the fn_gfms_sync DB trigger — no pre-filtering here.
"""

import csv
import io

import pandas as pd
import psycopg2

from .db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    list_server_files, parse_timestamp_hh, upsert_dataframe,
)

STAGE_TABLE   = "stage_gfms"
HISTORY_TABLE = "summary_gfms"
BASE_URL      = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GFMS/GFMS_summary/"


def get_timestamp(filename):
    return filename.rsplit("_", 1)[-1].split(".")[0]


def extract_df(content, timestamp):
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.insert(0, "timestamp", timestamp)
    return df


def _count_nonzero_gfms(df):
    """Mirror the zero-row filter in fn_gfms_sync: keep rows where any flood metric is non-zero."""
    import pandas as pd
    cols = ["GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"]
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
        all_files = list_server_files(BASE_URL, r'href="(Flood_byStor_\d+\.csv)"')
        new_files = [f for f in all_files
                     if parse_timestamp_hh(get_timestamp(f)) not in processed]
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
