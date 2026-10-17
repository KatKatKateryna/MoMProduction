"""
hwrf_hafs_summaries_default_offline.py
    -- regenerate the HWRF and HAFS rainfall summaries (and images) for a
       window of days, offline by default, without the MoM chain

    HWRF: from the host data backups (HWRF/HWRF_host_data_backups), never
    the host; writes the summary and image.

    HAFS, like the other summary scripts, either:
      - offline (default): the summary is rebuilt from the saved image
        HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff (only read); a cycle
        without an image is left as it is;
      - --online: everything is downloaded again from the HAFS host into a
        temporary folder (removed after each cycle) and the summary and image
        are recomputed. HAFS/HAFS_host_data_backups is neither read nor
        written. Cycles less than STORMS_FINAL (HAFS_tool.py) old are left
        for the cron job.

    Writes only:
        HWRF/HWRF_summary/hwrf.YYYYMMDDHHrainfall.csv
        HWRF/HWRF_image/hwrf.YYYYMMDDHHrainfall.tiff
        HAFS/HAFS_summary/hafs.YYYYMMDDHHrainfall.csv
        HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff   (--online, with rain)
    No MoM, final alert, track, done marker or backup is touched. A cycle
    that already has a summary is skipped unless --overwrite (then it is
    recomputed and replaced). A cycle with no rain over any watershed gets a
    header-only summary; an old HAFS image is then kept and reported. Old
    files are replaced only once the new ones exist; a cycle that fails
    keeps them.

Usage:
    python backfill/hwrf_hafs_summaries_default_offline.py 20261008 20261013
    python backfill/hwrf_hafs_summaries_default_offline.py 20261008          # up to today
    python backfill/hwrf_hafs_summaries_default_offline.py 20261001 20261008 --model hafs --online
    python backfill/hwrf_hafs_summaries_default_offline.py 20261008 20261013 --overwrite
"""

import argparse
import csv
import glob
import gzip
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import settings  # noqa: E402
import HWRF_tool  # noqa: E402
import HAFS_tool  # noqa: E402
from offline_hwrf_hafs_summaries_from_images import summary_rows  # noqa: E402
from utilities import RAIN_SUMMARY_HEADERS, watersheds_gdb_reader, write_empty_rain_summary  # noqa: E402

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


def hafs_paths(cycle):
    return (
        os.path.join(settings.HAFS_SUM_DIR, f"hafs.{cycle}rainfall.csv"),
        os.path.join(settings.HAFS_IMG_DIR, f"hafs.{cycle}rainfall.tiff"),
    )


def hafs_cycle_offline(cycle, watersheds):
    """HAFS summary rebuilt from the saved image; False without an image"""
    summary, image = hafs_paths(cycle)
    if not os.path.exists(image):
        return False
    rows = summary_rows(image, watersheds)
    part = f"{summary}.{os.getpid()}.part"
    with open(part, "w") as f:
        writer = csv.writer(f)
        writer.writerow(RAIN_SUMMARY_HEADERS)
        writer.writerows(rows)
    os.replace(part, summary)
    logging.info(f"HAFS {cycle}: summary from the image, {len(rows)} watersheds with rain")
    return True


def write_hafs(cycle, grids, note):
    """HAFS summary and image from the storm grids (header-only summary when
    there is no rain over any watershed; an old image is then kept)"""
    summary, image = hafs_paths(cycle)
    raintiff = os.path.join(HAFS_tool.WORK_DIR, os.path.basename(image))
    output_csv = os.path.join(HAFS_tool.WORK_DIR, os.path.basename(summary))
    has_data = False
    if grids:
        grid, top, left = HAFS_tool.merge(grids)
        HAFS_tool.write_tiff(raintiff, grid, top, left)
        has_data = np.nanmax(np.nan_to_num(grid)) > 0 and HAFS_tool.summarize(
            raintiff, output_csv, grid, top, left)
    if has_data:
        shutil.move(output_csv, summary)
        shutil.move(raintiff, image)
    else:
        write_empty_rain_summary(output_csv)
        shutil.move(output_csv, summary)
        if os.path.exists(raintiff):
            os.remove(raintiff)
        if os.path.exists(image):
            logging.warning(f"HAFS {cycle}: no rain over watersheds now, old image kept: {image}")
    logging.info(f"HAFS {cycle}: {note}, rain over watersheds: {has_data}")


# HAFS from the host data backups: replaced by the offline (images) and
# --online (host) modes, so HAFS reads either the local images or the host
# def hafs_cycle(cycle):
#     """HAFS summary and image of a backed-up cycle; False if not backed up"""
#     backup = os.path.join(settings.HAFS_BACKUP_DIR, f"{settings.HAFS_MODEL}.{cycle[:8]}", cycle[8:])
#     suffix = f".{cycle}.{settings.HAFS_MODEL}.parent.swath.prate.grb2"
#     storms = sorted(
#         os.path.basename(p)[: -len(suffix)] for p in glob.glob(os.path.join(backup, "*" + suffix))
#     )
#     storms = [s for s in storms if os.path.exists(
#         os.path.join(backup, f"{s}.{cycle}.{settings.HAFS_MODEL}.trak.atcfunix"))]
#     if not storms:
#         return False
#
#     grids = [HAFS_tool.storm_rain(cycle, s, HAFS_tool.fetch_track(cycle, s)) for s in storms]
#     write_hafs(cycle, grids, f"storms {' '.join(storms)}")
#     return True


def hafs_cycle_online(cycle):
    """HAFS summary and image downloaded again from the host into a
    temporary folder (the backups are not used); False if the cycle is too
    recent to be final"""
    if HAFS_tool.utc_now() - HAFS_tool.cycle_time(cycle) < HAFS_tool.STORMS_FINAL:
        return False
    tmp = tempfile.mkdtemp(prefix="hafs_host_data_", dir=HAFS_tool.WORK_DIR)
    # track_file / rain_file keep the host files where backup_path says
    HAFS_tool.backup_path = lambda c, name: os.path.join(tmp, name)
    try:
        storms = []
        for storm in HAFS_tool.list_storms(cycle):
            base = f"{HAFS_tool.cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}"
            if HAFS_tool.exists(base + ".parent.swath.grb2") and HAFS_tool.exists(base + ".trak.atcfunix"):
                storms.append(storm)
            else:
                logging.warning(f"HAFS {cycle}: {storm} files missing on the host, skipped")
        grids = [HAFS_tool.storm_rain(cycle, s, HAFS_tool.fetch_track(cycle, s)) for s in storms]
        write_hafs(cycle, grids, f"storms {' '.join(storms) or 'none'} (host)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start", help="first day, YYYYMMDD")
    parser.add_argument(
        "end", nargs="?", default=datetime.now(timezone.utc).strftime("%Y%m%d"),
        help="last day, YYYYMMDD (default: today, UTC)",
    )
    parser.add_argument("--model", choices=["hwrf", "hafs", "both"], default="both")
    parser.add_argument(
        "--online", action="store_true",
        help="HAFS: download again from the host instead of rebuilding from the images",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="recompute and replace summaries that already exist (default: skip them)",
    )
    args = parser.parse_args()

    # only to the console (redirected to backfill/logs/), not the processing
    # folder log that importing settings points logging at
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    models = ["HWRF", "HAFS"] if args.model == "both" else [args.model.upper()]
    logging.info(
        f"rain summaries {args.start} - {args.end}: {' '.join(models)}, HAFS "
        f"{'online' if args.online else 'offline'}, overwrite={args.overwrite}, "
        f"HAFS buffer {HAFS_tool.BUFFER_KM} km"
    )

    HAFS_tool.make_dirs()
    hwrf_work = os.path.join(settings.HWRF_PROC_DIR, "backfill_work")
    os.makedirs(hwrf_work, exist_ok=True)
    watersheds = watersheds_gdb_reader() if "HAFS" in models and not args.online else None
    if args.online:
        hafs_func = hafs_cycle_online
    else:
        def hafs_func(cycle):
            return hafs_cycle_offline(cycle, watersheds)
    funcs = {"HWRF": lambda cycle: hwrf_cycle(cycle, hwrf_work), "HAFS": hafs_func}
    summaries = {
        "HWRF": lambda cycle: os.path.join(settings.HWRF_SUM_DIR, f"hwrf.{cycle}rainfall.csv"),
        "HAFS": lambda cycle: hafs_paths(cycle)[0],
    }
    done = {m: [] for m in models}
    skipped = {m: [] for m in models}
    for cycle in cycles(args.start, args.end):
        for model in models:
            if os.path.exists(summaries[model](cycle)) and not args.overwrite:
                skipped[model].append(cycle)
                continue
            try:
                if funcs[model](cycle):
                    done[model].append(cycle)
            except Exception:
                logging.exception(f"{model} {cycle} failed, old summary and image kept")
            finally:
                os.chdir(settings.BASE_DIR)
    shutil.rmtree(hwrf_work, ignore_errors=True)
    for model in models:
        logging.info(f"{model}: {len(done[model])} cycles regenerated: {' '.join(done[model])}")
        logging.info(f"{model}: {len(skipped[model])} skipped (summary exists): {' '.join(skipped[model])}")


if __name__ == "__main__":
    main()
