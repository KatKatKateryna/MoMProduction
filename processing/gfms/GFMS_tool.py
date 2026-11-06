"""
GFMS_tool.py

Download and process GFMS data (GloFAS: processing/glofas/GloFAS_tool.py)

Two main function:
    * GFMS_cron : run the daily cron job
    * GFMS_cron_fix: rerun cron-job for a given date

The summary of a 3-hourly GFMS time is taken from the per-pixel maximum of
the grids at that time and the following 3-hourly times up to
GFMS_WINDOW_HOURS ahead (the host publishes ~4 days of forecast), as far as
the host has them. Only the times that have come are summarized; a later
run makes the others. The following grids are downloaded again on every run
(the host rewrites its files); their .bin files go to a temporary folder that
is removed afterwards.

Every grid downloaded, of a summarized time or a following one, is saved as
an image. An image is only replaced when the new grid differs from the
stored one; the stored one is then moved to GFMS_images_expired.
"""

import csv
import glob
import logging
import math
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import rasterio
import requests
import zipfile

from processing.gfms.GFMS_MoM import flood_severity
from processing.glofas.GloFAS_tool import (
    GloFAS_process,
    find_latest_glofas,
    glofas_summary,
)
from processing.watershed_cells import cells_summary, raster_summary

# from HWRF_MoM import update_HWRF_MoM, update_HWRFMoM_DFO_VIIRS, final_alert_pdc
from processing.hwrf.HWRF_MoM import hwrf_workflow
import settings
from processing.utilities import (
    bbox_data_extent,
    hwrf_today,
    steps_csv,
    watersheds_gdb_reader,
    write_steps,
)

load_dotenv()
WATERSHEDS = None

# summary = per-pixel maximum over the time and the following hours
GFMS_WINDOW_HOURS = 96
# a file the host does not give yet is requested again this many times, this
# far apart
GFMS_ONLINE_RETRIES = 3
GFMS_ONLINE_WAIT_SECONDS = 600
# the summaries the MoM reads (GFMS MoM: 00, HWRF/HAFS MoM: every cycle) also
# get a step file (utilities.steps_csv), for the lead times
GFMS_STEP_HOURS = ("00", "06", "12", "18")

# no need for cron-job
# from progressbar import progress


def GFMS_url(bin_file):
    """host url of a given bin file"""
    datestr = bin_file.split("_")[2]
    baseurl = settings.config.get("gfms", "HOST")
    return f"{baseurl.rstrip('/')}/{datestr[:4]}/{datestr[:6]}/{bin_file}"


# the grid of the host's GFMS files
GFMS_NCOLS = 2458
GFMS_NROWS = 800
GFMS_RES = 0.125
GFMS_TOP = 50.0
GFMS_LEFT = -127.25
# a global file that is at least mostly complete
GFMS_MIN_BYTES = 7000000


def gfms_rows():
    """(first row, number of rows) of the grid the box's watersheds reach;
    the whole grid without a box"""
    extent = bbox_data_extent()
    if extent is None:
        return 0, GFMS_NROWS
    _, minlat, _, maxlat = extent
    row0 = min(max(int(math.floor((GFMS_TOP - maxlat) / GFMS_RES)), 0), GFMS_NROWS - 1)
    row1 = min(
        max(int(math.ceil((GFMS_TOP - minlat) / GFMS_RES)), row0 + 1), GFMS_NROWS
    )
    return row0, row1 - row0


def gfms_saved_rows(binfile_local):
    """which rows a downloaded .bin holds: the box's band when it was fetched
    as one, else the whole grid"""
    try:
        with open(binfile_local + ".rows") as f:
            row0, nrows = (int(x) for x in f.read().split())
        return row0, nrows
    except (OSError, ValueError):
        return 0, GFMS_NROWS


def gfms_fetch(url, binfile_local):
    """download a GFMS .bin -- with a box only the rows it covers, as one
    range request, since the file is a plain grid of rows (the whole file
    when the host does not serve ranges). Writes the .bin and, beside it,
    which rows it holds."""
    row0, nrows = gfms_rows()
    if (row0, nrows) != (0, GFMS_NROWS):
        start = row0 * GFMS_NCOLS * 4
        size = nrows * GFMS_NCOLS * 4
        r = requests.get(
            url, headers={"Range": f"bytes={start}-{start + size - 1}"}, timeout=300
        )
        if r.status_code == 206 and len(r.content) == size:
            with open(binfile_local, "wb") as f:
                f.write(r.content)
            with open(binfile_local + ".rows", "w") as f:
                f.write(f"{row0} {nrows}")
            return
        logging.info(
            f"GFMS host served no range ({r.status_code}), whole file: "
            f"{os.path.basename(url)}"
        )
    r = requests.get(url, allow_redirects=True, timeout=300)
    r.raise_for_status()
    if len(r.content) < GFMS_MIN_BYTES:
        raise ValueError(f"only {len(r.content)} bytes")
    with open(binfile_local, "wb") as f:
        f.write(r.content)
    with open(binfile_local + ".rows", "w") as f:
        f.write(f"0 {GFMS_NROWS}")


def GFMS_fetch(url):
    """the host's answer for a bin file, retried while it does not give it"""
    for retry in range(1, GFMS_ONLINE_RETRIES + 1):
        try:
            r = requests.get(url, allow_redirects=True)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            logging.info(
                f"not online yet: {url}: {e}, retry {retry} of "
                f"{GFMS_ONLINE_RETRIES} in {GFMS_ONLINE_WAIT_SECONDS}s"
            )
            time.sleep(GFMS_ONLINE_WAIT_SECONDS)
    r = requests.get(url, allow_redirects=True)
    r.raise_for_status()
    return r


def gfms_fetch_retried(url, binfile_local):
    """gfms_fetch, retried while the host does not give the file"""
    for retry in range(1, GFMS_ONLINE_RETRIES + 1):
        try:
            gfms_fetch(url, binfile_local)
            return
        except (requests.exceptions.RequestException, ValueError) as e:
            logging.info(
                f"not online yet: {url}: {e}, retry {retry} of "
                f"{GFMS_ONLINE_RETRIES} in {GFMS_ONLINE_WAIT_SECONDS}s"
            )
            time.sleep(GFMS_ONLINE_WAIT_SECONDS)
    gfms_fetch(url, binfile_local)


def GFMS_download(bin_file):
    """download a given bin file"""

    # find download url
    download_data_url = GFMS_url(bin_file)

    # check if .bin is already downloaded
    binfile_local = os.path.join(settings.GFMS_PROC_DIR, bin_file)
    vrt_file = binfile_local.replace(".bin", ".vrt")

    # check if the size is ok: a file holds the whole grid, or the rows of
    # the box when it was fetched as a band
    if os.path.exists(binfile_local):
        binsize = os.path.getsize(binfile_local)
        _, saved_rows = gfms_saved_rows(binfile_local)
        if binsize < min(saved_rows * GFMS_NCOLS * 4, GFMS_MIN_BYTES):
            os.remove(binfile_local)

    if not os.path.exists(binfile_local):
        # download the data
        try:
            if settings.BBOX is None:
                r = GFMS_fetch(download_data_url)
                with open(binfile_local, "wb") as f:
                    f.write(r.content)
            else:
                gfms_fetch_retried(download_data_url, binfile_local)

        except (requests.exceptions.RequestException, ValueError) as e:
            logging.error(f"Download failed: {e}")
            print(f"Download failed: {e}")
            # sys.exit()
            return vrt_file

        logging.info(f"Download: {bin_file}")
        print(f"Download: {bin_file}")

    return GFMS_write_vrt(binfile_local)


def GFMS_write_vrt(binfile_local):
    """write the .hdr and .vrt of a downloaded bin file, return the vrt"""
    vrt_file = binfile_local.replace(".bin", ".vrt")

    # a .bin holds the whole grid, or only the rows of the box
    row0, nrows = gfms_saved_rows(binfile_local)
    top = GFMS_TOP - row0 * GFMS_RES

    # generate header file
    hdr_header = f"""NCOLS {GFMS_NCOLS}
    NROWS {nrows}
    XLLCORNER {GFMS_LEFT}
    YLLCORNER {top - nrows * GFMS_RES}

    CELLSIZE {GFMS_RES}
    PIXELTYPE FLOAT
    BYTEORDER LSBFIRST
    NODATA_VALUE -9999
    """
    header_file = binfile_local.replace(".bin", ".hdr")
    with open(header_file, "w") as f:
        f.write(hdr_header)

    # generate vrt file: the bin's own 0.125 degree grid, not resampled, in
    # WGS 84 (EPSG:4326, as WKT so reading it needs no proj.db lookup)
    vrt_template = """<VRTDataset rasterXSize="{ncols}" rasterYSize="{nrows}">
  <SRS dataAxisToSRSAxisMapping="2,1">GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],AXIS["Longitude",EAST],AUTHORITY["EPSG","4326"]]</SRS>
  <GeoTransform>{left}, {res}, 0, {top}, 0, -{res}</GeoTransform>
  <VRTRasterBand dataType="Float32" band="1">
    <NoDataValue>-9999</NoDataValue>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">{name}</SourceFilename>
      <SourceBand>1</SourceBand>
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>"""

    # generate VRT file
    with open(vrt_file, "w") as f:
        f.write(
            vrt_template.format(
                ncols=GFMS_NCOLS,
                nrows=nrows,
                left=GFMS_LEFT,
                res=GFMS_RES,
                top=top,
                name=os.path.basename(binfile_local),
            )
        )

    return vrt_file


def GFMS_window_vrt(bin_file, window_dir):
    """download a bin file of the window into window_dir (once per run),
    return its vrt, or None if the host does not have it"""
    binfile_local = os.path.join(window_dir, bin_file)
    vrt_file = binfile_local.replace(".bin", ".vrt")
    if os.path.exists(vrt_file):
        return vrt_file
    if os.path.exists(binfile_local + ".missing"):
        return None
    try:
        gfms_fetch(GFMS_url(bin_file), binfile_local)
    except Exception as e:
        # past the end of the forecast the host answers 404
        if not (isinstance(e, requests.HTTPError) and e.response.status_code == 404):
            logging.warning(f"GFMS window file not used: {bin_file}: {e}")
        open(binfile_local + ".missing", "w").close()
        return None
    vrt_file = GFMS_write_vrt(binfile_local)
    GFMS_save_image(vrt_file)
    return vrt_file


def GFMS_max_grid(paths):
    """per-pixel maximum of GFMS grids, with the profile of the first; dry
    cells are nodata (-9999), below every depth, so a plain maximum"""
    with rasterio.open(paths[0]) as src:
        grid = src.read(1)
        profile = {
            "driver": "GTiff",
            "width": src.width,
            "height": src.height,
            "count": 1,
            "dtype": "float32",
            "crs": src.crs,
            "transform": src.transform,
            "nodata": src.nodata,
        }
    for path in paths[1:]:
        with rasterio.open(path) as src:
            np.maximum(grid, src.read(1), out=grid)
    return grid, profile


def GFMS_window(vrt_file, window_dir):
    """[(hours, vrt)] of vrt_file (0) and of the following 3-hourly times up
    to GFMS_WINDOW_HOURS, those the host has; a bin's time stamp is its valid
    time, so hours is the step after vrt_file's time"""
    stamp = os.path.basename(vrt_file)[len("Flood_byStor_") : -len(".vrt")]
    start = datetime.strptime(stamp, "%Y%m%d%H")
    window = [(0, vrt_file)]
    for hours in range(3, GFMS_WINDOW_HOURS + 1, 3):
        stamp_h = (start + timedelta(hours=hours)).strftime("%Y%m%d%H")
        window_vrt = GFMS_window_vrt(f"Flood_byStor_{stamp_h}.bin", window_dir)
        if window_vrt is not None:
            window.append((hours, window_vrt))
    return window


def GFMS_window_max(vrt_file, window_dir):
    """per-pixel maximum of the grids of GFMS_window; written to a tiff in
    window_dir, whose path is returned"""
    stamp = os.path.basename(vrt_file)[len("Flood_byStor_") : -len(".vrt")]
    window = GFMS_window(vrt_file, window_dir)
    paths = [p for _, p in window]
    used = [h for h, _ in window[1:]]
    grid, profile = GFMS_max_grid(paths)
    logging.info(
        f"GFMS {stamp}: maximum of {len(used) + 1} grids, "
        f"up to +{used[-1] if used else 0} h of {GFMS_WINDOW_HOURS} h"
    )
    max_tiff = os.path.join(window_dir, f"Flood_byStor_{stamp}_max.tiff")
    with rasterio.open(max_tiff, "w", **profile) as dst:
        dst.write(grid, 1)
    return max_tiff


def GFMS_extract_by_watershed(vrt_file, window_dir=None):
    """extract and summary; with window_dir, of the maximum over the window
    (GFMS_window_max) instead of vrt_file alone"""

    # load watersheds data
    global WATERSHEDS  # declare global before potential Write
    if WATERSHEDS is None:
        WATERSHEDS = watersheds_gdb_reader()

    pfaf_id_list = WATERSHEDS.index.tolist()

    # setup output file
    headers_list = [
        "pfaf_id",
        "GFMS_TotalArea_km",
        "GFMS_perc_Area",
        "GFMS_MeanDepth",
        "GFMS_MaxDepth",
        "GFMS_Duration",
    ]
    # put summary file into proc folder before fix-duration
    summary_file = os.path.join(
        settings.GFMS_PROC_DIR, os.path.basename(vrt_file)[:-4] + ".csv"
    )
    if (
        not os.path.exists(summary_file) or os.path.getsize(summary_file) == 0
    ):  # ignores corrupted files
        with open(summary_file, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers_list)
    else:
        # already processed,
        return

    # the window's steps for the summaries the MoM reads
    window = None
    stamp = os.path.basename(summary_file)[len("Flood_byStor_") : -len(".csv")]
    if window_dir and os.path.exists(vrt_file) and stamp[-2:] in GFMS_STEP_HOURS:
        window = GFMS_window(vrt_file, window_dir)

    if window_dir and os.path.exists(vrt_file):
        vrt_file = GFMS_window_max(vrt_file, window_dir)

    # per-watershed flooded area (exact cell shares), depth mean and max
    # (watershed_cells.py); without the file every watershed gets 0s
    summary = None
    if os.path.exists(vrt_file):
        cells = [] if window else None
        summary = raster_summary("gfms", vrt_file, WATERSHEDS, cells=cells)
        if window:
            # no step file (lead times -1) rather than no summary
            try:
                GFMS_write_steps(
                    window,
                    cells,
                    os.path.join(settings.GFMS_SUM_DIR, os.path.basename(summary_file)),
                )
            except Exception:
                logging.exception(f"GFMS step file failed: {summary_file}")

    with open(summary_file, "a") as f:
        writer = csv.writer(f)

        for pfaf_id in pfaf_id_list:
            # a watershed without flooded cells gets 0s
            GFMS_TotalArea = 0.0
            GFMS_Area_percent = 0.0
            GFMS_MeanDepth = 0.0
            GFMS_MaxDepth = 0.0
            GFMS_Duration = 0
            if summary is not None and summary.at[pfaf_id, "has_data"]:
                GFMS_TotalArea = summary.at[pfaf_id, "area"]
                if GFMS_TotalArea > 100.0:
                    GFMS_Duration = 3
                GFMS_Area_percent = summary.at[pfaf_id, "perc_area"]
                GFMS_MeanDepth = summary.at[pfaf_id, "mean"]
                GFMS_MaxDepth = summary.at[pfaf_id, "max"]

            results_list = [
                pfaf_id,
                GFMS_TotalArea,
                GFMS_Area_percent,
                GFMS_MeanDepth,
                GFMS_MaxDepth,
                GFMS_Duration,
            ]
            writer.writerow(results_list)

    logging.info(f"generated: {summary_file}")

    return


def GFMS_write_steps(window, cells, summary_csv):
    """step file of a summary (utilities.steps_csv): the summary of the
    per-pixel maximum up to each step of the window, over the cells of the
    whole window's maximum (cells, from raster_summary)"""
    running, steps = None, []
    for hours, path in window:
        with rasterio.open(path) as src:
            grid, nodata = src.read(1), src.nodata
        running = grid if running is None else np.maximum(running, grid)
        values = running.astype(np.float64)
        if nodata is not None:
            values[values == nodata] = np.nan
        steps.append((hours, cells_summary("gfms", cells, values, WATERSHEDS)))
    path = steps_csv(summary_csv)
    write_steps(path, steps)
    logging.info(f"generated: {path} ({len(steps)} steps)")


def GFMS_data_extractor(bin_file, window_dir=None):
    """extract data from a given binfile (see GFMS_extract_by_watershed
    for window_dir); returns the image status of GFMS_store_image, or None
    without an image"""

    # download GFMS binfile, generate vrt file
    vrt_file = GFMS_download(bin_file)

    # extract data by watershed
    logging.info(f"processing: {vrt_file}")
    GFMS_extract_by_watershed(vrt_file, window_dir)

    if not os.path.exists(vrt_file):
        print(f"VRT not found: {bin_file}")
        return None

    return GFMS_save_image(vrt_file)


def GFMS_save_image(vrt_file):
    """save the grid of vrt_file as GFMS_IMG_DIR/<name>.tiff (see
    GFMS_store_image); returns its status, or None if it failed"""
    # generate tiff from bin file
    tiff_name = os.path.basename(vrt_file).replace(".vrt", ".tiff")
    tiff_file = os.path.join(settings.GFMS_IMG_DIR, tiff_name)
    # made in the processing folder (same disk: putting it in place is a
    # rename), the image folder is public
    new_tiff = os.path.join(settings.GFMS_PROC_DIR, "new_" + tiff_name)

    gdalcmd = f'gdal_translate -co TILED=YES -co COMPRESS=LZW -of GTiff "{vrt_file}" "{new_tiff}"'
    if os.system(gdalcmd) != 0:
        logging.error(f"gdal_translate failed: {tiff_file}")
        if os.path.exists(new_tiff):
            os.remove(new_tiff)
        return None

    return GFMS_store_image(new_tiff, tiff_file)


def GFMS_same_image(tiff_a, tiff_b):
    """same grid, position, nodata and values"""
    try:
        with rasterio.open(tiff_a) as a, rasterio.open(tiff_b) as b:
            if (a.shape, a.transform, a.crs, a.nodata) != (
                b.shape,
                b.transform,
                b.crs,
                b.nodata,
            ):
                return False
            return np.array_equal(a.read(1), b.read(1), equal_nan=True)
    except rasterio.errors.RasterioIOError:
        # an unreadable stored image is replaced
        return False


def GFMS_store_image(new_tiff, tiff_file):
    """put new_tiff in place of tiff_file, unless the stored image is the
    same (then new_tiff is dropped); a stored image that differs is moved to
    GFMS_IMG_EXPIRED_DIR, never deleted. Returns "new", "unchanged" or
    "replaced"."""
    status = "new"
    if os.path.exists(tiff_file):
        if GFMS_same_image(new_tiff, tiff_file):
            os.remove(new_tiff)
            logging.info(f"unchanged, kept: {tiff_file}")
            return "unchanged"
        os.makedirs(settings.GFMS_IMG_EXPIRED_DIR, exist_ok=True)
        # named after the time the stored one was made
        made = datetime.fromtimestamp(os.path.getmtime(tiff_file), timezone.utc)
        expired = os.path.join(
            settings.GFMS_IMG_EXPIRED_DIR,
            os.path.basename(tiff_file).replace(
                ".tiff", made.strftime("_%Y%m%dT%H%M%S.tiff")
            ),
        )
        shutil.move(tiff_file, expired)
        logging.info(f"expired: {tiff_file} -> {expired}")
        status = "replaced"
    shutil.move(new_tiff, tiff_file)
    logging.info(f"generated: {tiff_file}")
    return status


def GFMS_fix_duration(csv0, csvlist):
    """fix duration"""
    # notice the folder issue
    # base0 shall be in GFMS_SUM_DIR
    # unfixed are in GFMS_PROC_DIR

    # first check if csv0 exists
    basecsv = os.path.join(settings.GFMS_SUM_DIR, csv0)
    if os.path.exists(basecsv):
        df0 = pd.read_csv(basecsv)
        start_in = 0
    else:
        df0 = pd.read_csv(os.path.join(settings.GFMS_PROC_DIR, csvlist[0]))
        start_in = 1
        # also write out to SUM folder
        df0.to_csv(os.path.join(settings.GFMS_SUM_DIR, csvlist[0]), index=False)

    for name in csvlist[start_in:]:
        csv_file = os.path.join(settings.GFMS_PROC_DIR, name)
        df = pd.read_csv(csv_file)

        df["GFMS_Duration0"] = df["pfaf_id"].map(
            df0.set_index("pfaf_id")["GFMS_Duration"]
        )
        df["GFMS_Duration"] = df.apply(
            lambda row: (
                3 + int(row.GFMS_Duration0) if (row.GFMS_TotalArea_km > 100.0) else 0
            ),
            axis=1,
        )
        del df["GFMS_Duration0"]
        fix_csv = os.path.join(settings.GFMS_SUM_DIR, name)
        df.to_csv(fix_csv, index=False)
        logging.info(f"generated: {fix_csv}")
        df0 = None
        df0 = df
        df = None


def GFMS_processing(proc_dates_list):
    """process GFMS data with a given list of dates"""

    # the window files are downloaded again on every run: the host rewrites them
    window_dir = tempfile.mkdtemp(prefix="window_", dir=settings.GFMS_PROC_DIR)
    try:
        _GFMS_processing(proc_dates_list, window_dir)
    finally:
        shutil.rmtree(window_dir, ignore_errors=True)


def _GFMS_processing(proc_dates_list, window_dir):
    binhours = ["00", "03", "06", "09", "12", "15", "18", "21"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for data_date in proc_dates_list:
        real_date = data_date[:-2]

        fix_list = []
        for binhour in binhours:
            # only the times that have come: a later run makes the others
            if datetime.strptime(real_date + binhour, "%Y%m%d%H") > now:
                break
            bin_file = "Flood_byStor_" + real_date + binhour + ".bin"
            new_csv = "Flood_byStor_" + real_date + binhour + ".csv"
            # print(os.path.join(settings.GFMS_SUM_DIR, new_csv))
            if not os.path.exists(os.path.join(settings.GFMS_SUM_DIR, new_csv)):
                fix_list.append(new_csv)
                # process bin file, generate .csv - some might be missing
                GFMS_data_extractor(bin_file, window_dir)

        # run duration caculation
        # durations continue from the summary 3 h before the first new one
        # (a run may make only part of the day); if that one does not exist,
        # GFMS_fix_duration starts from the first new one
        if fix_list:
            first = datetime.strptime(fix_list[0][13:23], "%Y%m%d%H")
            base0 = (
                "Flood_byStor_"
                + (first - timedelta(hours=3)).strftime("%Y%m%d%H")
                + ".csv"
            )
            GFMS_fix_duration(base0, fix_list)
        else:
            logging.info(f"GFMS summary already exists for: {real_date}")

        # flood severity calculation
        # take the first file of each day ("00" hour bin)
        gfmscsv = os.path.join(
            settings.GFMS_SUM_DIR, "Flood_byStor_" + data_date + ".csv"
        )
        glofascsv = glofas_summary(data_date)

        # in case of glofascsv data is missing, use the latest
        if not os.path.exists(glofascsv):
            glofas_latest = find_latest_glofas(settings.GLOFAS_DIR, data_date, "csv")
            glofascsv = os.path.join(settings.GLOFAS_DIR, glofas_latest)
            logging.warning(
                f"GloFAS threspoints_{data_date}.csv not published yet, falling "
                f"back to the latest available: {glofas_latest}"
            )

        # set directory for adding/removing files
        curdir = os.getcwd()
        os.chdir(settings.GFMS_PROC_DIR)

        # TODO: handle missing file
        # if os.path.exists(glofascsv) and os.path.exists(gfmscsv):
        # only proceed if valid data is present
        try:
            flood_severity(gfmscsv, glofascsv, real_date)  # if exists checked inside
        except Exception as e:
            logging.error(
                f"Flood severity calculation (GFMS_MoM) failed for {real_date}: {e}"
            )

        # zip GFMS data after processing
        zipped = os.path.join(settings.GFMS_PROC_DIR, f"gfms_{real_date}.zip")

        # Only touch the archive when there is something to add. Opening it in
        # "w" mode unconditionally truncated a good archive to an empty 22-byte
        # one on every rerun, because the source files had already been deleted
        # by the first run of the day. "a" is not an option either: it appends
        # without checking names, so reruns stacked duplicate entries.
        files = glob.glob(
            os.path.join(settings.GFMS_PROC_DIR, f"Flood_byStor_{real_date}*.*")
        )
        if not files:
            logging.info(f"nothing to archive for {real_date}")
        else:
            # Rebuild into a temp archive carrying over anything already stored
            # (a run may only cover part of the day), then swap it in
            # atomically so the archive is never left truncated.
            tmp_zip = f"{zipped}.tmp"
            names = {os.path.basename(f) for f in files}
            carried = 0
            with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as z:
                if os.path.exists(zipped):
                    try:
                        with zipfile.ZipFile(zipped) as old_z:
                            for item in old_z.infolist():
                                if item.filename not in names:
                                    z.writestr(item, old_z.read(item.filename))
                                    carried += 1
                    except zipfile.BadZipFile:
                        logging.warning(f"ignoring corrupt archive: {zipped}")
                for f in files:
                    z.write(f, arcname=os.path.basename(f))
            os.replace(tmp_zip, zipped)
            logging.info(f"archived {len(files)} new + {carried} existing: {zipped}")

            # remove only the files that were just archived
            for filePath in files:
                try:
                    os.remove(filePath)
                except OSError:
                    logging.warning(f"Error while deleting file : {filePath}")

        os.chdir(curdir)

    return


def GFMS_cron():
    """run GFMS cron job"""

    # process GloFAS data
    processing_dates = GloFAS_process()
    # today too, before its GloFAS is published: _GFMS_processing then uses the
    # latest one, and only makes the summaries that are missing
    today_date = datetime.now(timezone.utc).strftime("%Y%m%d") + "00"
    if today_date not in processing_dates:
        processing_dates.append(today_date)
    # process GFMS data
    # processing_dates = ['2021120200']
    GFMS_processing(processing_dates)

    # check if today's date are generated
    # if now hwrf data, then generate the output for 00 hour
    hwrf_flag = hwrf_today()
    if hwrf_flag:
        return
    # otherwise
    today = datetime.now(timezone.utc)
    tstr = datetime.strftime(today, "%Y%m%d")
    tstr = tstr + "00"
    gfmscsv = os.path.join(settings.GFMS_SUM_DIR, "Flood_byStor_" + tstr + ".csv")
    glofascsv = glofas_summary(tstr)
    if os.path.exists(gfmscsv) and os.path.exists(glofascsv):
        logging.info(f"no hwrf: {tstr}, generating ...")
        # update_HWRF_MoM(tstr)
        # update_HWRFMoM_DFO_VIIRS(tstr)
        # final_alert_pdc(tstr)
        hwrf_workflow(tstr)


def GFMS_fixdate(adate):
    """run cron job"""
    # cron steup cd ~/ModelofModels/data && python datatool.py --cron
    # run every three hours
    # edit: crontab -e
    # 5 0,3,6,9,12,15,18,21 * * * commnad

    # it is likly only one date: 2020051600
    # processing_dates = GloFAS_process()
    # check if GMS data is available
    # processing_dates = ['2020061800','2020061900','2020062000']
    if len(adate) == 8:
        adate = adate + "00"
    processing_dates = [adate]
    binhours = ["00", "03", "06", "09", "12", "15", "18", "21"]
    for data_date in processing_dates:
        real_date = data_date[:-2]
        for binhour in binhours:
            # bin_file = "Flood_byStor_" + real_date + binhour + ".bin"
            summary_file = "Flood_byStor_{}.csv".format(real_date + binhour)
            summary_file = os.path.join(settings.GFMS_SUM_DIR, summary_file)
            # remove partial processed summary file
            if os.path.exists(summary_file):
                os.remove(summary_file)
            # also need remove the file processing folder
            csv_in_proc = "Flood_byStor_{}.csv".format(real_date + binhour)
            csv_in_proc = os.path.join(settings.GFMS_PROC_DIR, csv_in_proc)
            if os.path.exists(csv_in_proc):
                os.remove(csv_in_proc)
    # reprocessing file
    GFMS_processing(processing_dates)


def debug():
    """debug the function"""
    # issue 38: gfms broken bin file
    binfile = "Flood_byStor_2022103103.bin"
    GFMS_data_extractor(binfile)


def main():
    """run the cron job"""
    GFMS_cron()


if __name__ == "__main__":
    main()
