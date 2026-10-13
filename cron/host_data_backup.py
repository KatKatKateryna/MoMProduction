"""
host_data_backup.py
    -- HWRF: copy the original HWRF host files of every cycle still on the
       HWRF host into HWRF/HWRF_host_data_backups, keeping the host layout
       hwrf.YYYYMMDD/HH/:
           wind10m.ascii, wind10hrly.ascii, rainfall.ascii
       each gzipped (.ascii.gz, ~10x smaller).
       Files already saved are skipped.
    -- HAFS (when enabled in production.cfg): HAFS_tool.py --backup, in its
       own process, keeps the HAFS track and rain records of the last
       LOOKBACK_DAYS days in HAFS/HAFS_host_data_backups.

Usage:
    python cron/host_data_backup.py
"""

import gzip
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from bs4 import BeautifulSoup

import settings

BACKUP_DIR = settings.HWRF_BACKUP_DIR
HOURS = ["00", "06", "12", "18"]
WIND_SUFFIXES = (".wind10m.ascii", ".wind10hrly.ascii")
RAIN_SUFFIX = ".rainfall.ascii"
# host files are posted ~6.5 h after the cycle; a cycle saved and older than
# this is complete, so its listing is not requested again
COMPLETE_AFTER = timedelta(hours=12)


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
    """download gzipped to a temporary name, then move into place"""
    tmp = path + ".part"
    with requests.get(url, stream=True, timeout=300) as reqs:
        reqs.raise_for_status()
        with gzip.open(tmp, "wb") as f:
            for chunk in reqs.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    os.replace(tmp, path)


def backup_name(fname):
    """local file name of a host file"""
    return fname + ".gz"


def cycle_complete(outdir, cycle_time):
    """saved with both wind and rainfall files, and old enough to be final"""
    if datetime.now(timezone.utc) - cycle_time <= COMPLETE_AFTER:
        return False
    if not os.path.isdir(outdir):
        return False
    names = os.listdir(outdir)
    return any(x.endswith(WIND_SUFFIXES[0] + ".gz") for x in names) and any(
        x.endswith(RAIN_SUFFIX + ".gz") for x in names
    )


def backup_hafs():
    """HAFS host data, in its own process so it never affects the HWRF part"""
    if not settings.HAFS_ENABLED:
        return True
    cmd = [sys.executable, os.path.join(settings.BASE_DIR, "HAFS_tool.py"), "--backup"]
    result = subprocess.run(cmd, cwd=settings.BASE_DIR)
    log(f"HAFS backup exited with code {result.returncode}")
    return result.returncode == 0


def backup_hwrf():
    hosturl = settings.config.get("hwrf", "HOST").rstrip("/") + "/"
    saved, failed = 0, 0

    days = [x.rstrip("/") for x in list_links(hosturl) if x.startswith("hwrf.")]
    for day in sorted(days):
        dayurl = f"{hosturl}{day}/"
        for hh in [x.rstrip("/") for x in list_links(dayurl)]:
            if hh not in HOURS:
                continue
            outdir = os.path.join(BACKUP_DIR, day, hh)
            cycle_time = datetime.strptime(day[5:] + hh, "%Y%m%d%H").replace(
                tzinfo=timezone.utc
            )
            if cycle_complete(outdir, cycle_time):
                continue
            cycleurl = f"{dayurl}{hh}/"
            files = [
                x
                for x in list_links(cycleurl)
                if x.endswith(WIND_SUFFIXES) or x.endswith(RAIN_SUFFIX)
            ]
            for fname in files:
                path = os.path.join(outdir, backup_name(fname))
                if os.path.exists(path):
                    continue
                os.makedirs(outdir, exist_ok=True)
                try:
                    download(cycleurl + fname, path)
                    saved += 1
                    log(f"saved {day}/{hh}/{backup_name(fname)}")
                except requests.exceptions.RequestException as e:
                    failed += 1
                    log(f"FAILED {day}/{hh}/{fname}: {e}")

    log(f"HWRF done: {saved} saved, {failed} failed")
    return not failed


def main():
    ok = True
    try:
        ok = backup_hwrf()
    except requests.exceptions.RequestException as e:
        log(f"HWRF host listing FAILED: {e}")
        ok = False
    ok = backup_hafs() and ok
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
