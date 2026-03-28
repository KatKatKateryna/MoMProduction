"""
Incrementally update glofas_stations.csv and glofas_merged.csv from GloFAS files.

Each timestamp on the server has either a .geojson or a .csv file; GeoJSON is preferred
when both exist. For each new timestamp:
  1. Parse each row/feature's properties.
  2. Match against glofas_stations.csv:
     - GeoJSON: exact match on all STATIC_COLS.
     - CSV:     match on CSV_STATIC_COLS (subset available in CSV); Lat/Lon/Upstream area
                are float-normalised so "72.250" matches the stored "72.25".
     In both cases: match found → reuse station_id; no match → new row.
     CSV-sourced new rows leave GeoJSON-only columns as empty strings.
  3. Write station_id, pfaf_id, ID, Point No, and dynamic columns to glofas_merged.csv.
     ID and Point No are written to the merged table only, not used for matching.
"""

import csv
import io
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

# All columns stored in glofas_stations.csv.
# ID and Point No are excluded from matching and written to the merged table instead.
# CSV-sourced rows leave the GeoJSON-only columns as empty strings.
STATIC_COLS = [
    "Station", "Basin", "Country", "Country_code",
    "Continent", "ISO", "Admin0", "Admin1", "Location",
    "Lat", "Lon", "Upstream area", "area_km2", "pfaf_id",
    "rfr_score", "cfr_score",
]
STATIONS_COLS = ["station_id"] + STATIC_COLS

# Subset of STATIC_COLS present in CSV files (used for CSV-only matching).
CSV_STATIC_COLS = [
    "Station", "Basin", "Country",
    "Lat", "Lon", "Upstream area", "pfaf_id",
]
# These CSV cols are numeric and need float-normalisation to match GeoJSON-sourced values.
CSV_NUMERIC_COLS = {"Lat", "Lon", "Upstream area"}

# Columns only available in GeoJSON — filled in when a CSV-sourced station is later matched.
GEOJSON_ONLY_COLS = [c for c in STATIC_COLS if c not in CSV_STATIC_COLS]

# Dynamic columns written to glofas_merged.csv (change per forecast file).
# pfaf_id is repeated as a convenience join key.
# ID and Point No are written here instead of the stations table.
DYNAMIC_COLS = [
    "Alert_level", "Days_until_peak",
    "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
    "max_EPS", "Forecast Date",
]
MERGED_COLS = ["timestamp", "station_id", "pfaf_id", "ID", "Point No"] + DYNAMIC_COLS


def free_disk_gb():
    return shutil.disk_usage(base_dir).free / (1024 ** 3)


def prop(props, key):
    """Return a property as a string; empty string if missing or None."""
    v = props.get(key)
    return "" if v is None else str(v)


def norm_float(v):
    """Normalise a numeric string to its float representation, e.g. '72.250' → '72.25'."""
    try:
        return str(float(v))
    except (TypeError, ValueError):
        return str(v) if v is not None else ""


def geojson_key(props):
    """Match key for GeoJSON rows: exact string match on all STATIC_COLS."""
    return tuple(prop(props, c) for c in STATIC_COLS)


def csv_key(props):
    """Match key for CSV rows: CSV_STATIC_COLS, numeric ones float-normalised."""
    return tuple(
        norm_float(prop(props, c)) if c in CSV_NUMERIC_COLS else prop(props, c)
        for c in CSV_STATIC_COLS
    )


def load_stations():
    """
    Load glofas_stations.csv.
    Returns (rows list, rows_by_id, geojson_lookup, csv_lookup, next_id).
    rows_by_id:     {station_id: row dict} for fast in-place updates
    geojson_lookup: keyed by geojson_key (STATIC_COLS exact match)
    csv_lookup:     keyed by csv_key     (CSV_STATIC_COLS float-normalised match)
    """
    if not os.path.exists(STATIONS_FILE):
        return [], {}, {}, {}, 1
    with open(STATIONS_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows_by_id     = {int(r["station_id"]): r for r in rows}
    geojson_lookup = {geojson_key(row): int(row["station_id"]) for row in rows}
    csv_lookup     = {csv_key(row):     int(row["station_id"]) for row in rows}
    next_id        = max(rows_by_id) + 1 if rows_by_id else 1
    return rows, rows_by_id, geojson_lookup, csv_lookup, next_id


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


def list_server_files():
    """Return dict {timestamp: filename} preferring .geojson over .csv."""
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    files = {}
    for filename, ts in re.findall(r'href="(threspoints_(\d+)\.csv)"', resp.text):
        files[ts] = filename
    for filename, ts in re.findall(r'href="(threspoints_(\d+)\.geojson)"', resp.text):
        files[ts] = filename  # GeoJSON overwrites CSV for the same timestamp
    return files


def download(filename):
    url = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {filename}: {exc}") from exc


def parse_geojson(resp):
    return [f["properties"] for f in resp.json().get("features", [])]


def parse_csv(resp):
    return list(csv.DictReader(io.StringIO(resp.content.decode("utf-8"))))


def process_rows(props_list, fmt, timestamp,
                 stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id):
    """
    Match or create stations, build merged rows.

    GeoJSON: try geojson_lookup first; if missed, fall back to csv_lookup.
             On a csv_lookup hit, fill in the GeoJSON-only columns on the existing row
             and register the complete key in geojson_lookup.
    CSV:     use csv_lookup only.

    Mutates stations_rows, rows_by_id, and both lookups.
    Returns (merged_rows, new_stations_count, updated_rows_count, updated_next_id).
    """
    merged_rows   = []
    new_stations  = 0
    updated_rows  = 0

    for props in props_list:
        station_id = None

        if fmt == "geojson":
            station_id = geojson_lookup.get(geojson_key(props))

            if station_id is None:
                # Fall back: try matching on the 8 CSV-available cols
                station_id = csv_lookup.get(csv_key(props))
                if station_id is not None:
                    # Found a CSV-sourced row — fill in the GeoJSON-only columns
                    existing_row = rows_by_id[station_id]
                    for c in GEOJSON_ONLY_COLS:
                        existing_row[c] = prop(props, c)
                    # Register the complete key so future GeoJSON files match directly
                    geojson_lookup[geojson_key(existing_row)] = station_id
                    updated_rows += 1
        else:
            station_id = csv_lookup.get(csv_key(props))

        if station_id is None:
            station_id = next_id
            next_id   += 1
            new_row    = {"station_id": station_id}
            for c in STATIC_COLS:
                new_row[c] = prop(props, c)  # GeoJSON-only cols are empty string for CSV rows
            stations_rows.append(new_row)
            rows_by_id[station_id]               = new_row
            geojson_lookup[geojson_key(new_row)] = station_id
            csv_lookup[csv_key(new_row)]         = station_id
            new_stations += 1

        merged_row = {
            "timestamp":  timestamp,
            "station_id": station_id,
            "pfaf_id":    prop(props, "pfaf_id"),
            "ID":         prop(props, "ID"),
            "Point No":   prop(props, "Point No"),
        }
        for c in DYNAMIC_COLS:
            merged_row[c] = prop(props, c)
        merged_rows.append(merged_row)

    return merged_rows, new_stations, updated_rows, next_id


def append_merged_rows(rows):
    write_header = not os.path.exists(MERGED_FILE)
    with open(MERGED_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MERGED_COLS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def main():
    print("Loading stations lookup...")
    stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id = load_stations()
    print(f"  {len(stations_rows)} stations loaded")

    print("Reading existing timestamps from merged CSV...")
    existing  = read_existing_timestamps()
    print(f"  {len(existing)} timestamps already processed")

    print("Fetching file list from server...")
    all_files     = list_server_files()
    new_files     = {ts: fn for ts, fn in sorted(all_files.items()) if ts not in existing}
    geojson_count = sum(1 for fn in all_files.values() if fn.endswith(".geojson"))
    csv_count     = sum(1 for fn in all_files.values() if fn.endswith(".csv"))
    print(f"  {len(all_files)} timestamps on server "
          f"({geojson_count} geojson, {csv_count} csv), "
          f"{len(new_files)} new to process\n")

    if not new_files:
        print("Nothing to do.")
        return

    total_rows          = 0
    total_new_stations  = 0
    total_updated_rows  = 0

    for i, (timestamp, filename) in enumerate(new_files.items(), 1):
        disk = free_disk_gb()
        if disk < MIN_DISK_GB:
            print(f"\nDisk space low ({disk:.2f} GB free). Stopping.")
            print(f"Resume from: {filename}")
            break

        fmt = "geojson" if filename.endswith(".geojson") else "csv"
        try:
            resp       = download(filename)
            props_list = parse_geojson(resp) if fmt == "geojson" else parse_csv(resp)

            merged_rows, new_stations, updated_rows, next_id = process_rows(
                props_list, fmt, timestamp,
                stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id
            )
            append_merged_rows(merged_rows)

            if new_stations or updated_rows:
                save_stations(stations_rows)

            total_rows         += len(merged_rows)
            total_new_stations += new_stations
            total_updated_rows += updated_rows
            print(
                f"  [{i}/{len(new_files)}] {filename} ({fmt}) -> "
                f"{len(merged_rows)} rows, {new_stations} new, "
                f"{updated_rows} updated | disk: {disk:.2f} GB"
            )
        except Exception as exc:
            print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

    print(f"\nDone. {total_rows:,} rows appended to {MERGED_FILE}")
    print(f"      {total_new_stations} new stations, "
          f"{total_updated_rows} stations filled from GeoJSON")


if __name__ == "__main__":
    main()
