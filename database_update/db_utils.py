"""
Shared utilities for all update_db_* scripts.
"""

import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# DB connection parameters
# ---------------------------------------------------------------------------

DB_PARAMS = {
    "host":            os.getenv("DB_HOST"),
    "port":            int(os.getenv("DB_PORT", 5432)),
    "dbname":          os.getenv("DB_NAME", "postgres"),
    "user":            os.getenv("DB_USER"),
    "password":        os.getenv("DB_PASSWORD"),
    "sslmode":         os.getenv("DB_SSLMODE", "require"),
    "connect_timeout": 10,
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

def get_processed_timestamps(conn, table):
    """Return a set of all timestamps already present in the given table."""
    with conn.cursor() as cur:
        cur.execute(f'SELECT DISTINCT "timestamp" FROM {table}')
        return {row[0] for row in cur.fetchall()}

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
