"""
offline_hwrf_hafs_summaries_from_images.py
    -- HWRF or HAFS rainfall summaries (<model>.YYYYMMDDHHrainfall.csv) from
       the saved rainfall images, offline, with the current calculation

    The image (<model>.YYYYMMDDHHrainfall.tiff) holds exactly the rain grid
    its summary is computed from, so the summaries can be rebuilt from the
    images alone. For every image in IMAGE_DIR this writes
    OUT_DIR/<model>.YYYYMMDDHHrainfall.csv with the calculation of the cron
    job (watershed_cells.py): HWRF as HWRF_extract_by_watershed, HAFS as
    HAFS_tool.summarize, the rain area counting only the cells above half
    the HWRF mean rain minimum (HWRF_Weightage.csv).

    A cycle with no rain over any watershed has a header-only summary and no
    image. Such summaries in SUMMARY_DIR (optional) are copied as they are; a
    summary in SUMMARY_DIR with rows but without an image cannot be rebuilt
    and is reported.

    Offline and never deleting: nothing is downloaded, the images and
    SUMMARY_DIR are only read. A summary already in OUT_DIR is kept unless
    --overwrite (then it is replaced). A new one is written to a temporary
    name and moved into place, without --overwrite only if that name is
    still free. The log goes to the console only.

Usage:
    python backfill/offline_hwrf_hafs_summaries_from_images.py hwrf IMAGE_DIR OUT_DIR
    python backfill/offline_hwrf_hafs_summaries_from_images.py hafs IMAGE_DIR OUT_DIR \\
        --summary-dir <PRODUCT_DIR>/HAFS/HAFS_summary --start 20261008 --end 20261013
"""

import argparse
import csv
import glob
import logging
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import rasterio  # noqa: E402

import settings  # noqa: E402
from processing.hwrf.HWRF_tool import rain_summary_rows  # noqa: E402
from processing.utilities import RAIN_SUMMARY_HEADERS, read_data, watersheds_gdb_reader  # noqa: E402
from processing.watershed_cells import lattice_summary, raster_summary  # noqa: E402


def cycles_of(folder, model, suffix, start, end):
    """{YYYYMMDDHH: path} of <model>.YYYYMMDDHHrainfall<suffix> in folder"""
    found = {}
    if not folder or not os.path.isdir(folder):
        return found
    pattern = re.compile(rf"^{model}\.(\d{{10}})rainfall{re.escape(suffix)}$")
    for path in glob.glob(os.path.join(folder, f"{model}.*rainfall{suffix}")):
        m = pattern.match(os.path.basename(path))
        if m and not (start and m.group(1)[:8] < start) and not (end and m.group(1)[:8] > end):
            found[m.group(1)] = path
    return dict(sorted(found.items()))


def rain_value_above():
    """the cutoff HWRF_extract_by_watershed and HAFS_tool.summarize use: the
    rain area counts only the cells above half the HWRF mean rain minimum"""
    HWRF_weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "HWRF_Weightage.csv"))
    return float(HWRF_weightage.HWRF_MeanRain_minwt[0]) / 2


def summary_rows(tiff, watersheds, model):
    """the rows HWRF_extract_by_watershed (hwrf) or HAFS_tool.summarize
    (hafs) write for the rain grid of tiff"""
    value_above = rain_value_above()
    if model == "hwrf":
        return rain_summary_rows(raster_summary("hwrf", tiff, watersheds, value_above=value_above))
    # HAFS summarizes its rain grid in memory; the image holds that grid,
    # nodata where there is no rain, top-left pixel centre (left, top)
    with rasterio.open(tiff) as src:
        grid = src.read(1).astype(np.float64)
        nodata, transform = src.nodata, src.transform
    if nodata is not None:
        grid[grid == nodata] = np.nan
    top, left = transform.f + transform.e / 2, transform.c + transform.a / 2
    return rain_summary_rows(
        lattice_summary(np.where(grid > 0, grid, np.nan), top, left, watersheds, value_above=value_above)
    )


def place(part, path, overwrite):
    """move part to path; without overwrite only if path is free (False if
    it is taken)"""
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


def is_header_only(path):
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        return next(reader, None) is None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", choices=["hwrf", "hafs"])
    parser.add_argument("image_dir")
    parser.add_argument("out_dir")
    parser.add_argument("--summary-dir", help="existing summaries, for the header-only ones (only read)")
    parser.add_argument("--start", help="first date, YYYYMMDD")
    parser.add_argument("--end", help="last date, YYYYMMDD")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="recompute and replace summaries already in OUT_DIR (default: keep them)",
    )
    args = parser.parse_args()

    # only to the console (redirected to backfill/logs/), not the processing
    # folder log that importing settings points logging at
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    model = args.model
    image_dir, out_dir = os.path.abspath(args.image_dir), os.path.abspath(args.out_dir)
    summary_dir = os.path.abspath(args.summary_dir) if args.summary_dir else None
    os.makedirs(out_dir, exist_ok=True)

    images = cycles_of(image_dir, model, ".tiff", args.start, args.end)
    summaries = cycles_of(summary_dir, model, ".csv", args.start, args.end)
    logging.info(f"{model} summaries from {image_dir} to {out_dir}: {len(images)} images, "
                 f"{len(summaries)} existing summaries in {summary_dir}")
    watersheds = watersheds_gdb_reader()
    written, copied, kept, failed, missing = [], [], [], [], []
    for cycle in sorted(set(images) | set(summaries)):
        name = f"{model}.{cycle}rainfall.csv"
        out_csv = os.path.join(out_dir, name)
        if os.path.exists(out_csv) and not args.overwrite:
            logging.info(f"{cycle}: already exists, kept: {out_csv}")
            kept.append(cycle)
            continue
        part = f"{out_csv}.{os.getpid()}.part"
        if cycle in images:
            try:
                rows = summary_rows(images[cycle], watersheds, model)
            except Exception:
                logging.exception(f"{cycle}: failed")
                failed.append(cycle)
                continue
            with open(part, "w") as f:
                writer = csv.writer(f)
                writer.writerow(RAIN_SUMMARY_HEADERS)
                writer.writerows(rows)
            done, note = written, f"generated, {len(rows)} watersheds with rain"
        elif is_header_only(summaries[cycle]):
            shutil.copyfile(summaries[cycle], part)
            done, note = copied, "no rain over watersheds, header-only summary copied"
        else:
            logging.warning(f"{cycle}: summary with rain but no image, cannot be rebuilt: {summaries[cycle]}")
            missing.append(cycle)
            continue
        if place(part, out_csv, args.overwrite):
            logging.info(f"{cycle}: {note}: {out_csv}")
            done.append(cycle)
        else:
            logging.info(f"{cycle}: appeared meanwhile, kept: {out_csv}")
            kept.append(cycle)

    logging.info(f"written {len(written)}, copied {len(copied)}, kept {len(kept)}, "
                 f"failed {len(failed)}, without image {len(missing)}")
    for label, cycles in (("failed", failed), ("without image", missing)):
        if cycles:
            logging.info(f"{label}: {' '.join(cycles)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
