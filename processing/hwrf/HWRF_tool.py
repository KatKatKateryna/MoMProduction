"""
HWRF_tool.py
    -- cron job script for HWRF data
"""

import csv
import json
import logging
import math
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from bs4 import BeautifulSoup
from osgeo import gdal
from rasterio import Affine
from rasterio.mask import mask
from shapely.affinity import translate
from shapely.geometry import Point, mapping, shape

import settings
from processing.hwrf.HWRF_MoM import hwrf_workflow
from processing.utilities import (
    RAIN_SUMMARY_HEADERS,
    get_current_processing_datehour,
    hwrf_today,
    watersheds_gdb_reader,
)

# per-watershed summaries from the precomputed cell lookup (hwrf_cells.py);
# False = the original per-watershed masks
HWRF_CELL_LOOKUP = True


def check_status(adate):
    """check if a give date is processed"""

    processed_list = os.listdir(settings.HWRF_SUM_DIR)
    processed = any(adate in x for x in processed_list)

    if processed:
        return processed

    # extra check
    processed_list = os.listdir(settings.HWRF_PROC_DIR)
    zipped_list = [x for x in processed_list if "zip" in x]
    processed = any(adate in x for x in zipped_list)

    return processed


def check_hours(adate):
    """check if it is too early to process"""
    # adate in YYYYMMDDHH

    ct = datetime.now(timezone.utc)
    da = datetime.strptime(adate, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    delta = ct - da
    dhours = delta.total_seconds() / 3600.0
    if dhours > settings.HWRF_TIME_DELAY:
        return False
    else:
        return True


def generate_procesing_list():
    """generate the processing list"""

    hosturl = settings.config.get("hwrf", "HOST")

    try:
        # one request both checks the host and lists the dates
        reqs = requests.get(hosturl, timeout=60)
        if reqs.status_code != 200:
            logging.info(f"Host URL {hosturl} is not accessible")
            return {}  # Return an empty dictionary if URL isn't reachable
        soup = BeautifulSoup(reqs.text, "html.parser")

        datelist = {}
        for link in soup.find_all("a"):
            fstr = link.string
            if not fstr:
                continue
            if fstr[:5] == "hwrf.":
                a_entry = fstr.split(".")[1]
                a_entry = a_entry.replace("/", "")
                datelist[a_entry] = hosturl + fstr

        # first level output
        # {'20220712': 'https://ftpprd.ncep.noaa.gov/data/nccf/com/hwrf/prod/hwrf.20220712/', '20220713': 'https://ftpprd.ncep.noaa.gov/data/nccf/com/hwrf/prod/hwrf.20220713/'}
        # extract second level

        dataurllist = {}
        for key in datelist.keys():
            hosturl = datelist[key]
            reqs = requests.get(hosturl)
            soup = BeautifulSoup(reqs.text, "html.parser")
            for link in soup.find_all("a"):
                fstr = link.string
                if not fstr:
                    continue
                hhstr = fstr.replace("/", "")
                if hhstr in ["00", "06", "12", "18"]:
                    a_entry = key + hhstr
                    if check_status(a_entry):
                        continue
                    # check if it is too early to process the data
                    if check_hours(a_entry):
                        continue
                    dataurllist[a_entry] = hosturl + fstr

        return dataurllist

    except requests.exceptions.RequestException as e:
        logging.warning(f"Error accessing host URL {hosturl}: {e}")
        return {}  # Return an empty dictionary


def HWRF_download(hwrfurl):
    """download rainfall data"""
    reqs = requests.get(hwrfurl)
    soup = BeautifulSoup(reqs.text, "html.parser")

    ascii_list = []
    for link in soup.find_all("a"):
        fstr = link.string
        if "rainfall.ascii" in fstr:
            fstr_local = os.path.join(settings.HWRF_PROC_DIR, fstr)
            if not os.path.exists(fstr_local):
                subprocess.run(
                    [
                        "wget",
                        f"{hwrfurl.rstrip('/')}/{fstr}",
                        "-P",
                        settings.HWRF_PROC_DIR,
                    ],
                    check=True,
                )  # Raises CalledProcessError on non-zero exit
            ascii_list.append(fstr)

    return ascii_list


def process_rain(adate, TC_Rain):
    """process rainfall data"""

    ## VRT template to read the csv
    vrt_template = """<OGRVRTDataSource>
        <OGRVRTLayer name='{}'>
            <SrcDataSource>{}</SrcDataSource>
            <GeometryType>wkbPoint</GeometryType>
            <GeometryField encoding="PointFromColumns" x="lon" y="lat" z="Z"/>
        </OGRVRTLayer>
    </OGRVRTDataSource>"""

    ## Read each text file and create the separate tiff file
    for i in TC_Rain:
        with open(i, "r") as f:
            variable = csv.reader(f, delimiter=" ")
            row_count = 1
            for row in variable:
                if row_count == 1:
                    while "" in row:
                        row.remove("")
                    XLC = float(row[0])
                    XRC = float(row[1])
                    YBC = float(row[2])
                    YTC = float(row[3])
                    res = float(row[4])
                    nrows = float(row[5])
                    ncol = float(row[6])
                    row_count = row_count + 1
        df = (
            pd.read_table(
                i, skiprows=1, sep=r"\s+", names=("lat", "lon", "Z")
            )
        ).fillna(-999)
        df.sort_values(by=["lat", "lon"], ascending=[False, True])
        df = df[["lon", "lat", "Z"]]
        df = df[df.lon >= XLC]
        df = df[df.lon <= XRC]
        df = df[df.lat >= YBC]
        df = df[df.lat <= YTC]
        df = df[df.Z > 0]
        df.to_csv(i.replace(".ascii", ".csv"), index=False, sep=" ")
        with open(i.replace(".ascii", ".vrt"), "w") as g:
            g.write(
                vrt_template.format(
                    i.replace(".ascii", ""), i.replace(".ascii", ".csv")
                )
            )
        g.close()
        r = gdal.Rasterize(
            i.replace(".ascii", ".tiff"),
            i.replace(".ascii", ".vrt"),
            outputSRS="EPSG:4326",
            xRes=res,
            yRes=res,
            attribute="Z",
            noData=-999,
        )
        r = None
        os.remove(i.replace(".ascii", ".csv"))

    ##merge all  tiffs file als
    # nd delete the individual tiff, vrt and ascii file

    TC_Rain_tiff = []
    for i in TC_Rain:
        TC_Rain_tiff.append(i.replace(".ascii", ".tiff"))

    filename = "hwrf." + adate + "rainfall.vrt"
    raintiff = filename.replace(".vrt", ".tiff")
    vrt = gdal.BuildVRT(filename, TC_Rain_tiff)
    gdal.Translate(raintiff, vrt, creationOptions=["COMPRESS=LZW", "TILED=YES"])
    vrt = None

    # no need
    # gdalcmd = "gdal_translate -of GTiff " + filename + " " + raintiff
    # subprocess.call(gdalcmd, shell=True)

    # create a zipfile
    zip_file = "hwrf." + adate + "rainfall.zip"
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zipObj:
        for i in TC_Rain_tiff:
            asfile = i.replace(".tiff", ".ascii")
            zipObj.write(asfile)

    for i in TC_Rain_tiff:
        os.remove(i)
        os.remove(i.replace(".tiff", ".ascii"))
        os.remove(i.replace(".tiff", ".vrt"))

    return raintiff


def HWRF_extract_by_mask(mask_json, tiff):
    """extract by each watershed"""

    with rasterio.open(tiff) as src:
        shapes = [mask_json["features"][0]["geometry"]]
        if src.bounds.right > 180:
            # image past the dateline (0..360): the watershed there is at
            # longitude + 360
            shapes.append(mapping(translate(shape(shapes[0]), xoff=360)))
        try:
            out_image, out_transform = mask(src, shapes, crop=True)
        except ValueError as e:
            #'Input shapes do not overlap raster.'
            # print(e)
            src = None
            # return empty dataframe
            return pd.DataFrame()

    # extract data
    no_data = src.nodata
    # extract the values of the masked array
    # print(out_image)
    data = out_image[0]
    # extract the row, columns of the valid values
    row, col = np.where(data != no_data)
    point_value = np.extract(data != no_data, data)
    if len(point_value) == 0:
        src = None
        # return empty dataframe
        return pd.DataFrame()

    T1 = out_transform * Affine.translation(0.5, 0.5)  # reference the pixel centre
    rc2xy = lambda r, c: T1 * (c, r)
    px, py = src.res
    # print (px,py)
    pixel_area_km2 = (
        lambda lon, lat: 111.111 * 111.111 * math.cos(lat * 0.01745) * px * py
    )
    d = gpd.GeoDataFrame({"col": col, "row": row, "intensity": point_value})
    # coordinate transformation
    d["lon"] = d.apply(lambda row: rc2xy(row.row, row.col)[0], axis=1)
    d["lat"] = d.apply(lambda row: rc2xy(row.row, row.col)[1], axis=1)
    d["area"] = d.apply(lambda row: pixel_area_km2(row.lon, row.lat), axis=1)

    # geometry
    d["geometry"] = d.apply(lambda row: Point(row["lon"], row["lat"]), axis=1)
    # first 2 points
    src = None
    return d


def HWRF_extract_by_watershed(raintiff):
    """extract flood info by watershed"""

    ## zonal analysis using merged tiff and watersheds
    watersheds = watersheds_gdb_reader()
    pfafid_list = watersheds.index.tolist()

    headers_list = RAIN_SUMMARY_HEADERS
    output_csv = raintiff.replace(".tiff", ".csv")
    with open(output_csv, "w") as f:
        writer = csv.writer(f)
        writer.writerow(headers_list)
    has_data = False

    # fast path: precomputed watershed cells (hwrf_cells.py), same rows
    rows = None
    if HWRF_CELL_LOOKUP:
        from processing.hwrf.hwrf_cells import summary_rows_from_tiff

        try:
            rows = summary_rows_from_tiff(raintiff, watersheds)
        except Exception:
            logging.exception("HWRF cell lookup failed, using the per-watershed masks")
    if rows is not None:
        with open(output_csv, "a") as f:
            csv.writer(f).writerows(rows)
        has_data = len(rows) > 0
        pfafid_list = []

    with open(output_csv, "a") as f:
        writer = csv.writer(f)
        for the_pfafid in pfafid_list:
            test_json = json.loads(
                gpd.GeoSeries([watersheds.loc[the_pfafid, "geometry"]]).to_json()
            )
            if test_json["features"][0]["geometry"] == None:
                continue
            data_points = HWRF_extract_by_mask(test_json, raintiff)
            # write summary to a csv file
            if not data_points.empty:
                HWRF_TotalArea_km = data_points["area"].sum()
                HWRF_perc_Area = (
                    HWRF_TotalArea_km / watersheds.loc[the_pfafid]["area_km2"] * 100
                )
                HWRF_MeanRain = data_points["intensity"].mean()
                HWRF_MaxRain = data_points["intensity"].max()
                results_list = [
                    the_pfafid,
                    HWRF_TotalArea_km,
                    HWRF_perc_Area,
                    HWRF_MeanRain,
                    HWRF_MaxRain,
                ]
                writer.writerow(results_list)
                has_data = True
    # has_data, move file to the right locaition
    # no_data: keep the header-only summary (processed, no rain over any
    # watershed; a missing summary means not processed yet), delete the image
    shutil.move(output_csv, os.path.join(settings.HWRF_SUM_DIR, output_csv))
    if has_data:
        shutil.move(raintiff, os.path.join(settings.HWRF_IMG_DIR, raintiff))
        os.remove(raintiff.replace(".tiff", ".vrt"))
    else:
        os.remove(raintiff)
        os.remove(raintiff.replace(".tiff", ".vrt"))
        logging.info(f"no data, empty summary: {output_csv}")

    return [output_csv, has_data]


def HWRF_cron():
    """main cron script"""

    # get date list
    datelist = generate_procesing_list()
    print(datelist)
    # for debug
    # print(datelist)
    # sys.exit()

    if len(datelist) == 0:
        logging.info("no new data to process!")
        # get current processing hour
        curdatestr = get_current_processing_datehour(
            time_delay=settings.HWRF_TIME_DELAY
        )
        print(curdatestr)
        # check if there is the hwrf data for this hour
        if not hwrf_today(adate=curdatestr[:8], ahour=curdatestr[-2:]):
            logging.info(f"no HRWRF data, run {curdatestr}")
            hwrf_workflow(curdatestr)

        return

    # switch to processing folder
    os.chdir(settings.HWRF_PROC_DIR)

    # download - process ascii
    for key in datelist:
        logging.info(f"check: {key}")
        a_list = HWRF_download(datelist[key])
        if len(a_list) == 0:
            logging.info(f"no rainfall data {key}")
            continue
        logging.info(f"processing {key}")
        newtiff = process_rain(key, a_list)
        logging.info(f"processing {newtiff}")
        [hwrfcsv, dataflag] = HWRF_extract_by_watershed(newtiff)
        if not dataflag:
            logging.info(f"no rain over watersheds, empty summary: {hwrfcsv}")
            # if no csv produced, it shall just conitune to produce MoM output
            # continue
        logging.info(f"generated: {hwrfcsv}")

        # run MoM update
        testdate = key
        hwrf_workflow(testdate)

    os.chdir(settings.BASE_DIR)

    return


def main():
    HWRF_cron()


if __name__ == "__main__":
    main()
