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
import os
import re
import time

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/HWRF/HWRF_summary/"

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5

DB_PARAMS = {
    "host":            os.getenv("DB_HOST"),
    "port":            int(os.getenv("DB_PORT", 5432)),
    "dbname":          os.getenv("DB_NAME", "postgres"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "sslmode":         os.getenv("DB_SSLMODE", "require"),
    "connect_timeout": 10,
}

UPSERT_SQL = """
INSERT INTO summary_hwrf_latest (
    "timestamp", pfaf_id,
    "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
)
VALUES %s
ON CONFLICT (pfaf_id) DO UPDATE SET
    "timestamp"        = EXCLUDED."timestamp",
    "Rain_TotalArea_km" = EXCLUDED."Rain_TotalArea_km",
    "perc_Area"        = EXCLUDED."perc_Area",
    "MeanRain"         = EXCLUDED."MeanRain",
    "MaxRain"          = EXCLUDED."MaxRain"
WHERE EXCLUDED."timestamp" >= summary_hwrf_latest."timestamp"
"""


def to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def get_last_timestamp(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT MAX("timestamp") FROM summary_hwrf_latest')
        return cur.fetchone()[0]


def list_server_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(hwrf\.\d+rainfall\.csv)"', resp.text))


def get_timestamp(filename):
    match = re.search(r'\.(\d+)rainfall', filename)
    return match.group(1) if match else ""


def download_content(filename):
    url = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return resp.content.decode("utf-8")
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {filename}: {exc}") from exc


def extract_rows(content, timestamp):
    reader = csv.DictReader(io.StringIO(content))
    return [
        (
            timestamp,
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
        print("Querying latest timestamp from DB...")
        last_ts = get_last_timestamp(conn)
        print(f"  Latest timestamp in summary_hwrf_latest: {last_ts or '(none)'}")

        print("Fetching file list from server...")
        all_files = list_server_filenames()
        new_files = [f for f in all_files if get_timestamp(f) > (last_ts or "")]
        print(f"  {len(all_files)} files on server, {len(new_files)} new to process\n")

        if not new_files:
            print("Nothing to do.")
            return

        total_rows = 0
        for i, filename in enumerate(new_files, 1):
            timestamp = get_timestamp(filename)
            try:
                content = download_content(filename)
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
