"""
validate_3files.py
==================
For each data source, checks which files are already in the database and
downloads + inserts any that are missing.  Safe to re-run: existing timestamps
are skipped.

Set FILES_PER_SOURCE to an integer to limit how many files are processed per
source, or None to process all available files.
"""

import time

import psycopg2

from db_utils import (
    DB_PARAMS, download_text, download_resp,
    list_server_files, parse_timestamp_hh, parse_timestamp_day, upsert_dataframe,
)
from update_db_gfms        import get_timestamp as gfms_get_ts,  extract_df as gfms_extract
from update_db_hwrf        import get_timestamp as hwrf_get_ts,  extract_df as hwrf_extract
from update_db_dfo         import get_timestamp as dfo_get_ts,   extract_df as dfo_extract
from update_db_viirs       import get_timestamp as viirs_get_ts, extract_df as viirs_extract
from update_db_final_alert import get_timestamp as fa_get_ts,    extract_df as fa_extract
from update_db_glofas      import (
    list_server_files as glofas_list,
    parse_geojson, parse_csv as glofas_parse_csv, build_df as glofas_build,
)

# =============================================================================
# Configuration
# =============================================================================

FILES_PER_SOURCE = 5   # set to None to process all files
DOWNLOAD_DELAY   = 0.5  # seconds between downloads

# =============================================================================

BASE = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/"


def _limit(files):
    return files if FILES_PER_SOURCE is None else files[:FILES_PER_SOURCE]


def _in_history(conn, table, parsed_ts):
    with conn.cursor() as cur:
        cur.execute(f'SELECT 1 FROM {table} WHERE "timestamp" = %s LIMIT 1', (parsed_ts,))
        return cur.fetchone() is not None


def _insert(conn, stage_table, df, fname):
    try:
        upsert_dataframe(stage_table, df, conn=conn)
        print(f"  {fname}: inserted {len(df)} rows")
    except Exception as exc:
        conn.rollback()
        print(f"  {fname}: FAILED — {exc}")


conn = psycopg2.connect(**DB_PARAMS)
try:
    # ── GFMS ──────────────────────────────────────────────────────────────────
    print("GFMS")
    url   = BASE + "GFMS/GFMS_summary/"
    files = _limit(list_server_files(url, r'href="(Flood_byStor_\d+\.csv)"'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(gfms_get_ts(fname))
        if _in_history(conn, "summary_gfms", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = download_text(url + fname)
        time.sleep(DOWNLOAD_DELAY)
        _insert(conn, "stage_gfms", gfms_extract(content, gfms_get_ts(fname)), fname)

    # ── HWRF ──────────────────────────────────────────────────────────────────
    print("\nHWRF")
    url   = BASE + "HWRF/HWRF_summary/"
    files = _limit(list_server_files(url, r'href="(hwrf\.\d+rainfall\.csv)"'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(hwrf_get_ts(fname))
        if _in_history(conn, "summary_hwrf", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = download_text(url + fname)
        time.sleep(DOWNLOAD_DELAY)
        _insert(conn, "stage_hwrf", hwrf_extract(content, hwrf_get_ts(fname)), fname)

    # ── DFO ───────────────────────────────────────────────────────────────────
    print("\nDFO")
    url   = BASE + "DFO/DFO_summary/"
    files = _limit(list_server_files(url, r'href="(DFO_\w+\.csv)"'))
    for fname in files:
        parsed_ts = parse_timestamp_day(dfo_get_ts(fname))
        if _in_history(conn, "summary_dfo", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = download_text(url + fname)
        time.sleep(DOWNLOAD_DELAY)
        _insert(conn, "stage_dfo", dfo_extract(content, dfo_get_ts(fname)), fname)

    # ── VIIRS ─────────────────────────────────────────────────────────────────
    print("\nVIIRS")
    url   = BASE + "VIIRS/VIIRS_summary/"
    files = _limit(list_server_files(url, r'href="(VIIRS_Flood_\d+\.csv)"'))
    for fname in files:
        parsed_ts = parse_timestamp_day(viirs_get_ts(fname))
        if _in_history(conn, "summary_viirs", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = download_text(url + fname)
        time.sleep(DOWNLOAD_DELAY)
        _insert(conn, "stage_viirs", viirs_extract(content, viirs_get_ts(fname)), fname)

    # ── GloFAS ────────────────────────────────────────────────────────────────
    print("\nGloFAS")
    gfiles = _limit(list(sorted(glofas_list().items())))
    for ts, fname in gfiles:
        parsed_ts = parse_timestamp_hh(ts)
        if _in_history(conn, "summary_glofas", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        resp  = download_resp(BASE + "GLOFAS/" + fname)
        time.sleep(DOWNLOAD_DELAY)
        props = parse_geojson(resp) if fname.endswith(".geojson") else glofas_parse_csv(resp)
        _insert(conn, "stage_glofas", glofas_build(props, ts), fname)

    # ── Final Alert ───────────────────────────────────────────────────────────
    print("\nFinal Alert")
    url   = BASE + "Final_Alert/"
    files = _limit(list_server_files(url, r'href="(Final_Attributes_[^"]+\.csv)"'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(fa_get_ts(fname))
        if _in_history(conn, "summary_final_alert", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = download_text(url + fname, errors="ignore")
        time.sleep(DOWNLOAD_DELAY)
        _insert(conn, "stage_final_alert", fa_extract(content, fa_get_ts(fname)), fname)

finally:
    conn.close()

print("\nDone.")
