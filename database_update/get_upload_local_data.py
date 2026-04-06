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
from update_db_mom import (
    get_timestamp_gfms  as mom_gfms_get_ts,  extract_df_gfms  as mom_gfms_extract,
    get_timestamp_hwrf  as mom_hwrf_get_ts,  extract_df_hwrf  as mom_hwrf_extract,
    get_timestamp_dfo   as mom_dfo_get_ts,   extract_df_dfo   as mom_dfo_extract,
    get_timestamp_viirs as mom_viirs_get_ts, extract_df_viirs as mom_viirs_extract,
)

# =============================================================================
# Configuration
# =============================================================================

FILES_PER_SOURCE = None   # set to None to process all files

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


def _process_source(conn, label, folder, pattern, get_ts, parse_ts,
                    history_table, stage_table, extract,
                    encoding="utf-8", errors="strict"):
    """Process all local CSV files for one source, skipping already-loaded timestamps."""
    print(f"\n{label}")
    count = 0
    for fname in _list_local(folder, pattern):
        ts = get_ts(fname)
        if not ts:
            continue
        parsed_ts = parse_ts(ts)
        if _in_history(conn, history_table, parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        content = (folder / fname).read_text(encoding=encoding, errors=errors)
        _insert(conn, stage_table, extract(content, ts), fname)
        count += 1
        if FILES_PER_SOURCE is not None and count >= FILES_PER_SOURCE:
            print(f"  {fname}: reached file limit, skipping remaining files")
            break


# Each entry: (label, subfolder, file_pattern, get_ts, parse_ts, history_table, stage_table, extract, encoding, errors)
SOURCES = [
    ("GFMS",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_summary",
     r'Flood_byStor_\d+\.csv',
     gfms_get_ts, parse_timestamp_hh,
     "summary_gfms", "stage_gfms", gfms_extract, "utf-8", "strict"),

    ("HWRF",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_summary",
     r'hwrf\.\d+rainfall\.csv',
     hwrf_get_ts, parse_timestamp_hh,
     "summary_hwrf", "stage_hwrf", hwrf_extract, "utf-8", "strict"),

    ("DFO",
     DOWNLOADS_ROOT / "DFO" / "DFO_summary",
     r'DFO_\w+\.csv',
     dfo_get_ts, parse_timestamp_day,
     "summary_dfo", "stage_dfo", dfo_extract, "utf-8", "strict"),

    ("VIIRS",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_summary",
     r'VIIRS_Flood_\d+\.csv',
     viirs_get_ts, parse_timestamp_day,
     "summary_viirs", "stage_viirs", viirs_extract, "utf-8", "strict"),

    ("Final Alert",
     DOWNLOADS_ROOT / "Final_Alert",
     r'Final_Attributes_[^/]+\.csv',
     fa_get_ts, parse_timestamp_hh,
     "summary_final_alert", "stage_final_alert", fa_extract, "utf-8", "ignore"),

    ("MoM GFMS",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_MoM",
     r'Attributes_Clean_\d{8}\.csv',
     mom_gfms_get_ts, parse_timestamp_day,
     "summary_mom_gfms", "stage_mom_gfms", mom_gfms_extract, "utf-8", "ignore"),

    ("MoM HWRF",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_MoM",
     r'Attributes_Clean_\d{10}HWRFUpdated\.csv',
     mom_hwrf_get_ts, parse_timestamp_hh,
     "summary_mom_hwrf", "stage_mom_hwrf", mom_hwrf_extract, "utf-8", "ignore"),

    ("MoM DFO",
     DOWNLOADS_ROOT / "DFO" / "DFO_MoM",
     r'Attributes_Clean_\d{10}MOM\+DFOUpdated\.csv',
     mom_dfo_get_ts, parse_timestamp_hh,
     "summary_mom_dfo", "stage_mom_dfo", mom_dfo_extract, "utf-8", "ignore"),

    ("MoM VIIRS",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_MoM",
     r'Attributes_[Cc]lean_\d{10}MOM\+DFO\+VIIRSUpdated\.csv',
     mom_viirs_get_ts, parse_timestamp_hh,
     "summary_mom_viirs", "stage_mom_viirs", mom_viirs_extract, "utf-8", "ignore"),
]


conn = psycopg2.connect(**DB_PARAMS)
try:
    for label, folder, pattern, get_ts, parse_ts, hist, stage, extract, enc, err in SOURCES:
        _process_source(conn, label, folder, pattern, get_ts, parse_ts,
                        hist, stage, extract, encoding=enc, errors=err)

    # ── GloFAS ────────────────────────────────────────────────────────────────
    # Handled separately: file listing and parsing differ from the CSV sources.
    print("\nGloFAS")
    count = 0
    folder = DOWNLOADS_ROOT / "GLOFAS"
    for ts, fname in sorted(_glofas_local_files(folder).items()):
        parsed_ts = parse_timestamp_hh(ts)
        if _in_history(conn, "summary_glofas", parsed_ts):
            print(f"  {fname}: already in DB, skipping")
            continue
        local_resp = _LocalFile(folder / fname)
        props = parse_geojson(local_resp) if fname.endswith(".geojson") else glofas_parse_csv(local_resp)
        _insert(conn, "stage_glofas", glofas_build(props, ts), fname)
        count += 1
        if FILES_PER_SOURCE is not None and count >= FILES_PER_SOURCE:
            print(f"  {fname}: reached file limit, skipping remaining files")
            break

finally:
    conn.close()

print("\nDone.")
