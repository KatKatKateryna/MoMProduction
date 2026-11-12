"""
DFO_tool.py

Download and process DFO data

Two main function:
    * DFO_cron : run the daily cron job
    * DFO_cron_fix: rerun cron-job for a given date
"""

import csv
import logging
import math
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
import zipfile

import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from osgeo import gdal

from processing.dfo.DFO_MoM import update_DFO_MoM
import settings
from processing.utilities import bbox_wanted, watersheds_gdb_reader
from processing.watershed_cells import raster_summary

load_dotenv()

# for command line mode, no need for cron-job
# from progressbar import progress

# total number of hdf files
DFO_TOTAL_TILES = 287
# a date is processed with at least this share of DFO_TOTAL_TILES
DFO_MIN_TILE_SHARE = 0.8
DFO_MINIMUM_TILES = 280

# a date still without DFO data this many days later gets an all-zero
# summary (empty_summary), back to DFO_FILL_LOOKBACK_DAYS: D-1 and D-2, the
# dates the MoM cycles look at first (cycle_inputs.dfo_for_cycle); a date the
# host still lists is never filled, its data can still be processed
DFO_FILL_AFTER_DAYS = 1
DFO_FILL_LOOKBACK_DAYS = 2

# flood layers of the DFO hdf files, in the order of the summary columns
FLOOD_LAYERS = [
    "Flood 1-Day 250m",
    "Flood 1-Day CS 250m",
    "Flood 2-Day 250m",
    "Flood 3-Day 250m",
]


def get_real_date(year, day_num):
    """get the real date"""

    # allData/61/MCDWD_L3_NRT/2021/021

    # year,day_num = foldername.split("/")[-2:]
    res = datetime.strptime(str(year) + "-" + str(day_num), "%Y-%j").strftime("%Y%m%d")

    return res


def check_status(adate):
    """check if a give date is processed"""

    processed_list = os.listdir(settings.DFO_SUM_DIR)
    processed = any(adate in x for x in processed_list)

    return processed


def get_hosturl(year=None):
    """the host url of a year's folder (default: the current year)"""
    baseurl = settings.config.get("dfo", "HOST")
    cur_year = year or datetime.now(timezone.utc).year
    hosturl = f"{baseurl.rstrip('/')}/{str(cur_year)}"

    return hosturl


def host_dates():
    """{YYYYMMDD: day number} of the finished dates the host has: those in its
    folder of the current year and of the year before (in the first days of
    January the last days of December are still there), before today -- a
    date's final tiles come the next day. A year folder the host has not got
    adds none. The year of a date is its first four digits."""
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y%m%d")
    dates = {}
    for year in (today.year - 1, today.year):
        reqs = requests.get(get_hosturl(year), timeout=120)
        if reqs.status_code == 404:
            continue
        reqs.raise_for_status()
        soup = BeautifulSoup(reqs.text, "html.parser")
        for link in soup.find_all("a"):
            if not (link.string and link.string.isdigit()):
                continue
            real_date = get_real_date(year, link.string)
            if real_date < today_str:
                dates[real_date] = link.string
    return dates


def host_tiles(adate, day_num):
    """how many tiles (.hdf) the host's folder of a date (YYYYMMDD, its day
    number) lists"""
    reqs = requests.get(
        f"{get_hosturl(int(adate[:4])).rstrip('/')}/{day_num}", timeout=120
    )
    reqs.raise_for_status()
    soup = BeautifulSoup(reqs.text, "html.parser")
    return len(
        {a.get("href", "") for a in soup.find_all("a") if a.get("href", "").endswith(".hdf")}
    )


# the DFO job processes this many of the host's newest finished dates
DFO_LATEST_DATES = 2


def latest_processing_dates(dates):
    """the dates the DFO job processes: of the host's finished dates
    (host_dates, never today) the DFO_LATEST_DATES newest -- normally D-1 and
    D-2, so a day the job did not run is still caught up -- that have no
    summary yet, {day number: YYYYMMDD} as generate_procesing_list gives it;
    {} when they all have one, or the host has none. Older dates the host
    still keeps are not processed (backfill: online_dfo_images_from_host.py,
    offline_dfo_summaries_from_images.py)"""
    newest = sorted(dates or {})[-DFO_LATEST_DATES:]
    return {dates[d]: d for d in newest if not check_status(d)}


def generate_procesing_list():
    """the dates to process: the host's finished dates (host_dates) that have
    no summary yet, {day number: YYYYMMDD}, e.g. {'001': '20230101'}; the
    host keeps only about a week, so the day numbers of the two years never
    meet"""
    return {
        day_num: real_date
        for real_date, day_num in host_dates().items()
        if not check_status(real_date)
    }


# MCDWD tiles are 10 degrees: h counts east from 180 W, v south from 90 N,
# so h20v05 is the tile at 20 E, 30-40 N. A global day is 287 tiles.
TILE_DEG = 10
# how many tiles of the box the host had, kept with the downloaded day
EXPECTED_TILES_FILE = "bbox_tiles.txt"


def bbox_tile_ids():
    """the hXXvYY tile ids the box's watersheds reach, None without a box"""
    if settings.BBOX is None:
        return None
    return {
        f"h{h:02d}v{v:02d}"
        for h in range(36)
        for v in range(18)
        if bbox_wanted(
            -180 + h * TILE_DEG,
            90 - (v + 1) * TILE_DEG,
            -180 + (h + 1) * TILE_DEG,
            90 - v * TILE_DEG,
        )
    }


def dfo_download_tiles(subfolder, dataurl, tiles, dfokey):
    """download only the box's tiles of a day: the folder is listed once and
    each tile of the box is fetched by name, instead of the whole day"""
    try:
        reqs = requests.get(dataurl, timeout=120)
        reqs.raise_for_status()
    except requests.RequestException as e:
        logging.warning(f"Listing failed: {dataurl}, error: {e}")
        return False

    soup = BeautifulSoup(reqs.text, "html.parser")
    names = sorted(
        link.string.strip()
        for link in soup.find_all("a")
        if link.string
        and link.string.strip().endswith(".hdf")
        and any(t in link.string for t in tiles)
    )
    if not names:
        logging.warning(f"no tile of the box in {dataurl}")
        return False

    d_dir = os.path.join(settings.DFO_PROC_DIR, subfolder)
    os.makedirs(d_dir, exist_ok=True)
    ok = 0
    for name in names:
        if os.path.exists(os.path.join(d_dir, name)):
            ok += 1
            continue
        result = subprocess.run(
            [
                "wget",
                "-q",
                f"{dataurl}/{name}",
                "--header",
                f"Authorization: Bearer {dfokey}",
                "-P",
                d_dir,
            ],
            check=False,
        )
        if result.returncode == 0:
            ok += 1
        else:
            logging.warning(f"Download failed: {name}, error: {result.returncode}")
    # the completeness check of DFO_process counts against the box, not 287
    with open(os.path.join(d_dir, EXPECTED_TILES_FILE), "w") as f:
        f.write(str(len(names)))
    logging.info(f"DFO {subfolder}: {ok} of {len(names)} tiles of the box")
    return ok > 0


def expected_tiles(hdffolder):
    """how many tiles a day's folder should hold: every tile of the box the
    host had, or the global 287 without a box"""
    if settings.BBOX is None:
        return DFO_TOTAL_TILES
    try:
        with open(os.path.join(hdffolder, EXPECTED_TILES_FILE)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return len(bbox_tile_ids())


def dfo_download(subfolder, year=None):
    """download a subfolder (day number) of a year's folder on the host
    (default: the current year)"""

    # check if there is unfinished download
    d_dir = os.path.join(settings.DFO_PROC_DIR, subfolder)
    if os.path.exists(d_dir):
        # return
        # is file cases
        if os.path.isfile(d_dir):
            os.remove(d_dir)
        elif 0 < (
            len([f for f in os.listdir(d_dir) if f.endswith(".hdf")])
        ) >= DFO_MIN_TILE_SHARE * expected_tiles(d_dir):
            # enough tiles from an earlier run (of the box, as DFO_process
            # counts them), nothing to download
            return True
        else:
            # remove the subfolder
            shutil.rmtree(d_dir)

    dfokey = (
        settings.config.get("dfo", "TOKEN")
        if "???" not in settings.config.get("dfo", "TOKEN")
        else os.getenv("AUTH_DFO_TOKEN")
    )
    dataurl = f"{get_hosturl(year).rstrip('/')}/{subfolder}"

    # with a box, only its tiles of the day are downloaded
    tiles = bbox_tile_ids()
    if tiles is not None:
        return dfo_download_tiles(subfolder, dataurl, tiles, dfokey)

    # os-agnostic process
    cmd = [
        "wget",
        "-e",
        "robots=off",
        "-r",
        "--no-parent",
        "-l",
        "1",
        "-R",
        ".html,.tmp",
        "-nH",
        "--cut-dirs=8",
        dataurl,
        "--header",
        f"Authorization: Bearer {dfokey}",
        "-P",
        settings.DFO_PROC_DIR,
    ]

    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        # something wrong with downloading
        logging.warning(f"Download failed: {dataurl}, error: {result.returncode}")
        print("Exit code:", result.returncode)
        # sys.exit()
        return False

    return True


def summary_headers(vrt_file):
    """summary columns of a flood layer mosaic (e.g. Flood_1-Day_CS_250m.vrt)"""
    headerprefix = os.path.basename(vrt_file).split("_")[1]
    if "_CS_" in vrt_file:
        headerprefix = "1-Day_CS"

    return [
        "pfaf_id",
        headerprefix + "_TotalArea_km2",
        headerprefix + "_perc_Area",
    ]


def dfo_extract_by_watershed(vtk_file):
    """extract data by all the watersheds"""

    watersheds = watersheds_gdb_reader()
    pfaf_id_list = watersheds.index.tolist()

    headers_list = summary_headers(vtk_file)
    summary_file = os.path.basename(vtk_file)[:-4] + ".csv"
    if not os.path.exists(summary_file):
        with open(summary_file, "w") as f:
            writer = csv.writer(f)
            writer.writerow(headers_list)
    else:
        # already processed,
        return

    # per-watershed flooded area, exact cell shares (watershed_cells.py)
    summary = raster_summary("dfo", vtk_file, watersheds)
    with open(summary_file, "a") as f:
        writer = csv.writer(f)

        for pfaf_id in pfaf_id_list:
            DFO_TotalArea = summary.at[pfaf_id, "area"]
            DFO_Area_percent = summary.at[pfaf_id, "perc_area"]

            results_list = [
                pfaf_id,
                "{:.3f}".format(DFO_TotalArea),
                "{:.3f}".format(DFO_Area_percent),
            ]
            writer.writerow(results_list)

    return


def DFO_process(folder, adate):
    """processing dfo folder

    folder structure
    allData/61/MCDWD_L3_NRT/2021/021
        |-Flood 1-Day 250m
        |-Flood 1-Day CS 250m
        |-Flood 2-Day 250m
        |-Flood 3-Day 250m
        Flood_3-Day_250m.vrt
        Flood_2-Day_250m.vrt
        Flood_1-Day_CS_250m.vrt
        Flood_1-Day_250m.vrt
    """

    hdffolder = os.path.join(settings.DFO_PROC_DIR, folder)
    if os.path.isfile(hdffolder):
        logging.warning(f"Not downloaded properly: {folder}")
        return
    os.makedirs(hdffolder, exist_ok=True)

    # switch to working directory
    os.chdir(hdffolder)

    floodlayer = FLOOD_LAYERS
    # new layer name mapping
    floodsubdataset = {
        "Flood 1-Day 250m": "Flood_1Day_250m",
        "Flood 1-Day CS 250m": "FloodCS_1Day_250m",
        "Flood 2-Day 250m": "Flood_2Day_250m",
        "Flood 3-Day 250m": "Flood_3Day_250m",
    }
    # create sub folder if necessary
    for flood in floodlayer:
        subfolder = flood.replace(" ", "_")
        if not os.path.exists(subfolder):
            os.mkdir(subfolder)

    # MCDWD_L3_NRT.A2021022.h06v04.061.hdf
    # HDF4_EOS:EOS_GRID:"MCDWD_L3_NRT.A2021022.h06v04.061.hdf":Grid_Water_Composite:"Flood 1-Day 250m"
    # HDF4_EOS:EOS_GRID:"MCDWD_L3_NRT.A2021022.h06v04.061.hdf":Grid_Water_Composite:"Flood 1-Day CS 250m"
    # HDF4_EOS:EOS_GRID:"MCDWD_L3_NRT.A2021022.h06v04.061.hdf":Grid_Water_Composite:"Flood 2-Day 250m"
    # HDF4_EOS:EOS_GRID:"MCDWD_L3_NRT.A2021022.h06v04.061.hdf":Grid_Water_Composite:"Flood 3-Day 250m"
    # HDF4_EOS:EOS_GRID:"{HDF}":Grid_Water_Composite:"{floodLAYER}"

    # scan hdf files
    hdffiles = []
    for entry in os.listdir():
        if entry[-4:] != ".hdf":
            continue
        HDF = entry
        hdffiles.append(HDF)

    # check the number of files
    if len(hdffiles) < DFO_MIN_TILE_SHARE * expected_tiles(hdffolder):
        logging.warning(f"Not enough files: {folder}")
        return

    # one step one image operation
    vrt_list = []
    for flood in floodlayer:
        subfolder = flood.replace(" ", "_")
        
        vrt = f"{subfolder}.vrt"
        # DFO_20210603_Flood_3-Day_250m.tiff
        tiff = os.path.join(settings.DFO_IMG_DIR, f"DFO_{adate}_{subfolder}.tiff")
        # skip existing VRT when its image is there too
        if os.path.exists(vrt) and os.path.exists(tiff):
            vrt_list.append(vrt)
            continue

        subdataset = floodsubdataset[flood]
        tiff_list = []
        # geotiff convert
        for HDF in hdffiles:
            nameprefix = "_".join(HDF.split(".")[1:3])
            inputlayer = f'HDF4_EOS:EOS_GRID:"{HDF}":Grid_Water_Composite:{subdataset}'
            tile_tiff = nameprefix + "_" + subfolder
            outputtiff = os.path.join(subfolder, tile_tiff + ".tiff")
            tiff_list.append(outputtiff)
            if not os.path.exists(outputtiff):
                # gdal cmd
                gdalcmd = f"gdal_translate -of GTiff -co Tiled=Yes {inputlayer} {outputtiff}"
                # convert geotiff
                os.system(gdalcmd)
        # build vrt
        # gdalcmd = f"gdalbuildvrt {subfolder}.vrt {subfolder}/*.tiff"
        # print(gdalcmd)
        # os.system(gdalcmd)

        gdal.BuildVRT(vrt, tiff_list)
        vrt_list.append(vrt)
        # extract flood data
        dfo_extract_by_watershed(vrt)

        # build geotiff
        gdalcmd = f"gdal_translate -co TILED=YES -co COMPRESS=LZW -of GTiff {vrt} {tiff}"
        os.system(gdalcmd)

        # delete tiff folder
        if os.path.exists(subfolder):
            shutil.rmtree(subfolder)

    # merge flood data into one file
    csv_list = []
    for vrt in vrt_list:
        csvfile = vrt.replace(".vrt", ".csv")
        pdc = pd.read_csv(csvfile)
        csv_list.append(pdc)

    merged = csv_list[0].merge(csv_list[1], on="pfaf_id")
    merged = merged.merge(csv_list[2], on="pfaf_id")
    merged = merged.merge(csv_list[3], on="pfaf_id")

    # save output
    summary_csv = os.path.join(settings.DFO_SUM_DIR, "DFO_{}.csv".format(adate))
    merged.to_csv(summary_csv)
    logging.info(f"generated: {summary_csv}")

    # zip the original folder
    if settings.config["storage"].getboolean("dfo_save"):
        zipped = os.path.join(settings.DFO_PROC_DIR, "DFO_{}.zip".format(adate))

        # os-agnostic process
        with zipfile.ZipFile(zipped, "w", compression=zipfile.ZIP_STORED) as z:
            for root, _, files in os.walk("."):

                for name in files:
                    path = os.path.join(root, name)
                    # prevent self-inclusion in a zip folder
                    if os.path.abspath(path) == os.path.abspath(zipped):
                        continue
                    arcname = os.path.relpath(path, ".")
                    z.write(path, arcname)

        logging.info(f"generated: {zipped}")

    # remove all hdf file in the folder
    for entry in os.listdir():
        if ".hdf" in entry:
            os.remove(entry)

    # switch back script folder
    os.chdir(settings.BASE_DIR)

    return


def empty_summary(on_host):
    """write an all-zero DFO summary (no image) for each recent date that
    still has no DFO data DFO_FILL_AFTER_DAYS days later, so DFO adds nothing
    that day instead of blocking the DFO/VIIRS MoM and leaving the alerts on
    old DFO data. on_host: the host's dates (host_dates), none of which is
    filled -- the host has its data, which can still be processed; None when
    the host could not be listed (unreachable or gone), then every such date
    is filled"""
    today = datetime.now(timezone.utc).date()
    pfaf_ids = None
    columns = [
        col
        for flood in FLOOD_LAYERS
        for col in summary_headers(flood.replace(" ", "_") + ".vrt")[1:]
    ]
    for back in range(DFO_FILL_LOOKBACK_DAYS, DFO_FILL_AFTER_DAYS - 1, -1):
        adate = (today - timedelta(days=back)).strftime("%Y%m%d")
        summary_csv = os.path.join(settings.DFO_SUM_DIR, f"DFO_{adate}.csv")
        if os.path.exists(summary_csv):
            continue
        if on_host is not None and adate in on_host:
            logging.info(f"DFO {adate}: no summary, but on the host: not filled")
            continue
        if pfaf_ids is None:
            pfaf_ids = watersheds_gdb_reader().index
        empty = pd.DataFrame(0.0, index=range(len(pfaf_ids)), columns=columns)
        empty.insert(0, "pfaf_id", pfaf_ids)
        empty.to_csv(summary_csv)
        os.makedirs(settings.DFO_FILLED_DIR, exist_ok=True)
        with open(os.path.join(settings.DFO_FILLED_DIR, f"DFO_{adate}.txt"), "w") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} no DFO data {back} days after the date\n")
        logging.warning(
            f"DFO FILLED {adate}: no DFO data {back} days after the date, wrote an "
            f"ALL-ZERO summary (not observed data, no image): {summary_csv}"
        )


def DFO_cron():
    """cron job to process DFO"""

    # DFO reached EOL in September 2026
    try:
        on_host = host_dates()
    except Exception:
        # host unreachable or gone: the missing dates are still filled below
        logging.exception("DFO host listing failed")
        on_host = None
    datelist = latest_processing_dates(on_host)

    if len(datelist) == 0:
        # return instead of exiting the process: the caller still has to run
        # batchrun_DFO_MoM, which catches up any date whose MoM is missing or
        # was deferred because its cycles had not happened yet
        logging.info("no new data to process!")
        empty_summary(on_host)
        return

    logging.info(f"dates to process: {sorted(datelist.values())}")
    for key in datelist:
        # a failed or broken date never stops the run: it stays unprocessed,
        # is retried on the next run and filled by empty_summary below
        try:
            logging.info(f"download: {key} ({datelist[key]})")
            if not dfo_download(key, int(datelist[key][:4])):
                logging.warning(
                    f"download failed for {key} ({datelist[key]}), skipping: the "
                    "date stays unprocessed and is retried on the next run"
                )
                continue

            logging.info(f"download finished: {key}")
            logging.info(f"processing: {key} ({datelist[key]})")
            DFO_process(key, datelist[key])
            # run DFO_MoM
            update_DFO_MoM(datelist[key])
            logging.info(f"processing finished: {key}")
        except Exception:
            logging.exception(
                f"processing failed for {key} ({datelist[key]}), skipping: the "
                "date stays unprocessed and is retried on the next run"
            )
        finally:
            # DFO_process works inside the download folder
            os.chdir(settings.BASE_DIR)

    empty_summary(on_host)
    return


def DFO_fixdate(adate):
    """process a specific date (YYYYMMDD) that the host still has and that
    has no DFO summary yet"""
    datelist = generate_procesing_list()
    if adate not in datelist.values():
        logging.info(
            f"DFO fixdate {adate}: not on the host or already processed, "
            f"available: {sorted(datelist.values())}"
        )
        return

    key = [k for k, v in datelist.items() if v == adate][0]
    logging.info(f"try to fix a date: {adate}")
    logging.info(f"download: {key} ({adate})")
    if not dfo_download(key, int(adate[:4])):
        logging.warning(f"download failed for {key} ({adate})")
        return
    logging.info(f"download finished: {key}")
    logging.info(f"processing: {key} ({adate})")
    DFO_process(key, adate)
    update_DFO_MoM(adate)
    logging.info(f"processing finished: {key}")


def main():
    DFO_cron()


if __name__ == "__main__":
    main()
