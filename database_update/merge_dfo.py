"""
Merge all DFO summary CSVs into a single CSV with a timestamp column.

- Reads each file in dfo_csv_files/ in sorted order.
- Filters out zero/null rows (all numeric columns are 0); keeps at least
  the last row per file as a placeholder if no non-zero rows exist.
- Adds a 'timestamp' column from the filename (part after 'DFO_', before '.csv').
- Checks disk space before each file; exits cleanly if < 0.5 GB free,
  printing the filename so the run can be resumed from there.
"""

import csv
import os
import shutil

base_dir     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(base_dir, "dfo_csv_files")
OUTPUT_FILE  = os.path.join(base_dir, "dfo_summary_filtered.csv")
MIN_DISK_GB  = 0.5

FLOOD_COLS = [
    "1-Day_TotalArea_km2", "1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area",
]
ALL_COLS = ["timestamp", "pfaf_id"] + FLOOD_COLS


def free_disk_gb():
    return shutil.disk_usage(base_dir).free / (1024 ** 3)


def is_nonzero_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def get_timestamp(filename):
    return filename[4:].replace(".csv", "")  # strips 'DFO_' prefix and '.csv'


def process_file(filename, writer):
    timestamp = get_timestamp(filename)
    filepath  = os.path.join(DOWNLOAD_DIR, filename)

    flood_rows = []
    last_row   = None

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last_row = row
            if is_nonzero_row(row):
                flood_rows.append(row)

    rows_to_write = flood_rows if flood_rows else ([last_row] if last_row else [])

    for row in rows_to_write:
        writer.writerow({
            "timestamp":             timestamp,
            "pfaf_id":               row["pfaf_id"],
            "1-Day_TotalArea_km2":   row["1-Day_TotalArea_km2"],
            "1-Day_perc_Area":       row["1-Day_perc_Area"],
            "1-Day_CS_TotalArea_km2": row["1-Day_CS_TotalArea_km2"],
            "1-Day_CS_perc_Area":    row["1-Day_CS_perc_Area"],
            "2-Day_TotalArea_km2":   row["2-Day_TotalArea_km2"],
            "2-Day_perc_Area":       row["2-Day_perc_Area"],
            "3-Day_TotalArea_km2":   row["3-Day_TotalArea_km2"],
            "3-Day_perc_Area":       row["3-Day_perc_Area"],
        })

    return len(rows_to_write)


def main():
    files = sorted(os.listdir(DOWNLOAD_DIR))
    total = len(files)
    print(f"Found {total} files in {DOWNLOAD_DIR}/")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Disk free: {free_disk_gb():.2f} GB\n")

    total_rows = 0
    files_done = 0

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=ALL_COLS)
        writer.writeheader()

        for filename in files:
            disk = free_disk_gb()
            if disk < MIN_DISK_GB:
                print(f"\nDisk space low ({disk:.2f} GB free). Stopping.")
                print(f"Resume from: {filename}")
                break

            rows_written = process_file(filename, writer)
            total_rows  += rows_written
            files_done  += 1

            if files_done % 100 == 0 or files_done == total:
                print(f"  {files_done}/{total} files | {total_rows:,} rows written | disk: {disk:.2f} GB")

    print(f"\nDone. {files_done}/{total} files processed, {total_rows:,} rows written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
