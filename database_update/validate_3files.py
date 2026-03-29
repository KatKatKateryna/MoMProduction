"""
Download first 3 files per source, insert if not already in history,
then cross-check DB against original file for completeness.

Files are only downloaded if the timestamp is not already in history.

Cross-check logic (per DB_DATA_GUIDE and schema):
  - History: for every row in source, verify its primary key (pfaf_id) exists
    in the history table for that timestamp.
  - GFMS/DFO note: only non-zero flood rows go to history; source is already
    filtered to flood rows, so all source pfaf_ids should be present.
  - GloFAS/Final Alert: additionally verify every matching_id_station /
    matching_id_watershed in the history row has an entry in the reference table.
  - Latest (last file per source only): verify each source pfaf_id is present
    in the _latest table.
"""

import csv, io, re
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

SAVE_DIR = Path("downloaded") / datetime.now().strftime("%Y%m%d_%H%M%S")
SAVE_DIR.mkdir(parents=True, exist_ok=True)
print(f"Saving files to: {SAVE_DIR}\n")

# ── Per-source configuration ───────────────────────────────────────────────────
# history_pk:  column in history table used for row-level matching
# latest_pk:   PK of _latest table (pfaf_id for all)
# ref_fk:      column in history table that must resolve to ref_table (GloFAS/FA only)
# ref_table:   reference table containing the canonical matching IDs
# ref_pk:      PK of ref_table

SOURCE_CFG = {
    "GFMS": {
        "history_table": "summary_gfms",
        "latest_table":  "summary_gfms_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        "join_key":      ["pfaf_id"],
    },
    "HWRF": {
        "history_table": "summary_hwrf",
        "latest_table":  "summary_hwrf_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        "join_key":      ["pfaf_id"],
    },
    "DFO": {
        "history_table": "summary_dfo",
        "latest_table":  "summary_dfo_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        "join_key":      ["pfaf_id"],
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
        "latest_pk":     "pfaf_id",
        # matching_id_station is DB-assigned; join on pfaf_id + station "ID" from source
        "join_key":      ["pfaf_id", "ID"],
        "ref_fk":        "matching_id_station",
        "ref_table":     "all_glofas_stations",
        "ref_pk":        "matching_id_station",
    },
    "FinalAlert": {
        "history_table": "summary_final_alert",
        "latest_table":  "summary_final_alert_latest",
        "history_pk":    "pfaf_id",
        "latest_pk":     "pfaf_id",
        # matching_id_watershed is DB-assigned; join on pfaf_id (1:1 per watershed)
        "join_key":      ["pfaf_id"],
        "ref_fk":        "matching_id_watershed",
        "ref_table":     "all_watersheds",
        "ref_pk":        "matching_id_watershed",
    },
}


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
    """Compare column values between source DataFrame and history table rows.

    Joins on cfg['join_key'] columns (never on DB-assigned matching_id_*).
    Numeric columns are compared with relative tolerance 1e-5.
    Text columns require exact match (after stripping whitespace).
    """
    history_table = cfg["history_table"]
    join_key      = cfg.get("join_key", ["pfaf_id"])
    skip_cols     = {"timestamp", "matching_id_station", "matching_id_watershed"}

    # Verify all join key columns exist in source
    missing_jk = [k for k in join_key if k not in src_df.columns]
    if missing_jk:
        print(f"    values   [SKIP] join key column(s) not in source: {missing_jk}")
        return

    # Fetch full history rows for this timestamp
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT * FROM {history_table} WHERE "timestamp" = %s',
            (parsed_ts,),
        )
        db_cols = [d[0] for d in cur.description]
        db_df   = pd.DataFrame(cur.fetchall(), columns=db_cols)

    if db_df.empty:
        return

    # Verify join key columns exist in DB
    missing_jk_db = [k for k in join_key if k not in db_df.columns]
    if missing_jk_db:
        print(f"    values   [SKIP] join key column(s) not in history table: {missing_jk_db}")
        return

    # Coerce join keys to int (pfaf_id, ID are numeric)
    src_work = src_df.copy()
    db_work  = db_df.copy()
    for k in join_key:
        src_work[k] = src_work[k].apply(_to_int_safe)
        db_work[k]  = db_work[k].apply(_to_int_safe)

    merged = pd.merge(
        src_work, db_work,
        on=join_key, how="inner",
        suffixes=("_src", "_db"),
    )
    if merged.empty:
        print(f"    values   [SKIP] no rows matched on join key {join_key}")
        return

    # Determine columns to compare: in source, in DB history, not join keys, not skipped
    check_cols = [
        c for c in src_df.columns
        if c not in skip_cols
        and c not in join_key
        and c in db_df.columns
    ]

    col_issues = {}
    for col in check_cols:
        sc = col + "_src"
        dc = col + "_db"
        if sc not in merged.columns or dc not in merged.columns:
            continue

        sv = merged[sc]
        dv = merged[dc]

        # Attempt vectorized numeric comparison
        sv_num = pd.to_numeric(sv, errors="coerce")
        dv_num = pd.to_numeric(dv, errors="coerce")

        both_numeric = sv_num.notna() & dv_num.notna()
        both_null    = sv.isna() & dv.isna()
        null_mismatch = sv.isna() != dv.isna()

        if both_numeric.any():
            diff      = (sv_num - dv_num).abs()
            scale     = sv_num.abs().clip(lower=1.0)
            val_bad   = both_numeric & (diff / scale > 1e-5)
        else:
            val_bad = pd.Series(False, index=merged.index)

        # Fallback text comparison for non-numeric rows
        text_rows = ~both_numeric & ~both_null & ~null_mismatch
        if text_rows.any():
            sv_s = sv[text_rows].fillna("").astype(str).str.strip()
            dv_s = dv[text_rows].fillna("").astype(str).str.strip()
            text_bad_idx = sv_s[sv_s != dv_s].index
            val_bad = val_bad | merged.index.isin(text_bad_idx)

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


def crosscheck(conn, src_df, parsed_ts, source_name, is_last=False):
    cfg         = SOURCE_CFG[source_name]
    history_pk  = cfg["history_pk"]
    latest_pk   = cfg["latest_pk"]

    # Parse source IDs (pfaf_id) — handles string, float, or int values
    src_ids = set(int(float(v)) for v in src_df["pfaf_id"] if v not in (None, ""))

    # ── History check (row presence) ─────────────────────────────────────────
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT "{history_pk}" FROM {cfg["history_table"]} WHERE "timestamp" = %s',
            (parsed_ts,),
        )
        hist_ids = {row[0] for row in cur.fetchall()}

    missing = src_ids - hist_ids
    status  = "OK" if not missing else "ISSUES"
    print(f"    history  [{status}] src={len(src_ids)} db={len(hist_ids)} missing={len(missing)}")
    if missing:
        sample = sorted(missing)[:10]
        print(f"      missing pfaf_ids: {sample}{'...' if len(missing) > 10 else ''}")

    # ── Column-value verification ─────────────────────────────────────────────
    _crosscheck_values(conn, src_df, parsed_ts, cfg)

    # ── Reference integrity check (GloFAS / Final Alert only) ─────────────────
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

        orphans    = matching_ids - ref_ids
        ref_status = "OK" if not orphans else "ISSUES"
        print(f"    ref      [{ref_status}] {len(matching_ids)} {ref_fk} values in history, "
              f"{len(orphans)} without match in {ref_table}")
        if orphans:
            print(f"      orphan {ref_fk}s: {sorted(orphans)[:10]}{'...' if len(orphans) > 10 else ''}")

    # ── Latest check (last file per source only) ──────────────────────────────
    if is_last:
        with conn.cursor() as cur:
            cur.execute(f'SELECT "{latest_pk}" FROM {cfg["latest_table"]}')
            latest_ids = {row[0] for row in cur.fetchall()}

        missing_l  = src_ids - latest_ids
        status_l   = "OK" if not missing_l else "ISSUES"
        print(f"    latest   [{status_l}] src={len(src_ids)} db={len(latest_ids)} missing={len(missing_l)}")
        if missing_l:
            sample = sorted(missing_l)[:10]
            print(f"      missing pfaf_ids: {sample}{'...' if len(missing_l) > 10 else ''}")


conn = psycopg2.connect(**DB_PARAMS)
try:
    # ── GFMS ──────────────────────────────────────────────────────────────────
    print("=" * 60)
    print("GFMS")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GFMS/GFMS_summary/"
    files = list_server_files(BASE, r'href="(Flood_byStor_\d+\.csv)"')[:3]
    for i, fname in enumerate(files):
        ts        = gfms_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_gfms", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        content = download_text(BASE + fname)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = gfms_extract(content, ts)
        try:
            upsert_dataframe("stage_gfms", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "GFMS", is_last=(i == len(files) - 1))

    # ── HWRF ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("HWRF")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/HWRF/HWRF_summary/"
    files = list_server_files(BASE, r'href="(hwrf\.\d+rainfall\.csv)"')[:3]
    for i, fname in enumerate(files):
        ts        = hwrf_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_hwrf", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        content = download_text(BASE + fname)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = hwrf_extract(content, ts)
        try:
            upsert_dataframe("stage_hwrf", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "HWRF", is_last=(i == len(files) - 1))

    # ── DFO ───────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("DFO")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
    files = list_server_files(BASE, r'href="(DFO_\w+\.csv)"')[:3]
    for i, fname in enumerate(files):
        ts        = dfo_get_ts(fname)
        parsed_ts = parse_timestamp_day(ts)
        if already_in_history(conn, "summary_dfo", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        content = download_text(BASE + fname)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = dfo_extract(content, ts)
        try:
            upsert_dataframe("stage_dfo", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "DFO", is_last=(i == len(files) - 1))

    # ── VIIRS ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("VIIRS")
    BASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/VIIRS/VIIRS_summary/"
    files = list_server_files(BASE, r'href="(VIIRS_Flood_\d+\.csv)"')[:3]
    for i, fname in enumerate(files):
        ts        = viirs_get_ts(fname)
        parsed_ts = parse_timestamp_day(ts)
        if already_in_history(conn, "summary_viirs", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        content = download_text(BASE + fname)
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = viirs_extract(content, ts)
        try:
            upsert_dataframe("stage_viirs", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "VIIRS", is_last=(i == len(files) - 1))

    # ── GloFAS ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("GloFAS")
    GBASE  = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/GLOFAS/"
    gfiles = list(sorted(glofas_list().items()))[:3]
    for i, (ts, fname) in enumerate(gfiles):
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_glofas", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        fmt   = "geojson" if fname.endswith(".geojson") else "csv"
        resp  = download_resp(GBASE + fname)
        (SAVE_DIR / fname).write_bytes(resp.content)
        props = parse_geojson(resp) if fmt == "geojson" else glofas_parse_csv(resp)
        df    = glofas_build(props, ts)
        try:
            upsert_dataframe("stage_glofas", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "GloFAS", is_last=(i == len(gfiles) - 1))

    # ── Final Alert ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Final Alert")
    BASE   = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/Final_Alert/"
    files  = list_server_files(BASE, r'href="(Final_Attributes_[^"]+\.csv)"')[:3]
    for i, fname in enumerate(files):
        ts        = fa_get_ts(fname)
        parsed_ts = parse_timestamp_hh(ts)
        if already_in_history(conn, "summary_final_alert", parsed_ts):
            print(f"  {fname}: already in history, skipping download")
            continue
        content = download_text(BASE + fname, errors="ignore")
        (SAVE_DIR / fname).write_text(content, encoding="utf-8")
        df = fa_extract(content, ts)
        try:
            upsert_dataframe("stage_final_alert", df, conn=conn)
            print(f"  {fname}: inserted {len(df)} rows")
        except Exception as exc:
            conn.rollback()
            print(f"  {fname}: FAILED {exc}")
            continue
        crosscheck(conn, df, parsed_ts, "FinalAlert", is_last=(i == len(files) - 1))

finally:
    conn.close()

print(f"\nFiles saved to: {SAVE_DIR}")
