"""
offline_mom_from_summaries.py
    -- regenerate the MoM products (GFMS, HWRF, DFO, VIIRS MoM and final
       alerts, and the same for the HAFS chain) for the last N days from
       summaries that already exist locally, into --out-dir

    Everything is read from the Products tree of a root; the processing tree
    is never read, and its folders are pointed at the temporary folder. The
    per-step data a lead time needs lives in Products too (the GFMS cycle
    image, the HAFS rain records), but is not rebuilt yet, so Lead_h_GFMS and
    Lead_h_HAFS come out -1.

    Inputs, only read: the GFMS, HWRF, HAFS and VIIRS summaries under
    --input-root (default: the parent of PRODUCT_DIR), and the GloFAS and DFO
    summaries under --glofas-dfo-root (default: --input-root, for an archive
    that keeps them somewhere else). A root is in the production layout:
    every folder where settings.py puts it, summaries under ROOT/Products
    (GFMS/GFMS_summary, GLOFAS, ...), the step files beside the summaries
    (GFMS/GFMS_summary/steps, HAFS/HAFS_summary/steps).

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
    the days in the window are deleted from OUT first.

    The final alert of a cycle pairs with the MoM of the previous cycle, 6 h
    earlier. For the first cycle of the window that one is not in OUT, so it
    is read, not copied, from the MoM folder of --input-root, which is only
    read from. With --warmup, or when the input root has not got that cycle,
    it is built in OUT instead: that one cycle, no alert.

    Offline: nothing is downloaded or requested from any host. A cycle whose
    GFMS summary is missing is skipped (the MoM would download GFMS), and a
    missing rain summary is never filled in from the hosts. The MoM steps'
    temporary files go to a temporary folder (removed at the end), the log to
    backfill/logs/.

    Rain: each chain uses only its own rain summary, of the previous cycle;
    the chains are never mixed. The HWRF chain goes on without rain when
    neither that summary nor the one 12 h before it is there, as the cron job
    does; the HAFS chain runs a cycle only when its own summary of the
    previous cycle exists.

    Which file of each model a cycle uses is decided by the production code
    itself, from the cycle (YYYYMMDDHH) alone: no wall clock, no cron timing,
    so the same cycle on the same archive always gives the same output. The
    functions that choose, all called here rather than reimplemented:
      - DFO     cycle_inputs.dfo_for_cycle: from D-2 back for the 00 cycle,
                from D-1 back for 06, 12 and 18
      - VIIRS   cycle_inputs.viirs_for_cycle: from D-2 back for 00 and 06,
                from D-1 back for 12 and 18
      - GFMS    cycle_inputs.gfms_for_cycle: the cycle's own summary, else
                the one 6 or 12 h before it
      - GloFAS  cycle_inputs.glofas_for_cycle: the day's own threspoints_D00
                for the 12 and 18 cycles, the latest file before D for 00 and
                06, and never one older than GLOFAS_MAX_AGE_DAYS. A GFMS MoM
                day is its 00 cycle, so it gets the file before D too
      - rain    cycle_inputs.rain_for_cycle: the chain's summary of the previous cycle,
                else the one 12 h before, else none

    Order: the GFMS MoM, the HWRF chain and the HAFS chain (MoM,
    +DFO+VIIRS and final alert of each cycle) run in parallel, each in its
    own process; then the DFO and VIIRS MoM of each day, one process per
    chain (they read that chain's 18 MoM and DFO MoM).

**EXAMPLE**
    python backfill/offline_mom_from_summaries.py --days 7 --out-dir <rerun folder> --dry-run
    python backfill/offline_mom_from_summaries.py --days 3 --end 20261012 --out-dir <rerun folder>
    python backfill/offline_mom_from_summaries.py --days 15 --end 20261015 --out-dir <production root> --overwrite
    python backfill/offline_mom_from_summaries.py --days 3 --end 20261012 --out-dir <rerun folder> \\
        --overwrite --input-root /data/MoM_copy --glofas-dfo-root /data/MoM_copy
"""

import argparse
import contextlib
import glob
import multiprocessing
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# the project modules are one folder up
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402

import settings  # noqa: E402
from processing.gfms import GFMS_tool  # noqa: E402
from processing.gfms.GFMS_tool import GFMS_write_steps  # noqa: E402
from processing.hwrf import HWRF_MoM  # noqa: E402
from processing.dfo.DFO_MoM import update_DFO_MoM  # noqa: E402
from processing.gfms.GFMS_MoM import flood_severity  # noqa: E402
from processing.hwrf.HWRF_MoM import final_alert_pdc, update_HWRF_MoM, update_HWRFMoM_DFO_VIIRS  # noqa: E402
from processing.glofas import GloFAS_tool  # noqa: E402
from processing.cycle_inputs import (  # noqa: E402
    dfo_for_cycle,
    gfms_for_cycle,
    glofas_for_cycle,
    rain_for_cycle,
    viirs_for_cycle,
)
from processing.utilities import (  # noqa: E402
    gfms_steps_csv,
    hafs_steps_csv,
    watersheds_gdb_reader,
)
from processing.viirs.VIIRS_MoM import update_VIIRS_MoM  # noqa: E402
from processing.watershed_cells import raster_summary  # noqa: E402

HOURS = ["00", "06", "12", "18"]
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
DEFAULT_ROOT = os.path.dirname(settings.PRODUCT_DIR)  # holds Products/
PROC_DIRS = ["GFMS_PROC_DIR", "HWRF_PROC_DIR", "DFO_PROC_DIR", "VIIRS_PROC_DIR",
             "HAFS_PROC_DIR", "HAFS_WORK_DIR", "HAFS_DONE_DIR", "DFO_FILLED_DIR"]
# the step files are rebuilt from the Products data, never read from the
# processing tree; one folder for the whole run, so the parallel processes
# rebuild a cycle once between them instead of once each
STEP_DIRS = ["GFMS_STEP_DIR", "HAFS_STEP_DIR"]
INPUT_DIRS = ["GFMS_SUM_DIR", "GLOFAS_DIR", "GLOFAS_SUM_DIR", "DFO_SUM_DIR",
              "VIIRS_SUM_DIR", "HWRF_SUM_DIR", "HAFS_SUM_DIR",
              # the per-step data the lead times are rebuilt from
              "GFMS_IMG_DIR", "HAFS_STEPS_DIR"]
OUT_DIRS = ["GFMS_MOM_DIR", "HWRF_MOM_DIR", "DFO_MOM_DIR", "VIIRS_MOM_DIR",
            "FINAL_MOM_DIR", "HAFS_MOM_DIR", "DFO_HAFS_DIR", "VIIRS_HAFS_DIR",
            "FINAL_HAFS_DIR"]
# per chain: rain summaries (settings name, prefix), file tag, output folders
CHAINS = {
    "HWRF": {"rain": ("HWRF_SUM_DIR", "hwrf"), "tag": "HWRF",
             "mom": "HWRF_MOM_DIR", "dfo": "DFO_MOM_DIR",
             "viirs": "VIIRS_MOM_DIR", "final": "FINAL_MOM_DIR"},
    "HAFS": {"rain": ("HAFS_SUM_DIR", "hafs"), "tag": "HAFS",
             "mom": "HAFS_MOM_DIR", "dfo": "DFO_HAFS_DIR",
             "viirs": "VIIRS_HAFS_DIR", "final": "FINAL_HAFS_DIR"},
}
GLOFAS_DFO = ["GLOFAS_DIR", "GLOFAS_SUM_DIR", "DFO_SUM_DIR"]
# every folder the backfill touches, where settings.py puts it relative to the
# data root (read before any is changed). The same structure is used under the
# input root, the out-dir and the temporary roots, so none of them invents a
# layout and an out-dir can be compared with the production tree directly.
LAYOUT = {
    name: os.path.relpath(getattr(settings, name), DEFAULT_ROOT)
    for name in INPUT_DIRS + PROC_DIRS + STEP_DIRS + OUT_DIRS
}
# every settings value changed here, put back at the end
PATCHED = PROC_DIRS + INPUT_DIRS + STEP_DIRS + [
    "HWRF_SUM_PREFIX", "HWRF_FILE_TAG", "GFMS_MOM_DIR", "HWRF_MOM_DIR",
    "DFO_MOM_DIR", "VIIRS_MOM_DIR", "FINAL_MOM_DIR",
    # utilities.hafs_folders() points the HWRF/DFO/VIIRS folders at these
    "HAFS_MOM_DIR", "DFO_HAFS_DIR", "VIIRS_HAFS_DIR", "FINAL_HAFS_DIR",
]


def _no_download(*args):
    raise RuntimeError(f"MoM backfill never downloads ({args})")


class Folders:
    """input and output folders of the run"""

    def __init__(self, input_root, glofas_dfo_root, out_dir, steps_dir):
        self.steps_dir = steps_dir
        self.input_root = os.path.abspath(input_root)
        self.glofas_dfo_root = os.path.abspath(glofas_dfo_root)
        self.out_dir = os.path.abspath(out_dir)

    def under(self, root, name):
        """settings folder `name` under any root, where settings.py puts it"""
        return os.path.join(root, LAYOUT[name])

    def input(self, name):
        """settings input folder `name` under the input root (GloFAS and DFO:
        under the GloFAS/DFO root)"""
        root = self.glofas_dfo_root if name in GLOFAS_DFO else self.input_root
        return self.under(root, name)

    def out(self, name):
        return self.under(self.out_dir, name)

    def temp(self, root, name):
        """a working folder under a temporary root, created"""
        path = self.under(root, name)
        os.makedirs(path, exist_ok=True)
        return path

    def pair(self, name):
        """folder `name` of the input root: the MoM of the cycle before the
        window is read there, never written to"""
        return self.under(self.input_root, name)

    def rain_summary(self, chain, cycle):
        """rain summary of a cycle (YYYYMMDDHH) for a chain"""
        sum_dir, prefix = CHAINS[chain]["rain"]
        return os.path.join(self.input(sum_dir), f"{prefix}.{cycle}rainfall.csv")


@contextlib.contextmanager
def offline(folders, tmpdir):
    """no host access, the inputs under the input root, temporary files in
    tmpdir; everything put back afterwards"""
    saved = {k: getattr(settings, k) for k in PATCHED}
    saved_fixdate = GFMS_tool.GFMS_fixdate
    saved_processing = GFMS_tool.GFMS_processing
    saved_glofas = GloFAS_tool.download_glofas_until_found
    try:
        for k in PROC_DIRS:
            setattr(settings, k, folders.temp(tmpdir, k))
        for k in STEP_DIRS:
            setattr(settings, k, folders.temp(folders.steps_dir, k))
        for k in INPUT_DIRS:
            setattr(settings, k, folders.input(k))
        settings.GFMS_MOM_DIR = folders.out("GFMS_MOM_DIR")
        # the HAFS chain writes through its own folder names, which
        # hafs_folders() reads: without these it writes to production
        settings.HAFS_MOM_DIR = folders.out("HAFS_MOM_DIR")
        settings.DFO_HAFS_DIR = folders.out("DFO_HAFS_DIR")
        settings.VIIRS_HAFS_DIR = folders.out("VIIRS_HAFS_DIR")
        settings.FINAL_HAFS_DIR = folders.out("FINAL_HAFS_DIR")
        # a missing GFMS summary would be downloaded
        GFMS_tool.GFMS_fixdate = _no_download
        GFMS_tool.GFMS_processing = _no_download
        # the MoM downloads a missing GloFAS file over FTP, retrying for
        # 30 min: offline it keeps the latest local file instead
        GloFAS_tool.download_glofas_until_found = lambda found: None
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        GFMS_tool.GFMS_fixdate = saved_fixdate
        GFMS_tool.GFMS_processing = saved_processing
        GloFAS_tool.download_glofas_until_found = saved_glofas


def pair_cycle(first_day):
    """the cycle 6 h before the first cycle of the window"""
    return (datetime.strptime(first_day + HOURS[0], "%Y%m%d%H")
            - timedelta(hours=6)).strftime("%Y%m%d%H")


def pair_available(folders, chain, cycle):
    """True when the input root has the MoM of `cycle`, so it need not be
    built; asked with the MoM's own finder, before pair_from patches it"""
    pair_dir = folders.pair(CHAINS[chain]["mom"])
    if not os.path.isdir(pair_dir):
        return False
    out_mom = settings.HWRF_MOM_DIR
    try:
        settings.HWRF_MOM_DIR = pair_dir
        return HWRF_MoM.find_pair_HWRFoutput(cycle)[0] != ""
    finally:
        settings.HWRF_MOM_DIR = out_mom


@contextlib.contextmanager
def pair_from(folders, chain):
    """the final alert of the window's first cycle pairs with the cycle 6 h
    before it, which is not in out_dir: that one is read from the pair root by
    calling the MoM's own finder against it, so nothing has to be copied in"""
    original = HWRF_MoM.find_pair_HWRFoutput
    pair_dir = folders.pair(CHAINS[chain]["mom"])

    def find_pair(adate):
        own, previous = original(adate)
        if own == "" or previous != "" or not os.path.isdir(pair_dir):
            return [own, previous]
        pdate = (datetime.strptime(adate, "%Y%m%d%H")
                 - timedelta(hours=6)).strftime("%Y%m%d%H")
        out_mom = settings.HWRF_MOM_DIR
        try:
            settings.HWRF_MOM_DIR = pair_dir
            previous = original(pdate)[0]
        finally:
            settings.HWRF_MOM_DIR = out_mom
        if previous:
            logging.info(f"{chain} {adate}: previous cycle {pdate} read from {pair_dir}")
        return [own, previous]

    HWRF_MoM.find_pair_HWRFoutput = find_pair
    try:
        yield
    finally:
        HWRF_MoM.find_pair_HWRFoutput = original


def use_chain(folders, chain):
    """point the reused HWRF/DFO/VIIRS MoM functions at a chain's own rain
    summaries, file tag and output folders"""
    c = CHAINS[chain]
    sum_dir, prefix = c["rain"]
    settings.HWRF_SUM_DIR = folders.input(sum_dir)
    settings.HWRF_SUM_PREFIX = prefix
    settings.HWRF_FILE_TAG = c["tag"]
    settings.HWRF_MOM_DIR = folders.out(c["mom"])
    settings.DFO_MOM_DIR = folders.out(c["dfo"])
    settings.VIIRS_MOM_DIR = folders.out(c["viirs"])
    settings.FINAL_MOM_DIR = folders.out(c["final"])


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
        help=f"root with the GFMS, HWRF, HAFS and VIIRS summaries and step files, "
             f"in the layout of {DEFAULT_ROOT} (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--glofas-dfo-root", default=None,
        help="root with the GloFAS and DFO summaries, when they are not under "
             "--input-root (default: --input-root)",
    )
    parser.add_argument(
        "--out-dir", required=True,
        help="folder the outputs are written to: flat (gfms_mom, hwrf_mom, ..., Final_Alerts) or, "
             "when it has every production MoM folder (the production root), those folders",
    )
    parser.add_argument(
        "--warmup", action="store_true",
        help="build the cycle before the window in --out-dir (no alert) instead of "
             "reading it from --input-root; done anyway when it is not there",
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
        os.path.join(folders.out("GFMS_MOM_DIR"), f"Final_Attributes_{adate}.csv"),
        os.path.join(folders.out("GFMS_MOM_DIR"), f"Attributes_Clean_{adate}.csv"),
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


def gfms_steps_from_image(folders, stamp, tmpdir):
    """the GFMS step file of a cycle, written into GFMS_STEP_DIR (temporary)
    from the cycle image in Products, which holds every 3 h step as a band:
    the same two steps GFMS_tool takes when it processes a cycle, the cells of
    the window's maximum (raster_summary) and then GFMS_write_steps over the
    bands. Without it the GFMS lead time would be -1."""
    summary_csv = os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{stamp}.csv")
    path = gfms_steps_csv(summary_csv)
    if os.path.exists(path):
        return path
    image = os.path.join(folders.input("GFMS_IMG_DIR"), f"Flood_byStor_{stamp}.tiff")
    if not os.path.exists(image):
        logging.warning(f"no GFMS cycle image for {stamp}: {image}")
        return None
    if GFMS_tool.WATERSHEDS is None:
        GFMS_tool.WATERSHEDS = watersheds_gdb_reader()

    # the window's maximum, the raster the cells are taken over
    with rasterio.open(image) as src:
        profile, running = src.profile, None
        for band, description in enumerate(src.descriptions, start=1):
            if description == "missing":
                continue
            grid = src.read(band)
            running = grid if running is None else np.maximum(running, grid)
    if running is None:
        logging.warning(f"GFMS cycle image {stamp} has no step with data")
        return None
    window_max = os.path.join(tmpdir, f"gfms_window_max_{stamp}.tiff")
    profile.update(count=1)
    with rasterio.open(window_max, "w", **profile) as dst:
        dst.write(running, 1)

    cells = []
    raster_summary("gfms", window_max, GFMS_tool.WATERSHEDS, cells=cells)
    GFMS_write_steps(image, cells, summary_csv)
    os.remove(window_max)
    logging.info(f"GFMS step file from the cycle image: {os.path.basename(path)}")
    return path


def hafs_steps_from_records(folders, cycle, tmpdir):
    """the HAFS step file of a rain cycle, written into HAFS_STEP_DIR from the
    rain records kept in Products: every storm's 3 h steps merged and then
    HAFS_extract_by_watershed, as process_rain does it. Nothing is downloaded,
    HAFS_download returns the record already on disk. Without it the HAFS lead
    time would be -1."""
    # the only place the HAFS code is needed, and it is heavy
    from processing.hafs import HAFS_tool

    summary_csv = os.path.join(settings.HAFS_SUM_DIR, f"hafs.{cycle}rainfall.csv")
    path = hafs_steps_csv(summary_csv)
    if os.path.exists(path):
        return path
    records = os.path.join(folders.input("HAFS_STEPS_DIR"), cycle)
    files = glob.glob(os.path.join(records, "*.grb2"))
    if not files:
        logging.warning(f"no HAFS rain records for {cycle}: {records}")
        return None
    storms = sorted({os.path.basename(f).split(".")[0] for f in files})

    grids, hours = [], 0
    for storm in storms:
        grid, top, left, hour = HAFS_tool.storm_rain(cycle, storm)
        hours = max(hours, hour)
        grids.append((grid, top, left))
    grid, top, left = HAFS_tool.merge(grids)
    HAFS_tool.HAFS_extract_by_watershed(
        os.path.join(tmpdir, f"hafs.{cycle}rainfall.csv"),
        grid, top, left,
        lambda: HAFS_tool.merged_steps(cycle, storms),
        path,
        n_steps=hours // 3,
    )
    logging.info(
        f"HAFS step file from the rain records: {os.path.basename(path)} "
        f"({' '.join(storms)})"
    )
    return path


def gfms_sum(adatehour):
    """GFMS summary of a cycle (YYYYMMDDHH)"""
    return os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{adatehour}.csv")


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


def backfill_gfms(folders, adate, dry_run, tmpdir):
    if not dry_run:
        gfms_steps_from_image(folders, adate + "00", tmpdir)
    gfmscsv = gfms_sum(adate + "00")
    glofascsv = glofas_for_cycle(adate + "00") or ""
    logging.info(
        f"GFMS MoM {adate}: gfms={os.path.basename(gfmscsv)}, "
        f"glofas={os.path.basename(glofascsv)}"
    )
    if not dry_run:
        run_step("GFMS MoM", flood_severity, gfmscsv, glofascsv, adate,
                 workdir=settings.GFMS_PROC_DIR)


def backfill_chain(folders, tmpdir, chain, adate, dry_run, with_alert=True, hours=HOURS):
    """MoM, +DFO+VIIRS and final alert of the 4 cycles of a day for a chain,
    return the cycles run"""
    now = datetime.now(timezone.utc)
    done = []
    use_chain(folders, chain)
    for hh in hours:
        adatehour = adate + hh
        cycle_time = datetime.strptime(adatehour, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if cycle_time > now:
            logging.info(f"{chain} MoM {adatehour}: cycle not reached yet, stop here")
            break
        # the MoM's own GFMS rule: the cycle's summary, else 6 or 12 h back
        gfms = gfms_for_cycle(adatehour)
        if gfms is None:
            logging.warning(f"{chain} MoM {adatehour}: no GFMS summary, skipped")
            continue
        # the rain the MoM itself would take, of this chain only
        rain_cycle, rain_sum = rain_for_cycle(adatehour)
        if chain == "HAFS" and not os.path.exists(rain_sum):
            logging.warning(f"{chain} MoM {adatehour}: no {chain} rain summary, skipped")
            continue
        # the step files the lead times need, from the Products data
        if not dry_run:
            gfms_steps_from_image(folders, adatehour, tmpdir)
            if chain == "HAFS":
                hafs_steps_from_records(folders, rain_cycle, tmpdir)
        # the files the production finders pick for this cycle
        dfo = dfo_for_cycle(adatehour)[0]
        viirs = viirs_for_cycle(adatehour)[0]
        glofas = glofas_for_cycle(adatehour)
        logging.info(f"{chain} MoM {adatehour}: GFMS {os.path.basename(gfms)}, "
                     f"GloFAS {os.path.basename(glofas) if glofas else 'none'}, "
                     f"DFO {dfo or 'none'}, VIIRS {viirs or 'none'}, "
                     f"rain {os.path.basename(rain_sum)}")
        if dry_run:
            done.append(hh)
            continue
        ok = run_step(f"{chain} MoM", update_HWRF_MoM, adatehour, workdir=settings.HWRF_PROC_DIR)
        if not ok:
            continue
        run_step(f"{chain}+DFO+VIIRS MoM", update_HWRFMoM_DFO_VIIRS, adatehour,
                 workdir=settings.HWRF_PROC_DIR)
        if with_alert:
            run_step(f"{chain} final alert", final_alert_pdc, adatehour)
        done.append(hh)
    return done


def backfill_dfo_viirs(chain, adate, cycles_done, dry_run):
    # run with whatever cycles went through: a day only stops at the cycle
    # that has not been reached yet
    logging.info(f"{chain} DFO MoM {adate}, VIIRS MoM {adate} (cycles {cycles_done})")
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
    folders, dates, args = job

    def work(tmpdir):
        with offline(folders, tmpdir):
            for adate in dates:
                logging.info(f"===== GFMS MoM {adate} =====")
                backfill_gfms(folders, adate, args.dry_run, tmpdir)
    return in_process("GFMS", work)


def run_chain(job, chain):
    """MoM, +DFO+VIIRS and final alert of every cycle of a chain; returns
    {day: cycles run}"""
    folders, dates, args = job

    def work(tmpdir):
        done = {}
        with offline(folders, tmpdir):
            use_chain(folders, chain)
            # the alert pair of the first cycle: read from the pair root, or
            # built here when that root has not got it
            warmup = pair_cycle(dates[0])
            need_warmup = args.warmup or not pair_available(folders, chain, warmup)
            with pair_from(folders, chain):
                if need_warmup:
                    why = ("asked for" if args.warmup
                           else f"not in {folders.pair(CHAINS[chain]['mom'])}")
                    logging.info(f"===== {chain} warm-up {warmup} ({why}) =====")
                    backfill_chain(folders, tmpdir, chain, warmup[:8], args.dry_run,
                                   with_alert=False, hours=[warmup[8:]])
                else:
                    logging.info(f"{chain}: alert pair {warmup} comes from "
                                 f"{folders.pair(CHAINS[chain]['mom'])}, no warm-up")
                for adate in dates:
                    logging.info(f"===== {chain} chain {adate} =====")
                    done[adate] = backfill_chain(folders, tmpdir, chain, adate, args.dry_run)
        return done
    return in_process(chain, work) or {}


def run_dfo_viirs(job, chain, cycles_done):
    """DFO and VIIRS MoM of every day of a chain"""
    folders, dates, args = job

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

    steps_dir = tempfile.mkdtemp(prefix="mom_backfill_steps_")
    folders = Folders(
        args.input_root, args.glofas_dfo_root or args.input_root, args.out_dir, steps_dir
    )
    dates = backfill_dates(args.end, args.days)
    chains = ["HWRF"] + (["HAFS"] if settings.HAFS_ENABLED else [])
    logging.info(
        f"MoM backfill {dates[0]} - {dates[-1]}: input {folders.input_root}, GloFAS/DFO "
        f"{folders.glofas_dfo_root}, output "
        f"{folders.out_dir}, "
        f"pairs from {folders.input_root}, warmup={args.warmup}, chains {' '.join(chains)}, "
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


    # the MoM code changes settings and the working folder, so the parallel
    # parts are processes (forked: they inherit the settings and the log)
    ctx = multiprocessing.get_context("fork")
    job = (folders, dates, args)
    with ctx.Pool(1 + len(chains)) as pool:
        phase1 = [pool.apply_async(run_gfms, (job,))]
        phase1 += [pool.apply_async(run_chain, (job, chain)) for chain in chains]
        results = [r.get() for r in phase1]
    cycles_done = dict(zip(chains, results[1:]))
    with ctx.Pool(len(chains)) as pool:
        phase2 = [pool.apply_async(run_dfo_viirs, (job, chain, cycles_done[chain])) for chain in chains]
        [r.get() for r in phase2]

    shutil.rmtree(steps_dir, ignore_errors=True)
    logging.info(f"MoM backfill finished, log: {log_file}")


if __name__ == "__main__":
    main()
