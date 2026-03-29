"""
Push latest GFMS data to summary_gfms_latest in the database.

1. Query the latest timestamp already in summary_gfms_latest.
2. Fetch the file listing from the server and identify newer files.
3. For each new file (in sorted order):
   a. Download content into memory.
   b. Keep only flood rows (any numeric column non-zero); fall back to last row if none.
   c. Upsert rows into summary_gfms_latest (newest timestamp wins per pfaf_id).
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

BASE_URL   = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GFMS/GFMS_summary/"
FLOOD_COLS = ["GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"]

UPSERT_SQL = """
INSERT INTO summary_gfms_latest (
    "timestamp", pfaf_id,
    "GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
)
VALUES %s
ON CONFLICT (pfaf_id) DO UPDATE SET
    "timestamp"         = EXCLUDED."timestamp",
    "GFMS_TotalArea_km" = EXCLUDED."GFMS_TotalArea_km",
    "GFMS_perc_Area"    = EXCLUDED."GFMS_perc_Area",
    "GFMS_MeanDepth"    = EXCLUDED."GFMS_MeanDepth",
    "GFMS_MaxDepth"     = EXCLUDED."GFMS_MaxDepth",
    "GFMS_Duration"     = EXCLUDED."GFMS_Duration"
"""


def list_server_filenames():
    import requests
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(Flood_byStor_\d+\.csv)"', resp.text))


def get_timestamp(filename):
    return filename.rsplit("_", 1)[-1].split(".")[0]


def is_flood_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def extract_rows(content, timestamp):
    flood_rows = []
    last_row   = None
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        last_row = row
        if is_flood_row(row):
            flood_rows.append(row)
    source_rows = flood_rows if flood_rows else ([last_row] if last_row else [])
    return [
        (
            parse_timestamp_hh(timestamp),
            to_int(row["pfaf_id"]),
            to_float(row["GFMS_TotalArea_km"]),
            to_float(row["GFMS_perc_Area"]),
            to_float(row["GFMS_MeanDepth"]),
            to_float(row["GFMS_MaxDepth"]),
            to_int(row["GFMS_Duration"]),
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
        processed = get_processed_timestamps(conn, "summary_gfms_latest")
        print(f"  {len(processed)} timestamps in summary_gfms_latest")

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

        print(f"\nDone. {total_rows:,} rows upserted into summary_gfms_latest")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
