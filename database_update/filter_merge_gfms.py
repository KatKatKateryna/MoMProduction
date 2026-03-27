"""
Filter and merge GFMS CSV files into a single CSV containing only flood rows.

- Reads each file in download_gfms_csv_files/ in sorted order.
- A row is considered "flood" if any numeric column is non-zero.
- If a file has no flood rows, its last row is written as a placeholder
  (so every file contributes at least one row).
- Adds a 'timestamp' column from the filename (part after last '_', before '.').
- Checks disk space before each file; exits cleanly if < 0.5 GB free,
  printing the filename so the run can be resumed from there.
"""

import csv
import os
import shutil

base_dir = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(base_dir, "download_gfms_csv_files")
OUTPUT_FILE = os.path.join(base_dir, "gfms_summary_filtered.csv")
MIN_DISK_GB  = 0.5

FLOOD_COLS = ["GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"]
ALL_COLS   = ["timestamp", "pfaf_id"] + FLOOD_COLS


def free_disk_gb():
    return shutil.disk_usage(os.path.abspath(".")).free / (1024 ** 3)


def is_flood_row(row):
    return any(float(row[col]) != 0.0 for col in FLOOD_COLS)


def process_file(filename, writer):
    timestamp = filename.rsplit("_", 1)[-1].split(".")[0]
    filepath  = os.path.join(DOWNLOAD_DIR, filename)

    flood_rows = []
    last_row   = None

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            last_row = row
            if is_flood_row(row):
                flood_rows.append(row)

    rows_to_write = flood_rows if flood_rows else ([last_row] if last_row else [])

    for row in rows_to_write:
        writer.writerow({
            "timestamp":         timestamp,
            "pfaf_id":           row["pfaf_id"],
            "GFMS_TotalArea_km": row["GFMS_TotalArea_km"],
            "GFMS_perc_Area":    row["GFMS_perc_Area"],
            "GFMS_MeanDepth":    row["GFMS_MeanDepth"],
            "GFMS_MaxDepth":     row["GFMS_MaxDepth"],
            "GFMS_Duration":     row["GFMS_Duration"],
        })

    return len(rows_to_write)


def main():
    files = sorted(os.listdir(DOWNLOAD_DIR))
    total = len(files)
    print(f"Found {total} files in {DOWNLOAD_DIR}/")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Disk free: {free_disk_gb():.2f} GB\n")

    total_rows   = 0
    files_done   = 0

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=ALL_COLS)
        writer.writeheader()

        for filename in files:
            # Disk space check before reading each file
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
