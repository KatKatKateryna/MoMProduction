"""
Push latest DFO data to stage_dfo in the database.

For each new file: download, keep flood rows (fall back to last row if none),
build a DataFrame with raw values, and insert via upsert_dataframe which
handles all type conversion against the live DB schema.
"""

import csv
import io

import pandas as pd
import psycopg2

from db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    list_server_files, parse_timestamp_day, upsert_dataframe,
)

STAGE_TABLE  = "stage_dfo"
LATEST_TABLE = "summary_dfo_latest"
BASE_URL     = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
FLOOD_COLS   = [
    "1-Day_TotalArea_km2", "1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area",
]


def get_timestamp(filename):
    return filename[4:].replace(".csv", "")


def is_nonzero_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def extract_df(content, timestamp):
    flood_rows, last_row = [], None
    for row in csv.DictReader(io.StringIO(content)):
        last_row = row
        if is_nonzero_row(row):
            flood_rows.append(row)
    source_rows = flood_rows if flood_rows else ([last_row] if last_row else [])
    if not source_rows:
        return pd.DataFrame()
    df = pd.DataFrame(source_rows)
    df.insert(0, "timestamp", timestamp)
    return df


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, LATEST_TABLE)
        print(f"  {len(processed)} timestamps in {LATEST_TABLE}")

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
