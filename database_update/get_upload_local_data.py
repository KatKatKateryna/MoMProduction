"""
get_upload_local_data.py
========================
Same logic as get_upload_latest_data.py but reads CSV/GeoJSON files from
database_update/downloads_mom/<SOURCE>/<SOURCE>_summary/ instead of
downloading from the server.

Set FILES_PER_SOURCE to an integer to limit how many files are processed per
source, or None to process all available files.
"""

import re
from pathlib import Path

import psycopg2

from db_utils import (
    DB_PARAMS, parse_timestamp_hh, parse_timestamp_day, upsert_dataframe,
)
from update_db_gfms        import get_timestamp as gfms_get_ts,  extract_df as gfms_extract
from update_db_hwrf        import get_timestamp as hwrf_get_ts,  extract_df as hwrf_extract
from update_db_dfo         import get_timestamp as dfo_get_ts,   extract_df as dfo_extract
from update_db_viirs       import get_timestamp as viirs_get_ts, extract_df as viirs_extract
from update_db_final_alert import get_timestamp as fa_get_ts,    extract_df as fa_extract
from update_db_glofas      import parse_geojson, parse_csv as glofas_parse_csv, build_df as glofas_build

# =============================================================================
# Configuration
# =============================================================================

FILES_PER_SOURCE = 5   # set to None to process all files

# =============================================================================

DOWNLOADS_ROOT = Path(__file__).parent / "downloads_mom"


class _LocalFile:
    """Thin wrapper that mimics the requests.Response interface for local files."""

    def __init__(self, path: Path):
        self.content = path.read_bytes()

    def json(self):
        import json
        return json.loads(self.content)


def _list_local(folder: Path, pattern: str):
    """Return sorted filenames in folder whose names match the regex pattern."""
    if not folder.exists():
        return []
    return sorted(
        f.name for f in folder.iterdir()
        if f.is_file() and re.fullmatch(pattern, f.name)
    )


def _glofas_local_files(folder: Path):
    """
    Return {timestamp: filename} for GloFAS local files, preferring GeoJSON.
    Mirrors the logic of update_db_glofas.list_server_files().
    """
    if not folder.exists():
        return {}
    files = {}
    for f in sorted(folder.iterdir()):
        if not f.is_file():
            continue
        m = re.fullmatch(r'threspoints_(\d+)\.geojson', f.name)
        if m:
            files[m.group(1)] = f.name
            continue
        m = re.fullmatch(r'threspoints_(\d+)\.csv', f.name)
        if m and m.group(1) not in files:
            files[m.group(1)] = f.name
    return files


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
    folder = DOWNLOADS_ROOT / "GFMS" / "GFMS_summary"
    files  = _limit(_list_local(folder, r'Flood_byStor_\d+\.csv'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(gfms_get_ts(fname))
        if _in_history(conn, "summary_gfms", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding="utf-8")
        _insert(conn, "stage_gfms", gfms_extract(content, gfms_get_ts(fname)), fname)

    # ── HWRF ──────────────────────────────────────────────────────────────────
    print("\nHWRF")
    folder = DOWNLOADS_ROOT / "HWRF" / "HWRF_summary"
    files  = _limit(_list_local(folder, r'hwrf\.\d+rainfall\.csv'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(hwrf_get_ts(fname))
        if _in_history(conn, "summary_hwrf", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding="utf-8")
        _insert(conn, "stage_hwrf", hwrf_extract(content, hwrf_get_ts(fname)), fname)

    # ── DFO ───────────────────────────────────────────────────────────────────
    print("\nDFO")
    folder = DOWNLOADS_ROOT / "DFO" / "DFO_summary"
    files  = _limit(_list_local(folder, r'DFO_\w+\.csv'))
    for fname in files:
        parsed_ts = parse_timestamp_day(dfo_get_ts(fname))
        if _in_history(conn, "summary_dfo", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding="utf-8")
        _insert(conn, "stage_dfo", dfo_extract(content, dfo_get_ts(fname)), fname)

    # ── VIIRS ─────────────────────────────────────────────────────────────────
    print("\nVIIRS")
    folder = DOWNLOADS_ROOT / "VIIRS" / "VIIRS_summary"
    files  = _limit(_list_local(folder, r'VIIRS_Flood_\d+\.csv'))
    for fname in files:
        parsed_ts = parse_timestamp_day(viirs_get_ts(fname))
        if _in_history(conn, "summary_viirs", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding="utf-8")
        _insert(conn, "stage_viirs", viirs_extract(content, viirs_get_ts(fname)), fname)

    # ── GloFAS ────────────────────────────────────────────────────────────────
    print("\nGloFAS")
    folder = DOWNLOADS_ROOT / "GLOFAS"
    gfiles = _limit(list(sorted(_glofas_local_files(folder).items())))
    for ts, fname in gfiles:
        parsed_ts = parse_timestamp_hh(ts)
        if _in_history(conn, "summary_glofas", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        local_resp = _LocalFile(folder / fname)
        props = parse_geojson(local_resp) if fname.endswith(".geojson") else glofas_parse_csv(local_resp)
        _insert(conn, "stage_glofas", glofas_build(props, ts), fname)

    # ── Final Alert ───────────────────────────────────────────────────────────
    print("\nFinal Alert")
    folder = DOWNLOADS_ROOT / "Final_Alert"
    files  = _limit(_list_local(folder, r'Final_Attributes_[^/]+\.csv'))
    for fname in files:
        parsed_ts = parse_timestamp_hh(fa_get_ts(fname))
        if _in_history(conn, "summary_final_alert", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding="utf-8", errors="ignore")
        _insert(conn, "stage_final_alert", fa_extract(content, fa_get_ts(fname)), fname)

finally:
    conn.close()

print("\nDone.")
