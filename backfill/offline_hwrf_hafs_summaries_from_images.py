"""
offline_hwrf_hafs_summaries_from_images.py
    -- HWRF or HAFS rainfall summaries (<model>.YYYYMMDDHHrainfall.csv) from
       the saved rainfall images, offline, with the current calculation

    The image (<model>.YYYYMMDDHHrainfall.tiff) holds exactly the rain grid
    its summary is computed from, so the summaries can be rebuilt from the
    images alone. For every image in IMAGE_DIR this writes
    OUT_DIR/<model>.YYYYMMDDHHrainfall.csv with the HWRF calculation (cell
    lookup hwrf_cells.py, or the per-watershed masks, HWRF_extract_by_mask).

    A cycle with no rain over any watershed has a header-only summary and no
    image. Such summaries in SUMMARY_DIR (optional) are copied as they are; a
    summary in SUMMARY_DIR with rows but without an image cannot be rebuilt
    and is reported.

    Offline and never deleting: nothing is downloaded, the images and
    SUMMARY_DIR are only read. A summary already in OUT_DIR is kept unless
    --overwrite (then it is replaced). A new one is written to a temporary
    name and moved into place, without --overwrite only if that name is
    still free. The cell lookup data/hwrf_watershed_cells.npz is built only
    when it does not exist yet (a new file); if it exists for another
    shapefile, the per-watershed masks are used and it is left alone. The
    log goes to the console only.

Usage:
    python backfill/offline_hwrf_hafs_summaries_from_images.py hwrf IMAGE_DIR OUT_DIR
    python backfill/offline_hwrf_hafs_summaries_from_images.py hafs IMAGE_DIR OUT_DIR \\
        --summary-dir <PRODUCT_DIR>/HAFS/HAFS_summary --start 20261008 --end 20261013
"""

import argparse
import csv
import glob
import json
import logging
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas as gpd  # noqa: E402

import hwrf_cells  # noqa: E402
import settings  # noqa: E402
from HWRF_tool import HWRF_extract_by_mask  # noqa: E402
from utilities import RAIN_SUMMARY_HEADERS, watersheds_gdb_reader  # noqa: E402


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


def use_lookup():
    """True when the cell lookup can be used; builds it only when the file
    does not exist yet (never replaces it)"""
    if hwrf_cells._LOOKUP is None:
        lookup = hwrf_cells.read_lookup()
        if lookup is None and not os.path.exists(hwrf_cells.CELLS_FILE):
            logging.info(f"building the HWRF cell lookup: {hwrf_cells.CELLS_FILE}")
            hwrf_cells.build()
            lookup = hwrf_cells.read_lookup()
        if lookup is None:
            logging.warning("HWRF cell lookup does not match the watershed shapefile, using the per-watershed masks")
        # summary_rows takes the lookup from here instead of building one
        hwrf_cells._LOOKUP = lookup if lookup is not None else False
    return hwrf_cells._LOOKUP is not False


def summary_rows(tiff, watersheds):
    """the rows HWRF_extract_by_watershed / HAFS_tool.summarize write"""
    if use_lookup():
        rows = hwrf_cells.summary_rows_from_tiff(tiff, watersheds)
        if rows is not None:
            return rows
    rows = []
    for pfaf_id in watersheds.index.tolist():
        test_json = json.loads(gpd.GeoSeries([watersheds.loc[pfaf_id, "geometry"]]).to_json())
        if test_json["features"][0]["geometry"] is None:
            continue
        data_points = HWRF_extract_by_mask(test_json, tiff)
        if data_points.empty:
            continue
        total_area = data_points["area"].sum()
        rows.append([
            pfaf_id,
            total_area,
            total_area / watersheds.loc[pfaf_id]["area_km2"] * 100,
            data_points["intensity"].mean(),
            data_points["intensity"].max(),
        ])
    return rows


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
                rows = summary_rows(images[cycle], watersheds)
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
