"""
online_gfms_images_from_host.py
    -- the two images of the GFMS cycle times from a start cycle until now or
       an end cycle, every 6 h, the grids downloaded again from the host, as
       the GFMS job makes them: the window's maximum in GFMS_image
       (GFMS_save_max) and the window's steps in GFMS_steps
       (GFMS_save_steps). No summary or step file is made. Only cycles the
       host still has can be made.

    --start YYYYMMDD (its 00 cycle) or YYYYMMDDHH; --end YYYYMMDDHH, optional
    (default: the last cycle that has come, UTC). --out-dir: the root the images go under,
    in the production layout (default: the folders of production.cfg).
    --overwrite: both images of a cycle are made again and replace the stored
    ones; without it a cycle whose two images are there is skipped (nothing
    downloaded), and only a missing one is made.

**EXAMPLE** (from the repository root, conda environment active, under the
clone's MoM lock, as run_mom_job.sh takes it)
    flock logs/mom_job.lock python backfill/online_gfms_images_from_host.py --start 20261030
    flock logs/mom_job.lock python backfill/online_gfms_images_from_host.py --start 2026110506 --end 2026110506
    python backfill/online_gfms_images_from_host.py --start 2026110500 --end 2026110618 --out-dir /data/rerun
"""

import argparse
import logging
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
from backfill_args import add_date_args, add_output_args, cycles, point_at  # noqa: E402
from processing.gfms.GFMS_tool import (  # noqa: E402
    GFMS_download,
    GFMS_save_max,
    GFMS_save_steps,
    GFMS_window,
    GFMS_window_max,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    add_date_args(parser, by_cycle=True)
    add_output_args(parser)
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    point_at(args.out_dir, ["GFMS_IMG_DIR", "GFMS_STEPS_DIR", "GFMS_IMG_EXPIRED_DIR"])
    os.makedirs(settings.GFMS_IMG_DIR, exist_ok=True)
    os.makedirs(settings.GFMS_PROC_DIR, exist_ok=True)
    for cycle in cycles(args.start, args.end):
        stamp = cycle.strftime("%Y%m%d%H")
        name = f"Flood_byStor_{stamp}.tiff"
        image = os.path.join(settings.GFMS_IMG_DIR, name)
        steps_image = os.path.join(settings.GFMS_STEPS_DIR, name)
        if args.overwrite:
            # made again and put in place, not expired
            for f in (image, steps_image):
                if os.path.exists(f):
                    os.remove(f)
        elif os.path.exists(image) and os.path.exists(steps_image):
            logging.info(f"kept: {stamp}")
            continue
        window_dir = tempfile.mkdtemp(prefix="window_", dir=settings.GFMS_PROC_DIR)
        try:
            vrt_file = GFMS_download(f"Flood_byStor_{stamp}.bin")
            if os.path.exists(vrt_file):
                # the window's grids are downloaded once, into window_dir,
                # and read by both
                if not os.path.exists(image):
                    GFMS_save_max(GFMS_window_max(vrt_file, window_dir), stamp)
                done = GFMS_save_steps(GFMS_window(vrt_file, window_dir), stamp)
                logging.info(f"done: {done}")
            else:
                logging.warning(f"no grid: {stamp}")
        except Exception:
            logging.exception(f"failed: {stamp}")
        finally:
            shutil.rmtree(window_dir, ignore_errors=True)
    logging.info("finished")

if __name__ == "__main__":
    main()
