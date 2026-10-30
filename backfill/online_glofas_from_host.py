"""
online_glofas_from_host.py
    -- download every GloFAS date the host still has and rebuild its
       threspoints csv and geojson in GLOFAS_DIR, written over the existing
       ones (no GFMS, MoM or Final_Attributes update)

    The download is the cron job's (GloFAS_download): every file in the host
    folder, fixed and dynamic, into GLOFAS_PROC_DIR; a file without station
    lines from the host never replaces a local copy that has some. Each date
    is then rebuilt with the current reading of the station files
    (GloFAS_write).

Usage:
    python backfill/online_glofas_from_host.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings
from GFMS_tool import GloFAS_download, GloFAS_write


def main():
    # only to the console (redirected to backfill/logs/), not the processing log
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    dates = sorted(GloFAS_download())
    if not dates:
        logging.info("GloFAS host has no dates")
        return
    logging.info(f"GloFAS host has {len(dates)} dates: {dates[0]} .. {dates[-1]}")
    for data_date in dates:
        GloFAS_write(data_date)


if __name__ == "__main__":
    main()
