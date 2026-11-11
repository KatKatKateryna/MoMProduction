"""
online_dfo_images_from_host.py
    -- the images of all four DFO layers of the dates from --start to --end
       (YYYYMMDD; default: up to today), one date at a time from the oldest;
       the tiles of a date are deleted once it is done. Summaries are not
       touched.

    Today is never made: its tiles are not complete until the next day. Every
    other date is first looked up on the host with the DFO job's own
    DFO_tool.host_dates (the folders of this year and the year before, so a
    run on 2 January reaches the last days of December) and host_tiles: one
    the host has not got (no longer kept) or whose folder there has no tiles
    is skipped with a warning, before anything local is checked.

    The images go to DFO_image under --out-dir, a root in the production
    layout (default: the folders of production.cfg). A date whose four images
    are there is skipped (nothing downloaded), and of a date with some of
    them only the missing ones are made, unless --overwrite: then all four
    are made again and replace the stored ones (the final tiles replace what
    was built from the first tiles of the day).

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/online_dfo_images_from_host.py --start 20261101
    python backfill/online_dfo_images_from_host.py --start 20261101 --end 20261105 --overwrite
    python backfill/online_dfo_images_from_host.py --start 20261101 --out-dir /data/rerun
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from osgeo import gdal  # noqa: E402

import settings  # noqa: E402
from backfill_args import add_date_args, add_output_args, days, point_at  # noqa: E402
import processing.dfo.DFO_tool as dfo  # noqa: E402

SUBDATASETS = {
    "Flood 1-Day 250m": "Flood_1Day_250m",
    "Flood 1-Day CS 250m": "FloodCS_1Day_250m",
    "Flood 2-Day 250m": "Flood_2Day_250m",
    "Flood 3-Day 250m": "Flood_3Day_250m",
}


def layer_image(flood, adate):
    """the image of a DFO layer of a date in DFO_IMG_DIR"""
    return os.path.join(settings.DFO_IMG_DIR, f"DFO_{adate}_{flood.replace(' ', '_')}.tiff")


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
    image = layer_image(flood, adate)
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
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    add_date_args(parser, by_cycle=False)
    add_output_args(parser)
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    point_at(args.out_dir, ["DFO_IMG_DIR"])
    os.makedirs(settings.DFO_IMG_DIR, exist_ok=True)
    # its own folder, apart from the DFO job's
    settings.DFO_PROC_DIR = os.path.join(settings.DFO_PROC_DIR, "images_backfill")
    os.makedirs(settings.DFO_PROC_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    on_host = dfo.host_dates()
    for adate in days(args.start, args.end):
        if adate >= today:
            logging.warning(f"{adate}: skipped, a date's tiles are complete only the next day")
            continue
        # the host first: nothing local is looked at for a date it has not got
        day = on_host.get(adate)
        if day is None:
            logging.warning(f"{adate}: not on the host, skipped")
            continue
        try:
            tiles = dfo.host_tiles(adate, day)
        except requests.RequestException as e:
            logging.warning(f"{adate}: host folder {day} not read, skipped: {e}")
            continue
        if not tiles:
            logging.warning(f"{adate}: host folder {day} has no tiles, skipped")
            continue
        floods = [
            flood for flood in dfo.FLOOD_LAYERS
            if args.overwrite or not os.path.exists(layer_image(flood, adate))
        ]
        if not floods:
            logging.info(f"kept: {adate}")
            continue
        day_dir = os.path.join(settings.DFO_PROC_DIR, day)
        try:
            logging.info(f"download: {day} ({adate}), {tiles} tiles on the host")
            if not dfo.dfo_download(day, int(adate[:4])):
                logging.warning(f"download failed: {day} ({adate})")
                continue
            hdffiles = sorted(f for f in os.listdir(day_dir) if f.endswith(".hdf"))
            logging.info(f"{adate}: {len(hdffiles)} tiles")
            for flood in floods:
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
