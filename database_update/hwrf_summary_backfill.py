"""
hwrf_summary_backfill.py
    -- download HWRF rainfall for given dates and produce only the
       HWRF_summary CSV and HWRF_image TIFF (no MoM / Final_Attributes update)

Usage:
    python database_update/hwrf_summary_backfill.py                # today + yesterday (UTC)
    python database_update/hwrf_summary_backfill.py 20261011 20261012
"""

import csv
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

import settings
from HWRF_tool import (
    HWRF_download,
    HWRF_extract_by_watershed,
    check_hours,
    process_rain,
)


def is_fake_summary(path):
    """True if the summary CSV only contains zero rows (fabricated placeholder)"""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if any(float(v) != 0.0 for v in row[1:]):
                return False
    return True


def list_cycles(adate):
    """return {YYYYMMDDHH: url} for the cycles published for a date"""
    hosturl = settings.config.get("hwrf", "HOST")
    dayurl = f"{hosturl.rstrip('/')}/hwrf.{adate}/"
    reqs = requests.get(dayurl)
    if reqs.status_code != 200:
        logging.info(f"no HWRF directory for {adate}: {dayurl}")
        return {}
    soup = BeautifulSoup(reqs.text, "html.parser")
    cycles = {}
    for link in soup.find_all("a"):
        fstr = link.string
        if not fstr:
            continue
        hhstr = fstr.replace("/", "")
        if hhstr in ["00", "06", "12", "18"]:
            cycles[adate + hhstr] = dayurl + fstr
    return cycles


def backfill(dates):
    os.makedirs(settings.HWRF_SUM_DIR, exist_ok=True)
    os.makedirs(settings.HWRF_IMG_DIR, exist_ok=True)
    os.chdir(settings.HWRF_PROC_DIR)

    for adate in dates:
        for key, url in list_cycles(adate).items():
            if check_hours(key):
                logging.info(f"too early to process {key}")
                continue
            sum_csv = os.path.join(settings.HWRF_SUM_DIR, f"hwrf.{key}rainfall.csv")
            img_tiff = os.path.join(settings.HWRF_IMG_DIR, f"hwrf.{key}rainfall.tiff")
            if os.path.exists(sum_csv) and os.path.exists(img_tiff):
                logging.info(f"already processed {key}")
                continue
            if os.path.exists(sum_csv):
                if not is_fake_summary(sum_csv):
                    logging.info(f"real summary exists without image, skipping {key}")
                    continue
                logging.info(f"replacing zero-value placeholder {sum_csv}")
                os.remove(sum_csv)

            a_list = HWRF_download(url)
            if len(a_list) == 0:
                logging.info(f"no rainfall data {key}")
                continue
            newtiff = process_rain(key, a_list)
            [hwrfcsv, dataflag] = HWRF_extract_by_watershed(newtiff)
            logging.info(f"{key}: {hwrfcsv} has_data={dataflag}")

    os.chdir(settings.BASE_DIR)


def main():
    # settings already logs to file; also echo to the console
    logging.getLogger().addHandler(logging.StreamHandler())
    if len(sys.argv) > 1:
        dates = sys.argv[1:]
    else:
        today = datetime.now(timezone.utc)
        dates = [(today - timedelta(days=1)).strftime("%Y%m%d"), today.strftime("%Y%m%d")]
    backfill(dates)


if __name__ == "__main__":
    main()
