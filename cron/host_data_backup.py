"""
hwrf_wind_backup.py
    -- copy the HWRF wind files (wind10m.ascii, wind10hrly.ascii) of every cycle
       still on the HWRF host into PRODUCT_DIR/HWRF_host_data_backups, keeping
       the host layout hwrf.YYYYMMDD/HH/. Files already saved are skipped.

Usage:
    python database_update/hwrf_wind_backup.py
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

import settings

BACKUP_DIR = os.path.join(settings.PRODUCT_DIR, "HWRF_host_data_backups")
HOURS = ["00", "06", "12", "18"]


def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def list_links(url):
    """link names of a host directory listing"""
    reqs = requests.get(url, timeout=60)
    reqs.raise_for_status()
    soup = BeautifulSoup(reqs.text, "html.parser")
    return [link.string for link in soup.find_all("a") if link.string]


def download(url, path):
    """download to a temporary name, then move into place"""
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=300) as reqs:
        reqs.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in reqs.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(tmp, path)


def main():
    hosturl = settings.config.get("hwrf", "HOST").rstrip("/") + "/"
    saved, failed = 0, 0

    days = [x.rstrip("/") for x in list_links(hosturl) if x.startswith("hwrf.")]
    for day in sorted(days):
        dayurl = f"{hosturl}{day}/"
        for hh in [x.rstrip("/") for x in list_links(dayurl)]:
            if hh not in HOURS:
                continue
            cycleurl = f"{dayurl}{hh}/"
            winds = [
                x
                for x in list_links(cycleurl)
                if x.endswith((".wind10m.ascii", ".wind10hrly.ascii"))
            ]
            outdir = os.path.join(BACKUP_DIR, day, hh)
            for fname in winds:
                path = os.path.join(outdir, fname)
                if os.path.exists(path):
                    continue
                os.makedirs(outdir, exist_ok=True)
                try:
                    download(cycleurl + fname, path)
                    saved += 1
                    log(f"saved {day}/{hh}/{fname}")
                except requests.exceptions.RequestException as e:
                    failed += 1
                    log(f"FAILED {day}/{hh}/{fname}: {e}")

    log(f"done: {saved} saved, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
