"""
offline_gfms_images_split_steps.py
    -- give the stored GFMS cycle images the layout GFMS_tool now writes: the
       step bands in GFMS_steps, the window's maximum in GFMS_image

    A cycle image of the earlier layout is in GFMS_image with GFMS_BANDS (33)
    step bands, +0 h ... +96 h. It is moved to GFMS_steps under the same name,
    and GFMS_image gets in its place the one-band per-pixel maximum of those
    bands -- the same grids the cycle's summary was taken from; dry cells and
    times the host did not have are nodata (-9999), below every depth, so a
    plain maximum. Make a backup first.

    Only images with GFMS_BANDS bands, of cycles from --from on, are changed;
    a steps image already in GFMS_steps is not replaced (that cycle is left as
    it is). Reads and writes the GFMS folders of production.cfg
    (settings.GFMS_IMG_DIR, GFMS_STEPS_DIR); nothing is downloaded.

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/offline_gfms_images_split_steps.py --from 20261030 --dry-run
    python backfill/offline_gfms_images_split_steps.py --from 20261030
"""

import argparse
import glob
import logging
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rasterio  # noqa: E402

import settings  # noqa: E402
from processing.gfms.GFMS_tool import GFMS_BANDS  # noqa: E402

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def split(tiff_file):
    """tiff_file to GFMS_STEPS_DIR, its maximum in its place"""
    name = os.path.basename(tiff_file)
    new_tiff = os.path.join(os.path.dirname(tiff_file), "new_" + name)
    with rasterio.open(tiff_file) as src:
        profile = src.profile
        profile.update(count=1)
        grid = src.read().max(axis=0)
    with rasterio.open(new_tiff, "w", **profile) as dst:
        dst.write(grid, 1)
    os.makedirs(settings.GFMS_STEPS_DIR, exist_ok=True)
    shutil.move(tiff_file, os.path.join(settings.GFMS_STEPS_DIR, name))
    os.replace(new_tiff, tiff_file)
    logging.info(f"split: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    parser.add_argument(
        "--from", dest="first", required=True,
        help="first cycle date to change, YYYYMMDD",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="only report what would change"
    )
    args = parser.parse_args()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(
        LOG_DIR,
        "offline_gfms_images_split_steps_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}.log",
    )
    logging.basicConfig(
        filename=log_file, format=settings.LOG_FORMAT, level=logging.INFO, force=True
    )
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))

    changed = kept = 0
    for tiff_file in sorted(glob.glob(os.path.join(settings.GFMS_IMG_DIR, "Flood_byStor_*.tiff"))):
        name = os.path.basename(tiff_file)
        stamp = name[len("Flood_byStor_") : -len(".tiff")]
        with rasterio.open(tiff_file) as src:
            bands = src.count
        if (
            stamp[:8] < args.first
            or bands != GFMS_BANDS
            or os.path.exists(os.path.join(settings.GFMS_STEPS_DIR, name))
        ):
            kept += 1
            continue
        if args.dry_run:
            logging.info(f"would split: {name}")
        else:
            split(tiff_file)
        changed += 1
    logging.info(
        f"{'would change' if args.dry_run else 'changed'} {changed}, left as they "
        f"are {kept}, in {settings.GFMS_IMG_DIR}; log: {log_file}"
    )


if __name__ == "__main__":
    main()
