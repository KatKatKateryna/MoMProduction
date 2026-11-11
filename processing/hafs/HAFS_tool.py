"""
HAFS_tool.py
    -- cron job for NOAA HAFS hurricane rainfall, run alongside the HWRF
       workflow (processing/MoM_run.py -j HAFS) and never inside it

    For every HAFS cycle, the rain of each storm is taken from its parent
    swath file (average rain rate over 0-3 h, 0-6 h, ... 0-126 h; the 3 h rain
    is the difference of consecutive windows, and the steps are summed), over
    the whole parent domain. The storms are merged into one 0-126 h total in
    inches on the HWRF 0.05 deg grid, and summarized per watershed with the
    HWRF calculation; the total up to each 3 h step goes to the step file
    (lead times, HWRF_MoM.hafs_lead):
        HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff
        HAFS/HAFS_summary/hafs.YYYYMMDDHHrainfall.csv

    The rain rate records of the swath files, copied unchanged from the host
    (parent.swath.prate.grb2), are kept in HAFS/HAFS_steps/<cycle>/: the rain,
    its 3 h steps and the lead times are read from there, so a rerun needs no
    download.

    The MoM chain is then run with the unchanged HWRF functions, reading the
    HAFS summaries (settings.HWRF_SUM_DIR / HWRF_SUM_PREFIX) and with every
    output folder pointed at the HAFS ones, so the calculations are identical
    to HWRF but nothing is mixed:
        HAFS/HAFS_summary/hafs.* <- HWRF/HWRF_summary/hwrf.*
        HAFS/HAFS_MoM            <- HWRF/HWRF_MoM
        DFO/DFO_HAFS_MoM         <- DFO/DFO_MoM
        VIIRS/VIIRS_HAFS_MoM     <- VIIRS/VIIRS_MoM
        Final_Alerts_hafs        <- Final_Alert
    The shared inputs (GFMS, GloFAS, DFO, VIIRS summaries) are read.

    Turned on and off with ENABLED in the [hafs] section of production.cfg.

**EXAMPLE** (from the repository root)
    python -m processing.hafs.HAFS_tool                  # same as the cron job
    python -m processing.hafs.HAFS_tool --days 3 --end 20261012 --overwrite
"""

import argparse
import csv
import glob
import logging
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import geopandas as gpd  # noqa: F401  (load order for eccodes, below)
import numpy as np
import rasterio
import requests
from osgeo import gdal  # noqa: F401
from rasterio.transform import from_origin

# eccodes (pip wheel) must be imported after GDAL/rasterio/geopandas: imported
# first, its bundled libraries clash with the conda GDAL ones and the process
# crashes on exit
import eccodes  # isort: skip

import settings
from processing.utilities import (
    RAIN_SUMMARY_HEADERS,
    hafs_folders,
    hafs_storms,
    request_retry,
    hafs_steps_csv,
    retry_step,
    write_empty_rain_summary,
)

HOURS = ["00", "06", "12", "18"]
RES = 0.05
NODATA = -999.0
MM_PER_INCH = 25.4
# HAFS posts a cycle ~6 h after its time; a cycle with no storm by EMPTY_FINAL
# is taken as empty, and storm files still missing by STORMS_FINAL are skipped
PUBLISHED_AFTER = timedelta(hours=5)
EMPTY_FINAL = timedelta(hours=5)
STORMS_FINAL = timedelta(hours=12)
# a storm not published yet is checked again this many times, this far apart
HAFS_RETRIES = 3
HAFS_WAIT_SECONDS = 600

# internal folders: processed-cycle markers and temporary files


def utc_now():
    return datetime.now(timezone.utc)


def cycle_time(cycle):
    return datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def shift_cycle(cycle, hours):
    return (cycle_time(cycle) + timedelta(hours=hours)).strftime("%Y%m%d%H")


def cycle_url(cycle):
    return f"{settings.HAFS_HOST.rstrip('/')}/{settings.HAFS_MODEL}/{cycle[:8]}/{cycle[8:]}/"


def make_dirs():
    for d in (
        settings.HAFS_SUM_DIR,
        settings.HAFS_IMG_DIR,
        settings.HAFS_MOM_DIR,
        settings.HAFS_STEPS_DIR,
        settings.DFO_HAFS_DIR,
        settings.VIIRS_HAFS_DIR,
        settings.FINAL_HAFS_DIR,
        settings.HAFS_DONE_DIR,
        settings.HAFS_WORK_DIR,
    ):
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# HAFS data: storms and rain
# ---------------------------------------------------------------------------


def list_storms(cycle):
    """storm ids (e.g. 20e, 09l) HAFS has for a cycle"""
    return hafs_storms(cycle)


def exists(url):
    return request_retry(requests.head, url, timeout=60).status_code == 200


def is_rain_rate(gid):
    """0-N h average rain rate record"""
    return (
        eccodes.codes_get(gid, "shortName") == "prate"
        and eccodes.codes_get(gid, "typeOfStatisticalProcessing") == 0
    )


def swath_url(cycle, storm):
    """the parent swath file of a storm on the host"""
    return (
        f"{cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}.parent.swath.grb2"
    )


def rain_ranges(url):
    """the byte ranges of a swath file's rain rate records, from the .idx
    sidecar the host publishes beside it: (start, end) pairs, end None for
    the last record, records that follow each other merged into one range.

    None when the index is missing or does not parse -- then the whole file
    is downloaded, as before."""
    try:
        reqs = request_retry(requests.get, url + ".idx", timeout=60)
        if reqs.status_code != 200:
            return None
        records = [line.split(":") for line in reqs.text.splitlines() if line.strip()]
        starts = [int(r[1]) for r in records]
    except (requests.RequestException, ValueError, IndexError):
        return None
    if not starts or starts != sorted(starts):
        return None
    ranges = []
    for i, record in enumerate(records):
        # the records is_rain_rate keeps: the averaged rain rate, not the
        # convective one and not the wind or updraught fields beside it
        if len(record) < 6 or record[3] != "PRATE" or "ave" not in record[5]:
            continue
        start = starts[i]
        end = starts[i + 1] - 1 if i + 1 < len(starts) else None
        if ranges and ranges[-1][1] is not None and ranges[-1][1] + 1 == start:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges or None


def HAFS_download(cycle, storm):
    """rain rate records of a storm's swath file, unchanged, saved from the
    host in HAFS_steps/<cycle>/ (the swath file is ~300 MB, these ~1/10, and
    with the host's .idx only those are downloaded)"""
    url = swath_url(cycle, storm)
    name = f"{storm}.{cycle}.{settings.HAFS_MODEL}.parent.swath.prate.grb2"
    path = os.path.join(settings.HAFS_STEPS_DIR, cycle, name)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = f"{path}.{os.getpid()}.part"
    with tempfile.TemporaryFile(dir=settings.HAFS_WORK_DIR) as tmp:
        ranges = rain_ranges(url)
        for span in (
            [f"bytes={a}-" + ("" if b is None else str(b)) for a, b in ranges]
            if ranges
            else [None]
        ):
            headers = {"Range": span} if span else {}
            with request_retry(
                requests.get, url, headers=headers, stream=True, timeout=600
            ) as reqs:
                reqs.raise_for_status()
                for block in reqs.iter_content(chunk_size=1 << 22):
                    tmp.write(block)
        if ranges:
            logging.info(
                f"HAFS {cycle}: {storm} rain records only, "
                f"{len(ranges)} of {tmp.tell() / 1e6:.0f} MB"
            )
        tmp.seek(0)
        kept = 0
        with open(part, "wb") as out:
            while True:
                gid = eccodes.codes_grib_new_from_file(tmp)
                if gid is None:
                    break
                try:
                    if is_rain_rate(gid):
                        out.write(eccodes.codes_get_message(gid))
                        kept += 1
                finally:
                    eccodes.codes_release(gid)
    if not kept:
        os.remove(part)
        raise RuntimeError(f"no rain rates in {cycle_url(cycle)}{name}")
    os.replace(part, path)
    return path


def norm_lons(lons):
    """longitudes in -180..180, or 0..360 when the storm crosses the dateline
    (as HWRF rainfall.ascii does)"""
    west = np.where(lons > 180, lons - 360, lons)
    if west.max() - west.min() <= 180:
        return west
    return np.where(west < 0, west + 360, west)


def storm_rain_steps(cycle, storm):
    """the rain (inches) of one storm from 0 h to each 3 h step, the sum of
    its 3 h rain (difference of consecutive 0-N h average rain rates), over
    its whole parent domain on a 0.05 deg grid: yields (hour, grid, top,
    left), the same top/left at every step; the last step is the storm's
    0-126 h total"""
    path = HAFS_download(cycle, storm)
    with open(path, "rb") as tmp:
        # first pass: where the 0-N h average rain rates are
        rates = []
        while True:
            gid = eccodes.codes_grib_new_from_file(tmp)
            if gid is None:
                break
            try:
                if is_rain_rate(gid):
                    eccodes.codes_set(gid, "stepUnits", "h")
                    rates.append(
                        (
                            int(eccodes.codes_get(gid, "endStep")),
                            int(eccodes.codes_get(gid, "offset")),
                        )
                    )
            finally:
                eccodes.codes_release(gid)
        if not rates:
            raise RuntimeError(f"no rain rates in {path}")

        # second pass: 3 h rain, summed step by step. Not clipped at 0: the
        # differences carry the records' packing noise (~0.1-0.2 mm), and
        # clipping kept only its positive half, a few mm over 42 steps; the
        # sum is the 0-N h record then
        total = prev_acc = None
        for hour, offset in sorted(rates):
            tmp.seek(offset)
            gid = eccodes.codes_grib_new_from_file(tmp)
            try:
                if total is None:
                    shape = (eccodes.codes_get(gid, "Nj"), eccodes.codes_get(gid, "Ni"))
                    lats = eccodes.codes_get_array(gid, "latitudes").reshape(shape)
                    lons = norm_lons(
                        eccodes.codes_get_array(gid, "longitudes").reshape(shape)
                    )
                    ri, ci, top, left = lattice_index(lats, lons)
                    total = np.zeros(shape)
                    prev_acc = np.zeros(shape)
                values = eccodes.codes_get_values(gid).reshape(shape)
                if eccodes.codes_get(gid, "bitmapPresent"):
                    values = np.where(
                        values == eccodes.codes_get(gid, "missingValue"), np.nan, values
                    )
            finally:
                eccodes.codes_release(gid)
            acc = values * hour * 3600  # kg m-2 = mm over 0-hour
            step = acc - prev_acc
            total += np.where(np.isfinite(step), step, 0)
            prev_acc = np.where(np.isfinite(acc), acc, prev_acc)
            yield hour, total[ri, ci] / MM_PER_INCH, top, left


def lattice_index(lats, lons):
    """nearest parent cell of each cell of a 0.05 deg grid around the parent
    domain, pixel centres on multiples of RES: (rows, cols, top, left)"""
    top = np.ceil(lats.max() / RES) * RES
    left = np.floor(lons.min() / RES) * RES
    ny = int(round((top - np.floor(lats.min() / RES) * RES) / RES)) + 1
    nx = int(round((np.ceil(lons.max() / RES) * RES - left) / RES)) + 1
    dlat, dlon = lats[1, 0] - lats[0, 0], lons[0, 1] - lons[0, 0]
    ri = np.rint(((top - RES * np.arange(ny))[:, None] - lats[0, 0]) / dlat).astype(int)
    ci = np.rint(((left + RES * np.arange(nx))[None, :] - lons[0, 0]) / dlon).astype(
        int
    )
    ri, ci = np.broadcast_arrays(
        np.clip(ri, 0, lats.shape[0] - 1), np.clip(ci, 0, lats.shape[1] - 1)
    )
    return ri, ci, top, left


def storm_rain(cycle, storm):
    """0-126 h rain (inches) of one storm over its whole parent domain on a
    0.05 deg grid, the sum of its 3 h steps: (grid, top, left, last hour)"""
    for hour, grid, top, left in storm_rain_steps(cycle, storm):
        pass
    return grid, top, left, hour


def merged_steps(cycle, storms):
    """the storms' rain from 0 h to each 3 h step, merged as merge() does:
    yields (hour, grid, top, left); a storm whose steps end earlier keeps its
    last total"""
    gens = [storm_rain_steps(cycle, storm) for storm in storms]
    latest = [next(g) for g in gens]
    while True:
        hour = max(item[0] for item in latest)
        # every storm at its latest step not after hour
        yield hour, *merge([item[1:] for item in latest])
        ahead = []
        for i, g in enumerate(gens):
            item = next(g, None)
            if item is not None:
                ahead.append((i, item))
        if not ahead:
            return
        for i, item in ahead:
            latest[i] = item


def merge(grids):
    """merge storm grids on one canvas, keeping the larger value where they overlap"""
    top = max(g[1] for g in grids)
    left = min(g[2] for g in grids)
    bottom = min(g[1] - (g[0].shape[0] - 1) * RES for g in grids)
    right = max(g[2] + (g[0].shape[1] - 1) * RES for g in grids)
    ny = int(round((top - bottom) / RES)) + 1
    nx = int(round((right - left) / RES)) + 1
    canvas = np.full((ny, nx), np.nan)
    for grid, gtop, gleft in grids:
        r0 = int(round((top - gtop) / RES))
        c0 = int(round((gleft - left) / RES))
        view = canvas[r0 : r0 + grid.shape[0], c0 : c0 + grid.shape[1]]
        view[:] = np.fmax(view, grid)
    return canvas, top, left


def write_tiff(path, grid, top, left):
    """same layout as the HWRF images: -999 where there is no rain or no data"""
    out = np.where(np.isfinite(grid) & (grid > 0), grid, NODATA)
    transform = from_origin(left - RES / 2, top + RES / 2, RES, RES)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=out.shape[0],
        width=out.shape[1],
        count=1,
        dtype="float64",
        crs="EPSG:4326",
        transform=transform,
        nodata=NODATA,
        compress="lzw",
        tiled=True,
    ) as dst:
        dst.write(out, 1)


def HAFS_extract_by_watershed(output_csv, grid, top, left, steps=None, steps_path=None, n_steps=42):
    """per-watershed summary with the HWRF calculation (watershed_cells.py,
    exact cell shares); True if any watershed has rain. With steps (a function
    giving the (hour, grid, top, left) of the running totals that end with
    grid, as merged_steps), also the step file steps_path: per step the
    total so far, and that step's 3 h rain alone times n_steps (the cycle's
    number of 3 h steps; the summary's max is of the cell totals too)"""
    from processing.hwrf.HWRF_tool import rain_summary_rows
    from processing.utilities import read_data, watersheds_gdb_reader, write_steps
    from processing.watershed_cells import cells_summary, lattice_summary

    watersheds = watersheds_gdb_reader()
    HWRF_weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "HWRF_Weightage.csv"))
    # rain area: only the cells above a quarter of the mean rain minimum
    value_above = float(HWRF_weightage.HWRF_MeanRain_minwt[0]) / 4
    cells = [] if steps is not None else None
    rows = rain_summary_rows(
        lattice_summary(
            np.where(grid > 0, grid, np.nan), top, left, watersheds,
            value_above=value_above, cells=cells,
        )
    )
    with open(output_csv, "w") as f:
        writer = csv.writer(f)
        writer.writerow(RAIN_SUMMARY_HEADERS)
        writer.writerows(rows)
    if rows and steps is not None:

        def write_step_file():
            summaries, alone = [], []
            prev = np.zeros(grid.shape)
            for hour, step_grid, step_top, step_left in steps():
                if (step_top, step_left, step_grid.shape) != (top, left, grid.shape):
                    raise RuntimeError(f"step {hour} h not on the grid of the total")
                summaries.append(
                    (
                        hour,
                        cells_summary(
                            "hwrf", cells, np.where(step_grid > 0, step_grid, np.nan),
                            watersheds, value_above=value_above,
                        ),
                    )
                )
                # the 3 h rain alone: negative differences are packing noise
                now = np.nan_to_num(step_grid)
                rain = (now - prev) * n_steps
                prev = now
                alone.append(
                    cells_summary(
                        "hwrf", cells, np.where(rain > 0, rain, np.nan),
                        watersheds, value_above=value_above,
                    )
                )
            write_steps(steps_path, summaries, alone)
            logging.info(f"generated: {steps_path} ({len(summaries)} steps)")

        # no step file (lead times -1) rather than no summary
        retry_step(write_step_file, f"HAFS step file {steps_path}")
    return len(rows) > 0


def mark_done(cycle, note):
    with open(os.path.join(settings.HAFS_DONE_DIR, cycle), "w") as f:
        f.write(note + "\n")


def is_done(cycle):
    return os.path.exists(os.path.join(settings.HAFS_DONE_DIR, cycle))


def empty_summary(cycle, note):
    """settle a cycle without rain over any watershed: header-only summary,
    no image"""
    write_empty_rain_summary(
        os.path.join(settings.HAFS_SUM_DIR, f"hafs.{cycle}rainfall.csv")
    )
    logging.info(f"HAFS {cycle}: {note}, empty summary written")
    mark_done(cycle, note)


def published_storms(cycle, storms):
    """(storms with their swath file on the host, storms without it)"""
    ready, missing = [], []
    for storm in storms:
        (ready if exists(swath_url(cycle, storm)) else missing).append(storm)
    return ready, missing


def process_rain(cycle):
    """HAFS summary and image of one cycle; True once the cycle is
    settled (processed, or known to have nothing; then the summary is empty)"""
    if is_done(cycle):
        return True
    age = utc_now() - cycle_time(cycle)
    if age < PUBLISHED_AFTER:
        return False

    storms = list_storms(cycle)
    if not storms:
        if age >= EMPTY_FINAL:
            empty_summary(cycle, "no storms")
            return True
        return False

    for retry in range(1, HAFS_RETRIES + 1):
        ready, missing = published_storms(cycle, storms)
        if not missing or age >= STORMS_FINAL:
            break
        logging.info(
            f"HAFS {cycle}: {missing[0]} not published yet, retry {retry} of "
            f"{HAFS_RETRIES} in {HAFS_WAIT_SECONDS}s"
        )
        time.sleep(HAFS_WAIT_SECONDS)
    else:
        ready, missing = published_storms(cycle, storms)
    if missing and age < STORMS_FINAL:
        logging.info(f"HAFS {cycle}: {missing[0]} not published yet, exiting")
        return False
    for storm in missing:
        logging.warning(f"HAFS {cycle}: {storm} files missing, skipped")
    logging.info(f"HAFS {cycle}: storms {' '.join(ready)}")

    grids = []
    hours = 0
    for storm in ready:
        grid, top, left, hour = storm_rain(cycle, storm)
        hours = max(hours, hour)
        grids.append((grid, top, left))
        logging.info(f"HAFS {cycle}: {storm} max {np.nanmax(grid):.2f} in")
    if not grids:
        empty_summary(cycle, "no storm data")
        return True

    grid, top, left = merge(grids)
    raintiff = os.path.join(settings.HAFS_WORK_DIR, f"hafs.{cycle}rainfall.tiff")
    output_csv = os.path.join(settings.HAFS_WORK_DIR, f"hafs.{cycle}rainfall.csv")
    # the step file goes straight to its folder; only read with the summary
    steps_path = hafs_steps_csv(output_csv)
    write_tiff(raintiff, grid, top, left)
    if np.nanmax(np.nan_to_num(grid)) > 0 and HAFS_extract_by_watershed(
        output_csv, grid, top, left, lambda: merged_steps(cycle, ready), steps_path,
        n_steps=hours // 3,
    ):
        shutil.move(
            output_csv,
            os.path.join(settings.HAFS_SUM_DIR, os.path.basename(output_csv)),
        )
        shutil.move(
            raintiff, os.path.join(settings.HAFS_IMG_DIR, os.path.basename(raintiff))
        )
        logging.info(f"HAFS {cycle}: generated summary and image")
        mark_done(cycle, " ".join(ready))
    else:
        # same as HWRF: no rain over any watershed, empty summary, no image
        for f in (raintiff, output_csv):
            if os.path.exists(f):
                os.remove(f)
        empty_summary(cycle, " ".join(ready) + " (no rain over watersheds)")
    return True


# ---------------------------------------------------------------------------
# MoM chain with the unchanged HWRF functions on the HAFS folders
# ---------------------------------------------------------------------------


def run_step(name, func, *args):
    try:
        func(*args)
    except Exception:
        logging.exception(f"HAFS {name} failed for {args}")


def process_rain_retried(cycle):
    """process_rain, retried while the download fails"""
    for retry in range(1, HAFS_RETRIES + 1):
        try:
            process_rain(cycle)
            return
        except Exception:
            logging.exception(
                f"HAFS data failed for {cycle}, retry {retry} of {HAFS_RETRIES} "
                f"in {HAFS_WAIT_SECONDS}s"
            )
            time.sleep(HAFS_WAIT_SECONDS)
    try:
        process_rain(cycle)
    except Exception:
        logging.exception(f"HAFS data failed for {cycle}")


def hafs_workflow(cycle):
    """HAFS MoM, +DFO+VIIRS and final alert of a cycle; False if the cycle is
    not due yet (later cycles are not either)"""
    from processing.hwrf.HWRF_MoM import (
        final_alert_pdc,
        make_gfms_summary,
        update_HWRF_MoM,
        update_HWRFMoM_DFO_VIIRS,
    )

    if cycle_time(cycle) > utc_now():
        return False
    # GFMS first: the last rain files land while it runs
    run_step("GFMS", make_gfms_summary, cycle)
    # the HWRF MoM of a cycle uses the rain of the previous cycle
    previous = shift_cycle(cycle, -6)
    process_rain_retried(previous)
    # without it, update_HWRF_MoM uses the cycle before, or goes on without rain
    with hafs_folders():
        run_step("MoM", update_HWRF_MoM, cycle)
        run_step("MoM+DFO+VIIRS", update_HWRFMoM_DFO_VIIRS, cycle)
        run_step("final alert", final_alert_pdc, cycle)
    return True


def run_day_dfo_viirs(adate):
    """HAFS-based DFO MoM and VIIRS MoM of a day, once its 4 HAFS MoM exist"""
    from processing.dfo.DFO_MoM import update_DFO_MoM
    from processing.viirs.VIIRS_MoM import update_VIIRS_MoM

    for hh in HOURS:
        mom = os.path.join(
            settings.HAFS_MOM_DIR, f"Final_Attributes_{adate}{hh}HAFSUpdated.csv"
        )
        if not os.path.exists(mom):
            return
    with hafs_folders():
        run_step("DFO MoM", update_DFO_MoM, adate)
        run_step("VIIRS MoM", update_VIIRS_MoM, adate)


def hafs_outputs(adate):
    """HAFS MoM, DFO/VIIRS and final alert outputs of a day (YYYYMMDD)"""
    patterns = [
        os.path.join(settings.HAFS_MOM_DIR, f"*_{adate}[0-9][0-9]HAFS*.csv"),
        os.path.join(settings.DFO_HAFS_DIR, f"*_{adate}18MOM+DFOUpdated.csv"),
        os.path.join(settings.VIIRS_HAFS_DIR, f"*_{adate}18MOM+DFO+VIIRSUpdated.csv"),
        os.path.join(
            settings.FINAL_HAFS_DIR,
            f"Final_Attributes_{adate}[0-9][0-9]HAFS+MOM+DFO+VIIRSUpdated_PDC.csv",
        ),
    ]
    return sorted(f for p in patterns for f in glob.glob(p))


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def batchrun_HAFS_MoM(dates, overwrite=False):
    """HAFS data and MoM for the given days (oldest first); a final alert
    compares with the previous cycle's final alert when there is one, as in
    the cron job"""
    make_dirs()
    if overwrite:
        for adate in dates:
            for f in hafs_outputs(adate):
                logging.info(f"HAFS remove: {f}")
                os.remove(f)

    for adate in dates:
        due = True
        for hh in HOURS:
            due = hafs_workflow(adate + hh)
            if not due:
                break
        run_day_dfo_viirs(adate)
        if not due:
            break


def HAFS_cron():
    """cron job: catch up the last LOOKBACK_DAYS days"""
    if not settings.HAFS_ENABLED:
        logging.info("HAFS disabled in production.cfg, nothing to do")
        return
    today = utc_now().date()
    dates = [
        (today - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(settings.HAFS_LOOKBACK_DAYS - 1, -1, -1)
    ]
    batchrun_HAFS_MoM(dates)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=int, help="backfill this many days up to --end")
    parser.add_argument(
        "--end",
        default=utc_now().strftime("%Y%m%d"),
        help="last day to backfill, YYYYMMDD (default: today, UTC)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="delete the HAFS MoM, DFO/VIIRS and final alert outputs of these days first",
    )
    args = parser.parse_args()

    settings.configure_logging("HAFS")
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    if not settings.HAFS_ENABLED:
        logging.info("HAFS disabled in production.cfg, nothing to do")
        return
    if not args.days:
        HAFS_cron()
        return
    end = datetime.strptime(args.end, "%Y%m%d")
    dates = [
        (end - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(args.days - 1, -1, -1)
    ]
    logging.info(f"HAFS backfill {dates[0]} - {dates[-1]}, overwrite={args.overwrite}")
    batchrun_HAFS_MoM(dates, overwrite=args.overwrite)
    logging.info("HAFS backfill finished")


if __name__ == "__main__":
    main()
