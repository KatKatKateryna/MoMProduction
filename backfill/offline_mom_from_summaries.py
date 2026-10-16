"""
offline_mom_from_summaries.py
    -- regenerate the MoM products (GFMS, HWRF, DFO, VIIRS MoM and final
       alerts, and the same for the HAFS chain) for the last N days from
       summaries that already exist locally, into --out-dir

    Inputs, only read: the GFMS, HWRF, HAFS and VIIRS summaries under
    --input-root (default: the parent of PRODUCT_DIR), and the GloFAS and DFO summaries under
    --glofas-dfo-root (default: the parent of PRODUCT_DIR; offline_summaries_from_images.py
    does not rebuild them). Each folder can be flat (ROOT/gfms_summary,
    ROOT/glofas, ROOT/dfo_summary, ..., as written by
    offline_summaries_from_images.py) or in the production layout
    (ROOT/Products/GFMS/GFMS_summary, ...); the flat one is used if it exists.

    Outputs, all under --out-dir and nowhere else. A flat OUT:
        OUT/gfms_mom          GFMS MoM
        OUT/hwrf_mom          HWRF MoM and HWRF+DFO+VIIRS        (tag HWRF)
        OUT/dfo_mom           DFO MoM of the HWRF chain
        OUT/viirs_mom         VIIRS MoM of the HWRF chain
        OUT/hafs_mom          HAFS MoM and HAFS+DFO+VIIRS        (tag HAFS)
        OUT/dfo_hafs_mom      DFO MoM of the HAFS chain
        OUT/viirs_hafs_mom    VIIRS MoM of the HAFS chain
        OUT/Final_Alerts      final alerts of both chains (…HWRF+MOM… and …HAFS+MOM…)
    An OUT that has every production MoM folder (the production root, the
    parent of PRODUCT_DIR) gets the production folders instead, the ones the
    cron jobs write: Products/GFMS/GFMS_MoM, HWRF/HWRF_MoM, DFO/DFO_MoM,
    VIIRS/VIIRS_MoM, HAFS/HAFS_MoM, DFO/DFO_HAFS_MoM, VIIRS/VIIRS_HAFS_MoM,
    the final alert folder of production.cfg and Final_Alerts_hafs.
    An output already in OUT is kept, unless --overwrite: then the outputs of
    the days in the window are deleted from OUT first. The day before the
    window is built too (no alerts), because each final alert pairs with the
    same cycle of the previous day; --overwrite does not delete it.

    Offline: nothing is downloaded or requested from any host. A cycle whose
    GFMS summary is missing is skipped (the MoM would download GFMS), and a
    missing rain summary is never filled in from the hosts. The MoM steps'
    temporary files go to a temporary folder (removed at the end), the log to
    backfill/logs/.

    Rain: the HWRF chain uses the HWRF summary of the previous cycle. With
    --hafs-fallback, when that HWRF summary is missing the HAFS summary of the
    same (previous) cycle is used instead (outputs still tagged HWRF; the log
    says which). With
    neither, the MoM goes on without rain once the cycle is 12 h old, as the
    cron job does. The HAFS chain runs a cycle only when the HAFS summary of
    the previous cycle exists.

    The GloFAS file of each step is chosen by the MoM code of the cron jobs
    (e.g. the previous GloFAS for the 00 and 06 cycles).

    Replay of the cron timing: the cron job takes the DFO, VIIRS and GloFAS
    files that exist when it runs a cycle, so which day it uses depends on
    when they were written. Each cycle is run with the unchanged MoM code
    against views of the DFO, VIIRS and GloFAS folders that hold only the
    files that existed at the cycle's cron run, by this schedule (cron of
    first_setup/setup_crontab.sh, and which run writes each file):
      - a cycle is run by the first HWRF job after it (2, 7, 13, 19 h), the
        HAFS chain 30 min later
      - DFO of D is written by the 03:00 DFO run on D
      - VIIRS of D by the 12:00 VIIRS run on D+1
      - GloFAS of D by the 11:00 GFMS run on D
    This gives 00/06: DFO D-1, VIIRS D-2, GloFAS D-1; 12: DFO D-1,
    VIIRS D-1, GloFAS D; 18: DFO D, VIIRS D-1, GloFAS D. A day published
    late by its host (e.g. VIIRS only at the 17:00 run) is not replayed.
    The rain summary of the previous cycle is used whenever it exists, as the
    cron job waits for it.

    Order: the GFMS MoM, the HWRF chain and the HAFS chain (MoM,
    +DFO+VIIRS and final alert of each cycle) run in parallel, each in its
    own process; then the DFO and VIIRS MoM of each day, one process per
    chain (they read that chain's 18 MoM and DFO MoM).

**EXAMPLE**
    python backfill/offline_mom_from_summaries.py --days 7 --out-dir <rerun folder> --dry-run
    python backfill/offline_mom_from_summaries.py --days 3 --end 20261012 --out-dir <rerun folder>
    python backfill/offline_mom_from_summaries.py --days 15 --end 20261015 --out-dir <production root> --overwrite
    python backfill/offline_mom_from_summaries.py --days 3 --end 20261012 --out-dir <rerun folder> \\
        --hafs-fallback --overwrite --input-root /data/MoM_copy --glofas-dfo-root /data/MoM_copy
"""

import argparse
import contextlib
import glob
import multiprocessing
import re
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# the project modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
import folder_layout  # noqa: E402
import GFMS_tool  # noqa: E402
import HWRF_MoM  # noqa: E402
from DFO_MoM import update_DFO_MoM  # noqa: E402
from GFMS_MoM import flood_severity  # noqa: E402
from HWRF_MoM import final_alert_pdc, update_HWRF_MoM, update_HWRFMoM_DFO_VIIRS  # noqa: E402
from utilities import findLatestGlofas  # noqa: E402
from VIIRS_MoM import update_VIIRS_MoM  # noqa: E402

HOURS = ["00", "06", "12", "18"]
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
DEFAULT_ROOT = folder_layout.DEFAULT_ROOT  # parent of PRODUCT_DIR
PROC_DIRS = ["GFMS_PROC_DIR", "HWRF_PROC_DIR", "DFO_PROC_DIR", "VIIRS_PROC_DIR"]
INPUT_DIRS = ["GFMS_SUM_DIR", "GLOFAS_DIR", "GLOFAS_SUM_DIR", "DFO_SUM_DIR", "VIIRS_SUM_DIR"]
OUT_DIRS = ["gfms_mom", "hwrf_mom", "dfo_mom", "viirs_mom", "hafs_mom", "dfo_hafs_mom",
            "viirs_hafs_mom", "final_hwrf", "final_hafs"]
# per chain: rain summaries (settings name, prefix), file tag, output folders
CHAINS = {
    "HWRF": {"rain": ("HWRF_SUM_DIR", "hwrf"), "tag": "HWRF",
             "mom": "hwrf_mom", "dfo": "dfo_mom", "viirs": "viirs_mom", "final": "final_hwrf"},
    "HAFS": {"rain": ("HAFS_SUM_DIR", "hafs"), "tag": "HAFS",
             "mom": "hafs_mom", "dfo": "dfo_hafs_mom", "viirs": "viirs_hafs_mom", "final": "final_hafs"},
}
# output folder -> settings folder of the production layout (read before any
# is changed); in a flat out-dir both final alert folders are Final_Alerts
OUT_COMMON = {
    "gfms_mom": "GFMS_MOM_DIR", "hwrf_mom": "HWRF_MOM_DIR", "dfo_mom": "DFO_MOM_DIR",
    "viirs_mom": "VIIRS_MOM_DIR", "hafs_mom": "HAFS_MOM_DIR", "dfo_hafs_mom": "DFO_HAFS_DIR",
    "viirs_hafs_mom": "VIIRS_HAFS_DIR", "final_hwrf": "FINAL_MOM_DIR", "final_hafs": "FINAL_HAFS_DIR",
}
OUT_COMMON = {name: os.path.relpath(getattr(settings, attr), DEFAULT_ROOT) for name, attr in OUT_COMMON.items()}
OUT_FLAT = {"final_hwrf": "Final_Alerts", "final_hafs": "Final_Alerts"}
# settings input folder -> folder_layout name
GLOFAS_DFO = ["GLOFAS_DIR", "GLOFAS_SUM_DIR", "DFO_SUM_DIR"]
LAYOUT = {
    "GFMS_SUM_DIR": "gfms_summary", "GLOFAS_DIR": "glofas", "GLOFAS_SUM_DIR": "glofas",
    "DFO_SUM_DIR": "dfo_summary", "VIIRS_SUM_DIR": "viirs_summary",
    "HWRF_SUM_DIR": "hwrf_summary", "HAFS_SUM_DIR": "hafs_summary",
}
# cycle and publication schedule: see "Cycle and publication schedule" in
# Backfill guide.md, also for how the times were confirmed
# cron (first_setup/setup_crontab.sh): the HWRF job runs at these hours and
# runs the cycles before it; the HAFS job 30 minutes later
CRON_HOURS = [2, 7, 13, 19]
CRON_MINUTE = {"HWRF": 0, "HAFS": 30}
# input folders replayed as of the cycle's run: the day in the file name,
# and the cron run that writes the file (days after that day, hour)
REPLAYED = {
    "DFO_SUM_DIR": (re.compile(r"^DFO_(\d{8})\.csv$"), 0, 3),            # 03:00 DFO run on D
    "VIIRS_SUM_DIR": (re.compile(r"^VIIRS_Flood_(\d{8})\.csv$"), 1, 12),  # 12:00 VIIRS run on D+1
    "GLOFAS_DIR": (re.compile(r"^threspoints_(\d{8})00\."), 0, 11),      # 11:00 GFMS run on D
}
# every settings value changed here, put back at the end
PATCHED = PROC_DIRS + INPUT_DIRS + [
    "HWRF_SUM_DIR", "HWRF_SUM_PREFIX", "HWRF_FILE_TAG", "GFMS_MOM_DIR", "HWRF_MOM_DIR",
    "DFO_MOM_DIR", "VIIRS_MOM_DIR", "FINAL_MOM", "FINAL_MOM_DIR",
]


def _no_download(*args):
    raise RuntimeError(f"MoM backfill never downloads ({args})")


class Folders:
    """input and output folders of the run"""

    def __init__(self, input_root, glofas_dfo_root, out_dir):
        self.input_root = os.path.abspath(input_root)
        self.glofas_dfo_root = os.path.abspath(glofas_dfo_root)
        self.out_dir = os.path.abspath(out_dir)
        # the production layout when out_dir has every production MoM
        # folder (OUT_COMMON), else flat
        self.out_common = all(os.path.isdir(os.path.join(self.out_dir, d)) for d in OUT_COMMON.values())

    def input(self, name):
        """settings input folder `name` under the input root (GloFAS and DFO:
        under the GloFAS/DFO root), flat or common layout"""
        root = self.glofas_dfo_root if name in GLOFAS_DFO else self.input_root
        return folder_layout.folder(root, LAYOUT[name])

    def out(self, name):
        if self.out_common:
            return os.path.join(self.out_dir, OUT_COMMON[name])
        return os.path.join(self.out_dir, OUT_FLAT.get(name, name))

    def rain_summary(self, chain, cycle):
        """rain summary of a cycle (YYYYMMDDHH) for a chain"""
        sum_dir, prefix = CHAINS[chain]["rain"]
        return os.path.join(self.input(sum_dir), f"{prefix}.{cycle}rainfall.csv")


def run_time(cycle, tag):
    """when the cron job runs a cycle (YYYYMMDDHH) of a chain"""
    day = datetime.strptime(cycle[:8], "%Y%m%d")
    later = [h for h in CRON_HOURS if h > int(cycle[8:])]
    hours = later[0] if later else 24 + CRON_HOURS[0]
    return day + timedelta(hours=hours, minutes=CRON_MINUTE[tag])


def existed(key, name, when):
    """True if file `name` of replayed input folder `key` is written by `when`"""
    rx, days, hour = REPLAYED[key]
    m = rx.match(name)
    if not m:
        return True
    return datetime.strptime(m.group(1), "%Y%m%d") + timedelta(days=days, hours=hour) <= when


def as_of_views(sources, tmpdir, when):
    """{settings name: folder} with links to the files of the replayed input
    folders (sources) that existed at `when`"""
    views = {}
    for key, src in sources.items():
        view = os.path.join(tmpdir, "as_of", key)
        shutil.rmtree(view, ignore_errors=True)
        os.makedirs(view)
        for name in os.listdir(src):
            if existed(key, name, when):
                os.symlink(os.path.join(src, name), os.path.join(view, name))
        views[key] = view
    return views


def replayed_inputs(views, adatehour):
    """the DFO, VIIRS and GloFAS days the MoM code picks from the views"""
    datestr, hh = adatehour[:8], adatehour[8:]
    dfo = HWRF_MoM.find_latest_summary(datestr, views["DFO_SUM_DIR"], "DFO_{}.csv", hh)[0]
    viirs = HWRF_MoM.find_latest_summary(datestr, views["VIIRS_SUM_DIR"], "VIIRS_Flood_{}.csv", hh)[0]
    glofas = f"threspoints_{datestr}00.csv"
    # update_HWRF_MoM: the earlier GloFAS before 10 h or when the day's is missing
    if not os.path.exists(os.path.join(views["GLOFAS_DIR"], glofas)) or int(hh) < 10:
        glofas = findLatestGlofas(views["GLOFAS_DIR"], adatehour, "csv") or glofas
    return dfo or "none", viirs or "none", glofas[12:20]


@contextlib.contextmanager
def offline(folders, tmpdir):
    """no host access, the inputs under the input root, temporary files in
    tmpdir; everything put back afterwards"""
    saved = {k: getattr(settings, k) for k in PATCHED}
    saved_fixdate = GFMS_tool.GFMS_fixdate
    saved_no_storms = HWRF_MoM.no_storms_published
    try:
        for k in PROC_DIRS:
            setattr(settings, k, tmpdir)
        for k in INPUT_DIRS:
            setattr(settings, k, folders.input(k))
        settings.GFMS_MOM_DIR = folders.out("gfms_mom")
        # a missing GFMS summary would be downloaded
        GFMS_tool.GFMS_fixdate = _no_download
        # asks the HWRF/HAFS hosts and writes an empty rain summary
        HWRF_MoM.no_storms_published = lambda hwrf_cycle: False
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        GFMS_tool.GFMS_fixdate = saved_fixdate
        HWRF_MoM.no_storms_published = saved_no_storms


def use_chain(folders, chain, rain_chain=None):
    """point the reused HWRF/DFO/VIIRS MoM functions at a chain's rain
    summaries (or those of rain_chain), file tag and output folders"""
    c = CHAINS[chain]
    sum_dir, prefix = CHAINS[rain_chain or chain]["rain"]
    settings.HWRF_SUM_DIR = folders.input(sum_dir)
    settings.HWRF_SUM_PREFIX = prefix
    settings.HWRF_FILE_TAG = c["tag"]
    settings.HWRF_MOM_DIR = folders.out(c["mom"])
    settings.DFO_MOM_DIR = folders.out(c["dfo"])
    settings.VIIRS_MOM_DIR = folders.out(c["viirs"])
    settings.FINAL_MOM = settings.FINAL_MOM_DIR = folders.out(c["final"])


def _getParser():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--days", type=int, required=True,
        help="number of days to backfill, counted back from --end (inclusive)",
    )
    parser.add_argument(
        "--end", default=datetime.now(timezone.utc).strftime("%Y%m%d"),
        help="last day to backfill, YYYYMMDD (default: today, UTC)",
    )
    parser.add_argument(
        "--input-root", default=DEFAULT_ROOT,
        help=f"folder with the GFMS, HWRF, HAFS and VIIRS summaries, flat or in the layout of "
             f"{DEFAULT_ROOT} (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--glofas-dfo-root", default=DEFAULT_ROOT,
        help=f"folder with the GloFAS and DFO summaries, flat or in the layout of {DEFAULT_ROOT} "
             f"(default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--out-dir", required=True,
        help="folder the outputs are written to: flat (gfms_mom, hwrf_mom, ..., Final_Alerts) or, "
             "when it has every production MoM folder (the production root), those folders",
    )
    parser.add_argument(
        "--hafs-fallback", action="store_true",
        help="HWRF chain: use the HAFS summary of a cycle that has no HWRF summary",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="delete the outputs of these days from --out-dir first, so they are regenerated",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="only report what would be run and removed, write nothing",
    )
    return parser


def backfill_dates(end, days):
    """list of YYYYMMDD, oldest first"""
    end_date = datetime.strptime(end, "%Y%m%d")
    return [
        (end_date - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(days - 1, -1, -1)
    ]


def mom_outputs(folders, adate):
    """the output files of a day (YYYYMMDD) in the output folder"""
    patterns = [
        os.path.join(folders.out("gfms_mom"), f"Final_Attributes_{adate}.csv"),
        os.path.join(folders.out("gfms_mom"), f"Attributes_Clean_{adate}.csv"),
    ]
    for c in CHAINS.values():
        patterns += [
            os.path.join(folders.out(c["mom"]), f"*_{adate}[0-9][0-9]{c['tag']}*.csv"),
            os.path.join(folders.out(c["dfo"]), f"*_{adate}18MOM+DFOUpdated.csv"),
            os.path.join(folders.out(c["viirs"]), f"*_{adate}18MOM+DFO+VIIRSUpdated.csv"),
            os.path.join(folders.out(c["final"]),
                         f"Final_Attributes_{adate}[0-9][0-9]{c['tag']}+MOM+DFO+VIIRSUpdated_PDC.csv"),
        ]
    return sorted(f for p in patterns for f in glob.glob(p))


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
    # today's GloFAS is published later in the day: the cron job waits for it
    # instead of falling back to an older one
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    if adate == today and os.path.basename(glofascsv) != f"threspoints_{adate}00.csv":
        logging.warning(f"GFMS MoM {adate}: today's GloFAS not available yet, skipped")
        return
    if not glofascsv:
        logging.warning(f"GFMS MoM {adate}: no GloFAS available, skipped")
        return
    logging.info(
        f"GFMS MoM {adate}: gfms={os.path.basename(gfmscsv)}, "
        f"glofas={os.path.basename(glofascsv)}"
    )
    if not dry_run:
        run_step("GFMS MoM", flood_severity, gfmscsv, glofascsv, adate,
                 workdir=settings.GFMS_PROC_DIR)


def rain_chain_for(folders, chain, adatehour, hafs_fallback):
    """whose rain summary the MoM of a cycle uses (that of the previous
    cycle): the chain's own, HAFS for the HWRF chain with --hafs-fallback when
    HWRF has none, or None when the HAFS chain has none"""
    rain_cycle = (datetime.strptime(adatehour, "%Y%m%d%H") - timedelta(hours=6)).strftime("%Y%m%d%H")
    if os.path.exists(folders.rain_summary(chain, rain_cycle)):
        return chain
    if chain == "HWRF":
        if hafs_fallback and os.path.exists(folders.rain_summary("HAFS", rain_cycle)):
            return "HAFS"
        return chain  # goes on without rain once 12 h old, as the cron job
    return None


def backfill_chain(folders, tmpdir, chain, adate, dry_run, hafs_fallback=False, with_alert=True):
    """MoM, +DFO+VIIRS and final alert of the 4 cycles of a day for a chain,
    return the cycles run"""
    now = datetime.now(timezone.utc)
    done = []
    for hh in HOURS:
        adatehour = adate + hh
        cycle_time = datetime.strptime(adatehour, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if cycle_time > now:
            logging.info(f"{chain} MoM {adatehour}: cycle not reached yet, stop here")
            break
        # update_HWRF_MoM would call GFMS_fixdate (download) on a missing summary
        if not os.path.exists(gfms_sum(adatehour)):
            logging.warning(f"{chain} MoM {adatehour}: no GFMS summary, skipped")
            continue
        rain_chain = rain_chain_for(folders, chain, adatehour, hafs_fallback)
        if rain_chain is None:
            logging.warning(f"{chain} MoM {adatehour}: no {chain} summary of the previous cycle, skipped")
            continue
        source = f", rain from {rain_chain} (no HWRF summary)" if rain_chain != chain else ""
        # the DFO, VIIRS and GloFAS files as the cron job found them
        when = run_time(adatehour, CHAINS[chain]["tag"])
        sources = {key: getattr(settings, key) for key in REPLAYED}
        views = as_of_views(sources, tmpdir, when)
        dfo, viirs, glofas = replayed_inputs(views, adatehour)
        logging.info(f"{chain} MoM {adatehour}{source}: as of {when:%Y-%m-%d %H:%M}: "
                     f"DFO {dfo}, VIIRS {viirs}, GloFAS {glofas}")
        if dry_run:
            done.append(hh)
            continue
        ok = False
        try:
            for key, view in views.items():
                setattr(settings, key, view)
            # only update_HWRF_MoM reads the rain summary
            use_chain(folders, chain, rain_chain)
            ok = run_step(f"{chain} MoM", update_HWRF_MoM, adatehour, workdir=settings.HWRF_PROC_DIR)
            use_chain(folders, chain)
            if ok:
                run_step(f"{chain}+DFO+VIIRS MoM", update_HWRFMoM_DFO_VIIRS, adatehour,
                         workdir=settings.HWRF_PROC_DIR)
        finally:
            for key, src in sources.items():
                setattr(settings, key, src)
        if not ok:
            continue
        if with_alert:
            run_step(f"{chain} final alert", final_alert_pdc, adatehour)
        done.append(hh)
    return done


def backfill_dfo_viirs(chain, adate, cycles_done, dry_run):
    # update_DFO_MoM force-generates missing cycles, so only run it when
    # every cycle of the day went through
    if cycles_done != HOURS:
        logging.warning(
            f"{chain} DFO/VIIRS MoM {adate}: cycles run {cycles_done}, need all {HOURS}, skipped"
        )
        return
    logging.info(f"{chain} DFO MoM {adate}, VIIRS MoM {adate}")
    if not dry_run:
        run_step(f"{chain} DFO MoM", update_DFO_MoM, adate, workdir=settings.DFO_PROC_DIR)
        run_step(f"{chain} VIIRS MoM", update_VIIRS_MoM, adate, workdir=settings.VIIRS_PROC_DIR)


def in_process(name, func):
    """run func(tmpdir) offline in this process, with its own temporary folder"""
    multiprocessing.current_process().name = name
    tmpdir = tempfile.mkdtemp(prefix=f"mom_backfill_{name}_")
    try:
        return func(tmpdir)
    except Exception:
        logging.exception(f"{name} failed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_gfms(job):
    folders, dates, _, args = job

    def work(tmpdir):
        with offline(folders, tmpdir):
            for adate in dates:
                logging.info(f"===== GFMS MoM {adate} =====")
                backfill_gfms(adate, args.dry_run)
    return in_process("GFMS", work)


def run_chain(job, chain):
    """MoM, +DFO+VIIRS and final alert of every cycle of a chain; returns
    {day: cycles run}"""
    folders, dates, warmup, args = job

    def work(tmpdir):
        done = {}
        with offline(folders, tmpdir):
            use_chain(folders, chain)
            logging.info(f"===== {chain} chain, warm-up {warmup} =====")
            backfill_chain(folders, tmpdir, chain, warmup, args.dry_run, args.hafs_fallback, with_alert=False)
            for adate in dates:
                logging.info(f"===== {chain} chain {adate} =====")
                done[adate] = backfill_chain(folders, tmpdir, chain, adate, args.dry_run, args.hafs_fallback)
        return done
    return in_process(chain, work) or {}


def run_dfo_viirs(job, chain, cycles_done):
    """DFO and VIIRS MoM of every day of a chain"""
    folders, dates, _, args = job

    def work(tmpdir):
        with offline(folders, tmpdir):
            use_chain(folders, chain)
            for adate in dates:
                backfill_dfo_viirs(chain, adate, cycles_done.get(adate, []), args.dry_run)
    return in_process(f"{chain}-DFO-VIIRS", work)


def main():
    args = _getParser().parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        LOG_DIR, f"offline_mom_from_summaries_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log"
    )
    logging.basicConfig(
        filename=log_file, format=settings.LOG_FORMAT.replace("%(module)s", "%(processName)s - %(module)s"),
        level=logging.INFO, force=True,
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(processName)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(console)

    folders = Folders(args.input_root, args.glofas_dfo_root, args.out_dir)
    dates = backfill_dates(args.end, args.days)
    chains = ["HWRF"] + (["HAFS"] if settings.HAFS_ENABLED else [])
    logging.info(
        f"MoM backfill {dates[0]} - {dates[-1]}: input {folders.input_root}, GloFAS/DFO "
        f"{folders.glofas_dfo_root}, output "
        f"{folders.out_dir} ({'production layout' if folders.out_common else 'flat'}), chains {' '.join(chains)}, hafs_fallback={args.hafs_fallback}, "
        f"overwrite={args.overwrite}, dry_run={args.dry_run}"
    )

    if not args.dry_run:
        for name in OUT_DIRS:
            os.makedirs(folders.out(name), exist_ok=True)
    if args.overwrite:
        for adate in dates:
            for f in mom_outputs(folders, adate):
                logging.info(f"remove: {f}")
                if not args.dry_run:
                    os.remove(f)

    # the final alert of a cycle needs the same cycle of the previous day,
    # so build the MoM of the day before the window first (no alerts)
    warmup = (datetime.strptime(dates[0], "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    logging.info(f"warm-up day for the final alert pairs: {warmup}")

    # the MoM code changes settings and the working folder, so the parallel
    # parts are processes (forked: they inherit the settings and the log)
    ctx = multiprocessing.get_context("fork")
    job = (folders, dates, warmup, args)
    with ctx.Pool(1 + len(chains)) as pool:
        phase1 = [pool.apply_async(run_gfms, (job,))]
        phase1 += [pool.apply_async(run_chain, (job, chain)) for chain in chains]
        results = [r.get() for r in phase1]
    cycles_done = dict(zip(chains, results[1:]))
    with ctx.Pool(len(chains)) as pool:
        phase2 = [pool.apply_async(run_dfo_viirs, (job, chain, cycles_done[chain])) for chain in chains]
        [r.get() for r in phase2]

    logging.info(f"MoM backfill finished, log: {log_file}")


if __name__ == "__main__":
    main()
