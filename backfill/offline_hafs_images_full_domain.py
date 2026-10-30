"""
offline_hafs_images_full_domain.py
    -- HAFS images of past cycles, made as the HAFS cron job makes them
       (HAFS_tool.storm_rain, 0-126 h, the storms merged), into a folder of
       their own; no summary, track, done marker or MoM file is written

    Only the HAFS host data backups are read (settings.HAFS_BACKUP_DIR): the
    storms of a cycle are those kept there with both their rain and track
    files. A cycle with none gets no image. Existing images are kept unless
    --overwrite.

Usage:
    python backfill/offline_hafs_images_full_domain.py --out-dir <folder> \\
        --start 2026101300 --end 2026101518
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import settings
import HAFS_tool


def backup_storms(cycle):
    """storms kept in the backups for cycle with both rain and track files"""
    folder = os.path.dirname(HAFS_tool.backup_path(cycle, "x"))
    if not os.path.isdir(folder):
        return []
    suffix = f".{cycle}.{settings.HAFS_MODEL}"
    files = set(os.listdir(folder))
    return sorted(
        f.split(".")[0] for f in files
        if f.endswith(suffix + ".parent.swath.prate.grb2")
        and f.replace(".parent.swath.prate.grb2", ".trak.atcfunix") in files
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out-dir", required=True, help="the images go here")
    parser.add_argument("--start", required=True, help="first cycle YYYYMMDDHH")
    parser.add_argument("--end", required=True, help="last cycle YYYYMMDDHH")
    parser.add_argument("--overwrite", action="store_true", help="replace existing images")
    args = parser.parse_args()

    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    os.makedirs(args.out_dir, exist_ok=True)
    logging.info(f"HAFS full-domain images {args.start} - {args.end} into {args.out_dir}")

    cycle_time = datetime.strptime(args.start, "%Y%m%d%H")
    end = datetime.strptime(args.end, "%Y%m%d%H")
    while cycle_time <= end:
        cycle = cycle_time.strftime("%Y%m%d%H")
        cycle_time += timedelta(hours=6)
        out = os.path.join(args.out_dir, f"hafs.{cycle}rainfall.tiff")
        if os.path.exists(out) and not args.overwrite:
            logging.info(f"HAFS {cycle}: image exists, kept")
            continue
        storms = backup_storms(cycle)
        if not storms:
            logging.info(f"HAFS {cycle}: no storms in the backups, no image")
            continue
        grids = []
        for storm in storms:
            try:
                grid, top, left = HAFS_tool.storm_rain(cycle, storm, HAFS_tool.fetch_track(cycle, storm))
            except Exception as e:
                logging.warning(f"HAFS {cycle}: {storm} skipped: {e}")
                continue
            grids.append((grid, top, left))
            logging.info(f"HAFS {cycle}: {storm} max {np.nanmax(grid):.2f} in, {grid.shape[0]}x{grid.shape[1]}")
        if not grids:
            logging.info(f"HAFS {cycle}: no storm data, no image")
            continue
        grid, top, left = HAFS_tool.merge(grids)
        HAFS_tool.write_tiff(out, grid, top, left)
        logging.info(f"HAFS {cycle}: generated {out} ({' '.join(storms)})")


if __name__ == "__main__":
    main()
