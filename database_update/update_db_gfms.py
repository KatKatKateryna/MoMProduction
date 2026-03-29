"""
Push latest GFMS data to stage_gfms in the database.

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
    list_server_files, parse_timestamp_hh, upsert_dataframe,
)

STAGE_TABLE  = "stage_gfms"
LATEST_TABLE = "summary_gfms_latest"
BASE_URL     = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GFMS/GFMS_summary/"
FLOOD_COLS   = ["GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"]


def get_timestamp(filename):
    return filename.rsplit("_", 1)[-1].split(".")[0]


def is_flood_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def extract_df(content, timestamp):
    flood_rows, last_row = [], None
    for row in csv.DictReader(io.StringIO(content)):
        last_row = row
        if is_flood_row(row):
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
