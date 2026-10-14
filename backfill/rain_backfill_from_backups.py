"""
rain_backfill_from_backups.py
    -- regenerate the HWRF and HAFS rainfall summaries and images from the
       host data backups, overwriting the existing ones

    Reads only the backups (HWRF/HWRF_host_data_backups, HAFS/HAFS_host_data_backups),
    never the hosts, and leaves them unchanged. Writes only:
        HWRF/HWRF_summary/hwrf.YYYYMMDDHHrainfall.csv
        HWRF/HWRF_image/hwrf.YYYYMMDDHHrainfall.tiff
        HAFS/HAFS_summary/hafs.YYYYMMDDHHrainfall.csv
        HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff
    No MoM, final alert or track output is touched. A cycle whose summary
    has no rain over any watershed keeps a header-only summary and loses its
    old image. Old files are replaced only once the new ones exist; a cycle
    that fails keeps them.

Usage:
    python backfill/rain_backfill_from_backups.py 20261008 20261013
    python backfill/rain_backfill_from_backups.py 20261008          # up to today
"""

import glob
import gzip
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import settings  # noqa: E402
import HWRF_tool  # noqa: E402
import HAFS_tool  # noqa: E402
from utilities import write_empty_rain_summary  # noqa: E402

HOURS = ["00", "06", "12", "18"]


def cycles(start, end):
    day = datetime.strptime(start, "%Y%m%d")
    last = datetime.strptime(end, "%Y%m%d")
    while day <= last:
        for hh in HOURS:
            yield day.strftime("%Y%m%d") + hh
        day += timedelta(days=1)


def hwrf_cycle(cycle, workdir):
    """HWRF summary and image of a backed-up cycle; False if not backed up"""
    backup = os.path.join(settings.HWRF_BACKUP_DIR, f"hwrf.{cycle[:8]}", cycle[8:])
    rain_gz = sorted(glob.glob(os.path.join(backup, "*.rainfall.ascii.gz")))
    if not rain_gz:
        return False
    os.chdir(workdir)
    names = []
    for gz in rain_gz:
        name = os.path.basename(gz)[:-3]
        with gzip.open(gz, "rb") as src, open(name, "wb") as dst:
            shutil.copyfileobj(src, dst)
        names.append(name)
    newtiff = HWRF_tool.process_rain(cycle, names)
    # moves the summary (and the image, with rain) over the old ones
    hwrfcsv, has_data = HWRF_tool.HWRF_extract_by_watershed(newtiff)
    image = os.path.join(settings.HWRF_IMG_DIR, f"hwrf.{cycle}rainfall.tiff")
    if not has_data and os.path.exists(image):
        os.remove(image)
    os.remove(f"hwrf.{cycle}rainfall.zip")
    logging.info(f"HWRF {cycle}: {len(names)} storms, rain over watersheds: {has_data}")
    return True


def hafs_cycle(cycle):
    """HAFS summary and image of a backed-up cycle; False if not backed up"""
    backup = os.path.join(settings.HAFS_BACKUP_DIR, f"{settings.HAFS_MODEL}.{cycle[:8]}", cycle[8:])
    suffix = f".{cycle}.{settings.HAFS_MODEL}.parent.swath.prate.grb2"
    storms = sorted(
        os.path.basename(p)[: -len(suffix)] for p in glob.glob(os.path.join(backup, "*" + suffix))
    )
    storms = [s for s in storms if os.path.exists(
        os.path.join(backup, f"{s}.{cycle}.{settings.HAFS_MODEL}.trak.atcfunix"))]
    if not storms:
        return False

    grids = [HAFS_tool.storm_rain(cycle, s, HAFS_tool.fetch_track(cycle, s)) for s in storms]
    grid, top, left = HAFS_tool.merge(grids)
    raintiff = os.path.join(HAFS_tool.WORK_DIR, f"hafs.{cycle}rainfall.tiff")
    output_csv = os.path.join(HAFS_tool.WORK_DIR, f"hafs.{cycle}rainfall.csv")
    summary = os.path.join(settings.HAFS_SUM_DIR, os.path.basename(output_csv))
    image = os.path.join(settings.HAFS_IMG_DIR, os.path.basename(raintiff))
    HAFS_tool.write_tiff(raintiff, grid, top, left)
    has_data = np.nanmax(np.nan_to_num(grid)) > 0 and HAFS_tool.summarize(
        raintiff, output_csv, grid, top, left)
    if has_data:
        shutil.move(output_csv, summary)
        shutil.move(raintiff, image)
    else:
        write_empty_rain_summary(output_csv)
        shutil.move(output_csv, summary)
        os.remove(raintiff)
        if os.path.exists(image):
            os.remove(image)
    logging.info(f"HAFS {cycle}: storms {' '.join(storms)}, rain over watersheds: {has_data}")
    return True


def main():
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    logging.getLogger().setLevel(logging.INFO)
    start = sys.argv[1]
    end = sys.argv[2] if len(sys.argv) > 2 else datetime.now(timezone.utc).strftime("%Y%m%d")
    logging.info(f"rain backfill from backups: {start} - {end}, HAFS window {settings.HAFS_WINDOW_KM} km")

    HAFS_tool.make_dirs()
    hwrf_work = os.path.join(settings.HWRF_PROC_DIR, "backfill_work")
    os.makedirs(hwrf_work, exist_ok=True)
    done = {"HWRF": [], "HAFS": []}
    for cycle in cycles(start, end):
        for model, func, args in (("HWRF", hwrf_cycle, (cycle, hwrf_work)), ("HAFS", hafs_cycle, (cycle,))):
            try:
                if func(*args):
                    done[model].append(cycle)
            except Exception:
                logging.exception(f"{model} {cycle} failed, old summary and image kept")
            finally:
                os.chdir(settings.BASE_DIR)
    shutil.rmtree(hwrf_work, ignore_errors=True)
    for model, done_cycles in done.items():
        logging.info(f"{model}: {len(done_cycles)} cycles regenerated: {' '.join(done_cycles)}")


if __name__ == "__main__":
    main()
