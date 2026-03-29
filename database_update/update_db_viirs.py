"""
Push latest VIIRS data to summary_viirs_latest in the database.

1. Query the latest timestamp already in summary_viirs_latest.
2. Fetch the file listing from the server and identify newer files.
3. For each new file (in sorted order):
   a. Download content into memory.
   b. Upsert all rows into summary_viirs_latest (newest timestamp wins per pfaf_id).
"""

import csv
import io
import re

import psycopg2
import psycopg2.extras

from db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    parse_timestamp_day, to_float, to_int,
)

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/VIIRS/VIIRS_summary/"

UPSERT_SQL = """
INSERT INTO summary_viirs_latest (
    "timestamp", pfaf_id,
    "onedayFlood_Area_km", "onedayperc_Area", "fivedayFlood_Area_km", "fivedayperc_Area"
)
VALUES %s
ON CONFLICT (pfaf_id) DO UPDATE SET
    "timestamp"            = EXCLUDED."timestamp",
    "onedayFlood_Area_km"  = EXCLUDED."onedayFlood_Area_km",
    "onedayperc_Area"      = EXCLUDED."onedayperc_Area",
    "fivedayFlood_Area_km" = EXCLUDED."fivedayFlood_Area_km",
    "fivedayperc_Area"     = EXCLUDED."fivedayperc_Area"
"""


def list_server_filenames():
    import requests
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(VIIRS_Flood_\d+\.csv)"', resp.text))


def get_timestamp(filename):
    return filename.replace("VIIRS_Flood_", "").replace(".csv", "")


def extract_rows(content, timestamp):
    reader = csv.DictReader(io.StringIO(content))
    return [
        (
            parse_timestamp_day(timestamp),
            to_int(row["pfaf_id"]),
            to_float(row["onedayFlood_Area_km"]),
            to_float(row["onedayperc_Area"]),
            to_float(row["fivedayFlood_Area_km"]),
            to_float(row["fivedayperc_Area"]),
        )
        for row in reader
    ]


def upsert_rows(conn, rows):
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT_SQL, rows, page_size=500)
    conn.commit()


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, "summary_viirs_latest")
        print(f"  {len(processed)} timestamps in summary_viirs_latest")

        print("Fetching file list from server...")
        all_files = list_server_filenames()
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
                rows    = extract_rows(content, timestamp)
                upsert_rows(conn, rows)
                total_rows += len(rows)
                print(f"  [{i}/{len(new_files)}] {filename} -> {len(rows)} rows upserted")
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

        print(f"\nDone. {total_rows:,} rows upserted into summary_viirs_latest")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
