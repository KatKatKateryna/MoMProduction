"""
online_dfo_images_from_host.py
    -- the images of all four DFO layers for every finished date the host
       still has, one date at a time from the oldest; the tiles of a date are
       deleted once it is done. Summaries are not touched.

    An image that already exists is replaced: the final tiles replace what
    was built from the first tiles of the day.

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/online_dfo_images_from_host.py
"""

import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402
from osgeo import gdal  # noqa: E402

import settings  # noqa: E402
import processing.dfo.DFO_tool as dfo  # noqa: E402

SUBDATASETS = {
    "Flood 1-Day 250m": "Flood_1Day_250m",
    "Flood 1-Day CS 250m": "FloodCS_1Day_250m",
    "Flood 2-Day 250m": "Flood_2Day_250m",
    "Flood 3-Day 250m": "Flood_3Day_250m",
}


def host_days():
    """[(day number, YYYYMMDD)] of the host's finished dates, oldest first"""
    hosturl = dfo.get_hosturl()
    reqs = requests.get(hosturl, timeout=120)
    reqs.raise_for_status()
    soup = BeautifulSoup(reqs.text, "html.parser")
    year = hosturl[-4:]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    days = [a.string for a in soup.find_all("a") if a.string and a.string.isdigit()]
    dated = [(day, dfo.get_real_date(year, day)) for day in days]
    return sorted(d for d in dated if d[1] < today)


def build_layer(day_dir, hdffiles, flood, adate):
    layer = flood.replace(" ", "_")
    tile_dir = os.path.join(day_dir, layer)
    os.makedirs(tile_dir, exist_ok=True)
    tiffs = []
    for hdf in hdffiles:
        tiff = os.path.join(tile_dir, "_".join(hdf.split(".")[1:3]) + ".tiff")
        source = (
            f'HDF4_EOS:EOS_GRID:"{os.path.join(day_dir, hdf)}":'
            f"Grid_Water_Composite:{SUBDATASETS[flood]}"
        )
        subprocess.run(
            ["gdal_translate", "-q", "-of", "GTiff", "-co", "TILED=YES", source, tiff],
            check=True,
        )
        tiffs.append(tiff)
    vrt = os.path.join(day_dir, f"{layer}.vrt")
    gdal.BuildVRT(vrt, tiffs).FlushCache()
    image = os.path.join(settings.DFO_IMG_DIR, f"DFO_{adate}_{layer}.tiff")
    part = image + ".part"
    subprocess.run(
        ["gdal_translate", "-q", "-co", "TILED=YES", "-co", "COMPRESS=LZW", "-of",
         "GTiff", vrt, part],
        check=True,
    )
    os.replace(part, image)
    shutil.rmtree(tile_dir)
    logging.info(f"generated: {image}")


def main():
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    # its own folder, apart from the DFO job's
    settings.DFO_PROC_DIR = os.path.join(settings.DFO_PROC_DIR, "images_backfill")
    os.makedirs(settings.DFO_PROC_DIR, exist_ok=True)
    days = host_days()
    logging.info(f"dates: {[d[1] for d in days]}")
    for day, adate in days:
        day_dir = os.path.join(settings.DFO_PROC_DIR, day)
        try:
            logging.info(f"download: {day} ({adate})")
            if not dfo.dfo_download(day):
                logging.warning(f"download failed: {day} ({adate})")
                continue
            hdffiles = sorted(f for f in os.listdir(day_dir) if f.endswith(".hdf"))
            logging.info(f"{adate}: {len(hdffiles)} tiles")
            for flood in dfo.FLOOD_LAYERS:
                build_layer(day_dir, hdffiles, flood, adate)
        except Exception:
            logging.exception(f"failed: {day} ({adate})")
        finally:
            shutil.rmtree(day_dir, ignore_errors=True)
            logging.info(f"tiles deleted: {day} ({adate})")
    shutil.rmtree(settings.DFO_PROC_DIR, ignore_errors=True)
    logging.info("finished")


if __name__ == "__main__":
    main()
