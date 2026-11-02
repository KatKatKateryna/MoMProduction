"""
offline_viirs_summaries_from_images.py
    -- VIIRS summaries (VIIRS_Flood_YYYYMMDD.csv) from saved VIIRS images,
       with the current calculation (watershed_cells.py)

    For every date with both images in IMAGE_DIR
        VIIRS_1day_compositeYYYYMMDD_flood.tiff
        VIIRS_5day_compositeYYYYMMDD_flood.tiff
    writes OUT_DIR/VIIRS_Flood_YYYYMMDD.csv, the same file
    VIIRS_extract_by_watershed writes. Nothing is deleted: the images are
    only read, and a summary already in OUT_DIR is skipped unless
    --overwrite (then it is replaced). A new one is written to a temporary
    name and moved into place, without --overwrite only if that name is
    still free. A date with only one of the two images is
    skipped.

Usage:
    python backfill/offline_viirs_summaries_from_images.py IMAGE_DIR OUT_DIR
    python backfill/offline_viirs_summaries_from_images.py IMAGE_DIR OUT_DIR --start 20260601 --end 20261012
"""

import argparse
import csv
import glob
import io
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import settings  # noqa: E402
from processing.utilities import watersheds_gdb_reader  # noqa: E402
from processing.watershed_cells import raster_summary  # noqa: E402

IMAGE_RE = re.compile(r"^VIIRS_(1day|5day)_composite(\d{8})_flood\.tiff$")
PREFIX = {"1day": "oneday", "5day": "fiveday"}


def find_images(image_dir, start, end):
    """{date: {"1day": path, "5day": path}} of the images in image_dir"""
    images = {}
    for path in glob.glob(os.path.join(image_dir, "VIIRS_*_composite*_flood.tiff")):
        m = IMAGE_RE.match(os.path.basename(path))
        if not m:
            continue
        product, adate = m.groups()
        if (start and adate < start) or (end and adate > end):
            continue
        images.setdefault(adate, {})[product] = path
    return dict(sorted(images.items()))


def image_table(tiff, watersheds, prefix):
    """the per-image table VIIRS_extract_by_watershed writes and reads back
    (written with csv and read with pandas, so the values are exactly those
    of the cron files): pfaf_id, area and perc_area of every watershed"""
    summary = raster_summary("viirs", tiff, watersheds)
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(["pfaf_id", prefix + "Flood_Area_km", prefix + "perc_Area"])
    for pfaf_id in watersheds.index.tolist():
        writer.writerow([pfaf_id, summary.at[pfaf_id, "area"], summary.at[pfaf_id, "perc_area"]])
    text.seek(0)
    return pd.read_csv(text)


def summary(images, watersheds):
    """the VIIRS_Flood_YYYYMMDD table of a date, merged as
    VIIRS_extract_by_watershed does"""
    tables = {}
    for product in ("1day", "5day"):
        prefix = PREFIX[product]
        tables[prefix] = image_table(images[product], watersheds, prefix)
    join = tables["oneday"][tables["oneday"].onedayFlood_Area_km != 0]
    join1 = tables["fiveday"][tables["fiveday"].fivedayFlood_Area_km != 0]
    merge = pd.merge(
        join.set_index("pfaf_id"), join1.set_index("pfaf_id"), on="pfaf_id", how="outer"
    )
    merge.fillna(0, inplace=True)
    return merge


def write_new(table, path, overwrite=False):
    """write table to path; with overwrite an existing file is replaced, else
    kept (False)"""
    part = f"{path}.{os.getpid()}.part"
    table.to_csv(part)
    if overwrite:
        os.replace(part, path)
        return True
    try:
        os.link(part, path)  # fails if path exists: never overwrites
    except FileExistsError:
        return False
    finally:
        if os.path.exists(part):
            os.remove(part)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image_dir")
    parser.add_argument("out_dir")
    parser.add_argument("--start", help="first date, YYYYMMDD")
    parser.add_argument("--end", help="last date, YYYYMMDD")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="recompute and replace summaries already in OUT_DIR (default: skip them)",
    )
    args = parser.parse_args()

    # only to the console (redirected to backfill/logs/), not the processing
    # folder log that importing settings points logging at
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    image_dir, out_dir = os.path.abspath(args.image_dir), os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    dates = find_images(image_dir, args.start, args.end)
    logging.info(f"VIIRS summaries from {image_dir} to {out_dir}: {len(dates)} dates")
    watersheds = watersheds_gdb_reader()
    written, skipped, failed = [], [], []
    for adate, images in dates.items():
        out_csv = os.path.join(out_dir, f"VIIRS_Flood_{adate}.csv")
        if os.path.exists(out_csv) and not args.overwrite:
            logging.info(f"{adate}: already exists, skipped: {out_csv}")
            skipped.append(adate)
            continue
        missing = [p for p in ("1day", "5day") if p not in images]
        if missing:
            logging.warning(f"{adate}: no {' / '.join(missing)} image, skipped")
            skipped.append(adate)
            continue
        try:
            table = summary(images, watersheds)
        except Exception:
            logging.exception(f"{adate}: failed")
            failed.append(adate)
            continue
        if write_new(table, out_csv, args.overwrite):
            logging.info(f"{adate}: generated {out_csv}, {len(table)} watersheds with flooding")
            written.append(adate)
        else:
            logging.info(f"{adate}: appeared meanwhile, kept: {out_csv}")
            skipped.append(adate)

    logging.info(f"written {len(written)}, skipped {len(skipped)}, failed {len(failed)}")
    if failed:
        logging.info(f"failed: {' '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
