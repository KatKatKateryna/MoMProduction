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
    load_failed_log,
)
from update_db_gfms        import get_timestamp as gfms_get_ts,  extract_df as gfms_extract, _count_nonzero_gfms
from update_db_hwrf        import get_timestamp as hwrf_get_ts,  extract_df as hwrf_extract
from update_db_dfo         import get_timestamp as dfo_get_ts,   extract_df as dfo_extract, _count_nonzero_dfo
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

# Load failed.txt once; _process_source checks it per file.
_failed = load_failed_log()
if _failed:
    print(f"Note: {len(_failed)} incomplete upload(s) in failed.txt — will re-attempt.")


# Each entry: (label, log_name, subfolder, file_pattern, get_ts, parse_ts,
#              history_table, stage_table, extract, encoding, errors, count_fn)
# count_fn defaults to len; GFMS and DFO use custom functions that mirror
# their history trigger's zero-row filter.
SOURCES = [
    ("GFMS",        "gfms_summary",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_summary",
     r'Flood_byStor_\d+\.csv',
     gfms_get_ts, parse_timestamp_hh,
     "summary_gfms", "stage_gfms", gfms_extract, "utf-8", "strict", _count_nonzero_gfms),

    ("HWRF",        "hwrf_summary",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_summary",
     r'hwrf\.\d+rainfall\.csv',
     hwrf_get_ts, parse_timestamp_hh,
     "summary_hwrf", "stage_hwrf", hwrf_extract, "utf-8", "strict", len),

    ("DFO",         "dfo_summary",
     DOWNLOADS_ROOT / "DFO" / "DFO_summary",
     r'DFO_\w+\.csv',
     dfo_get_ts, parse_timestamp_day,
     "summary_dfo", "stage_dfo", dfo_extract, "utf-8", "strict", _count_nonzero_dfo),

    ("VIIRS",       "viirs_summary",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_summary",
     r'VIIRS_Flood_\d+\.csv',
     viirs_get_ts, parse_timestamp_day,
     "summary_viirs", "stage_viirs", viirs_extract, "utf-8", "strict", len),

    ("Final Alert", "final_alert",
     DOWNLOADS_ROOT / "Final_Alert",
     r'Final_Attributes_[^/]+\.csv',
     fa_get_ts, parse_timestamp_hh,
     "summary_final_alert", "stage_final_alert", fa_extract, "utf-8", "ignore", len),

    ("MoM GFMS",   "mom_gfms",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_MoM",
     r'Attributes_Clean_\d{8}\.csv',
     mom_gfms_get_ts, parse_timestamp_day,
     "summary_mom_gfms", "stage_mom_gfms", mom_gfms_extract, "utf-8", "ignore", len),

    ("MoM HWRF",   "mom_hwrf",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_MoM",
     r'Attributes_Clean_\d{10}HWRFUpdated\.csv',
     mom_hwrf_get_ts, parse_timestamp_hh,
     "summary_mom_hwrf", "stage_mom_hwrf", mom_hwrf_extract, "utf-8", "ignore", len),

    ("MoM DFO",    "mom_dfo",
     DOWNLOADS_ROOT / "DFO" / "DFO_MoM",
     r'Attributes_Clean_\d{10}MOM\+DFOUpdated\.csv',
     mom_dfo_get_ts, parse_timestamp_hh,
     "summary_mom_dfo", "stage_mom_dfo", mom_dfo_extract, "utf-8", "ignore", len),

    ("MoM VIIRS",  "mom_viirs",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_MoM",
     r'Attributes_[Cc]lean_\d{10}MOM\+DFO\+VIIRSUpdated\.csv',
     mom_viirs_get_ts, parse_timestamp_hh,
     "summary_mom_viirs", "stage_mom_viirs", mom_viirs_extract, "utf-8", "ignore", len),
]


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


def _in_history(conn, table, parsed_ts, expected_count):
    """Return (True, actual_count) if the history table has exactly expected_count rows for this timestamp, else (False, actual_count)."""
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT COUNT(*) FROM {table} WHERE "timestamp" = %s',
            (parsed_ts,)
        )
        actual_count = cur.fetchone()[0]
        return actual_count == expected_count, actual_count


def _insert(conn, stage_table, df, fname, failed_key=None):
    try:
        upsert_dataframe(stage_table, df, conn=conn, failed_key=failed_key)
        print(f"  {fname}: inserted {len(df)} rows")
    except Exception as exc:
        conn.rollback()
        print(f"  {fname}: FAILED — {exc}")


def _process_source(conn, label, log_name, folder, pattern, get_ts, parse_ts,
                    history_table, stage_table, extract,
                    encoding="utf-8", errors="strict", count_fn=len):
    """Process all local CSV files for one source, skipping already-loaded timestamps.

    The file is read first so its row count can be compared against the history
    table.  A timestamp is only skipped if the DB count matches exactly.
    Entries in failed.txt are always re-attempted regardless of the DB count.

    log_name:  short source identifier used in failed.txt (e.g. "dfo_summary").
               The full key written to failed.txt is "{ts}_{log_name}".
    count_fn:  callable(df) → int giving the expected number of rows in the
               history table.  Defaults to len(df).  Pass a custom function for
               sources where the history trigger filters rows (GFMS, DFO).
    """
    print(f"\n{label}")
    count = 0
    for fname in _list_local(folder, pattern):
        ts = get_ts(fname)
        if not ts:
            continue
        parsed_ts = parse_ts(ts)
        failed_key = f"{ts}_{log_name}"
        content = (folder / fname).read_text(encoding=encoding, errors=errors)
        df = extract(content, ts)
        if failed_key in _failed:
            print(f"  {fname}: previously incomplete, re-attempting")
        else:
            success, actual_count = _in_history(conn, history_table, parsed_ts, count_fn(df))
            if success:
                print(f"{history_table}: {actual_count}/{count_fn(df)}")
                continue
        _insert(conn, stage_table, df, fname, failed_key=failed_key)
        count += 1
        if FILES_PER_SOURCE is not None and count >= FILES_PER_SOURCE:
            print(f"  {fname}: reached file limit, skipping remaining files")
            break


conn = psycopg2.connect(**DB_PARAMS)
try:
    for label, log_name, folder, pattern, get_ts, parse_ts, hist, stage, extract, enc, err, count_fn in SOURCES:
        _process_source(conn, label, log_name, folder, pattern, get_ts, parse_ts,
                        hist, stage, extract, encoding=enc, errors=err, count_fn=count_fn)

    # ── GloFAS ────────────────────────────────────────────────────────────────
    # Handled separately: file listing and parsing differ from the CSV sources.
    print("\nGloFAS")
    count = 0
    folder = DOWNLOADS_ROOT / "GLOFAS"
    for ts, fname in sorted(_glofas_local_files(folder).items()):
        parsed_ts = parse_timestamp_hh(ts)
        local_resp = _LocalFile(folder / fname)
        props = parse_geojson(local_resp) if fname.endswith(".geojson") else glofas_parse_csv(local_resp)
        df = glofas_build(props, ts)
        failed_key = f"{ts}_glofas"
        if failed_key in _failed:
            print(f"  {fname}: previously incomplete, re-attempting")
        else:
            success, actual_count = _in_history(conn, "summary_glofas", parsed_ts, len(df))
            if success:
                print(f"summary_glofas: {actual_count}/{len(df)}")
                continue
        _insert(conn, "stage_glofas", df, fname, failed_key=failed_key)
        count += 1
        if FILES_PER_SOURCE is not None and count >= FILES_PER_SOURCE:
            print(f"  {fname}: reached file limit, skipping remaining files")
            break

finally:
    conn.close()

print("\nDone.")
