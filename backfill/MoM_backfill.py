"""
MoM_backfill.py
    -- regenerate the MoM products (GFMS, HWRF, DFO, VIIRS MoM and Final_Alert,
       and the same for the HAFS chain) for the last N days from summaries
       that already exist locally

    Offline and MoM-only:
      - nothing is downloaded or requested from any host: the *_summary and
        GloFAS files are expected to be in place. A cycle whose GFMS summary
        is missing is skipped (the MoM would download GFMS), and a missing
        HWRF/HAFS rain summary is never filled in from the hosts.
      - the only files written are the MoM and final alert outputs:
        GFMS_MoM, HWRF_MoM, DFO_MoM, VIIRS_MoM, Final_Alert, HAFS_MoM,
        DFO_HAFS_MoM, VIIRS_HAFS_MoM, Final_Alerts_hafs. The MoM steps' own
        temporary files go to a temporary folder (removed at the end) instead
        of the processing folders, and the log to backfill/logs/.
      - HAFS: only its MoM chain is run; cycles HAFS has not processed yet
        are skipped, never processed here.
    The GloFAS file of each step is chosen by the MoM code of the cron jobs
    (e.g. the previous GloFAS for the 00 and 06 HWRF cycles).

**EXAMPLE**
    python backfill/MoM_backfill.py --days 7
    python backfill/MoM_backfill.py --days 3 --end 20261010 --overwrite
    python backfill/MoM_backfill.py --days 7 --dry-run
"""

import argparse
import contextlib
import glob
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# the project modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
import GFMS_tool  # noqa: E402
import HWRF_MoM  # noqa: E402
from DFO_MoM import update_DFO_MoM
from GFMS_MoM import flood_severity
from HWRF_MoM import final_alert_pdc, update_HWRF_MoM, update_HWRFMoM_DFO_VIIRS
from utilities import findLatestGlofas
from VIIRS_MoM import update_VIIRS_MoM

HOURS = ["00", "06", "12", "18"]
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
PROC_DIRS = ["GFMS_PROC_DIR", "HWRF_PROC_DIR", "DFO_PROC_DIR", "VIIRS_PROC_DIR"]


def _no_download(*args):
    raise RuntimeError(f"MoM backfill never downloads ({args})")


@contextlib.contextmanager
def offline(tmpdir, hafs_tool=None):
    """MoM steps with no host access and their temporary files in tmpdir
    instead of the processing folders, always put back afterwards"""
    saved = {k: getattr(settings, k) for k in PROC_DIRS}
    saved_fixdate = GFMS_tool.GFMS_fixdate
    saved_no_storms = HWRF_MoM.no_storms_published
    saved_work = hafs_tool.WORK_DIR if hafs_tool else None
    try:
        for k in PROC_DIRS:
            setattr(settings, k, tmpdir)
        # a missing GFMS summary would be downloaded
        GFMS_tool.GFMS_fixdate = _no_download
        # asks the HWRF/HAFS hosts and writes an empty rain summary
        HWRF_MoM.no_storms_published = lambda hwrf_cycle: False
        if hafs_tool:
            # hafs_folders points the processing folders at WORK_DIR
            hafs_tool.WORK_DIR = tmpdir
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        GFMS_tool.GFMS_fixdate = saved_fixdate
        HWRF_MoM.no_storms_published = saved_no_storms
        if hafs_tool:
            hafs_tool.WORK_DIR = saved_work


def _getParser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="number of days to backfill, counted back from --end (inclusive)",
    )
    parser.add_argument(
        "--end",
        default=datetime.now(timezone.utc).strftime("%Y%m%d"),
        help="last day to backfill, YYYYMMDD (default: today, UTC)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="delete existing MoM outputs of these days (HAFS ones too) so they are regenerated",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report what would be run, write nothing",
    )
    return parser


def backfill_dates(end, days):
    """list of YYYYMMDD, oldest first"""
    end_date = datetime.strptime(end, "%Y%m%d")
    return [
        (end_date - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(days - 1, -1, -1)
    ]


def mom_outputs(adate):
    """all MoM output files of a day (YYYYMMDD)"""
    patterns = [
        os.path.join(settings.GFMS_MOM_DIR, f"Final_Attributes_{adate}.csv"),
        os.path.join(settings.GFMS_MOM_DIR, f"Attributes_Clean_{adate}.csv"),
        os.path.join(settings.HWRF_MOM_DIR, f"*_{adate}[0-9][0-9]HWRF*.csv"),
        os.path.join(settings.DFO_MOM_DIR, f"*_{adate}18MOM+DFOUpdated.csv"),
        os.path.join(settings.VIIRS_MOM_DIR, f"*_{adate}18MOM+DFO+VIIRSUpdated.csv"),
        os.path.join(
            settings.FINAL_MOM,
            f"Final_Attributes_{adate}[0-9][0-9]HWRF+MOM+DFO+VIIRSUpdated_PDC.csv",
        ),
    ]
    found = []
    for pattern in patterns:
        found.extend(glob.glob(pattern))
    return sorted(found)


def gfms_sum(adatehour):
    """GFMS summary of a cycle (YYYYMMDDHH)"""
    return os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{adatehour}.csv")


def glofas_for_gfms(adate):
    """GloFAS file for the GFMS MoM of a day, same rule as GFMS_processing"""
    glofascsv = os.path.join(settings.GLOFAS_DIR, f"threspoints_{adate}00.csv")
    if os.path.exists(glofascsv):
        return glofascsv
    glofas_latest = findLatestGlofas(settings.GLOFAS_DIR, adate, "csv")
    if glofas_latest:
        logging.warning(f"GloFAS for {adate}: using the previous {glofas_latest}")
        return os.path.join(settings.GLOFAS_DIR, glofas_latest)
    return ""


def run_step(name, func, *args, workdir=None):
    """run one MoM step, log and continue on failure"""
    try:
        if workdir:
            os.chdir(workdir)
        func(*args)
        return True
    except Exception:
        logging.exception(f"{name} failed for {args}")
        return False
    finally:
        os.chdir(settings.BASE_DIR)


def backfill_gfms(adate, dry_run):
    gfmscsv = gfms_sum(adate + "00")
    if not os.path.exists(gfmscsv):
        logging.warning(f"GFMS MoM {adate}: no GFMS summary, skipped")
        return
    glofascsv = glofas_for_gfms(adate)
    # today's GloFAS is published later in the day; a fallback to an older one
    # would block the scheduled run that uses the same day file
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if adate == today and os.path.basename(glofascsv) != f"threspoints_{adate}00.csv":
        logging.error(f"GFMS MoM {adate}: today's GloFAS not available yet, stop here")
        sys.exit(1)
    if not glofascsv:
        logging.warning(f"GFMS MoM {adate}: no GloFAS available, skipped")
        return
    logging.info(
        f"GFMS MoM {adate}: gfms={os.path.basename(gfmscsv)}, "
        f"glofas={os.path.basename(glofascsv)}"
    )
    if not dry_run:
        run_step(
            "GFMS MoM",
            flood_severity,
            gfmscsv,
            glofascsv,
            adate,
            workdir=settings.GFMS_PROC_DIR,
        )


def backfill_hwrf(adate, dry_run, with_alert=True):
    """HWRF MoM chain for the 4 cycles of a day, return the cycles run"""
    now = datetime.now(timezone.utc)
    done = []
    for hh in HOURS:
        adatehour = adate + hh
        cycle_time = datetime.strptime(adatehour, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if cycle_time > now:
            logging.info(f"HWRF MoM {adatehour}: cycle not reached yet, stop here")
            break
        # update_HWRF_MoM would call GFMS_fixdate (download) on a missing summary
        if not os.path.exists(gfms_sum(adatehour)):
            logging.warning(f"HWRF MoM {adatehour}: no GFMS summary, skipped")
            continue
        logging.info(f"HWRF MoM {adatehour}")
        if dry_run:
            done.append(hh)
            continue
        if not run_step(
            "HWRF MoM", update_HWRF_MoM, adatehour, workdir=settings.HWRF_PROC_DIR
        ):
            continue
        run_step(
            "HWRF+DFO+VIIRS MoM",
            update_HWRFMoM_DFO_VIIRS,
            adatehour,
            workdir=settings.HWRF_PROC_DIR,
        )
        if with_alert:
            run_step("Final alert", final_alert_pdc, adatehour)
        done.append(hh)
    return done


def backfill_dfo_viirs(adate, hwrf_done, dry_run):
    # update_DFO_MoM force-generates missing HWRF cycles, which could download
    # GFMS, so only run it when every cycle of the day went through
    if hwrf_done != HOURS:
        logging.warning(
            f"DFO/VIIRS MoM {adate}: HWRF cycles run {hwrf_done}, need all "
            f"{HOURS}, skipped"
        )
        return
    logging.info(f"DFO MoM {adate}, VIIRS MoM {adate}")
    if not dry_run:
        run_step("DFO MoM", update_DFO_MoM, adate, workdir=settings.DFO_PROC_DIR)
        run_step("VIIRS MoM", update_VIIRS_MoM, adate, workdir=settings.VIIRS_PROC_DIR)


def backfill_hafs(hafs_tool, warmup, dates, dry_run):
    """the HAFS MoM chain of the same days (HAFS_tool.run without the HAFS
    data step): HAFS MoM, +DFO+VIIRS and final alert of each cycle, then the
    DFO and VIIRS HAFS MoM of each day; a cycle HAFS has not processed yet
    stops its day"""
    for adate in [warmup] + dates:
        is_warmup = adate == warmup
        logging.info(f"HAFS MoM {adate}{' (warm-up, no alerts)' if is_warmup else ''}")
        if dry_run:
            continue
        due = True
        for hh in HOURS:
            due = hafs_tool.run_cycle_mom(adate + hh, with_alert=not is_warmup)
            if not due:
                break
        if not is_warmup:
            hafs_tool.run_day_dfo_viirs(adate)
        if not due:
            break


def main():
    args = _getParser().parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        LOG_DIR, f"MoM_backfill_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log"
    )
    logging.basicConfig(
        filename=log_file, format=settings.LOG_FORMAT, level=logging.INFO, force=True
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.getLogger().addHandler(console)

    dates = backfill_dates(args.end, args.days)
    logging.info(
        f"MoM backfill {dates[0]} - {dates[-1]}, overwrite={args.overwrite}, "
        f"dry_run={args.dry_run}"
    )

    hafs_tool = None
    if settings.HAFS_ENABLED:
        import HAFS_tool as hafs_tool  # eccodes: imported after the GDAL modules

    if args.overwrite:
        for adate in dates:
            outputs = mom_outputs(adate) + (hafs_tool.hafs_outputs(adate) if hafs_tool else [])
            for f in outputs:
                logging.info(f"remove: {f}")
                if not args.dry_run:
                    os.remove(f)

    # the final alert of a cycle needs the same cycle of the previous day,
    # so build the HWRF MoM of the day before the window first (no alerts)
    warmup = (datetime.strptime(dates[0], "%Y%m%d") - timedelta(days=1)).strftime(
        "%Y%m%d"
    )
    logging.info(f"warm-up day for the final alert pairs: {warmup}")

    tmpdir = tempfile.mkdtemp(prefix="mom_backfill_")
    try:
        with offline(tmpdir, hafs_tool):
            backfill_hwrf(warmup, args.dry_run, with_alert=False)

            for adate in dates:
                logging.info(f"===== backfill {adate} =====")
                backfill_gfms(adate, args.dry_run)
                hwrf_done = backfill_hwrf(adate, args.dry_run)
                backfill_dfo_viirs(adate, hwrf_done, args.dry_run)

            if hafs_tool:
                logging.info("===== HAFS MoM chain =====")
                backfill_hafs(hafs_tool, warmup, dates, args.dry_run)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    logging.info(f"MoM backfill finished, log: {log_file}")


if __name__ == "__main__":
    main()
