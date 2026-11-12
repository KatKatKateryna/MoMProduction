"""
online_download_and_summarize_all.py
    -- download every source again from its host and make its summaries,
       images and step files for the days from --start to --end, with the
       jobs' own code; no MoM is made (offline_mom_from_summaries.py does)

    Per source, as its cron job makes them:
      - GloFAS  GloFAS_tool.GloFAS_process: every file the FTP site has;
                the days and D-1 (the file the first day's 00 and 06 cycles
                use, cycle_inputs.glofas_for_cycle) are checked for
      - GFMS    GFMS_tool.GFMS_processing of the days: the summaries of the
                times that have come, the cycle images (GFMS_image,
                GFMS_steps) and step files
      - HAFS    HAFS_tool.process_rain of every cycle (00, 06, 12, 18) and
                of the cycle 6 h before the first (the rain the first day's
                00 cycle uses, cycle_inputs.rain_for_cycle): the rain
                records, summary, image and step file
      - HWRF    the HWRF job's download and summary (HWRF_tool) of the host's
                cycles among the same ones
      - DFO     DFO_tool.dfo_download and DFO_process: summary and images
      - VIIRS   VIIRS_tool.VIIRS_run_adate: the date's flood composite parts,
                its mosaic (VIIRS_image) and summary
    DFO and VIIRS are made for the days and the two before --start (D-2 and
    D-1, the ones the first day's cycles read), finished dates only (before
    today); DFO only those the host still has (DFO_tool.host_dates).
    Only what the hosts still keep can be made (GFMS, HAFS and DFO about a
    week). A source that fails is logged and the others go on.

    --start / --end: days, YYYYMMDD (--end default: today, UTC). --out-dir:
    the root the products go under, in the production layout (default: the
    folders of production.cfg). The working files go to a temporary folder,
    removed at the end, so nothing of the jobs' own Processing folder is used
    or changed. An output already there is kept, unless --overwrite: then the
    outputs of the window are deleted first and made again.

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/online_download_and_summarize_all.py --start 20261109
    python backfill/online_download_and_summarize_all.py --start 20261105 --end 20261106 --out-dir /data/rerun --overwrite
"""

import argparse
import glob
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
from backfill_args import (  # noqa: E402
    DEFAULT_ROOT,
    add_date_args,
    add_output_args,
    days,
    point_at,
)

HOURS = ["00", "06", "12", "18"]
GFMS_HOURS = ["00", "03", "06", "09", "12", "15", "18", "21"]
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def point_folders(out_dir, tmp_root):
    """every product folder of settings under out_dir, every working folder
    (but the log folder) under tmp_root, each where settings.py puts it under
    WORKING_DIR"""
    work_root = settings.WORKING_DIR
    products, working = [], []
    for name in dir(settings):
        value = getattr(settings, name)
        if not (name.isupper() and isinstance(value, str)):
            continue
        if value.startswith(settings.PRODUCT_DIR + os.sep):
            products.append(name)
        elif value.startswith(settings.WORKING_DIR + os.sep) and name != "LOG_DIR":
            working.append(name)
    point_at(out_dir, products)
    for name in working:
        rel = os.path.relpath(getattr(settings, name), work_root)
        setattr(settings, name, os.path.join(tmp_root, rel))
        os.makedirs(getattr(settings, name), exist_ok=True)
    for name in products:
        os.makedirs(getattr(settings, name), exist_ok=True)


def remove(paths):
    """delete outputs (--overwrite)"""
    for path in paths:
        for f in glob.glob(path):
            logging.info(f"remove: {f}")
            os.remove(f)


def daily_dates(dates):
    """the dates DFO and VIIRS are made for: D-2 and D-1 of the first day,
    then the days, those before today only (a day's data is complete only the
    next day)"""
    first = datetime.strptime(dates[0], "%Y%m%d")
    before = [(first - timedelta(days=n)).strftime("%Y%m%d") for n in (2, 1)]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return [d for d in before + list(dates) if d < today]


def rain_cycles(dates):
    """the HAFS / HWRF cycles made for the days: the cycle 6 h before the
    first day (the rain its 00 cycle uses, as rain_for_cycle takes the
    previous cycle's) and the days' own 00, 06, 12, 18"""
    first = datetime.strptime(dates[0], "%Y%m%d") - timedelta(hours=6)
    return [first.strftime("%Y%m%d%H")] + [d + h for d in dates for h in HOURS]


def glofas(dates, overwrite):
    from processing.glofas.GloFAS_tool import GloFAS_process

    # D-1 too: the first day's 00 and 06 cycles use the file of the day before
    first = datetime.strptime(dates[0], "%Y%m%d")
    wanted = [(first - timedelta(days=1)).strftime("%Y%m%d")] + list(dates)
    if overwrite:
        remove(os.path.join(settings.GLOFAS_DIR, f"threspoints_{d}00.*") for d in wanted)
    logging.info(f"GloFAS: {GloFAS_process()}")
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for d in wanted:
        csv = os.path.join(settings.GLOFAS_DIR, f"threspoints_{d}00.csv")
        # today's is only published around midday
        if d < today and not os.path.exists(csv):
            logging.warning(f"GloFAS {d}: not on the host, not made: {csv}")


def gfms(dates, overwrite):
    from processing.gfms.GFMS_tool import GFMS_processing

    if overwrite:
        for d in dates:
            remove(
                [os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{d}??.csv"),
                 os.path.join(settings.GFMS_STEP_DIR, f"Flood_byStor_{d}??.csv"),
                 os.path.join(settings.GFMS_IMG_DIR, f"Flood_byStor_{d}??.tiff"),
                 os.path.join(settings.GFMS_STEPS_DIR, f"Flood_byStor_{d}??.tiff")]
            )
    GFMS_processing([d + "00" for d in dates])


def hafs(dates, overwrite):
    from processing.hafs.HAFS_tool import make_dirs, process_rain

    make_dirs()
    now = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    for cycle in rain_cycles(dates):
        if cycle > now:
            break
        summary = os.path.join(settings.HAFS_SUM_DIR, f"hafs.{cycle}rainfall.csv")
        if overwrite:
            remove(
                [summary,
                 os.path.join(settings.HAFS_STEP_DIR, f"hafs.{cycle}rainfall.csv"),
                 os.path.join(settings.HAFS_IMG_DIR, f"hafs.{cycle}rainfall.tiff"),
                 os.path.join(settings.HAFS_STEPS_DIR, cycle, "*")]
            )
        elif os.path.exists(summary):
            logging.info(f"HAFS {cycle}: kept, {summary}")
            continue
        try:
            if not process_rain(cycle):
                logging.warning(f"HAFS {cycle}: not settled (not published yet)")
        except Exception:
            logging.exception(f"HAFS {cycle} failed")


def hwrf(dates, overwrite):
    from processing.hwrf import HWRF_tool

    wanted = rain_cycles(dates)
    if overwrite:
        for cycle in wanted:
            remove(
                [os.path.join(settings.HWRF_SUM_DIR, f"hwrf.{cycle}rainfall.csv"),
                 os.path.join(settings.HWRF_IMG_DIR, f"hwrf.{cycle}rainfall.tiff")]
            )
    # the host's cycles that have no summary yet, among the wanted ones
    cycles = {
        k: v for k, v in HWRF_tool.generate_procesing_list().items() if k in wanted
    }
    logging.info(f"HWRF cycles: {sorted(cycles)}")
    cwd = os.getcwd()
    os.chdir(settings.HWRF_PROC_DIR)
    try:
        for cycle in sorted(cycles):
            try:
                files = HWRF_tool.HWRF_download(cycles[cycle])
                if not files:
                    logging.info(f"HWRF {cycle}: no rainfall data")
                    continue
                tiff = HWRF_tool.process_rain(cycle, files)
                csv, has_data = HWRF_tool.HWRF_extract_by_watershed(tiff)
                logging.info(f"HWRF {cycle}: {csv}{'' if has_data else ' (no rain over watersheds)'}")
            except Exception:
                logging.exception(f"HWRF {cycle} failed")
    finally:
        os.chdir(cwd)


def dfo(dates, overwrite):
    from processing.dfo import DFO_tool

    on_host = DFO_tool.host_dates()
    for adate in daily_dates(dates):
        summary = os.path.join(settings.DFO_SUM_DIR, f"DFO_{adate}.csv")
        day = on_host.get(adate)
        if day is None:
            logging.warning(f"DFO {adate}: not on the host, skipped")
            continue
        if overwrite:
            remove([summary, os.path.join(settings.DFO_IMG_DIR, f"DFO_{adate}_*.tiff")])
        elif os.path.exists(summary):
            logging.info(f"DFO {adate}: kept, {summary}")
            continue
        day_dir = os.path.join(settings.DFO_PROC_DIR, day)
        try:
            if not DFO_tool.dfo_download(day, int(adate[:4])):
                logging.warning(f"DFO {adate}: download failed")
                continue
            DFO_tool.DFO_process(day, adate)
        except Exception:
            logging.exception(f"DFO {adate} failed")
        finally:
            # DFO_process works inside the download folder; the tiles of a
            # day are large
            os.chdir(settings.BASE_DIR)
            shutil.rmtree(day_dir, ignore_errors=True)


def viirs(dates, overwrite):
    from processing.viirs.VIIRS_tool import VIIRS_run_adate

    for adate in daily_dates(dates):
        if overwrite:
            remove(
                [os.path.join(settings.VIIRS_SUM_DIR, f"VIIRS_Flood_{adate}.csv"),
                 os.path.join(settings.VIIRS_IMG_DIR, f"*{adate}*.tiff")]
            )
        try:
            VIIRS_run_adate(adate)
        except Exception:
            logging.exception(f"VIIRS {adate} failed")
        finally:
            os.chdir(settings.BASE_DIR)


SOURCES = [("GloFAS", glofas), ("GFMS", gfms), ("HAFS", hafs), ("HWRF", hwrf),
           ("DFO", dfo), ("VIIRS", viirs)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    add_date_args(parser, by_cycle=False)
    add_output_args(parser)
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        LOG_DIR,
        "online_download_and_summarize_all_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log",
    )
    logging.basicConfig(
        filename=log_file, format=settings.LOG_FORMAT, level=logging.INFO, force=True
    )
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    dates = list(days(args.start, args.end))
    if not dates:
        sys.exit("no days between --start and --end (or today)")
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(settings.WORKING_DIR, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="download_all_", dir=settings.WORKING_DIR)
    logging.info(
        f"download and summarize {dates[0]} - {dates[-1]} into {out_dir} "
        f"(production root {DEFAULT_ROOT}), working files in {tmp_root}, "
        f"overwrite={args.overwrite}"
    )
    try:
        point_folders(out_dir, tmp_root)
        for name, step in SOURCES:
            logging.info(f"===== {name} =====")
            try:
                step(dates, args.overwrite)
            except Exception:
                logging.exception(f"{name} failed")
            finally:
                os.chdir(settings.BASE_DIR)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    logging.info(f"finished, log: {log_file}")


if __name__ == "__main__":
    main()
