"""
online_hafs_steps_from_host.py
    -- the rain rate records of every storm of the HAFS cycles from --start to
       --end, every 6 h, downloaded again from the host into
       HAFS_steps/<cycle>/ (HAFS_tool.HAFS_download); nothing else is made.
       Only cycles the host still has, and has published, can be made.

    --start YYYYMMDD (its 00 cycle) or YYYYMMDDHH; --end YYYYMMDDHH, optional
    (default: the last cycle that has come, UTC; one the host has not
    published yet has no storms or swath files and is passed over).
    --out-dir: the root HAFS/HAFS_steps goes under, in the production layout
    (default: the folders of production.cfg). A record already there is kept,
    unless --overwrite: then it is downloaded again and replaces the stored
    one (which is put back if the download fails).

**EXAMPLE** (from the repository root, conda environment active)
    python backfill/online_hafs_steps_from_host.py --start 20261030
    python backfill/online_hafs_steps_from_host.py --start 2026110506 --end 2026110618 --overwrite
    python backfill/online_hafs_steps_from_host.py --start 20261105 --out-dir /data/rerun
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
from backfill_args import add_date_args, add_output_args, cycles, point_at  # noqa: E402
from processing.hafs.HAFS_tool import (  # noqa: E402
    HAFS_download,
    exists,
    list_storms,
    swath_url,
)


def record_path(stamp, storm):
    """where HAFS_download keeps a storm's rain records of a cycle"""
    name = f"{storm}.{stamp}.{settings.HAFS_MODEL}.parent.swath.prate.grb2"
    return os.path.join(settings.HAFS_STEPS_DIR, stamp, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    add_date_args(parser, by_cycle=True)
    add_output_args(parser)
    args = parser.parse_args()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    point_at(args.out_dir, ["HAFS_STEPS_DIR"])
    os.makedirs(settings.HAFS_STEPS_DIR, exist_ok=True)
    # HAFS_download's temporary file
    os.makedirs(settings.HAFS_WORK_DIR, exist_ok=True)
    for cycle in cycles(args.start, args.end):
        stamp = cycle.strftime("%Y%m%d%H")
        try:
            storms = list_storms(stamp)
            logging.info(f"{stamp}: storms {' '.join(storms)}")
            for storm in storms:
                path = record_path(stamp, storm)
                if os.path.exists(path) and not args.overwrite:
                    logging.info(f"kept: {path}")
                    continue
                if not exists(swath_url(stamp, storm)):
                    logging.warning(f"{stamp}: {storm} has no swath file")
                    continue
                # with --overwrite the stored one is put aside, and back if
                # the download fails
                old = path + ".old"
                if os.path.exists(path):
                    os.replace(path, old)
                try:
                    HAFS_download(stamp, storm)
                    if os.path.exists(old):
                        os.remove(old)
                except Exception:
                    logging.exception(f"failed: {stamp} {storm}")
                    if os.path.exists(old):
                        os.replace(old, path)
        except Exception:
            logging.exception(f"failed: {stamp}")
    logging.info("finished")


if __name__ == "__main__":
    main()
