"""
Push latest GloFAS data to all_glofas_stations and summary_glofas_latest in the database.

For each new timestamp on the server (GeoJSON preferred over CSV):
  1. Parse each row/feature's properties.
  2. Match against all_glofas_stations (loaded from DB into memory):
     - GeoJSON: match on all STATIC_COLS with numeric cols float-normalised.
     - CSV:     match on CSV_STATIC_COLS (subset available in CSV).
     Match found → reuse matching_id_station; no match → INSERT new station into DB.
  3. Upsert the merged row (forecast data + station metadata) into summary_glofas_latest,
     newest timestamp wins per matching_id_station.

Station management mirrors update_glofas.py but uses the DB instead of CSVs.
"""

import csv
import io
import os
import re
import time
from decimal import Decimal, InvalidOperation

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GLOFAS/"

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5

STATIC_COLS = [
    "Station", "Basin", "Country", "Country_code",
    "Continent", "ISO", "Admin0", "Admin1", "Location",
    "Lat", "Lon", "Upstream area", "area_km2", "pfaf_id",
    "rfr_score", "cfr_score",
]
STATIONS_COLS = ["matching_id_station"] + STATIC_COLS

CSV_STATIC_COLS = ["Station", "Country", "Lat", "Lon", "pfaf_id"]
NUMERIC_COLS    = {"Lat", "Lon", "Upstream area", "area_km2", "rfr_score", "cfr_score"}
GEOJSON_ONLY_COLS  = [c for c in STATIC_COLS if c not in CSV_STATIC_COLS]
COMPLETABLE_COLS   = ["Basin", "Upstream area"]

DYNAMIC_COLS = [
    "Alert_level", "Days_until_peak",
    "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
    "max_EPS", "Forecast Date",
]
# All columns written to summary_glofas_latest (forecast + station metadata)
LATEST_COLS = (
    ["timestamp", "matching_id_station", "pfaf_id", "ID", "Point No"]
    + DYNAMIC_COLS
    + STATIC_COLS
)

DB_PARAMS = {
    "host":            os.getenv("DB_HOST"),
    "port":            int(os.getenv("DB_PORT", 5432)),
    "dbname":          os.getenv("DB_NAME", "postgres"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "sslmode":         os.getenv("DB_SSLMODE", "require"),
    "connect_timeout": 10,
}

# Upsert into summary_glofas_latest — newest timestamp wins per matching_id_station
UPSERT_LATEST_SQL = """
INSERT INTO summary_glofas_latest (
    "timestamp", matching_id_station, pfaf_id, "ID", "Point No",
    "Alert_level", "Days_until_peak", "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
    "max_EPS", "Forecast Date",
    "Station", "Basin", "Country", "Country_code", "Continent", "ISO",
    "Admin0", "Admin1", "Location", "Lat", "Lon", "Upstream area",
    "area_km2", "rfr_score", "cfr_score"
)
VALUES %s
ON CONFLICT (matching_id_station) DO UPDATE SET
    "timestamp"        = EXCLUDED."timestamp",
    pfaf_id            = EXCLUDED.pfaf_id,
    "ID"               = EXCLUDED."ID",
    "Point No"         = EXCLUDED."Point No",
    "Alert_level"      = EXCLUDED."Alert_level",
    "Days_until_peak"  = EXCLUDED."Days_until_peak",
    "GloFAS_2yr"       = EXCLUDED."GloFAS_2yr",
    "GloFAS_5yr"       = EXCLUDED."GloFAS_5yr",
    "GloFAS_20yr"      = EXCLUDED."GloFAS_20yr",
    "max_EPS"          = EXCLUDED."max_EPS",
    "Forecast Date"    = EXCLUDED."Forecast Date",
    "Station"          = EXCLUDED."Station",
    "Basin"            = EXCLUDED."Basin",
    "Country"          = EXCLUDED."Country",
    "Country_code"     = EXCLUDED."Country_code",
    "Continent"        = EXCLUDED."Continent",
    "ISO"              = EXCLUDED."ISO",
    "Admin0"           = EXCLUDED."Admin0",
    "Admin1"           = EXCLUDED."Admin1",
    "Location"         = EXCLUDED."Location",
    "Lat"              = EXCLUDED."Lat",
    "Lon"              = EXCLUDED."Lon",
    "Upstream area"    = EXCLUDED."Upstream area",
    "area_km2"         = EXCLUDED."area_km2",
    "rfr_score"        = EXCLUDED."rfr_score",
    "cfr_score"        = EXCLUDED."cfr_score"
WHERE EXCLUDED."timestamp" >= summary_glofas_latest."timestamp"
"""

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def to_str(v):
    return "" if v is None else str(v)


def to_int_or_none(v):
    try:
        return int(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def to_float_or_none(v):
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def to_decimal_or_none(v, places=3):
    try:
        if v in (None, ""):
            return None
        return round(Decimal(str(float(v))), places)
    except (ValueError, TypeError, InvalidOperation):
        return None


def norm_float(v):
    """Normalise a numeric string to float repr for lookup keys."""
    try:
        return str(float(v))
    except (TypeError, ValueError):
        return str(v) if v is not None else ""


def prop(props, key):
    v = props.get(key)
    return "" if v is None else str(v)


# ---------------------------------------------------------------------------
# Lookup key builders (identical logic to update_glofas.py)
# ---------------------------------------------------------------------------

def geojson_key(props):
    return tuple(
        norm_float(prop(props, c)) if c in NUMERIC_COLS else prop(props, c)
        for c in STATIC_COLS
    )


def csv_key(props):
    return tuple(
        norm_float(prop(props, c)) if c in NUMERIC_COLS else prop(props, c)
        for c in CSV_STATIC_COLS
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_last_timestamp(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT MAX("timestamp") FROM summary_glofas_latest')
        return cur.fetchone()[0]


def load_stations(conn):
    """Load all_glofas_stations from DB into the same in-memory structure as update_glofas.py."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM all_glofas_stations")
        cols = [desc[0] for desc in cur.description]
        db_rows = cur.fetchall()

    # Normalise all values to strings to match the CSV-sourced behaviour
    rows = []
    for db_row in db_rows:
        row = {}
        for col, val in zip(cols, db_row):
            row[col] = "" if val is None else str(val)
        rows.append(row)

    rows_by_id     = {int(r["matching_id_station"]): r for r in rows}
    geojson_lookup = {geojson_key(r): int(r["matching_id_station"]) for r in rows}
    csv_lookup     = {csv_key(r):     int(r["matching_id_station"]) for r in rows}
    next_id        = max(rows_by_id) + 1 if rows_by_id else 1
    return rows, rows_by_id, geojson_lookup, csv_lookup, next_id


def insert_station(conn, row):
    """INSERT a new station row into all_glofas_stations."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO all_glofas_stations (
                matching_id_station, "Station", "Basin", "Country", "Country_code",
                "Continent", "ISO", "Admin0", "Admin1", "Location",
                "Lat", "Lon", "Upstream area", "area_km2", pfaf_id,
                "rfr_score", "cfr_score"
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (matching_id_station) DO NOTHING
            """,
            (
                int(row["matching_id_station"]),
                row.get("Station") or None,
                row.get("Basin") or None,
                row.get("Country") or None,
                row.get("Country_code") or None,
                row.get("Continent") or None,
                row.get("ISO") or None,
                row.get("Admin0") or None,
                row.get("Admin1") or None,
                row.get("Location") or None,
                to_decimal_or_none(row.get("Lat"), 3),
                to_decimal_or_none(row.get("Lon"), 3),
                to_decimal_or_none(row.get("Upstream area"), 3),
                to_float_or_none(row.get("area_km2")),
                to_int_or_none(row.get("pfaf_id")),
                to_float_or_none(row.get("rfr_score")),
                to_float_or_none(row.get("cfr_score")),
            ),
        )


def update_station(conn, row):
    """UPDATE an existing station row in all_glofas_stations (for completions and GeoJSON fills)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE all_glofas_stations SET
                "Station"      = %s, "Basin"        = %s, "Country"      = %s,
                "Country_code" = %s, "Continent"    = %s, "ISO"          = %s,
                "Admin0"       = %s, "Admin1"       = %s, "Location"     = %s,
                "Lat"          = %s, "Lon"          = %s, "Upstream area"= %s,
                "area_km2"     = %s, pfaf_id        = %s, "rfr_score"    = %s,
                "cfr_score"    = %s
            WHERE matching_id_station = %s
            """,
            (
                row.get("Station") or None,
                row.get("Basin") or None,
                row.get("Country") or None,
                row.get("Country_code") or None,
                row.get("Continent") or None,
                row.get("ISO") or None,
                row.get("Admin0") or None,
                row.get("Admin1") or None,
                row.get("Location") or None,
                to_decimal_or_none(row.get("Lat"), 3),
                to_decimal_or_none(row.get("Lon"), 3),
                to_decimal_or_none(row.get("Upstream area"), 3),
                to_float_or_none(row.get("area_km2")),
                to_int_or_none(row.get("pfaf_id")),
                to_float_or_none(row.get("rfr_score")),
                to_float_or_none(row.get("cfr_score")),
                int(row["matching_id_station"]),
            ),
        )


def upsert_latest(conn, rows):
    """Upsert merged forecast+station rows into summary_glofas_latest."""
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT_LATEST_SQL, rows, page_size=500)
    conn.commit()


# ---------------------------------------------------------------------------
# Server interaction
# ---------------------------------------------------------------------------

def list_server_files():
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


# ---------------------------------------------------------------------------
# Station matching + merged row building (mirrors update_glofas.py logic)
# ---------------------------------------------------------------------------

def complete_row(existing_row, props):
    changed = False
    for c in COMPLETABLE_COLS:
        incoming = prop(props, c)
        if incoming and not existing_row.get(c):
            existing_row[c] = incoming
            changed = True
    return changed


def process_rows(props_list, fmt, timestamp,
                 stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id):
    """
    Match or create stations and build merged (forecast + station metadata) rows.
    Returns (merged_tuples, new_station_rows, updated_station_rows, updated_next_id).
    """
    merged_tuples    = []
    new_station_rows     = []
    updated_station_rows = []

    for props in props_list:
        matching_id_station = None
        station_changed     = False

        if fmt == "geojson":
            matching_id_station = geojson_lookup.get(geojson_key(props))

            if matching_id_station is not None:
                if complete_row(rows_by_id[matching_id_station], props):
                    station_changed = True
            else:
                matching_id_station = csv_lookup.get(csv_key(props))
                if matching_id_station is not None:
                    existing_row = rows_by_id[matching_id_station]
                    for c in GEOJSON_ONLY_COLS:
                        existing_row[c] = prop(props, c)
                    if complete_row(existing_row, props):
                        pass  # already marking as changed below
                    geojson_lookup[geojson_key(existing_row)] = matching_id_station
                    station_changed = True
        else:
            matching_id_station = csv_lookup.get(csv_key(props))
            if matching_id_station is not None:
                if complete_row(rows_by_id[matching_id_station], props):
                    station_changed = True

        if matching_id_station is None:
            matching_id_station = next_id
            next_id += 1
            new_row = {"matching_id_station": str(matching_id_station)}
            for c in STATIC_COLS:
                new_row[c] = prop(props, c)
            stations_rows.append(new_row)
            rows_by_id[matching_id_station]      = new_row
            geojson_lookup[geojson_key(new_row)] = matching_id_station
            csv_lookup[csv_key(new_row)]         = matching_id_station
            new_station_rows.append(new_row)
        elif station_changed:
            updated_station_rows.append(rows_by_id[matching_id_station])

        station = rows_by_id[matching_id_station]

        # Build the merged tuple matching UPSERT_LATEST_SQL column order:
        # timestamp, matching_id_station, pfaf_id(forecast), ID, Point No,
        # dynamic cols..., static cols... (station)
        merged_tuples.append((
            timestamp,
            matching_id_station,
            to_int_or_none(prop(props, "pfaf_id")),
            prop(props, "ID") or None,
            to_int_or_none(prop(props, "Point No")),
            to_int_or_none(prop(props, "Alert_level")),
            to_int_or_none(prop(props, "Days_until_peak")),
            to_float_or_none(prop(props, "GloFAS_2yr")),
            to_float_or_none(prop(props, "GloFAS_5yr")),
            to_float_or_none(prop(props, "GloFAS_20yr")),
            prop(props, "max_EPS") or None,
            prop(props, "Forecast Date") or None,
            # station metadata
            station.get("Station") or None,
            station.get("Basin") or None,
            station.get("Country") or None,
            station.get("Country_code") or None,
            station.get("Continent") or None,
            station.get("ISO") or None,
            station.get("Admin0") or None,
            station.get("Admin1") or None,
            station.get("Location") or None,
            to_decimal_or_none(station.get("Lat"), 3),
            to_decimal_or_none(station.get("Lon"), 3),
            to_decimal_or_none(station.get("Upstream area"), 3),
            to_float_or_none(station.get("area_km2")),
            to_float_or_none(station.get("rfr_score")),
            to_float_or_none(station.get("cfr_score")),
        ))

    return merged_tuples, new_station_rows, updated_station_rows, next_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying latest timestamp from DB...")
        last_ts = get_last_timestamp(conn)
        print(f"  Latest timestamp in summary_glofas_latest: {last_ts or '(none)'}")

        print("Fetching file list from server...")
        all_files     = list_server_files()
        new_files     = {ts: fn for ts, fn in sorted(all_files.items())
                         if ts > (last_ts or "")}
        geojson_count = sum(1 for fn in all_files.values() if fn.endswith(".geojson"))
        csv_count     = sum(1 for fn in all_files.values() if fn.endswith(".csv"))
        print(f"  {len(all_files)} timestamps on server "
              f"({geojson_count} geojson, {csv_count} csv), "
              f"{len(new_files)} new to process\n")

        if not new_files:
            print("Nothing to do.")
            return

        print("Loading stations from DB...")
        stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id = load_stations(conn)
        print(f"  {len(stations_rows)} stations loaded\n")

        total_rows         = 0
        total_new_stations = 0
        total_updated      = 0

        for i, (timestamp, filename) in enumerate(new_files.items(), 1):
            fmt = "geojson" if filename.endswith(".geojson") else "csv"
            try:
                resp       = download(filename)
                props_list = parse_geojson(resp) if fmt == "geojson" else parse_csv(resp)

                merged_tuples, new_station_rows, updated_station_rows, next_id = process_rows(
                    props_list, fmt, timestamp,
                    stations_rows, rows_by_id, geojson_lookup, csv_lookup, next_id
                )

                # Persist station changes first
                for row in new_station_rows:
                    insert_station(conn, row)
                for row in updated_station_rows:
                    update_station(conn, row)

                # Upsert merged rows into summary_glofas_latest
                upsert_latest(conn, merged_tuples)

                total_rows         += len(merged_tuples)
                total_new_stations += len(new_station_rows)
                total_updated      += len(updated_station_rows)
                print(
                    f"  [{i}/{len(new_files)}] {filename} ({fmt}) -> "
                    f"{len(merged_tuples)} rows, {len(new_station_rows)} new stations, "
                    f"{len(updated_station_rows)} updated"
                )
            except Exception as exc:
                conn.rollback()
                print(f"  [{i}/{len(new_files)}] FAILED {filename}: {exc}")

        print(f"\nDone. {total_rows:,} rows upserted into summary_glofas_latest")
        print(f"      {total_new_stations} new stations, {total_updated} stations updated")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
