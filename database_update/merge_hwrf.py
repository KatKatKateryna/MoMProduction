"""
Merge all HWRF summary CSVs into a single CSV with a timestamp column.

- Reads each file in hwrf_csv_files/ in sorted order.
- All rows are written (no filtering).
- Adds a 'timestamp' column from the filename (numeric part between '.' and 'rainfall').
- Checks disk space before each file; exits cleanly if < 0.5 GB free,
  printing the filename so the run can be resumed from there.
"""

import csv
import os
import re
import shutil

base_dir     = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(base_dir, "hwrf_csv_files")
OUTPUT_FILE  = os.path.join(base_dir, "hwrf_summary_all.csv")
MIN_DISK_GB  = 0.5

ALL_COLS = ["timestamp", "pfaf_id", "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"]


def free_disk_gb():
    return shutil.disk_usage(os.path.abspath(".")).free / (1024 ** 3)


def get_timestamp(filename):
    match = re.search(r'\.(\d+)rainfall', filename)
    return match.group(1) if match else ""


def process_file(filename, writer):
    timestamp = get_timestamp(filename)
    filepath  = os.path.join(DOWNLOAD_DIR, filename)

    rows_written = 0
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            writer.writerow({
                "timestamp":        timestamp,
                "pfaf_id":          row["pfaf_id"],
                "Rain_TotalArea_km": row["Rain_TotalArea_km"],
                "perc_Area":        row["perc_Area"],
                "MeanRain":         row["MeanRain"],
                "MaxRain":          row["MaxRain"],
            })
            rows_written += 1

    return rows_written


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

            if files_done % 500 == 0 or files_done == total:
                print(f"  {files_done}/{total} files | {total_rows:,} rows written | disk: {disk:.2f} GB")

    print(f"\nDone. {files_done}/{total} files processed, {total_rows:,} rows written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
