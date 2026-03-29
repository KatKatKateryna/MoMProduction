"""
validate_files.py
=================
Downloads FILES_PER_SOURCE files per data source (or all files if set to None),
inserts each into the database via the staging pipeline, then cross-checks the
DB against the original source file.

A file is skipped (not re-downloaded or re-inserted) if its timestamp is already
present in the corresponding history table.  This makes the script safe to re-run.

DOWNLOAD_DELAY seconds are waited between consecutive file downloads to avoid
overwhelming the remote server.  Directory listing requests are not rate-limited.

Console output  — verbose: counts, sample pfaf_ids, value-mismatch details,
                  causes for unexpected missing rows.
Report file     — compact: one fixed-width row per processed file, appended to
                  validation_report.txt in the same folder as this script.
                  Header is written once when the file is first created.

Report columns
--------------
RUN_TS      Timestamp of this validation run (YYYYMMDD_HHMMSS).
SOURCE      Data source name (GFMS, HWRF, DFO, VIIRS, GloFAS, FinalAlert).
FILE        Source filename (truncated to 42 chars).
SRC         Row count in the source file.
HIST        Row count found in the history table for that timestamp.
ZFILT       Rows absent from history because all flood values are zero/null
            (expected behaviour for GFMS and DFO).  "-" for other sources.
UNEXP       Rows with non-zero values absent from history (should be 0).
VALUES      OK / ISSUES / SKIP — column-value comparison result.
REF         OK / ISSUES / "-" — reference-table integrity (GloFAS/FinalAlert only).
LATEST      OK / ISSUES / "-" — _latest table check (last file per source only).
INSERT      OK / FAILED        — whether the staging insert succeeded.

Cross-check logic
-----------------
1. History — row presence
   Every pfaf_id in the source file is looked up in the history table for that
   timestamp.  Missing rows are categorised:
   zero-filtered (OK)   — all flood-value columns are zero/null; the history
                          trigger intentionally discards these (GFMS, DFO only).
   unexpected (ISSUES)  — non-zero values absent from history; indicates a data
                          integrity problem.  Possible causes are printed to console.

2. History — column values
   For every row present in history the column values are compared to the source.
   Numeric: relative tolerance 1e-5.  Text: exact match after stripping whitespace.

3. Reference integrity  (GloFAS and Final Alert only)
   Every matching_id_station / matching_id_watershed in history must resolve to
   all_glofas_stations / all_watersheds respectively.

4. Latest — row presence  (last file per source only)
   Every pfaf_id in the source must be present in the _latest table.
"""

import csv, io, re, sys, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2

from db_utils import (
    DB_PARAMS, download_text, download_resp, get_processed_timestamps,
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

# Number of files to validate per source.  Set to None to process all files.
FILES_PER_SOURCE = 3

# Seconds to wait between consecutive file downloads to avoid overwhelming
# the server.  Applied after every download; directory listings are exempt.
DOWNLOAD_DELAY = 0.5

# =============================================================================

_RUN_TS     = datetime.now().strftime("%Y%m%d_%H%M%S")
SAVE_DIR    = Path("downloaded") / _RUN_TS
REPORT_FILE = Path(__file__).parent / "validation_report.txt"
LOG_FILE    = Path(__file__).parent / "validation_log.txt"

SAVE_DIR.mkdir(parents=True, exist_ok=True)


class _Tee:
    """Write to both the original stream and a log file."""
    def __init__(self, stream, log_path):
        self._stream = stream
        self._log    = open(log_path, "a", encoding="utf-8", buffering=1)
        self._log.write(f"\n{'=' * 70}\nRUN {_RUN_TS}\n{'=' * 70}\n")

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def close(self):
        self._log.close()

    # Pass through anything else (e.g. fileno) to the real stream.
    def __getattr__(self, name):
        return getattr(self._stream, name)


_tee    = _Tee(sys.stdout, LOG_FILE)
sys.stdout = _tee

# ── Report file — fixed-width column layout ───────────────────────────────────
_COL_WIDTHS = {
    "RUN_TS":  15, "SOURCE": 12, "FILE":   42,
    "SRC":      6, "HIST":    6, "ZFILT":   6, "UNEXP":  6,
    "VALUES":   8, "REF":     8, "LATEST":  8, "INSERT": 8,
}
_HEADER = "  ".join(k.ljust(w) for k, w in _COL_WIDTHS.items())
_SEP    = "  ".join("-" * w for w in _COL_WIDTHS.values())


def _report_open():
    """Open the report file for appending; write header if the file is new."""
    new_file = not REPORT_FILE.exists()
    fh = open(REPORT_FILE, "a", encoding="utf-8", buffering=1)
    if new_file:
        fh.write(_HEADER + "\n")
        fh.write(_SEP    + "\n")
    else:
        fh.write("\n")   # blank line between runs for readability
    return fh


def _fmt(val, width):
    """Right-align numeric values; left-align everything else."""
    s = str(val)
    if s.lstrip("-").isdigit():
        return s.rjust(width)
    return s.ljust(width)


def _write_report_row(fh, source, fname, result):
    """Append one fixed-width row to the open report file handle."""
    row = {
        "RUN_TS":  _RUN_TS,
        "SOURCE":  source,
        "FILE":    (fname[:39] + "...") if len(fname) > 42 else fname,
        "SRC":     result.get("src",    "-"),
        "HIST":    result.get("hist",   "-"),
        "ZFILT":   result.get("zfilt",  "-"),
        "UNEXP":   result.get("unexp",  "-"),
        "VALUES":  result.get("values", "-"),
        "REF":     result.get("ref",    "-"),
        "LATEST":  result.get("latest", "-"),
        "INSERT":  result.get("insert", "-"),
    }
    line = "  ".join(_fmt(row[k], w) for k, w in _COL_WIDTHS.items())
    fh.write(line + "\n")

# ── Per-source configuration ───────────────────────────────────────────────────
# history_table:    history table name (one row per timestamp+pfaf_id)
# latest_table:     _latest table name (one row per pfaf_id, most recent batch)
# history_pk:       primary-key column used for row-presence checks (always pfaf_id)
# latest_pk:        primary-key column of the _latest table (always pfaf_id)
# join_key:         columns used to join source rows to DB rows for value checks.
#                   Must be source-available — never DB-assigned surrogate keys.
# zero_filter_cols: columns that the history trigger uses to decide whether a row
#                   is a "flood row".  A row is zero-filtered (intentionally absent
#                   from history) when ALL of these columns are zero or null.
#                   Absent for sources that write every row (HWRF, VIIRS, GloFAS,
#                   Final Alert).
# ref_fk:           FK column in the history table that references a lookup table
#                   (GloFAS and Final Alert only).
# ref_table:        lookup table that ref_fk must resolve to.
# ref_pk:           primary key of ref_table.

SOURCE_CFG = {
    "GFMS": {
        "history_table":    "summary_gfms",
        "latest_table":     "summary_gfms_latest",
        "history_pk":       "pfaf_id",
        "latest_pk":        "pfaf_id",
        "join_key":         ["pfaf_id"],
        "zero_filter_cols": [
            "GFMS_TotalArea_km", "GFMS_perc_Area",
            "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration",
        ],
    },
    "HWRF": {
        "history_table": "summary_hwrf",
        "latest_table":  "summary_hwrf_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        "join_key":      ["pfaf_id"],
    },
    "DFO": {
        "history_table":    "summary_dfo",
        "latest_table":     "summary_dfo_latest",
        "history_pk":       "pfaf_id",
        "latest_pk":        "pfaf_id",
        "join_key":         ["pfaf_id"],
        "zero_filter_cols": [
            "1-Day_TotalArea_km2", "1-Day_perc_Area",
            "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
            "2-Day_TotalArea_km2", "2-Day_perc_Area",
            "3-Day_TotalArea_km2", "3-Day_perc_Area",
        ],
    },
    "VIIRS": {
        "history_table": "summary_viirs",
        "latest_table":  "summary_viirs_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        "join_key":      ["pfaf_id"],
    },
    "GloFAS": {
        "history_table": "summary_glofas",
        "latest_table":  "summary_glofas_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "matching_id_station",
        "join_key":      ["pfaf_id", "ID"],
        "ref_fk":        "matching_id_station",
        "ref_table":     "all_glofas_stations",
        "ref_pk":        "matching_id_station",
    },
    "FinalAlert": {
        "history_table": "summary_final_alert",
        "latest_table":  "summary_final_alert_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "matching_id_watershed",
        "join_key":      ["pfaf_id", "name", "name_1"],
        "ref_fk":        "matching_id_watershed",
        "ref_table":     "all_watersheds",
        "ref_pk":        "matching_id_watershed",
    },
}

# Printed to console alongside unexpected missing rows.
UNEXPECTED_MISSING_CAUSES = (
    "Possible causes for unexpected missing rows:\n"
    "  1. Timestamp already partially present in history from a prior failed run —\n"
    "     the staging flush timestamp guard discarded the entire batch.\n"
    "  2. pfaf_id not present in watershed_shapes — FK violation prevented the\n"
    "     INSERT into the history table.\n"
    "  3. Trigger error during the history sync (fn_*_sync) — check server logs.\n"
    "  4. upsert_dataframe verification failed and the exception was caught —\n"
    "     the batch may have been rolled back silently."
)


def already_in_history(conn, history_table, parsed_ts):
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT COUNT(*) FROM {history_table} WHERE "timestamp" = %s',
            (parsed_ts,),
        )
        return cur.fetchone()[0] > 0


def _to_int_safe(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _crosscheck_values(conn, src_df, parsed_ts, cfg):
    """Compare column values between source rows and the matching history rows.

    Only rows present in both source and DB are compared (inner join on join_key).
    join_key columns are coerced to int before joining so that "3.0" matches 3.
    Numeric columns: relative tolerance 1e-5 (handles float rounding across
    CSV → Python → Postgres round-trips).
    Text columns: exact match after stripping leading/trailing whitespace.
    timestamp and DB-assigned surrogate-key columns are always skipped.

    Returns "OK", "ISSUES", or "SKIP".
    """
    history_table = cfg["history_table"]
    join_key      = cfg.get("join_key", ["pfaf_id"])
    skip_cols     = {"timestamp", "matching_id_station", "matching_id_watershed"}

    missing_jk = [k for k in join_key if k not in src_df.columns]
    if missing_jk:
        print(f"    values   [SKIP] join key column(s) not in source: {missing_jk}")
        return "SKIP"

    with conn.cursor() as cur:
        cur.execute(
            f'SELECT * FROM {history_table} WHERE "timestamp" = %s',
            (parsed_ts,),
        )
        db_cols = [d[0] for d in cur.description]
        db_df   = pd.DataFrame(cur.fetchall(), columns=db_cols)

    if db_df.empty:
        return "SKIP"

    missing_jk_db = [k for k in join_key if k not in db_df.columns]
    if missing_jk_db:
        print(f"    values   [SKIP] join key column(s) not in history table: {missing_jk_db}")
        return "SKIP"

    src_work = src_df.copy()
    db_work  = db_df.copy()
    for k in join_key:
        if k == "pfaf_id":
            # pfaf_id is always integer — coerce so "3.0" matches 3
            src_work[k] = src_work[k].apply(_to_int_safe)
            db_work[k]  = db_work[k].apply(_to_int_safe)
        else:
            # Text join keys (e.g. "ID"="G0001", "name") — keep as stripped string
            src_work[k] = src_work[k].fillna("").astype(str).str.strip()
            db_work[k]  = db_work[k].fillna("").astype(str).str.strip()

    merged = pd.merge(src_work, db_work, on=join_key, how="inner", suffixes=("_src", "_db"))
    if merged.empty:
        print(f"    values   [SKIP] no rows matched on join key {join_key}")
        return "SKIP"

    check_cols = [
        c for c in src_df.columns
        if c not in skip_cols and c not in join_key and c in db_df.columns
    ]

    col_issues = {}
    for col in check_cols:
        sc = col + "_src"
        dc = col + "_db"
        if sc not in merged.columns or dc not in merged.columns:
            continue

        sv = merged[sc]
        dv = merged[dc]

        sv_num = pd.to_numeric(sv, errors="coerce")
        dv_num = pd.to_numeric(dv, errors="coerce")

        both_numeric  = sv_num.notna() & dv_num.notna()
        both_null     = sv.isna() & dv.isna()
        null_mismatch = sv.isna() != dv.isna()

        if both_numeric.any():
            diff    = (sv_num - dv_num).abs()
            scale   = sv_num.abs().clip(lower=1.0)
            val_bad = both_numeric & (diff / scale > 1e-5)
        else:
            val_bad = pd.Series(False, index=merged.index)

        text_rows = ~both_numeric & ~both_null & ~null_mismatch
        if text_rows.any():
            sv_s = sv[text_rows].fillna("").astype(str).str.strip()
            dv_s = dv[text_rows].fillna("").astype(str).str.strip()
            val_bad = val_bad | merged.index.isin(sv_s[sv_s != dv_s].index)

        n = int((val_bad | null_mismatch).sum())
        if n:
            col_issues[col] = n

    status = "OK" if not col_issues else "ISSUES"
    print(f"    values   [{status}] {len(merged)} rows × {len(check_cols)} columns compared")
    if col_issues:
        shown = sorted(col_issues.items(), key=lambda x: -x[1])[:8]
        for col, n in shown:
            print(f"      '{col}': {n} mismatch(es)")
        if len(col_issues) > 8:
            print(f"      ...and {len(col_issues) - 8} more columns with mismatches")
    return status


def _is_zero_filtered(row, zero_filter_cols):
    """Return True when every zero_filter_col in the row is zero or null.

    A row that satisfies this condition is intentionally excluded from history
    by the flood-row filter in the history sync trigger (GFMS and DFO only).
    Its absence from the history table is expected behaviour, not a bug.
    """
    for col in zero_filter_cols:
        v = row.get(col)
        try:
            if v is not None and v != "" and float(v) != 0.0:
                return False
        except (ValueError, TypeError):
            return False
    return True


def crosscheck(conn, src_df, parsed_ts, source_name, is_last=False):
    """Run all checks for one file.  Prints verbose output; returns a result dict
    suitable for _write_report_row().
    """
    cfg              = SOURCE_CFG[source_name]
    history_pk       = cfg["history_pk"]
    latest_pk        = cfg["latest_pk"]
    zero_filter_cols = cfg.get("zero_filter_cols", [])

    result = {
        "src": 0, "hist": 0, "zfilt": "-", "unexp": 0,
        "values": "-", "ref": "-", "latest": "-", "insert": "OK",
    }

    src_ids = set(int(float(v)) for v in src_df["pfaf_id"] if v not in (None, ""))
    result["src"] = len(src_ids)

    # ── History — row presence ────────────────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT "{history_pk}" FROM {cfg["history_table"]} WHERE "timestamp" = %s',
            (parsed_ts,),
        )
        hist_ids = {row[0] for row in cur.fetchall()}

    result["hist"] = len(hist_ids)
    missing = src_ids - hist_ids

    if not missing:
        print(f"    history  [OK] src={len(src_ids)} db={len(hist_ids)} missing=0")
        if zero_filter_cols:
            result["zfilt"] = 0
        result["unexp"] = 0
    else:
        src_lookup = {
            int(float(row["pfaf_id"])): row
            for _, row in src_df.iterrows()
            if row.get("pfaf_id") not in (None, "")
        }
        zero_filtered = sorted(
            pid for pid in missing
            if _is_zero_filtered(src_lookup.get(pid, {}), zero_filter_cols)
        )
        unexpected = sorted(pid for pid in missing if pid not in zero_filtered)

        if zero_filter_cols:
            result["zfilt"] = len(zero_filtered)
        result["unexp"] = len(unexpected)

        status = "ISSUES" if unexpected else "OK"
        print(f"    history  [{status}] src={len(src_ids)} db={len(hist_ids)} "
              f"missing={len(missing)}")

        if zero_filtered:
            print(f"      zero-filtered (expected — all flood values are zero/null): "
                  f"{len(zero_filtered)} row(s)")
            print(f"        sample: {zero_filtered[:10]}"
                  f"{'...' if len(zero_filtered) > 10 else ''}")

        if unexpected:
            print(f"      UNEXPECTED missing (non-zero values absent from history): "
                  f"{len(unexpected)} row(s)")
            print(f"        sample: {unexpected[:10]}"
                  f"{'...' if len(unexpected) > 10 else ''}")
            for pid in unexpected[:3]:
                row = src_lookup.get(pid, {})
                if zero_filter_cols:
                    vals = {c: row.get(c) for c in zero_filter_cols if c in row}
                    print(f"        pfaf_id={pid} values: {vals}")
            for line in UNEXPECTED_MISSING_CAUSES.splitlines():
                print(f"      {line}")

    # ── History — column values ───────────────────────────────────────────────
    result["values"] = _crosscheck_values(conn, src_df, parsed_ts, cfg)

    # ── Reference integrity (GloFAS / Final Alert only) ───────────────────────
    if "ref_table" in cfg:
        ref_fk    = cfg["ref_fk"]
        ref_table = cfg["ref_table"]
        ref_pk    = cfg["ref_pk"]

        with conn.cursor() as cur:
            cur.execute(
                f'SELECT "{ref_fk}" FROM {cfg["history_table"]} WHERE "timestamp" = %s',
                (parsed_ts,),
            )
            matching_ids = {row[0] for row in cur.fetchall() if row[0] is not None}

        with conn.cursor() as cur:
            cur.execute(f'SELECT "{ref_pk}" FROM {ref_table}')
            ref_ids = {row[0] for row in cur.fetchall()}

        orphans = matching_ids - ref_ids
        ref_st  = "OK" if not orphans else "ISSUES"
        result["ref"] = ref_st
        print(f"    ref      [{ref_st}] {len(matching_ids)} {ref_fk} values, "
              f"{len(orphans)} orphan(s)")
        if orphans:
            print(f"      orphan {ref_fk}s: {sorted(orphans)[:10]}"
                  f"{'...' if len(orphans) > 10 else ''}")

    # ── Latest (last file per source only) ────────────────────────────────────
    if is_last:
        with conn.cursor() as cur:
            cur.execute(f'SELECT "{latest_pk}" FROM {cfg["latest_table"]}')
            latest_ids = {row[0] for row in cur.fetchall()}

        missing_l = src_ids - latest_ids
        lat_st    = "OK" if not missing_l else "ISSUES"
        result["latest"] = lat_st
        print(f"    latest   [{lat_st}] src={len(src_ids)} db={len(latest_ids)} "
              f"missing={len(missing_l)}")
        if missing_l:
            print(f"      missing: {sorted(missing_l)[:10]}"
                  f"{'...' if len(missing_l) > 10 else ''}")

    return result


def _limit(files):
    """Slice a file list to FILES_PER_SOURCE entries.  None means no limit."""
    return files if FILES_PER_SOURCE is None else files[:FILES_PER_SOURCE]


print(f"Report file      : {REPORT_FILE}")
print(f"Saving files to  : {SAVE_DIR}")
print(f"Files per source : {FILES_PER_SOURCE if FILES_PER_SOURCE is not None else 'all'}")
print(f"Download delay   : {DOWNLOAD_DELAY}s\n")

report = _report_open()
conn   = psycopg2.connect(**DB_PARAMS)
try:
    # ── GFMS ──────────────────────────────────────────────────────────────────
    print("=" * 60)
    print("GFMS")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GFMS/GFMS_summary/"
    files = _limit(list_server_files(BASE, r'href="(Flood_byStor_\d+\.csv)"'))
    for i, fname in enumerate(files):
        ts        = gfms_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_gfms", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        content = download_text(BASE + fname)
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = gfms_extract(content, ts)
        result = {"insert": "OK"}
        try:
            upsert_dataframe("stage_gfms", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            result = {"insert": "FAILED"}
            _write_report_row(report, "GFMS", fname, result)
            continue
        result = crosscheck(conn, df, parsed_ts, "GFMS", is_last=(i == len(files) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "GFMS", fname, result)

    # ── HWRF ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("HWRF")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/HWRF/HWRF_summary/"
    files = _limit(list_server_files(BASE, r'href="(hwrf\.\d+rainfall\.csv)"'))
    for i, fname in enumerate(files):
        ts        = hwrf_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_hwrf", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        content = download_text(BASE + fname)
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = hwrf_extract(content, ts)
        try:
            upsert_dataframe("stage_hwrf", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            _write_report_row(report, "HWRF", fname, {"insert": "FAILED"})
            continue
        result = crosscheck(conn, df, parsed_ts, "HWRF", is_last=(i == len(files) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "HWRF", fname, result)

    # ── DFO ───────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DFO")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
    files = _limit(list_server_files(BASE, r'href="(DFO_\w+\.csv)"'))
    for i, fname in enumerate(files):
        ts        = dfo_get_ts(fname)
        parsed_ts = parse_timestamp_day(ts)
        if already_in_history(conn, "summary_dfo", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        content = download_text(BASE + fname)
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = dfo_extract(content, ts)
        try:
            upsert_dataframe("stage_dfo", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            _write_report_row(report, "DFO", fname, {"insert": "FAILED"})
            continue
        result = crosscheck(conn, df, parsed_ts, "DFO", is_last=(i == len(files) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "DFO", fname, result)

    # ── VIIRS ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("VIIRS")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/VIIRS/VIIRS_summary/"
    files = _limit(list_server_files(BASE, r'href="(VIIRS_Flood_\d+\.csv)"'))
    for i, fname in enumerate(files):
        ts        = viirs_get_ts(fname)
        parsed_ts = parse_timestamp_day(ts)
        if already_in_history(conn, "summary_viirs", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        content = download_text(BASE + fname)
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = viirs_extract(content, ts)
        try:
            upsert_dataframe("stage_viirs", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            _write_report_row(report, "VIIRS", fname, {"insert": "FAILED"})
            continue
        result = crosscheck(conn, df, parsed_ts, "VIIRS", is_last=(i == len(files) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "VIIRS", fname, result)

    # ── GloFAS ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("GloFAS")
    GBASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GLOFAS/"
    gfiles = _limit(list(sorted(glofas_list().items())))
    for i, (ts, fname) in enumerate(gfiles):
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_glofas", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        fmt  = "geojson" if fname.endswith(".geojson") else "csv"
        resp = download_resp(GBASE + fname)
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_bytes(resp.content)
        props = parse_geojson(resp) if fmt == "geojson" else glofas_parse_csv(resp)
        df    = glofas_build(props, ts)
        try:
            upsert_dataframe("stage_glofas", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            _write_report_row(report, "GloFAS", fname, {"insert": "FAILED"})
            continue
        result = crosscheck(conn, df, parsed_ts, "GloFAS", is_last=(i == len(gfiles) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "GloFAS", fname, result)

    # ── Final Alert ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Final Alert")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/Final_Alert/"
    files = _limit(list_server_files(BASE, r'href="(Final_Attributes_[^"]+\.csv)"'))
    for i, fname in enumerate(files):
        ts        = fa_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_final_alert", parsed_ts):
            print(f"  {fname}: already in history, skipping")
            continue
        content = download_text(BASE + fname, errors="ignore")
        time.sleep(DOWNLOAD_DELAY)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = fa_extract(content, ts)
        try:
            upsert_dataframe("stage_final_alert", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            _write_report_row(report, "FinalAlert", fname, {"insert": "FAILED"})
            continue
        result = crosscheck(conn, df, parsed_ts, "FinalAlert", is_last=(i == len(files) - 1))
        result["insert"] = "OK"
        _write_report_row(report, "FinalAlert", fname, result)

finally:
    conn.close()
    report.close()

print(f"\nFiles saved to : {SAVE_DIR}")
print(f"Report saved to: {REPORT_FILE}")
print(f"Log saved to   : {LOG_FILE}")

sys.stdout = _tee._stream
_tee.close()
