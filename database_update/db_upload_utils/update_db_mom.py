"""
Shared extraction helpers for the four MoM file types.

Each MoM stage can produce two CSV variants per timestamp:
  - Attributes_Clean_*.csv  — intermediate output
  - Final_Attributes_*.csv  — final enriched output (preferred when both exist)

Both variants share the same extract function (_extract).  The timestamp
extraction functions match either prefix so callers don't need to know which
variant they're looking at.

File patterns and timestamp formats
------------------------------------
GFMS : Attributes_Clean_YYYYMMDD.csv / Final_Attributes_YYYYMMDD.csv   (8-digit date)
HWRF : Attributes_Clean_YYYYMMDDHHHWRFUpdated.csv / Final_…            (10-digit datetime)
DFO  : Attributes_Clean_YYYYMMDDHHMOM+DFOUpdated.csv / Final_…         (10-digit datetime)
VIIRS: Attributes_[Cc]lean_YYYYMMDDHHWRF+MOM+DFO+VIIRSUpdated.csv / Final_…
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
# GFMS MoM — Attributes_Clean_YYYYMMDD.csv / Final_Attributes_YYYYMMDD.csv
# ---------------------------------------------------------------------------

def get_timestamp_gfms(filename):
    """Return the 8-digit YYYYMMDD string from a GFMS MoM Attributes_Clean filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{8})\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def get_timestamp_final_gfms(filename):
    """Return the 8-digit YYYYMMDD string from a GFMS MoM Final_Attributes filename, or ''."""
    m = re.search(r'Final_Attributes_(\d{8})\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_gfms(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# HWRF MoM — Attributes_Clean_YYYYMMDDHHHWRFUpdated.csv
#           / Final_Attributes_YYYYMMDDHHHWRFUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_hwrf(filename):
    """Return the 10-digit YYYYMMDDHH string from an HWRF MoM Attributes_Clean filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{10})HWRFUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def get_timestamp_final_hwrf(filename):
    """Return the 10-digit YYYYMMDDHH string from an HWRF MoM Final_Attributes filename, or ''."""
    m = re.search(r'Final_Attributes_(\d{10})HWRFUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_hwrf(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# DFO MoM — Attributes_Clean_YYYYMMDDHHMOM+DFOUpdated.csv
#          / Final_Attributes_YYYYMMDDHHMOM+DFOUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_dfo(filename):
    """Return the 10-digit YYYYMMDDHH string from a DFO MoM Attributes_Clean filename, or ''."""
    m = re.search(r'Attributes_Clean_(\d{10})MOM\+DFOUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def get_timestamp_final_dfo(filename):
    """Return the 10-digit YYYYMMDDHH string from a DFO MoM Final_Attributes filename, or ''."""
    m = re.search(r'Final_Attributes_(\d{10})MOM\+DFOUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_dfo(content, timestamp):
    return _extract(content, timestamp)


# ---------------------------------------------------------------------------
# VIIRS MoM — Attributes_[Cc]lean_YYYYMMDDHHWRF+MOM+DFO+VIIRSUpdated.csv
#           / Final_Attributes_YYYYMMDDHHWRF+MOM+DFO+VIIRSUpdated.csv
# ---------------------------------------------------------------------------

def get_timestamp_viirs(filename):
    """Return the 10-digit YYYYMMDDHH string from a VIIRS MoM Attributes_Clean filename, or ''."""
    m = re.search(r'Attributes_[Cc]lean_(\d{10})MOM\+DFO\+VIIRSUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def get_timestamp_final_viirs(filename):
    """Return the 10-digit YYYYMMDDHH string from a VIIRS MoM Final_Attributes filename, or ''."""
    m = re.search(r'Final_Attributes_(\d{10})MOM\+DFO\+VIIRSUpdated\.csv', filename, re.IGNORECASE)
    return m.group(1) if m else ""


def extract_df_viirs(content, timestamp):
    return _extract(content, timestamp)
