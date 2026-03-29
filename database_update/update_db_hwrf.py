"""
Push latest HWRF data to summary_hwrf_latest in the database.

1. Query the latest timestamp already in summary_hwrf_latest.
2. Fetch the file listing from the server and identify newer files.
3. For each new file (in sorted order):
   a. Download content into memory.
   b. Upsert all rows into summary_hwrf_latest (newest timestamp wins per pfaf_id).
"""

import csv
import io
import re

import psycopg2
import psycopg2.extras

from db_utils import (
    DB_PARAMS, download_text, get_processed_timestamps,
    parse_timestamp_hh, to_float, to_int,
)

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/HWRF/HWRF_summary/"

UPSERT_SQL = """
INSERT INTO summary_hwrf_latest (
    "timestamp", pfaf_id,
    "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
)
VALUES %s
ON CONFLICT (pfaf_id) DO UPDATE SET
    "timestamp"         = EXCLUDED."timestamp",
    "Rain_TotalArea_km" = EXCLUDED."Rain_TotalArea_km",
    "perc_Area"         = EXCLUDED."perc_Area",
    "MeanRain"          = EXCLUDED."MeanRain",
    "MaxRain"           = EXCLUDED."MaxRain"
"""


def list_server_filenames():
    import requests
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(hwrf\.\d+rainfall\.csv)"', resp.text))


def get_timestamp(filename):
    match = re.search(r'\.(\d+)rainfall', filename)
    return match.group(1) if match else ""


def extract_rows(content, timestamp):
    reader = csv.DictReader(io.StringIO(content))
    return [
        (
            parse_timestamp_hh(timestamp),
            to_int(row["pfaf_id"]),
            to_float(row["Rain_TotalArea_km"]),
            to_float(row["perc_Area"]),
            to_float(row["MeanRain"]),
            to_float(row["MaxRain"]),
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
        processed = get_processed_timestamps(conn, "summary_hwrf_latest")
        print(f"  {len(processed)} timestamps in summary_hwrf_latest")

        print("Fetching file list from server...")
        all_files = list_server_filenames()
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
                rows    = extract_rows(content, timestamp)
                upsert_rows(conn, rows)
                total_rows += len(rows)
                print(f"  [{i}/{len(new_files)}] {filename} -> {len(rows)} rows upserted")
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

        print(f"\nDone. {total_rows:,} rows upserted into summary_hwrf_latest")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
