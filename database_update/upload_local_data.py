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
import argparse
from pathlib import Path
from datetime import datetime, timezone

import psycopg2

from db_upload_utils.db_utils import (
    DB_PARAMS, parse_timestamp_hh, parse_timestamp_day, upsert_dataframe,
    load_failed_log, _in_history,
)
from db_upload_utils.update_db_gfms        import get_timestamp as gfms_get_ts,  extract_df as gfms_extract, _count_nonzero_gfms
from db_upload_utils.update_db_hwrf        import get_timestamp as hwrf_get_ts,  extract_df as hwrf_extract
from db_upload_utils.update_db_dfo         import get_timestamp as dfo_get_ts,   extract_df as dfo_extract, _count_nonzero_dfo
from db_upload_utils.update_db_viirs       import get_timestamp as viirs_get_ts, extract_df as viirs_extract
from db_upload_utils.update_db_final_alert import get_timestamp as fa_get_ts,    extract_df as fa_extract
from db_upload_utils.update_db_glofas      import parse_geojson, parse_csv as glofas_parse_csv, build_df as glofas_build
from db_upload_utils.update_db_mom import (
    get_timestamp_gfms        as mom_gfms_get_ts,        extract_df_gfms  as mom_gfms_extract,
    get_timestamp_hwrf        as mom_hwrf_get_ts,        extract_df_hwrf  as mom_hwrf_extract,
    get_timestamp_dfo         as mom_dfo_get_ts,         extract_df_dfo   as mom_dfo_extract,
    get_timestamp_viirs       as mom_viirs_get_ts,       extract_df_viirs as mom_viirs_extract,
    get_timestamp_final_gfms  as mom_final_gfms_get_ts,
    get_timestamp_final_hwrf  as mom_final_hwrf_get_ts,
    get_timestamp_final_dfo   as mom_final_dfo_get_ts,
    get_timestamp_final_viirs as mom_final_viirs_get_ts,
)

# =============================================================================
# Configuration
# =============================================================================

FILES_PER_SOURCE = None   # set to None to process all files

parser = argparse.ArgumentParser(description="Upload local flood data to database")
parser.add_argument("-d", "--date", default="20260417",
                    help="Minimum upload date (yyyymmdd format, default: 20260410)")
args = parser.parse_args()
UPLOAD_DATE_MIN = args.date

# =============================================================================

DOWNLOADS_ROOT = Path(__file__).parent / "downloads_mom" if not Path("/mnt").exists() else Path("/mnt/volume_ams3_02/downloads_mom")

# Load failed.txt once; _process_source checks it per file.
_failed = load_failed_log()
if _failed:
    print(f"Note: {len(_failed)} incomplete upload(s) in failed.txt — will re-attempt.")


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


def _csv_files(pattern, get_ts):
    """Returns a file-lister: folder -> sorted [(ts, fname)] pairs."""
    def lister(folder):
        return [(get_ts(f), f) for f in _list_local(folder, pattern) if get_ts(f)]
    return lister


def _text_ext(fn, encoding="utf-8", errors="strict"):
    """Wraps a text-based extract function into a path-based extractor."""
    def extractor(path, ts):
        return fn(path.read_text(encoding=encoding, errors=errors), ts)
    return extractor


def _glofas_files(folder):
    return sorted(_glofas_local_files(folder).items())


class _LocalFile:
    """Thin wrapper that mimics the requests.Response interface for local files."""

    def __init__(self, path: Path):
        self.content = path.read_bytes()

    def json(self):
        import json
        return json.loads(self.content)


def _glofas_ext(path, ts):
    resp = _LocalFile(path)
    props = parse_geojson(resp) if path.suffix == '.geojson' else glofas_parse_csv(resp)
    return glofas_build(props, ts)


# Each entry: (label, log_name, folder, file_lister, parse_ts,
#              history_table, stage_table, extract_fn, count_fn, always_upload)
# file_lister:   callable(folder) -> [(ts_str, fname)]
# extract_fn:    callable(path, ts) -> DataFrame
# count_fn:      callable(df) -> int; defaults to len.
#                Pass a custom function for sources where the history trigger
#                filters rows (GFMS, DFO).
# always_upload: if True, skip the history row-count check and always upload.
#                Used for Final_Attributes files (Phase 2 COALESCE enrichment)
#                where the row count is unchanged but score columns are filled in.
SOURCES = [
    ("GFMS",        "gfms_summary",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_summary",
     _csv_files(r'Flood_byStor_\d+\.csv', gfms_get_ts),
     parse_timestamp_hh,
     "summary_gfms", "stage_gfms",
     _text_ext(gfms_extract), _count_nonzero_gfms, False),

    ("HWRF",        "hwrf_summary",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_summary",
     _csv_files(r'hwrf\.\d+rainfall\.csv', hwrf_get_ts),
     parse_timestamp_hh,
     "summary_hwrf", "stage_hwrf",
     _text_ext(hwrf_extract), len, False),

    ("DFO",         "dfo_summary",
     DOWNLOADS_ROOT / "DFO" / "DFO_summary",
     _csv_files(r'DFO_\w+\.csv', dfo_get_ts),
     parse_timestamp_day,
     "summary_dfo", "stage_dfo",
     _text_ext(dfo_extract), _count_nonzero_dfo, False),

    ("VIIRS",       "viirs_summary",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_summary",
     _csv_files(r'VIIRS_Flood_\d+\.csv', viirs_get_ts),
     parse_timestamp_day,
     "summary_viirs", "stage_viirs",
     _text_ext(viirs_extract), len, False),

    ("Final Alert", "final_alert",
     DOWNLOADS_ROOT / "Final_Alert",
     _csv_files(r'Final_Attributes_[^/]+\.csv', fa_get_ts),
     parse_timestamp_hh,
     "summary_final_alert", "stage_final_alert",
     _text_ext(fa_extract, errors="ignore"), len, False),

    # Phase 1: Attributes_Clean — base watershed data (score columns may be absent)
    ("MoM GFMS",        "mom_gfms",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_MoM",
     _csv_files(r'Attributes_Clean_\d{8}\.csv', mom_gfms_get_ts),
     parse_timestamp_day,
     "mom_gfms", "stage_mom_gfms",
     _text_ext(mom_gfms_extract, errors="ignore"), len, False),

    # Phase 2: Final_Attributes — enriched output with computed scores (COALESCE update)
    ("MoM GFMS Final",  "mom_gfms_final",
     DOWNLOADS_ROOT / "GFMS" / "GFMS_MoM",
     _csv_files(r'Final_Attributes_\d{8}\.csv', mom_final_gfms_get_ts),
     parse_timestamp_day,
     "mom_gfms", "stage_mom_gfms",
     _text_ext(mom_gfms_extract, errors="ignore"), len, True),

    # Phase 1: Attributes_Clean
    ("MoM HWRF",        "mom_hwrf",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_MoM",
     _csv_files(r'Attributes_Clean_\d{10}HWRFUpdated\.csv', mom_hwrf_get_ts),
     parse_timestamp_hh,
     "mom_hwrf", "stage_mom_hwrf",
     _text_ext(mom_hwrf_extract, errors="ignore"), len, False),

    # Phase 2: Final_Attributes
    ("MoM HWRF Final",  "mom_hwrf_final",
     DOWNLOADS_ROOT / "HWRF" / "HWRF_MoM",
     _csv_files(r'Final_Attributes_\d{10}HWRFUpdated\.csv', mom_final_hwrf_get_ts),
     parse_timestamp_hh,
     "mom_hwrf", "stage_mom_hwrf",
     _text_ext(mom_hwrf_extract, errors="ignore"), len, True),

    # Phase 1: Attributes_Clean
    ("MoM DFO",         "mom_dfo",
     DOWNLOADS_ROOT / "DFO" / "DFO_MoM",
     _csv_files(r'Attributes_Clean_\d{10}MOM\+DFOUpdated\.csv', mom_dfo_get_ts),
     parse_timestamp_hh,
     "mom_dfo", "stage_mom_dfo",
     _text_ext(mom_dfo_extract, errors="ignore"), len, False),

    # Phase 2: Final_Attributes
    ("MoM DFO Final",   "mom_dfo_final",
     DOWNLOADS_ROOT / "DFO" / "DFO_MoM",
     _csv_files(r'Final_Attributes_\d{10}MOM\+DFOUpdated\.csv', mom_final_dfo_get_ts),
     parse_timestamp_hh,
     "mom_dfo", "stage_mom_dfo",
     _text_ext(mom_dfo_extract, errors="ignore"), len, True),

    # Phase 1: Attributes_Clean
    ("MoM VIIRS",       "mom_viirs",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_MoM",
     _csv_files(r'Attributes_[Cc]lean_\d{10}MOM\+DFO\+VIIRSUpdated\.csv', mom_viirs_get_ts),
     parse_timestamp_hh,
     "mom_viirs", "stage_mom_viirs",
     _text_ext(mom_viirs_extract, errors="ignore"), len, False),

    # Phase 2: Final_Attributes
    ("MoM VIIRS Final", "mom_viirs_final",
     DOWNLOADS_ROOT / "VIIRS" / "VIIRS_MoM",
     _csv_files(r'Final_Attributes_\d{10}MOM\+DFO\+VIIRSUpdated\.csv', mom_final_viirs_get_ts),
     parse_timestamp_hh,
     "mom_viirs", "stage_mom_viirs",
     _text_ext(mom_viirs_extract, errors="ignore"), len, True),

    ("GloFAS",          "glofas",
     DOWNLOADS_ROOT / "GLOFAS",
     _glofas_files,
     parse_timestamp_hh,
     "summary_glofas", "stage_glofas",
     _glofas_ext, len, False),
]


def _insert(conn, stage_table, df, fname, failed_key=None, expected_rows=None, history_table_name=""):
    try:
        upsert_dataframe(stage_table, df, conn=conn, failed_key=failed_key, expected_rows=expected_rows)
        print(f"  {history_table_name} - {fname}: inserted {len(df)} rows")
    except Exception as exc:
        conn.rollback()
        print(f"  {fname}: FAILED — {exc}")


def _process_source(conn, label, log_name, folder, file_lister, parse_ts,
                    history_table, stage_table, extract_fn, count_fn=len,
                    always_upload=False):
    print(f"\n{label}")
    #if label in ["GFMS","HWRF","DFO","VIIRS"]:
    #    return
    count = 0
    start_date = datetime(
        int(UPLOAD_DATE_MIN[0:4]),
        int(UPLOAD_DATE_MIN[4:6]),
        int(UPLOAD_DATE_MIN[6:8])
    ).replace(tzinfo=timezone.utc)

    for ts, fname in [f for f in file_lister(folder)]:
        parsed_ts = parse_ts(ts)
        if parsed_ts < start_date:
            continue

        failed_key = f"{ts}_{log_name}"
        df = extract_fn(folder / fname, ts)
        if failed_key in _failed:
            print(f"  {fname}: previously incomplete, re-attempting")
        elif not always_upload:
            success, actual_count = _in_history(conn, history_table, parsed_ts, count_fn(df))
            if success:
                print(f"{history_table} - {fname}: {actual_count}/{count_fn(df)}")
                continue
        _insert(conn, stage_table, df, fname, failed_key=failed_key, expected_rows=count_fn(df), history_table_name=history_table)
        count += 1
        if FILES_PER_SOURCE is not None and count >= FILES_PER_SOURCE:
            print(f"  {fname}: reached file limit, skipping remaining files")
            break


conn = psycopg2.connect(**DB_PARAMS)
try:
    for label, log_name, folder, file_lister, parse_ts, hist, stage, extract_fn, count_fn, always_upload in SOURCES:
        try:
            _process_source(conn, label, log_name, folder, file_lister, parse_ts,
                        hist, stage, extract_fn, count_fn=count_fn,
                        always_upload=always_upload)
        except psycopg2.InterfaceError:
            conn = psycopg2.connect(**DB_PARAMS)
            _process_source(conn, label, log_name, folder, file_lister, parse_ts,
                        hist, stage, extract_fn, count_fn=count_fn,
                        always_upload=always_upload)
finally:
    conn.close()

print("\nDone.")
