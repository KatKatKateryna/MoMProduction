"""
cycle_inputs.py

Which file of each model a cycle (YYYYMMDDHH) uses, one function per model and
nothing else: every rule the MoM and the backfill share lives here. Imports
are deliberately kept to the standard library and settings, so asking what a
cycle reads never pulls in the MoM, pandas or geopandas.
"""

import glob
import logging
import os
from datetime import datetime, timedelta

import settings

# a missing file falls back to the last one available, but no older than
# this: beyond it the GloFAS data says nothing about the cycle any more
GLOFAS_MAX_AGE_DAYS = 3


def gfms_for_cycle(adate):
    """the GFMS summary of adate, else of the cycle 6 or 12 h before it; None
    without any"""
    cycle = datetime.strptime(adate, "%Y%m%d%H")
    for hours in (0, 6, 12):
        stamp = (cycle - timedelta(hours=hours)).strftime("%Y%m%d%H")
        gfms_sum = os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{stamp}.csv")
        if os.path.exists(gfms_sum):
            return gfms_sum
    return None


def glofas_summary(adate):
    """the GloFAS csv of the day of adate (YYYYMMDD[HH])"""
    return os.path.join(settings.GLOFAS_DIR, f"threspoints_{adate[:8]}00.csv")


def find_latest_glofas(apath, adate, atype):
    """the latest GloFAS file dated BEFORE adate (YYYYMMDD[HH]) and at most
    GLOFAS_MAX_AGE_DAYS older than it; "" when nothing that recent is there"""
    target = f"threspoints_{adate[:8]}00.{atype}"
    oldest = (
        datetime.strptime(adate[:8], "%Y%m%d") - timedelta(days=GLOFAS_MAX_AGE_DAYS)
    ).strftime("%Y%m%d")
    oldest_name = f"threspoints_{oldest}00.{atype}"
    candidates = [
        os.path.basename(f)
        for f in glob.glob(os.path.join(apath, f"threspoints_*.{atype}"))
        if oldest_name <= os.path.basename(f) < target
    ]
    return max(candidates) if candidates else ""


def glofas_for_cycle(adate):
    """the GloFAS csv a cycle (YYYYMMDDHH) uses, from the files already on
    disk: the day's own threspoints_D00 for the 12 and 18 cycles, the latest
    file before D for the 00 and 06 ones, as GloFAS of D is only published
    around midday. The fallback is never older than GLOFAS_MAX_AGE_DAYS; then
    the same day if that is all there is, and None when there is nothing
    usable, so a cycle is never blocked by a missing GloFAS file. Nothing is
    downloaded here: GloFAS_tool.glofas_download fetches what is missing."""
    glofas_sum = glofas_summary(adate)
    # before 12:00 the file of the day before is used: today's is not looked for
    early = int(adate[8:10]) < 12
    if not early and os.path.exists(glofas_sum):
        return glofas_sum

    glofas_latest = find_latest_glofas(settings.GLOFAS_DIR, adate, "csv")
    if glofas_latest:
        if early:
            # the 00 and 06 cycles are meant to use the day before
            logging.info(f"GloFAS for {adate}: {glofas_latest}")
        else:
            logging.warning(
                f"GloFAS for {adate}: {os.path.basename(glofas_sum)} not published, "
                f"using the previous available {glofas_latest}"
            )
        return os.path.join(settings.GLOFAS_DIR, glofas_latest)
    if os.path.exists(glofas_sum):
        # no earlier GloFAS to fall back to, use the same day file
        logging.warning(
            f"GloFAS for {adate}: no earlier file, using the same day "
            f"{os.path.basename(glofas_sum)}"
        )
        return glofas_sum
    logging.error(f"no GloFAS available for {adate}, using no GloFAS data")
    return None


def dfo_for_cycle(adate):
    """[YYYYMMDD, summary] of the DFO summary a cycle (YYYYMMDDHH) uses, from
    the day of the cycle back; ["", ""] when there is none"""

    # DFO of D is written at 04:00 on D+1: the 00 cycle (00:30) looks from
    # D-2, the others from D-1
    da = datetime.strptime(adate[:8], "%Y%m%d")
    startd = 2 if adate[8:] == "00" else 1
    for i in range(startd, 6):
        cdatestr = (da - timedelta(days=i)).strftime("%Y%m%d")
        cfile = os.path.join(settings.DFO_SUM_DIR, f"DFO_{cdatestr}.csv")
        if os.path.exists(cfile):
            return [cdatestr, cfile]

    return ["", ""]


# no summary: no watershed flooded, one row of zeros scores nothing


def viirs_for_cycle(adate):
    """[YYYYMMDD, path] of the VIIRS summary a cycle (YYYYMMDDHH) uses: from
    D-2 back for the 00 and 06 cycles, from D-1 back for 12 and 18;
    ["", ""] if none"""
    # VIIRS of D is published on D+1 (~10:00-11:15 UTC) and written by the
    # 11:30 VIIRS run, at times only later that day or a day after
    da = datetime.strptime(adate[:8], "%Y%m%d")
    startd = 2 if adate[8:] in ("00", "06") else 1
    for i in range(startd, 6):
        cdatestr = (da - timedelta(days=i)).strftime("%Y%m%d")
        cfile = os.path.join(settings.VIIRS_SUM_DIR, f"VIIRS_Flood_{cdatestr}.csv")
        if os.path.exists(cfile):
            return [cdatestr, cfile]
    return ["", ""]


def rain_for_cycle(adate):
    """[cycle, summary] of the rain a cycle (YYYYMMDDHH) uses, of whichever
    chain the folders are set to: the summary of the previous cycle, as a
    cycle's own rainfall is only published hours after its time, or the one
    12 h before when that is missing. The previous cycle's path is returned
    even when neither is there, and the MoM goes on without rain."""
    rain_cycle = (datetime.strptime(adate, "%Y%m%d%H") - timedelta(hours=6)).strftime(
        "%Y%m%d%H"
    )
    rain_sum = os.path.join(
        settings.HWRF_SUM_DIR, f"{settings.HWRF_SUM_PREFIX}.{rain_cycle}rainfall.csv"
    )
    if os.path.exists(rain_sum):
        return [rain_cycle, rain_sum]

    older_cycle = (datetime.strptime(adate, "%Y%m%d%H") - timedelta(hours=12)).strftime(
        "%Y%m%d%H"
    )
    older_sum = os.path.join(
        settings.HWRF_SUM_DIR, f"{settings.HWRF_SUM_PREFIX}.{older_cycle}rainfall.csv"
    )
    if os.path.exists(older_sum):
        logging.warning(f"no rainfall of {rain_cycle} for {adate}, using {older_cycle}")
        return [older_cycle, older_sum]

    logging.warning(
        f"no rainfall of the previous cycle {rain_cycle} for {adate}, "
        f"continuing without rain"
    )
    return [rain_cycle, rain_sum]
