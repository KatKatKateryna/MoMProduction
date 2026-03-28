"""
Incrementally update summary_dfo_filtered.csv with new DFO summary files.

1. Read existing timestamps from summary_dfo_filtered.csv.
2. Fetch the file listing from the server and identify new timestamps.
3. For each new file (in sorted order):
   a. Download content into memory (no temp file written to disk).
   b. Filter rows (keep non-zero flood rows; fall back to last row).
   c. Append filtered rows to summary_dfo_filtered.csv.
"""

import csv
import os
import re
import shutil
import io
import time

import requests

BASE_URL    = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
base_dir    = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(base_dir, "summary_dfo_filtered.csv")

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5
MIN_DISK_GB    = 0.5

FLOOD_COLS = [
    "1-Day_TotalArea_km2", "1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area",
]
ALL_COLS = ["timestamp", "pfaf_id"] + FLOOD_COLS


def free_disk_gb():
    return shutil.disk_usage(base_dir).free / (1024 ** 3)


def read_existing_timestamps():
    if not os.path.exists(OUTPUT_FILE):
        return set()
    with open(OUTPUT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return {row["timestamp"] for row in reader}


def list_server_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(DFO_\w+\.csv)"', resp.text))


def get_timestamp(filename):
    return filename[4:].replace(".csv", "")  # strips 'DFO_' and '.csv'


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
        {
            "timestamp":              timestamp,
            "pfaf_id":                row["pfaf_id"],
            "1-Day_TotalArea_km2":    row["1-Day_TotalArea_km2"],
            "1-Day_perc_Area":        row["1-Day_perc_Area"],
            "1-Day_CS_TotalArea_km2": row["1-Day_CS_TotalArea_km2"],
            "1-Day_CS_perc_Area":     row["1-Day_CS_perc_Area"],
            "2-Day_TotalArea_km2":    row["2-Day_TotalArea_km2"],
            "2-Day_perc_Area":        row["2-Day_perc_Area"],
            "3-Day_TotalArea_km2":    row["3-Day_TotalArea_km2"],
            "3-Day_perc_Area":        row["3-Day_perc_Area"],
        }
        for row in source_rows
    ]


def append_rows(rows):
    write_header = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_COLS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    print("Reading existing timestamps from output CSV...")
    existing = read_existing_timestamps()
    print(f"  {len(existing)} timestamps already in {OUTPUT_FILE}")

    print("Fetching file list from server...")
    all_files = list_server_filenames()
    new_files  = [f for f in all_files if get_timestamp(f) not in existing]
    print(f"  {len(all_files)} files on server, {len(new_files)} new to process\n")

    if not new_files:
        print("Nothing to do.")
        return

    total_rows = 0
    for i, filename in enumerate(new_files, 1):
        disk = free_disk_gb()
        if disk < MIN_DISK_GB:
            print(f"\nDisk space low ({disk:.2f} GB free). Stopping.")
            print(f"Resume from: {filename}")
            break

        timestamp = get_timestamp(filename)
        try:
            content = download_content(filename)
            rows    = extract_rows(content, timestamp)
            append_rows(rows)
            total_rows += len(rows)
            print(f"  [{i}/{len(new_files)}] {filename} -> {len(rows)} rows | disk: {disk:.2f} GB")
        except Exception as exc:
            print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

    print(f"\nDone. {total_rows:,} rows appended to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
