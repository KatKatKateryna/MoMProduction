"""
Incrementally update glofas_stations.csv and glofas_merged.csv from GloFAS GeoJSON files.

For each new GeoJSON file:
  1. Parse each feature's properties.
  2. Match the static properties against glofas_stations.csv (exact match on all static cols).
     - Match found  → reuse existing id.
     - No match     → append new row with next auto-increment id and save stations file.
  3. Write dynamic properties + station_id + pfaf_id to glofas_merged.csv.
"""

import csv
import os
import re
import shutil
import time

import requests

BASE_URL      = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GLOFAS/"
base_dir      = os.path.dirname(os.path.abspath(__file__))
STATIONS_FILE = os.path.join(base_dir, "glofas_stations.csv")
MERGED_FILE   = os.path.join(base_dir, "glofas_merged.csv")

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5
MIN_DISK_GB    = 0.5

# Static columns — stored in glofas_stations.csv, matched for deduplication.
# All 18 values must match exactly before reusing an existing id.
STATIC_COLS = [
    "ID", "Point No", "Station", "Basin", "Country", "Country_code",
    "Continent", "ISO", "Admin0", "Admin1", "Location",
    "Lat", "Lon", "Upstream area", "area_km2", "pfaf_id",
    "rfr_score", "cfr_score",
]
STATIONS_COLS = ["station_id"] + STATIC_COLS

# Dynamic columns — change per forecast file, stored in glofas_merged.csv.
# pfaf_id is repeated here as a convenience join key.
DYNAMIC_COLS = [
    "Alert_level", "Days_until_peak",
    "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
    "max_EPS", "Forecast Date",
]
MERGED_COLS = ["timestamp", "station_id", "pfaf_id"] + DYNAMIC_COLS


def free_disk_gb():
    return shutil.disk_usage(base_dir).free / (1024 ** 3)


def load_stations():
    """Load glofas_stations.csv. Returns (rows list, lookup dict keyed by static tuple, next_id)."""
    if not os.path.exists(STATIONS_FILE):
        return [], {}, 1
    with open(STATIONS_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    lookup = {
        tuple(row[c] for c in STATIC_COLS): int(row["station_id"])
        for row in rows
    }
    next_id = max(int(r["station_id"]) for r in rows) + 1 if rows else 1
    return rows, lookup, next_id


def save_stations(rows):
    with open(STATIONS_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=STATIONS_COLS)
        writer.writeheader()
        writer.writerows(rows)


def read_existing_timestamps():
    if not os.path.exists(MERGED_FILE):
        return set()
    with open(MERGED_FILE, newline="", encoding="utf-8") as f:
        return {row["timestamp"] for row in csv.DictReader(f)}


def list_server_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(threspoints_\d+\.geojson)"', resp.text))


def get_timestamp(filename):
    return re.search(r"threspoints_(\d+)", filename).group(1)


def download_geojson(filename):
    url = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {filename}: {exc}") from exc


def prop(props, key):
    """Return a property as a string; empty string if missing or None."""
    v = props.get(key)
    return "" if v is None else str(v)


def process_geojson(data, timestamp, stations_rows, stations_lookup, next_id):
    """
    Process all features in a FeatureCollection.
    Mutates stations_rows and stations_lookup when new stations are discovered.
    Returns (merged_rows, new_stations_count, updated_next_id).
    """
    merged_rows  = []
    new_stations = 0

    for feature in data.get("features", []):
        props = feature.get("properties", {})

        static_key = tuple(prop(props, c) for c in STATIC_COLS)
        station_id = stations_lookup.get(static_key)

        if station_id is None:
            station_id = next_id
            next_id   += 1
            new_row    = {"station_id": station_id}
            for c in STATIC_COLS:
                new_row[c] = prop(props, c)
            stations_rows.append(new_row)
            stations_lookup[static_key] = station_id
            new_stations += 1

        merged_row = {
            "timestamp":  timestamp,
            "station_id": station_id,
            "pfaf_id":    prop(props, "pfaf_id"),
        }
        for c in DYNAMIC_COLS:
            merged_row[c] = prop(props, c)
        merged_rows.append(merged_row)

    return merged_rows, new_stations, next_id


def append_merged_rows(rows):
    write_header = not os.path.exists(MERGED_FILE)
    with open(MERGED_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MERGED_COLS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    print("Loading stations lookup...")
    stations_rows, stations_lookup, next_id = load_stations()
    print(f"  {len(stations_rows)} stations loaded")

    print("Reading existing timestamps from merged CSV...")
    existing  = read_existing_timestamps()
    print(f"  {len(existing)} timestamps already processed")

    print("Fetching file list from server...")
    all_files = list_server_filenames()
    new_files = [f for f in all_files if get_timestamp(f) not in existing]
    print(f"  {len(all_files)} files on server, {len(new_files)} new to process\n")

    if not new_files:
        print("Nothing to do.")
        return

    total_rows         = 0
    total_new_stations = 0

    for i, filename in enumerate(new_files, 1):
        disk = free_disk_gb()
        if disk < MIN_DISK_GB:
            print(f"\nDisk space low ({disk:.2f} GB free). Stopping.")
            print(f"Resume from: {filename}")
            break

        timestamp = get_timestamp(filename)
        try:
            data = download_geojson(filename)
            merged_rows, new_stations, next_id = process_geojson(
                data, timestamp, stations_rows, stations_lookup, next_id
            )
            append_merged_rows(merged_rows)

            # Save stations immediately whenever new ones are discovered so that
            # glofas_stations.csv stays consistent with the merged file even if
            # the run is interrupted mid-way.
            if new_stations:
                save_stations(stations_rows)

            total_rows         += len(merged_rows)
            total_new_stations += new_stations
            print(
                f"  [{i}/{len(new_files)}] {filename} -> "
                f"{len(merged_rows)} rows, {new_stations} new stations | disk: {disk:.2f} GB"
            )
        except Exception as exc:
            print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

    print(f"\nDone. {total_rows:,} rows appended to {MERGED_FILE}")
    print(f"      {total_new_stations} new stations added to {STATIONS_FILE}")


if __name__ == "__main__":
    main()
