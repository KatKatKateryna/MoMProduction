"""
Shared utilities for all update_db_* scripts.
"""

import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Failed-upload log
# ---------------------------------------------------------------------------
# failed.txt lives next to this file and tracks uploads that were started but
# not confirmed complete.  Each line is a human-readable key supplied by the
# caller, e.g. "20211215_dfo_summary" or "2021121500_gfms_summary".
#
# Written BEFORE upsert_rows (the commit), removed AFTER the row-count check
# passes.  If the process crashes, or the row-count check fails, the entry
# stays — giving subsequent runs (and operators) visibility into what may be
# incomplete.
# ---------------------------------------------------------------------------

FAILED_LOG = Path(__file__).parent / "failed.txt"


def _failed_log_add(key: str) -> None:
    if FAILED_LOG.exists():
        existing = {l for l in FAILED_LOG.read_text().splitlines() if l.strip()}
        if key in existing:
            return
    else:
        existing = set()
    existing.add(key)
    FAILED_LOG.write_text("\n".join(sorted(existing)) + "\n")


def _failed_log_remove(key: str) -> None:
    if not FAILED_LOG.exists():
        return
    lines = [l for l in FAILED_LOG.read_text().splitlines()
             if l.strip() and l.strip() != key]
    if lines:
        FAILED_LOG.write_text("\n".join(lines) + "\n")
    else:
        FAILED_LOG.unlink(missing_ok=True)


def load_failed_log() -> set:
    """Return the set of keys from failed.txt (one plain string per line)."""
    if not FAILED_LOG.exists():
        return set()
    return {l.strip() for l in FAILED_LOG.read_text().splitlines() if l.strip()}

# ---------------------------------------------------------------------------
# DB connection parameters
# ---------------------------------------------------------------------------

DB_PARAMS = {
    "host":               os.getenv("DB_HOST"),
    "port":               int(os.getenv("DB_PORT", 5432)),
    "dbname":             os.getenv("DB_NAME", "postgres"),
    "user":               os.getenv("DB_USER"),
    "password":           os.getenv("DB_PASSWORD"),
    "sslmode":            os.getenv("DB_SSLMODE", "require"),
    "connect_timeout":    10,
    "keepalives":         1,
    "keepalives_idle":    30,
    "keepalives_interval": 10,
    "keepalives_count":   5,
    "options":            "-c statement_timeout=300000",  # 5 minutes
}

RETRY_ATTEMPTS = 3
RETRY_DELAY    = 5

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def parse_timestamp_hh(ts):
    """Parse a 10-digit YYYYMMDDHH string to a timezone-aware UTC datetime."""
    return datetime.strptime(ts, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def parse_timestamp_day(ts):
    """Parse an 8-digit YYYYMMDD string to a timezone-aware UTC datetime."""
    return datetime.strptime(ts, "%Y%m%d").replace(tzinfo=timezone.utc)


def parse_forecast_date(v):
    """Parse an ISO datetime string (YYYY-MM-DDTHH:MM:SS) to a naive datetime."""
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None

# ---------------------------------------------------------------------------
# Type coercion
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


def to_dec(v, places):
    try:
        return round(Decimal(str(float(v))), places) if v not in (None, "") else None
    except (ValueError, TypeError, InvalidOperation):
        return None

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _in_history(conn, table, parsed_ts, expected_count):
    """Return (True, actual_count) if history has exactly expected_count rows for this timestamp."""
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM {table} WHERE "timestamp" = %s', (parsed_ts,))
        actual_count = cur.fetchone()[0]
    return actual_count == expected_count, actual_count


def get_processed_timestamps(conn, table):
    """Return a set of all timestamps already present in the given table."""
    with conn.cursor() as cur:
        cur.execute(f'SELECT DISTINCT "timestamp" FROM {table}')
        return {row[0] for row in cur.fetchall()}


def upsert_rows(conn, sql, rows):
    """Batch-insert rows using execute_values and commit."""
    import psycopg2.extras
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, rows, page_size=len(rows))
    conn.commit()


def upsert_dataframe(table, df, conn=None, failed_key: str = None, expected_rows: int = None):
    """Insert a DataFrame into a staging table.

    Queries the actual column types from the database and converts every value
    to the exact expected type. Columns in the DataFrame that don't exist in the
    table are silently ignored.

    If conn is supplied the caller's connection is used (and kept open).
    If omitted a new connection is created and closed automatically.

    failed_key: human-readable string written to failed.txt before the commit
    and removed only after the row-count verification passes.  If omitted, a
    fallback key of "{table}|{timestamp}" is used.

    Usage:
        upsert_dataframe("stage_gfms", df)                          # own connection
        upsert_dataframe("stage_gfms", df, conn=conn)               # shared connection
        upsert_dataframe("stage_gfms", df, conn=conn,
                         failed_key="2021121400_gfms_summary")      # named key
    """
    import psycopg2

    def _is_null(v):
        if v is None or v == "":
            return True
        try:
            return v != v       # catches float NaN and pandas NaT
        except (TypeError, ValueError):
            return False

    def _to_scalar(v):
        if hasattr(v, 'item'):          # numpy scalar → Python native
            return v.item()
        if hasattr(v, 'to_pydatetime'): # pandas Timestamp → datetime
            return v.to_pydatetime()
        return v

    def _coerce(v, data_type):
        if _is_null(v):
            return None
        v = _to_scalar(v)
        dt = data_type.lower()
        try:
            if dt in ('integer', 'smallint', 'bigint'):
                return int(float(v))    # float first so "3.0" strings work
            if dt in ('double precision', 'real'):
                return float(v)
            if dt == 'numeric':
                return Decimal(str(float(v)))
            if dt in ('text', 'character varying', 'character'):
                return str(v)
            if dt in ('timestamp with time zone', 'timestamp without time zone'):
                aware = (dt == 'timestamp with time zone')
                if isinstance(v, datetime):
                    if aware:
                        # Ensure timezone-aware and normalised to UTC
                        v_tz = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
                        return v_tz.astimezone(timezone.utc)
                    return v.replace(tzinfo=None)
                s = str(v).strip()
                for fmt in (
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y%m%d",               # filename daily   e.g. 20240101 — must be before %Y%m%d%H
                    "%Y%m%d%H",             # filename hourly  e.g. 2024010112
                ):
                    try:
                        dt_val = datetime.strptime(s, fmt)
                        # All string timestamps are assumed to be UTC
                        return dt_val.replace(tzinfo=timezone.utc) if aware else dt_val.replace(tzinfo=None)
                    except ValueError:
                        continue
                return None
            if dt == 'boolean':
                return bool(v)
        except Exception:
            return None         # malformed value → NULL rather than a crash
        return v                # unknown type — pass through

    _own_conn = conn is None
    if _own_conn:
        conn = psycopg2.connect(**DB_PARAMS)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                ORDER BY ordinal_position
                """,
                (table,),
            )
            col_types = {row[0]: row[1] for row in cur.fetchall()}

        # Only insert columns present in both the DataFrame and the DB table
        cols = [c for c in df.columns if c in col_types]
        if not cols:
            return

        col_names = ", ".join(f'"{c}"' for c in cols)
        sql = f'INSERT INTO {table} ({col_names}) VALUES %s'
        rows = [
            tuple(_coerce(v, col_types[col]) for col, v in zip(cols, row))
            for row in df[cols].itertuples(index=False, name=None)
        ]

        if table.startswith("stage_mom_"):
            history_table = table[len("stage_"):]               # mom_gfms
        else:
            history_table = table.replace("stage_", "summary_") # summary_gfms
        batch_timestamp = _coerce(df["timestamp"].iloc[0], col_types.get("timestamp", "text"))
        expected_hist   = expected_rows if expected_rows is not None else len(df)

        # Write-ahead: mark this upload as in-flight before committing.
        # Removed below only after the row-count check passes.  If the process
        # crashes or verification fails the entry stays in failed.txt.
        log_key = failed_key if failed_key else f"{table}|{batch_timestamp.isoformat()}"
        _failed_log_add(log_key)

        for attempt in range(RETRY_ATTEMPTS):
            try:
                # Tell the staging flush trigger how many rows to expect in history.
                # The trigger uses this to decide whether to skip (already complete),
                # or delete partial rows and re-insert.
                if expected_rows is not None:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL mom.expected_rows = %s", (expected_rows,))

                # Insert into staging. The BEFORE INSERT trigger clears the table first
                # so leftover rows from a previous failed attempt never accumulate.
                # The AFTER INSERT trigger flushes staging → _latest → history atomically.
                upsert_rows(conn, sql, rows)

                # Verify the expected number of rows landed in the history table.
                # All trigger writes are committed by this point, so the count is final.
                success, confirmed = _in_history(conn, history_table, batch_timestamp, expected_hist)
                if success:
                    break

            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                if attempt < RETRY_ATTEMPTS - 1:
                    # Connection is dead — close it and open a fresh one before retrying.
                    # _own_conn is set to True so the finally block closes the new connection.
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = psycopg2.connect(**DB_PARAMS)
                    _own_conn = True
                    time.sleep(RETRY_DELAY)
                    continue
                raise  # all retries exhausted — re-raise the connection error

            # History count did not match — sleep and retry. The staging flush trigger
            # will delete any partial history rows on the next attempt before re-inserting.
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
        else:
            # Loop completed without break — all attempts failed the history count check.
            raise RuntimeError(
                f"Insert into {table} failed after {RETRY_ATTEMPTS} attempts: expected "
                f"{expected_hist} rows in {history_table} with timestamp {batch_timestamp} "
                f"but found {confirmed}."
            )

        _failed_log_remove(log_key)
    finally:
        if _own_conn:
            conn.close()

# ---------------------------------------------------------------------------
# Server listing helper
# ---------------------------------------------------------------------------

def list_server_files(url, pattern):
    """Fetch a directory listing and return sorted filenames matching pattern."""
    import re
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(pattern, resp.text))

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def download_text(url, errors="strict"):
    """Fetch a URL and return the decoded UTF-8 content, with retries."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return resp.content.decode("utf-8", errors=errors)
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {url}: {exc}") from exc


def download_resp(url, timeout=120):
    """Fetch a URL and return the response object, with retries."""
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(f"Failed to download {url}: {exc}") from exc
