"""
Push latest Final Alert data to stage_final_alert in the database.

For each new file: download, build a DataFrame with raw values, and insert
via upsert_dataframe which handles all type conversion against the live DB schema.
"""

import csv
import io
import re

import pandas as pd
import psycopg2

from db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    list_server_files, parse_timestamp_hh, upsert_dataframe,
)

STAGE_TABLE  = "stage_final_alert"
LATEST_TABLE = "summary_final_alert_latest"
BASE_URL     = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/Final_Alert/"


def get_timestamp(filename):
    match = re.search(r'Final_Attributes_(\d{10})', filename)
    return match.group(1) if match else ""


def extract_df(content, timestamp):
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.insert(0, "timestamp", timestamp)
    return df


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, LATEST_TABLE)
        print(f"  {len(processed)} timestamps in {LATEST_TABLE}")

        print("Fetching file list from server...")
        all_files  = list_server_files(BASE_URL, r'href="(Final_Attributes_[^"]+\.csv)"')
        to_process = [f for f in all_files
                      if parse_timestamp_hh(get_timestamp(f)) not in processed]
        print(f"  {len(all_files)} files on server | "
              f"{len(processed)} already in DB | "
              f"{len(to_process)} to process\n")

        if not to_process:
            print("Nothing to do.")
            return

        total_rows = 0
        failed     = []

        for i, filename in enumerate(to_process, 1):
            timestamp = get_timestamp(filename)
            try:
                content = download_text(BASE_URL + filename, errors="ignore")
                df      = extract_df(content, timestamp)
                if not df.empty:
                    upsert_dataframe(STAGE_TABLE, df, conn=conn)
                total_rows += len(df)
                print(f"  [{i}/{len(to_process)}] {filename} -> {len(df)} rows inserted")
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(to_process)}] FAILED {filename}: {exc}")
                failed.append(filename)

        print(f"\nDone. {len(to_process) - len(failed)}/{len(to_process)} files processed, "
              f"{total_rows:,} rows inserted into {STAGE_TABLE}")
        if failed:
            print(f"\nFailed files ({len(failed)}):")
            for f in failed:
                print(f"  {f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
