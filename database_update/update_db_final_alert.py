"""
Push latest Final Alert data to all_watersheds and summary_final_alert_latest in the database.

For each new file on the server:
  1. Skip if its timestamp is already in summary_final_alert_latest (via MAX timestamp).
  2. Download the file.
  3. Resolve matching_id_watershed from the DB-backed lookup; insert new watersheds as needed.
  4. Build the merged row (alert data + watershed metadata) and upsert into
     summary_final_alert_latest (newest timestamp wins per matching_id_watershed).

Watershed management mirrors update_final_alert.py but uses the DB instead of CSVs.
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

BASE_URL       = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/Final_Alert/"
RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5

LOOKUP_KEY  = ("pfaf_id", "name", "name_1", "CentroidX", "CentroidY")
LOOKUP_COLS = ["matching_id_watershed", "pfaf_id", "name", "name_1",
               "CentroidX", "CentroidY", "Admin1_count", "Admin1_names", "area_km2"]

ALERT_COLS = [
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

# Watershed metadata columns appended to ALERT_COLS in summary_final_alert_latest
WATERSHED_EXTRA_COLS = ["name", "name_1", "CentroidX", "CentroidY",
                        "Admin1_count", "Admin1_names", "area_km2"]

DB_PARAMS = {
    "host":            os.getenv("DB_HOST"),
    "port":            int(os.getenv("DB_PORT", 5432)),
    "dbname":          os.getenv("DB_NAME", "postgres"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "sslmode":         os.getenv("DB_SSLMODE", "require"),
    "connect_timeout": 10,
}

# fmt: off
UPSERT_LATEST_SQL = """
INSERT INTO summary_final_alert_latest (
    "timestamp", matching_id_watershed, pfaf_id,
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
    "name", "name_1", "CentroidX", "CentroidY",
    "Admin1_count", "Admin1_names", "area_km2"
)
VALUES %s
ON CONFLICT (matching_id_watershed) DO UPDATE SET
    "timestamp"               = EXCLUDED."timestamp",
    pfaf_id                   = EXCLUDED.pfaf_id,
    "rfr_score"               = EXCLUDED."rfr_score",
    "cfr_score"               = EXCLUDED."cfr_score",
    "Alert_level"             = EXCLUDED."Alert_level",
    "Days_until_peak"         = EXCLUDED."Days_until_peak",
    "GloFAS_2yr"              = EXCLUDED."GloFAS_2yr",
    "GloFAS_5yr"              = EXCLUDED."GloFAS_5yr",
    "GloFAS_20yr"             = EXCLUDED."GloFAS_20yr",
    "Alert_Score"             = EXCLUDED."Alert_Score",
    "PeakArrivalScore"        = EXCLUDED."PeakArrivalScore",
    "TwoYScore"               = EXCLUDED."TwoYScore",
    "FiveYScore"              = EXCLUDED."FiveYScore",
    "TwtyYScore"              = EXCLUDED."TwtyYScore",
    "Sum_Score_x"             = EXCLUDED."Sum_Score_x",
    "GFMS_TotalArea_km"       = EXCLUDED."GFMS_TotalArea_km",
    "GFMS_perc_Area"          = EXCLUDED."GFMS_perc_Area",
    "GFMS_MeanDepth"          = EXCLUDED."GFMS_MeanDepth",
    "GFMS_MaxDepth"           = EXCLUDED."GFMS_MaxDepth",
    "GFMS_Duration"           = EXCLUDED."GFMS_Duration",
    "GFMS_area_score"         = EXCLUDED."GFMS_area_score",
    "GFMS_perc_area_score"    = EXCLUDED."GFMS_perc_area_score",
    "MeanD_Score"             = EXCLUDED."MeanD_Score",
    "MaxD_Score"              = EXCLUDED."MaxD_Score",
    "Duration_Score"          = EXCLUDED."Duration_Score",
    "Sum_Score_y"             = EXCLUDED."Sum_Score_y",
    "MOM_Score"               = EXCLUDED."MOM_Score",
    "Hazard_Score"            = EXCLUDED."Hazard_Score",
    "Scaled_Riverine_Risk"    = EXCLUDED."Scaled_Riverine_Risk",
    "Scaled_Coastal_Risk"     = EXCLUDED."Scaled_Coastal_Risk",
    "Flag"                    = EXCLUDED."Flag",
    "1-Day_TotalArea_km2"     = EXCLUDED."1-Day_TotalArea_km2",
    "1-Day_perc_Area"         = EXCLUDED."1-Day_perc_Area",
    "1-Day_CS_TotalArea_km2"  = EXCLUDED."1-Day_CS_TotalArea_km2",
    "1-Day_CS_perc_Area"      = EXCLUDED."1-Day_CS_perc_Area",
    "2-Day_TotalArea_km2"     = EXCLUDED."2-Day_TotalArea_km2",
    "2-Day_perc_Area"         = EXCLUDED."2-Day_perc_Area",
    "3-Day_TotalArea_km2"     = EXCLUDED."3-Day_TotalArea_km2",
    "3-Day_perc_Area"         = EXCLUDED."3-Day_perc_Area",
    "DFO_area_1day_score"     = EXCLUDED."DFO_area_1day_score",
    "DFO_percarea_1day_score" = EXCLUDED."DFO_percarea_1day_score",
    "DFO_area_2day_score"     = EXCLUDED."DFO_area_2day_score",
    "DFO_percarea_2day_score" = EXCLUDED."DFO_percarea_2day_score",
    "DFO_area_3day_score"     = EXCLUDED."DFO_area_3day_score",
    "DFO_percarea_3day_score" = EXCLUDED."DFO_percarea_3day_score",
    "DFOTotal_Score"          = EXCLUDED."DFOTotal_Score",
    "onedayFlood_Area_km"     = EXCLUDED."onedayFlood_Area_km",
    "onedayperc_Area"         = EXCLUDED."onedayperc_Area",
    "fivedayFlood_Area_km"    = EXCLUDED."fivedayFlood_Area_km",
    "fivedayperc_Area"        = EXCLUDED."fivedayperc_Area",
    "VIIRS_area_1day_score"   = EXCLUDED."VIIRS_area_1day_score",
    "VIIRS_percarea_1day_score" = EXCLUDED."VIIRS_percarea_1day_score",
    "VIIRS_area_5day_score"   = EXCLUDED."VIIRS_area_5day_score",
    "VIIRS_percarea_5day_score" = EXCLUDED."VIIRS_percarea_5day_score",
    "VIIRSTotal_Score"        = EXCLUDED."VIIRSTotal_Score",
    "Severity"                = EXCLUDED."Severity",
    "Alert"                   = EXCLUDED."Alert",
    "Status"                  = EXCLUDED."Status",
    "name"                    = EXCLUDED."name",
    "name_1"                  = EXCLUDED."name_1",
    "CentroidX"               = EXCLUDED."CentroidX",
    "CentroidY"               = EXCLUDED."CentroidY",
    "Admin1_count"            = EXCLUDED."Admin1_count",
    "Admin1_names"            = EXCLUDED."Admin1_names",
    "area_km2"                = EXCLUDED."area_km2"
WHERE EXCLUDED."timestamp" >= summary_final_alert_latest."timestamp"
"""
# fmt: on

# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

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


def to_str(v):
    return v if v not in (None, "") else None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_last_timestamp(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT MAX("timestamp") FROM summary_final_alert_latest')
        return cur.fetchone()[0]


def load_lookup(conn):
    """Load all_watersheds from DB. Returns (lookup_dict, max_id, rows_by_id)."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM all_watersheds")
        cols    = [desc[0] for desc in cur.description]
        db_rows = cur.fetchall()

    lookup     = {}
    rows_by_id = {}
    max_id     = 0

    for db_row in db_rows:
        row = {col: ("" if val is None else str(val)) for col, val in zip(cols, db_row)}
        key    = tuple(row.get(c, "") for c in LOOKUP_KEY)
        wid    = int(row["matching_id_watershed"])
        lookup[key]     = str(wid)
        rows_by_id[wid] = row
        max_id = max(max_id, wid)

    return lookup, max_id, rows_by_id


def insert_watershed(conn, row, new_id):
    """INSERT a new watershed into all_watersheds and return the new matching_id_watershed."""
    from decimal import Decimal, InvalidOperation

    def to_dec(v, places=6):
        try:
            return round(Decimal(str(float(v))), places) if v not in (None, "") else None
        except (ValueError, TypeError, InvalidOperation):
            return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO all_watersheds (
                matching_id_watershed, pfaf_id, "name", "name_1",
                "CentroidX", "CentroidY", "Admin1_count", "Admin1_names", "area_km2"
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (matching_id_watershed) DO NOTHING
            """,
            (
                new_id,
                to_int(row.get("pfaf_id")),
                to_str(row.get("name")),
                to_str(row.get("name_1")),
                to_dec(row.get("CentroidX"), 6),
                to_dec(row.get("CentroidY"), 6),
                to_int(row.get("Admin1_count")),
                to_str(row.get("Admin1_names")),
                to_float(row.get("area_km2")),
            ),
        )
    print(f"  [lookup] Added new watershed id={new_id}: "
          f"pfaf_id={row.get('pfaf_id')}, name={row.get('name')}, "
          f"name_1={row.get('name_1')}")


def build_merged_tuple(timestamp, matching_id_watershed, row, watershed):
    """Build a tuple matching UPSERT_LATEST_SQL column order."""
    f = to_float
    i = to_int
    s = to_str
    from decimal import Decimal, InvalidOperation

    def to_dec(v, places=6):
        try:
            return round(Decimal(str(float(v))), places) if v not in (None, "") else None
        except (ValueError, TypeError, InvalidOperation):
            return None

    return (
        timestamp,
        i(matching_id_watershed),
        i(row.get("pfaf_id")),
        f(row.get("rfr_score")),
        f(row.get("cfr_score")),
        f(row.get("Alert_level")),
        f(row.get("Days_until_peak")),
        f(row.get("GloFAS_2yr")),
        f(row.get("GloFAS_5yr")),
        f(row.get("GloFAS_20yr")),
        f(row.get("Alert_Score")),
        f(row.get("PeakArrivalScore")),
        f(row.get("TwoYScore")),
        f(row.get("FiveYScore")),
        f(row.get("TwtyYScore")),
        f(row.get("Sum_Score_x")),
        f(row.get("GFMS_TotalArea_km")),
        f(row.get("GFMS_perc_Area")),
        f(row.get("GFMS_MeanDepth")),
        f(row.get("GFMS_MaxDepth")),
        f(row.get("GFMS_Duration")),
        f(row.get("GFMS_area_score")),
        f(row.get("GFMS_perc_area_score")),
        f(row.get("MeanD_Score")),
        f(row.get("MaxD_Score")),
        f(row.get("Duration_Score")),
        f(row.get("Sum_Score_y")),
        f(row.get("MOM_Score")),
        f(row.get("Hazard_Score")),
        f(row.get("Scaled_Riverine_Risk")),
        f(row.get("Scaled_Coastal_Risk")),
        f(row.get("Flag")),
        f(row.get("1-Day_TotalArea_km2")),
        f(row.get("1-Day_perc_Area")),
        f(row.get("1-Day_CS_TotalArea_km2")),
        f(row.get("1-Day_CS_perc_Area")),
        f(row.get("2-Day_TotalArea_km2")),
        f(row.get("2-Day_perc_Area")),
        f(row.get("3-Day_TotalArea_km2")),
        f(row.get("3-Day_perc_Area")),
        f(row.get("DFO_area_1day_score")),
        f(row.get("DFO_percarea_1day_score")),
        f(row.get("DFO_area_2day_score")),
        f(row.get("DFO_percarea_2day_score")),
        f(row.get("DFO_area_3day_score")),
        f(row.get("DFO_percarea_3day_score")),
        f(row.get("DFOTotal_Score")),
        f(row.get("onedayFlood_Area_km")),
        f(row.get("onedayperc_Area")),
        f(row.get("fivedayFlood_Area_km")),
        f(row.get("fivedayperc_Area")),
        f(row.get("VIIRS_area_1day_score")),
        f(row.get("VIIRS_percarea_1day_score")),
        f(row.get("VIIRS_area_5day_score")),
        f(row.get("VIIRS_percarea_5day_score")),
        f(row.get("VIIRSTotal_Score")),
        f(row.get("Severity")),
        s(row.get("Alert")),
        s(row.get("Status")),
        # watershed metadata
        s(watershed.get("name")),
        s(watershed.get("name_1")),
        to_dec(watershed.get("CentroidX"), 6),
        to_dec(watershed.get("CentroidY"), 6),
        i(watershed.get("Admin1_count")),
        s(watershed.get("Admin1_names")),
        f(watershed.get("area_km2")),
    )


# ---------------------------------------------------------------------------
# Server interaction
# ---------------------------------------------------------------------------

def list_server_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(Final_Attributes_[^"]+\.csv)"', resp.text))


def get_timestamp(filename):
    match = re.search(r'Final_Attributes_(\d{10})', filename)
    return match.group(1) if match else ""


def download_content(filename):
    url = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return resp.content.decode("utf-8", errors="ignore")
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {filename}: {exc}") from exc


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_file(conn, content, filename, lookup, max_id, rows_by_id):
    """
    Resolve/add watershed IDs, build merged tuples, upsert into summary_final_alert_latest.
    Returns (rows_written, updated_max_id).
    """
    timestamp     = get_timestamp(filename)
    merged_tuples = []
    new_watersheds = 0

    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        key = tuple(row.get(c, "") for c in LOOKUP_KEY)
        matching_id_watershed = lookup.get(key)

        if matching_id_watershed is None:
            new_id = max_id + 1
            max_id = new_id
            new_watershed_row = {
                "matching_id_watershed": str(new_id),
                "pfaf_id":      row.get("pfaf_id", ""),
                "name":         row.get("name", ""),
                "name_1":       row.get("name_1", ""),
                "CentroidX":    row.get("CentroidX", ""),
                "CentroidY":    row.get("CentroidY", ""),
                "Admin1_count": row.get("Admin1_count", ""),
                "Admin1_names": row.get("Admin1_names", ""),
                "area_km2":     row.get("area_km2", ""),
            }
            insert_watershed(conn, new_watershed_row, new_id)
            lookup[key]       = str(new_id)
            rows_by_id[new_id] = new_watershed_row
            matching_id_watershed = str(new_id)
            new_watersheds += 1

        watershed = rows_by_id[int(matching_id_watershed)]
        merged_tuples.append(
            build_merged_tuple(timestamp, matching_id_watershed, row, watershed)
        )

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, UPSERT_LATEST_SQL, merged_tuples, page_size=500)
    conn.commit()

    return len(merged_tuples), max_id, new_watersheds


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    conn = psycopg2.connect(**DB_PARAMS)
    try:
        print("Querying latest timestamp from DB...")
        last_ts = get_last_timestamp(conn)
        print(f"  Latest timestamp in summary_final_alert_latest: {last_ts or '(none)'}")

        print("Fetching file list from server...")
        all_files  = list_server_filenames()
        to_process = [f for f in all_files if get_timestamp(f) > (last_ts or "")]
        print(f"  {len(all_files)} files on server | "
              f"{len(all_files) - len(to_process)} skipped | "
              f"{len(to_process)} to process\n")

        if not to_process:
            print("Nothing to do.")
            return

        print("Loading watershed lookup from DB...")
        lookup, max_id, rows_by_id = load_lookup(conn)
        print(f"  {len(lookup):,} entries loaded (max id={max_id})\n")

        total_rows     = 0
        total_new_ws   = 0
        failed         = []

        for i, filename in enumerate(to_process, 1):
            try:
                content = download_content(filename)
                rows_written, max_id, new_ws = process_file(
                    conn, content, filename, lookup, max_id, rows_by_id
                )
                total_rows   += rows_written
                total_new_ws += new_ws

                if i % 100 == 0 or i == len(to_process):
                    print(f"  {i}/{len(to_process)} done | "
                          f"{total_rows:,} rows upserted | "
                          f"new watersheds: {total_new_ws}")
            except Exception as exc:
                conn.rollback()
                print(f"  FAILED {filename}: {exc}")
                failed.append(filename)

        print(f"\nDone. {len(to_process) - len(failed)}/{len(to_process)} files processed, "
              f"{total_rows:,} rows upserted into summary_final_alert_latest")
        if total_new_ws:
            print(f"  {total_new_ws} new watershed(s) added to all_watersheds")
        if failed:
            print(f"\nFailed files ({len(failed)}):")
            for f in failed:
                print(f"  {f}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
