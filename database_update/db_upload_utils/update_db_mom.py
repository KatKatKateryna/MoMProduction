"""
Shared extraction helpers for the four MoM Attributes_Clean_* file types.

Each MoM stage produces an Attributes_Clean_*.csv that contains per-watershed
alert results.  Columns already stored in watershed_shapes (area_km2, ISO,
Admin0, Admin1, rfr_score, cfr_score) are excluded from the extracted DataFrame.

File patterns and timestamp formats
------------------------------------
GFMS : Attributes_Clean_YYYYMMDD.csv                             (8-digit date)
HWRF : Attributes_Clean_YYYYMMDDHHHWRFUpdated.csv                (10-digit datetime)
DFO  : Attributes_Clean_YYYYMMDDHHMOM+DFOUpdated.csv             (10-digit datetime)
VIIRS: Attributes_clean_YYYYMMDDHHWRF+MOM+DFO+VIIRSUpdated.csv  (10-digit datetime)
"""

import csv
import io
import re

import pandas as pd

# Columns already in watershed_shapes — skip them here.
_SKIP_COLS = {"area_km2", "ISO", "Admin0", "Admin1", "rfr_score", "cfr_score"}


def _extract(content, timestamp):
    """Parse an Attributes_Clean CSV and return a DataFrame ready for upsert.

    Strips whitespace from column headers, drops watershed_shapes columns,
    and inserts 'timestamp' as the first column.
    """
    rows = list(csv.DictReader(io.StringIO(content)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Normalise column names: strip whitespace and BOM (files open with \ufeff prefix)
    df.columns = [c.strip().lstrip('\ufeff') for c in df.columns]
    df = df.drop(columns=[c for c in _SKIP_COLS if c in df.columns], errors="ignore")
    df.insert(0, "timestamp", timestamp)
    return df


# ---------------------------------------------------------------------------
# GFMS MoM — Attributes_Clean_YYYYMMDD.csv
# ---------------------------------------------------------------------------

def get_timestamp_gfms(filename):
    """Return the 8-digit YYYYMMDD string from a GFMS MoM filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{8})\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_gfms(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# HWRF MoM — Attributes_Clean_YYYYMMDDHHHWRFUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_hwrf(filename):
    """Return the 10-digit YYYYMMDDHH string from an HWRF MoM filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{10})HWRFUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_hwrf(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# DFO MoM — Attributes_Clean_YYYYMMDDHHMOM+DFOUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_dfo(filename):
    """Return the 10-digit YYYYMMDDHH string from a DFO MoM filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{10})MOM\+DFOUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_dfo(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# VIIRS MoM — Attributes_clean_YYYYMMDDHHWRF+MOM+DFO+VIIRSUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_viirs(filename):
    """Return the 10-digit YYYYMMDDHH string from a VIIRS MoM filename, or ''."""
    m = re.search(r'Attributes_[Cc]lean_(\d{10})MOM\+DFO\+VIIRSUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_viirs(content, timestamp):
    return _extract(content, timestamp)
