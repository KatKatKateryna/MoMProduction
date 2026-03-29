"""
Push latest GloFAS data to stage_glofas in the database.

For each new timestamp on the server (GeoJSON preferred over CSV):
  1. Download the file.
  2. Parse each feature's properties into a DataFrame.
  3. Insert via upsert_dataframe which handles all type conversion against
     the live DB schema.
"""

import csv
import io
import re

import pandas as pd
import psycopg2

from db_utils import (
    DB_PARAMS, download_resp, get_processed_timestamps,
    parse_timestamp_hh, upsert_dataframe,
)

STAGE_TABLE  = "stage_glofas"
LATEST_TABLE = "summary_glofas_latest"
BASE_URL     = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GLOFAS/"


def list_server_files():
    import requests
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    files = {}
    for filename, ts in re.findall(r'href="(threspoints_(\d+)\.csv)"', resp.text):
        files[ts] = filename
    for filename, ts in re.findall(r'href="(threspoints_(\d+)\.geojson)"', resp.text):
        files[ts] = filename  # GeoJSON overwrites CSV for the same timestamp
    return files


def parse_geojson(resp):
    return [f["properties"] for f in resp.json().get("features", [])]


def parse_csv(resp):
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))


def build_df(props_list, timestamp):
    if not props_list:
        return pd.DataFrame()
    df = pd.DataFrame(props_list)
    df.insert(0, "timestamp", timestamp)
    return df


def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying processed timestamps from DB...")
        processed = get_processed_timestamps(conn, LATEST_TABLE)
        print(f"  {len(processed)} timestamps in {LATEST_TABLE}")

        print("Fetching file list from server...")
        all_files     = list_server_files()
        new_files     = {ts: fn for ts, fn in sorted(all_files.items())
                         if parse_timestamp_hh(ts) not in processed}
        geojson_count = sum(1 for fn in all_files.values() if fn.endswith(".geojson"))
        csv_count     = sum(1 for fn in all_files.values() if fn.endswith(".csv"))
        print(f"  {len(all_files)} timestamps on server "
              f"({geojson_count} geojson, {csv_count} csv), "
              f"{len(processed)} already in DB, {len(new_files)} to process\n")

        if not new_files:
            print("Nothing to do.")
            return

        total_rows = 0
        for i, (timestamp, filename) in enumerate(new_files.items(), 1):
            fmt = "geojson" if filename.endswith(".geojson") else "csv"
            try:
                resp       = download_resp(BASE_URL + filename)
                props_list = parse_geojson(resp) if fmt == "geojson" else parse_csv(resp)
                df         = build_df(props_list, timestamp)
                if not df.empty:
                    upsert_dataframe(STAGE_TABLE, df, conn=conn)
                total_rows += len(df)
                print(f"  [{i}/{len(new_files)}] {filename} ({fmt}) -> {len(df)} rows inserted")
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

        print(f"\nDone. {total_rows:,} rows inserted into {STAGE_TABLE}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
