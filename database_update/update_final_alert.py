"""
Stream-merge Final_Alert CSVs one at a time.

For each file on the server:
  1. Skip if its timestamp is already present in the output CSV.
  2. Download the file.
  3. Keep all rows (no filtering).
  4. Resolve matching_id_watershed from lookup; add new entries to lookup if missing.
  5. Append rows to summary_final_alert_all_partial.csv.
  6. Delete the downloaded file immediately.

Run repeatedly to pick up new files without reprocessing old ones.
"""

import csv
import os
import re
import shutil
import time

import requests

BASE_URL       = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/Final_Alert/"
base_dir       = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR   = os.path.join(base_dir, "final_alert_csv_files")
OUTPUT_FILE    = os.path.join(base_dir, "summary_final_alert_all_partial.csv")
LOOKUP_FILE    = os.path.join(base_dir, "all_watersheds.csv")
MIN_DISK_GB    = 0.1
RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5

LOOKUP_KEY = ("pfaf_id", "name", "name_1", "CentroidX", "CentroidY")
LOOKUP_COLS = ["matching_id_watershed", "pfaf_id", "name", "name_1", "CentroidX", "CentroidY",
               "Admin1_count", "Admin1_names", "area_km2"]

ALL_COLS = [
    "timestamp", "matching_id_watershed", "pfaf_id",
    "rfr_score", "cfr_score",
    "Alert_level", "Days_until_peak", "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
    "Alert_Score", "PeakArrivalScore", "TwoYScore", "FiveYScore", "TwtyYScore",
    "Sum_Score_x", "GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth",
    "GFMS_MaxDepth", "GFMS_Duration", "GFMS_area_score", "GFMS_perc_area_score",
    "MeanD_Score", "MaxD_Score", "Duration_Score", "Sum_Score_y", "MOM_Score",
    "Hazard_Score", "Scaled_Riverine_Risk", "Scaled_Coastal_Risk", "Flag",
    "1-Day_TotalArea_km2", "1-Day_perc_Area", "1-Day_CS_TotalArea_km2",
    "1-Day_CS_perc_Area", "2-Day_TotalArea_km2", "2-Day_perc_Area",
    "3-Day_TotalArea_km2", "3-Day_perc_Area", "DFO_area_1day_score",
    "DFO_percarea_1day_score", "DFO_area_2day_score", "DFO_percarea_2day_score",
    "DFO_area_3day_score", "DFO_percarea_3day_score", "DFOTotal_Score",
    "onedayFlood_Area_km", "onedayperc_Area", "fivedayFlood_Area_km",
    "fivedayperc_Area", "VIIRS_area_1day_score", "VIIRS_percarea_1day_score",
    "VIIRS_area_5day_score", "VIIRS_percarea_5day_score", "VIIRSTotal_Score",
    "Severity", "Alert", "Status",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def free_disk_gb():
    return shutil.disk_usage(base_dir).free / (1024 ** 3)


def get_timestamp(filename):
    match = re.search(r'Final_Attributes_(\d{10})', filename)
    return match.group(1) if match else ""


def is_nonzero_row(row):
    val = row.get("Alert_Score", "").strip()
    try:
        return float(val) != 0.0
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Lookup management
# ---------------------------------------------------------------------------

def load_lookup():
    """Return (lookup_dict, max_id, full_rows_list).

    lookup_dict : {(pfaf_id, name, name_1, CentroidX, CentroidY) -> str(id)}
    max_id      : highest numeric id currently in the file
    full_rows   : list of dicts (all rows, in order) — used when rewriting
    """
    lookup = {}
    full_rows = []
    max_id = 0
    with open(LOOKUP_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = tuple(row[c] for c in LOOKUP_KEY)
            lookup[key] = row["matching_id_watershed"]
            full_rows.append(row)
            try:
                max_id = max(max_id, int(row["matching_id_watershed"]))
            except (ValueError, TypeError):
                pass
    return lookup, max_id, full_rows


def add_to_lookup(row, lookup, full_rows, max_id):
    """Add a new watershed row to the in-memory structures and append it to the CSV.

    Returns the new matching_id_watershed string.
    """
    new_id   = max_id + 1
    new_id_s = str(new_id)
    key      = tuple(row.get(c, "") for c in LOOKUP_KEY)

    new_entry = {
        "matching_id_watershed": new_id_s,
        "pfaf_id":      row.get("pfaf_id", ""),
        "name":         row.get("name", ""),
        "name_1":       row.get("name_1", ""),
        "CentroidX":    row.get("CentroidX", ""),
        "CentroidY":    row.get("CentroidY", ""),
        "Admin1_count": row.get("Admin1_count", ""),
        "Admin1_names": row.get("Admin1_names", ""),
        "area_km2":     row.get("area_km2", ""),
    }

    # Update in-memory state
    lookup[key]  = new_id_s
    full_rows.append(new_entry)

    # Append a single line to the lookup CSV (fast — no full rewrite)
    with open(LOOKUP_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOOKUP_COLS)
        writer.writerow(new_entry)

    print(f"  [lookup] Added new watershed id={new_id_s}: "
          f"pfaf_id={new_entry['pfaf_id']}, name={new_entry['name']}, "
          f"name_1={new_entry['name_1']}")

    return new_id_s, new_id  # return new max_id too


# ---------------------------------------------------------------------------
# Output CSV helpers
# ---------------------------------------------------------------------------

def load_last_timestamp():
    """Return the last timestamp in the output CSV by reading the final line only."""
    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
        return None
    with open(OUTPUT_FILE, "rb") as f:
        # Read last 4 KB — enough to contain the last line
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4096))
        tail = f.read().decode("utf-8", errors="ignore")
    lines = [l.strip() for l in tail.splitlines() if l.strip()]
    if not lines:
        return None
    last_line = lines[-1]
    ts = last_line.split(",")[0].strip()
    if ts == "timestamp":
        return None
    return ts if ts else None


def open_output_csv():
    """Open output CSV for appending. Write header only if the file is new/empty."""
    write_header = not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0
    f = open(OUTPUT_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=ALL_COLS)
    if write_header:
        writer.writeheader()
    return f, writer


# ---------------------------------------------------------------------------
# Server interaction
# ---------------------------------------------------------------------------

def list_server_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(Final_Attributes_[^"]+\.csv)"', resp.text))


def download_file(filename):
    """Download filename into DOWNLOAD_DIR. Returns local path or raises."""
    dest = os.path.join(DOWNLOAD_DIR, filename)
    url  = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            with open(dest, "wb") as f:
                f.write(resp.content)
            return dest
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {filename}: {exc}") from exc


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_file(local_path, filename, writer, lookup, full_rows, max_id):
    """Filter rows, resolve/add watershed IDs, write to output.

    Returns (rows_written, updated_max_id).
    """
    timestamp     = get_timestamp(filename)
    rows_to_write = []

    with open(local_path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_to_write.append(row)

    for row in rows_to_write:
        key          = tuple(row.get(c, "") for c in LOOKUP_KEY)
        matching_id_watershed = lookup.get(key)

        if matching_id_watershed is None:
            matching_id_watershed, max_id = add_to_lookup(row, lookup, full_rows, max_id)

        out_row = {"timestamp": timestamp, "matching_id_watershed": matching_id_watershed}
        for col in ALL_COLS[2:]:  # skip timestamp and matching_id_watershed
            out_row[col] = row.get(col, "")
        writer.writerow(out_row)

    os.remove(local_path)
    return len(rows_to_write), max_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print("Reading last timestamp from output CSV...")
    last_ts = load_last_timestamp()
    if last_ts:
        print(f"  Last timestamp in file: {last_ts} — skipping all files up to and including this timestamp")
    else:
        print("  Output file is empty or missing — processing all files")

    print("Fetching file list from server...")
    all_files  = list_server_filenames()
    if last_ts:
        to_process = [f for f in all_files if get_timestamp(f) > last_ts]
    else:
        to_process = all_files
    skipped = len(all_files) - len(to_process)
    print(f"  {len(all_files)} files on server | {skipped} skipped | {len(to_process)} to process\n")

    if not to_process:
        print("Nothing to do.")
        return

    print("Loading watershed lookup...")
    lookup, max_id, full_rows = load_lookup()
    print(f"  {len(lookup):,} entries loaded (max id={max_id})\n")

    out_f, writer = open_output_csv()

    total_rows    = 0
    files_done    = 0
    failed        = []
    new_watersheds = 0

    try:
        for i, filename in enumerate(to_process, 1):
            disk = free_disk_gb()
            if disk < MIN_DISK_GB:
                print(f"\nDisk space low ({disk:.2f} GB free) — stopping.")
                print(f"Resume from: {filename}")
                break

            try:
                local_path = download_file(filename)
            except RuntimeError as exc:
                print(f"  FAILED (download): {exc}")
                failed.append(filename)
                continue

            prev_max_id = max_id
            try:
                rows_written, max_id = process_file(
                    local_path, filename, writer, lookup, full_rows, max_id
                )
            except Exception as exc:
                print(f"  FAILED (process): {filename} — {exc}")
                # Remove the file if still present
                if os.path.exists(local_path):
                    os.remove(local_path)
                failed.append(filename)
                continue

            if max_id > prev_max_id:
                new_watersheds += max_id - prev_max_id

            total_rows += rows_written
            files_done += 1

            if files_done % 100 == 0 or files_done == len(to_process):
                print(f"  {files_done}/{len(to_process)} done | "
                      f"{total_rows:,} rows written | "
                      f"new watersheds: {new_watersheds} | "
                      f"disk: {free_disk_gb():.2f} GB")

    finally:
        out_f.flush()
        out_f.close()

    print(f"\nDone. {files_done}/{len(to_process)} files processed, "
          f"{total_rows:,} rows appended to {OUTPUT_FILE}")
    if new_watersheds:
        print(f"  {new_watersheds} new watershed(s) added to {LOOKUP_FILE}")
    if failed:
        print(f"\nFailed files ({len(failed)}):")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
