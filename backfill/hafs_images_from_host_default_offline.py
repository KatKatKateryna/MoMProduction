# Mainly for custom illustrations of selected storms, not a cron-style backfill.
"""
hafs_images_from_host_default_offline.py
    -- HAFS rainfall images for chosen storms (or all) and cycles: only the
       HAFS_image TIFF, the storms merged per cycle as in HAFS_tool.py
       (no summary, no MoM / Final_Attributes update)

    The rain kept is one 0-N h accumulation record of the swath file over the
    whole parent domain: no 3 h steps and no buffer along the track, so
    everything the domain covers (the Gulf included) is in the image. N is
    --hours, 6 by default, so each image is the rain of the 6 h following its
    own cycle; --hours 126 gives the whole forecast storm total instead. The
    track is not used at all, a storm only needs its swath file.

    By default only the HAFS host data backups are read
    (settings.HAFS_BACKUP_DIR) and the host is never contacted: the storms of
    a cycle are those with a rain file there. With --online the swath files
    are downloaded from the host instead, kept in
    <out-root>/HAFS_host_data_backups (host layout, as HAFS_tool.py keeps
    them); <out-root>/HAFS_work holds only the temporary files of the
    downloads (deleted as each one is read). The images go to
    <out-root>/HAFS_image or to --img-dir. Without --storms (or with
    --storms all) every storm of a cycle is taken. A storm missing from a
    cycle is skipped; a cycle with none of the storms gets no image.

Usage:
    python backfill/hafs_images_from_host_default_offline.py --out-root <out root> \\
        --start 2026101300 --end 2026101518 --hours 126
    python backfill/hafs_images_from_host_default_offline.py --out-root <out root> \\
        --start 2025100600 --end 2025101218 --storms 16e 17e 90e --hours 6 --online
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import settings
import HAFS_tool
from HAFS_tool import (
    MM_PER_INCH,
    RES,
    cycle_url,
    exists,
    is_rain_rate,
    list_storms,
    merge,
    norm_lons,
    rain_file,
    shift_cycle,
    write_tiff,
)


# eccodes (pip wheel) must be imported after GDAL/rasterio/geopandas, as
# HAFS_tool does: imported first, its bundled libraries clash with the conda
# GDAL ones and the process crashes on exit
import eccodes  # isort: skip


def storm_rain(cycle, storm, hours):
    """0-hours h rain (inches) of one storm over its whole swath domain on a
    0.05 deg grid: (grid, top, left). The rain rate records are 0-N h
    averages, so the 0-hours one alone is that accumulation -- no stepping,
    no track, nothing masked out"""
    path = rain_file(cycle, storm)
    with open(path, "rb") as tmp:
        # first pass: where the 0-hours h average rain rate is
        offset, steps = None, []
        while True:
            gid = eccodes.codes_grib_new_from_file(tmp)
            if gid is None:
                break
            try:
                if is_rain_rate(gid):
                    eccodes.codes_set(gid, "stepUnits", "h")
                    step = int(eccodes.codes_get(gid, "endStep"))
                    steps.append(step)
                    if step == hours:
                        offset = int(eccodes.codes_get(gid, "offset"))
            finally:
                eccodes.codes_release(gid)
        if offset is None:
            raise RuntimeError(
                f"no 0-{hours} h rain rate in {path}"
                + (f" (have 0-{min(steps)} .. 0-{max(steps)} h)" if steps else "")
            )

        # second pass: that record only
        tmp.seek(offset)
        gid = eccodes.codes_grib_new_from_file(tmp)
        try:
            shape = (eccodes.codes_get(gid, "Nj"), eccodes.codes_get(gid, "Ni"))
            lats = eccodes.codes_get_array(gid, "latitudes").reshape(shape)
            lons = norm_lons(eccodes.codes_get_array(gid, "longitudes").reshape(shape))
            values = eccodes.codes_get_values(gid).reshape(shape)
            if eccodes.codes_get(gid, "bitmapPresent"):
                values = np.where(values == eccodes.codes_get(gid, "missingValue"), np.nan, values)
        finally:
            eccodes.codes_release(gid)
    if not (np.array_equal(lats, np.broadcast_to(lats[:, :1], shape))
            and np.array_equal(lons, np.broadcast_to(lons[:1, :], shape))):
        raise RuntimeError(f"HAFS {storm} {cycle}: swath grid is not regular")
    total = values * hours * 3600  # kg m-2 = mm over 0-hours

    # nearest parent cell onto a 0.05 deg grid over the whole domain, pixel
    # centres on multiples of RES
    top = np.ceil(lats.max() / RES) * RES
    left = np.floor(lons.min() / RES) * RES
    ny = int(round((top - np.floor(lats.min() / RES) * RES) / RES)) + 1
    nx = int(round((np.ceil(lons.max() / RES) * RES - left) / RES)) + 1
    dlat, dlon = lats[1, 0] - lats[0, 0], lons[0, 1] - lons[0, 0]
    ri = np.rint(((top - RES * np.arange(ny))[:, None] - lats[0, 0]) / dlat).astype(int)
    ci = np.rint(((left + RES * np.arange(nx))[None, :] - lons[0, 0]) / dlon).astype(int)
    ri = np.clip(ri, 0, shape[0] - 1)
    ci = np.clip(ci, 0, shape[1] - 1)
    return total[ri, ci] / MM_PER_INCH, top, left


def backup_storms(cycle):
    """storms with a rain file in the backups for cycle (no host access)"""
    folder = os.path.dirname(HAFS_tool.backup_path(cycle, "x"))
    suffix = f".{cycle}.{settings.HAFS_MODEL}.parent.swath.prate.grb2"
    if not os.path.isdir(folder):
        return []
    return sorted(f[: -len(suffix)] for f in os.listdir(folder) if f.endswith(suffix))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out-root", required=True,
                        help="HAFS_image (and with --online the downloaded host data) go here")
    parser.add_argument("--start", required=True, help="first cycle YYYYMMDDHH")
    parser.add_argument("--end", required=True, help="last cycle YYYYMMDDHH")
    parser.add_argument("--img-dir", help="images here instead of <out-root>/HAFS_image")
    parser.add_argument("--storms", nargs="+", default=["all"],
                        help="storm ids, e.g. 16e 17e (default: all)")
    parser.add_argument("--online", action="store_true",
                        help="download from the host (default: the backups only, never the host)")
    parser.add_argument("--hours", type=int, default=6,
                        help="accumulation 0-HOURS h of each cycle, multiple of 3 (default 6)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing images")
    args = parser.parse_args()
    if args.hours <= 0 or args.hours % 3:
        parser.error("--hours must be a positive multiple of 3")

    # only to the console (redirected to backfill/logs/), not the processing log
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)

    img_dir = args.img_dir or os.path.join(args.out_root, "HAFS_image")
    if args.online:
        # host data and temporary swath files on the output disk, not the production one
        settings.HAFS_BACKUP_DIR = os.path.join(args.out_root, "HAFS_host_data_backups")
        HAFS_tool.WORK_DIR = os.path.join(args.out_root, "HAFS_work")
        os.makedirs(settings.HAFS_BACKUP_DIR, exist_ok=True)
        os.makedirs(HAFS_tool.WORK_DIR, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)
    logging.info(
        f"HAFS images {args.start} - {args.end}, 0-{args.hours} h, storms {' '.join(args.storms)}, "
        f"{'online' if args.online else 'offline'}, backups {settings.HAFS_BACKUP_DIR}, images {img_dir}"
    )

    wanted = {s.lower() for s in args.storms}
    cycle = args.start
    while cycle <= args.end:
        out = os.path.join(img_dir, f"hafs.{cycle}rainfall.tiff")
        if os.path.exists(out) and not args.overwrite:
            logging.info(f"HAFS {cycle}: image exists, skipped")
            cycle = shift_cycle(cycle, 6)
            continue
        storms = list_storms(cycle) if args.online else backup_storms(cycle)
        if "all" not in wanted:
            storms = [s for s in storms if s in wanted]
        grids = []
        for storm in storms:
            if args.online and not exists(f"{cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}.parent.swath.grb2"):
                logging.warning(f"HAFS {cycle}: {storm} swath file missing, skipped")
                continue
            grid, top, left = storm_rain(cycle, storm, args.hours)
            grids.append((grid, top, left))
            logging.info(f"HAFS {cycle}: {storm} 0-{args.hours} h max {np.nanmax(grid):.2f} in")
        if grids:
            grid, top, left = merge(grids)
            # next to the image: the work folder may be on another disk
            part = f"{out}.part"
            write_tiff(part, grid, top, left)
            os.replace(part, out)
            logging.info(f"HAFS {cycle}: image {os.path.basename(out)} ({' '.join(storms)})")
        else:
            logging.info(f"HAFS {cycle}: none of {' '.join(sorted(wanted))}"
                         f"{'' if args.online else ' in the backups'}, no image")
        cycle = shift_cycle(cycle, 6)


if __name__ == "__main__":
    main()
