"""
offline_dfo_summaries_from_images.py
    -- DFO summaries of the dates from --start to --end (YYYYMMDD; default:
       up to yesterday, today is never finished), rebuilt from the four
       layer images with the DFO job's own calculation. Only local files are
       read.

    The images are read from DFO/DFO_image and the summaries written to
    DFO/DFO_summary under --out-dir, a root in the production layout
    (default: the folders of production.cfg) -- where
    online_dfo_images_from_host.py writes the images with the same
    --out-dir. An existing summary is kept, unless --overwrite; a date
    without all four images, or today or later, is skipped.

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/offline_dfo_summaries_from_images.py --start 20261030 --end 20261031
    python backfill/offline_dfo_summaries_from_images.py --start 20261030 --overwrite
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402
from osgeo import gdal  # noqa: E402

import settings  # noqa: E402
from backfill_args import add_date_args, add_output_args, days, point_at  # noqa: E402
import processing.dfo.DFO_tool as dfo  # noqa: E402


def rebuild(adate, overwrite):
    images = {}
    for flood in dfo.FLOOD_LAYERS:
        layer = flood.replace(" ", "_")
        images[layer] = os.path.join(settings.DFO_IMG_DIR, f"DFO_{adate}_{layer}.tiff")
    missing = [p for p in images.values() if not os.path.exists(p)]
    if missing:
        logging.warning(f"{adate}: skipped, no {missing[0]}")
        return
    summary_csv = os.path.join(settings.DFO_SUM_DIR, f"DFO_{adate}.csv")
    if os.path.exists(summary_csv) and not overwrite:
        logging.info(f"{adate}: kept, {summary_csv}")
        return
    work = tempfile.mkdtemp(prefix=f"summary_{adate}_", dir=settings.DFO_PROC_DIR)
    cwd = os.getcwd()
    try:
        os.chdir(work)
        csv_list = []
        for layer, image in images.items():
            # the job's extraction works on <layer>.vrt in the current folder
            vrt = f"{layer}.vrt"
            gdal.BuildVRT(vrt, [image]).FlushCache()
            dfo.dfo_extract_by_watershed(vrt)
            csv_list.append(pd.read_csv(f"{layer}.csv"))
        merged = csv_list[0].merge(csv_list[1], on="pfaf_id")
        merged = merged.merge(csv_list[2], on="pfaf_id")
        merged = merged.merge(csv_list[3], on="pfaf_id")
        merged.to_csv(summary_csv + ".part")
        os.replace(summary_csv + ".part", summary_csv)
        logging.info(f"generated: {summary_csv}")
    finally:
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    add_date_args(parser, by_cycle=False)
    add_output_args(parser)
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    point_at(args.out_dir, ["DFO_IMG_DIR", "DFO_SUM_DIR"])
    os.makedirs(settings.DFO_SUM_DIR, exist_ok=True)
    os.makedirs(settings.DFO_PROC_DIR, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for adate in days(args.start, args.end):
        if adate >= today:
            logging.warning(f"{adate}: skipped, only dates up to yesterday")
            continue
        try:
            rebuild(adate, args.overwrite)
        except Exception:
            logging.exception(f"failed: {adate}")


if __name__ == "__main__":
    main()
