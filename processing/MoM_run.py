"""
main script to run various MoM processing jobs
"""

prolog = """
**PROGRAM**
    processing/MoM_run.py, run from the repository root as
    python -m processing.MoM_run (run_mom_job.sh does it so)
      
**PURPOSE**
    main script to run various MoM processing jobs

**USAGE**
"""
epilog = """
**EXAMPLE**
    python -m processing.MoM_run -j GFMS
    python -m processing.MoM_run -j DFO --fixdate 20261007
               
"""

import argparse
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
import sys

# the repository root, which holds settings.py and first_setup/: on the path
# also when this file is run directly (python processing/MoM_run.py)
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

# point PROJ at GDAL's own proj.db in a Windows .venv; before any PROJ-dependent import
import processing.proj_env  # noqa: F401

# needed to add gdal tp PATH, so that "gdal" console commands are accessible through os.system or subprocess
# even in the modules that don't import Python gdal bindings (especially for Windows .venv)
from osgeo import gdal

# append "wget" path for Windows
if os.name == "nt":
    installers_path = os.path.join(REPO_DIR, "first_setup")
    os.environ["PATH"] = installers_path + os.pathsep + os.environ["PATH"]

import settings
from processing.dfo.DFO_MoM import batchrun_DFO_MoM
from processing.dfo.DFO_tool import DFO_cron
from processing.gfms.GFMS_tool import GFMS_cron, GFMS_fixdate
from processing.hwrf.HWRF_MoM import batchrun_HWRF_MoM
from processing.hwrf.HWRF_tool import HWRF_cron
from processing.viirs.VIIRS_MoM import batchrun_VIIRS_MoM
from processing.viirs.VIIRS_tool import VIIRS_cron
from processing.utilities import bbox_prepare, run_label

# the run times of each job, "HH:MM"
CRON_TIMES = {
    job: [f"{int(hour):02d}:{int(minute):02d}" for hour in hours.split(",")]
    for job, (minute, hours) in settings.CRON_SCHEDULE.items()
}


installers_path = os.path.join(REPO_DIR, "first_setup")
sys.path.insert(0, installers_path)


def _getParser():
    parser = argparse.ArgumentParser(
        description=prolog,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    joblist = [
        "GFMS",
        "HWRF",
        "HWRF_MOM",
        "DFO",
        "DFO_MOM",
        "VIIRS",
        "VIIRS_MOM",
        "HAFS",
        "BACKFILL",
    ]
    parser.add_argument(
        "-j",
        "--job",
        action="store",
        type=str.upper,
        dest="job",
        required=True,
        help="run a job",
        choices=joblist,
    )
    parser.add_argument(
        "-fd",
        "--fixdate",
        action="store",
        dest="adate",
        required=False,
        help="fix a date",
    )
    # -j BACKFILL: the options of the backfill scripts (backfill/backfill_args.py)
    parser.add_argument(
        "--start", help="BACKFILL: first day, YYYYMMDD (default: today, UTC)"
    )
    parser.add_argument(
        "--end", help="BACKFILL: last day, YYYYMMDD (default: today, UTC)"
    )
    parser.add_argument(
        "--out-dir",
        help="BACKFILL: root the products go under, in the production layout "
        "(default: the folders of production.cfg)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="BACKFILL: replace outputs that already exist (default: keep them)",
    )

    return parser


# console tools the jobs shell out to by name. Without the conda environment
# activated they are unresolvable: gdal_translate then converts nothing and the
# failure only surfaces much later (a missing mosaic, or a silently partial
# one). Checked once per run, before any job starts.
REQUIRED_TOOLS = ("gdal_translate", "wget")


def require_tools():
    """exit before doing any work if a console tool is not on PATH"""
    missing = [t for t in REQUIRED_TOOLS if shutil.which(t) is None]
    if missing:
        logging.error(
            f"not on PATH: {', '.join(missing)} - activate the conda "
            "environment first (run_mom_job.sh does it)"
        )
        sys.exit(1)


def run_job(cronjob):
    """run various cron job"""
    logging.info(f"run {cronjob} ({run_label(CRON_TIMES.get(cronjob, []))})")
    logging.info(f"main PID: {os.getpid()}")
    if cronjob == "GFMS":
        GFMS_cron()
    elif cronjob == "HWRF":
        HWRF_cron()
        batchrun_HWRF_MoM()
    elif cronjob == "DFO":
        DFO_cron()
        batchrun_DFO_MoM()
    elif cronjob == "VIIRS":
        VIIRS_cron()
        batchrun_VIIRS_MoM()
    elif cronjob == "HAFS":
        # imported here only: the other jobs never load the HAFS code
        from processing.hafs.HAFS_tool import HAFS_cron

        HAFS_cron()
    else:
        logging.warning(f"unknown job: {cronjob}")
        return

    logging.info(f"finished {cronjob}")


def run_fixdate(cronjob, adate):
    """run fixdate funtion"""
    logging.info(f"run fixdate {cronjob} {adate}")
    if cronjob == "GFMS":
        GFMS_fixdate(adate)
    elif cronjob == "VIIRS":
        VIIRS_cron(adate)
    else:
        return


def run_backfill(results):
    """-j BACKFILL: the days from --start to --end made from the hosts, as
    the cron jobs would have made them: every source downloaded and
    summarized (backfill/online_download_and_summarize_all.py), then the MoM
    and alerts of both chains from those summaries
    (backfill/offline_mom_from_summaries.py), both into --out-dir. Nothing is
    published (run_mom_job.sh skips it after BACKFILL). On a new
    installation, e.g. -j BACKFILL --start <two days ago>; without --start,
    today."""
    # without --start / --end: today (UTC), fixed here once, so both steps
    # cover the same days even when the run goes past midnight
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    start = results.start or today
    options = ["--start", start, "--end", results.end or today]
    if results.out_dir:
        options += ["--out-dir", results.out_dir]
    if results.overwrite:
        options.append("--overwrite")
    root = os.path.join(REPO_DIR, "backfill")
    mom_options = list(options)
    if results.out_dir:
        # the MoM is made from the summaries just made there
        mom_options += ["--input-root", results.out_dir]
    for script, script_options in (
        ("online_download_and_summarize_all.py", options),
        ("offline_mom_from_summaries.py", mom_options),
    ):
        logging.info(f"BACKFILL: {script} {' '.join(script_options)}")
        status = subprocess.run(
            [sys.executable, os.path.join(root, script)] + script_options,
            cwd=REPO_DIR,
        ).returncode
        if status != 0:
            logging.error(f"BACKFILL: {script} failed ({status}), stopped")
            sys.exit(status)
    logging.info("finished BACKFILL")


def main():
    """execute momjob"""
    # Read command line arguments
    parser = _getParser()
    results = parser.parse_args()
    # keep each job's log in its own file
    settings.configure_logging(results.job)
    require_tools()
    # with a BBOX set: the watersheds it covers and the extent to download,
    # worked out once here and reused by every step of the job
    bbox_prepare()
    if results.job == "BACKFILL":
        run_backfill(results)
    elif results.adate:
        run_fixdate(results.job, results.adate)
    else:
        run_job(results.job)


if __name__ == "__main__":
    main()
