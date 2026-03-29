"""
Push latest DFO data to summary_dfo_latest in the database.

1. Query the latest timestamp already in summary_dfo_latest.
2. Fetch the file listing from the server and identify newer files.
3. For each new file (in sorted order):
   a. Download content into memory.
   b. Filter rows (keep non-zero flood rows; fall back to last row).
   c. Upsert rows into summary_dfo_latest (newest timestamp wins per pfaf_id).
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

BASE_URL   = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
FLOOD_COLS = [
    "1-Day_TotalArea_km2", "1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area",
]

UPSERT_SQL = """
INSERT INTO summary_dfo_latest (
    "timestamp", pfaf_id,
    "1-Day_TotalArea_km2", "1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area"
)
VALUES %s
ON CONFLICT (pfaf_id) DO UPDATE SET
    "timestamp"              = EXCLUDED."timestamp",
    "1-Day_TotalArea_km2"    = EXCLUDED."1-Day_TotalArea_km2",
    "1-Day_perc_Area"        = EXCLUDED."1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2" = EXCLUDED."1-Day_CS_TotalArea_km2",
    "1-Day_CS_perc_Area"     = EXCLUDED."1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2"    = EXCLUDED."2-Day_TotalArea_km2",
    "2-Day_perc_Area"        = EXCLUDED."2-Day_perc_Area",
    "3-Day_TotalArea_km2"    = EXCLUDED."3-Day_TotalArea_km2",
    "3-Day_perc_Area"        = EXCLUDED."3-Day_perc_Area"
"""


def list_server_filenames():
    import requests
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(DFO_\w+\.csv)"', resp.text))


def get_timestamp(filename):
    return filename[4:].replace(".csv", "")  # strips 'DFO_' and '.csv'


def is_nonzero_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def extract_rows(content, timestamp):
    flood_rows = []
    last_row   = None
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        last_row = row
        if is_nonzero_row(row):
            flood_rows.append(row)
    source_rows = flood_rows if flood_rows else ([last_row] if last_row else [])
    return [
        (
            parse_timestamp_day(timestamp),
            to_int(row["pfaf_id"]),
            to_float(row["1-Day_TotalArea_km2"]),
            to_float(row["1-Day_perc_Area"]),
            to_float(row["1-Day_CS_TotalArea_km2"]),
            to_float(row["1-Day_CS_perc_Area"]),
            to_float(row["2-Day_TotalArea_km2"]),
            to_float(row["2-Day_perc_Area"]),
            to_float(row["3-Day_TotalArea_km2"]),
            to_float(row["3-Day_perc_Area"]),
        )
        for row in source_rows
    ]


def upsert_rows(conn, rows):
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT_SQL, rows, page_size=500)
    conn.commit()


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, "summary_dfo_latest")
        print(f"  {len(processed)} timestamps in summary_dfo_latest")

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

        print(f"\nDone. {total_rows:,} rows upserted into summary_dfo_latest")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
