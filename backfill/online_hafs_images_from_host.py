"""
online_hafs_images_from_host.py
    -- download HAFS rainfall for chosen storms and cycles and produce only the
       HAFS_image TIFF, the chosen storms merged per cycle as in HAFS_tool.py
       (no summary, no MoM / Final_Attributes update)

    The rain kept is one 0-N h accumulation record of the swath file over the
    whole parent domain: no 3 h steps and no buffer along the track, so
    everything the domain covers (the Gulf included) is in the image. N is
    --hours, 6 by default, so each image is the rain of the 6 h following its
    own cycle; --hours 126 gives the whole forecast storm total instead. The
    track is not used at all, a storm only needs its swath file.

    The host data used is kept in <out-root>/HAFS_host_data_backups (host
    layout, as HAFS_tool.py keeps it), the images go to <out-root>/HAFS_image
    or to --img-dir. <out-root>/HAFS_work holds only the temporary files of
    the swath downloads (deleted as each one is read), whatever --img-dir is.
    A storm missing from a cycle is skipped; a cycle with none of the storms
    gets no image.

Usage:
    python backfill/online_hafs_images_from_host.py --out-root <backup root> \\
        --start 2025100600 --end 2025101218 --storms 16e 17e 90e --hours 6
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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out-root", required=True, help="HAFS_image and host backups go here")
    parser.add_argument("--start", required=True, help="first cycle YYYYMMDDHH")
    parser.add_argument("--end", required=True, help="last cycle YYYYMMDDHH")
    parser.add_argument("--img-dir", help="images here instead of <out-root>/HAFS_image")
    parser.add_argument("--storms", nargs="+", required=True, help="storm ids, e.g. 16e 17e")
    parser.add_argument("--hours", type=int, default=6,
                        help="accumulation 0-HOURS h of each cycle, multiple of 3 (default 6)")
    parser.add_argument("--overwrite", action="store_true", help="replace existing images")
    args = parser.parse_args()
    if args.hours <= 0 or args.hours % 3:
        parser.error("--hours must be a positive multiple of 3")

    # only to the console (redirected to backfill/logs/), not the processing log
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)

    img_dir = args.img_dir or os.path.join(args.out_root, "HAFS_image")
    # host data and temporary swath files on the output disk, not the production one
    settings.HAFS_BACKUP_DIR = os.path.join(args.out_root, "HAFS_host_data_backups")
    HAFS_tool.WORK_DIR = os.path.join(args.out_root, "HAFS_work")
    for d in (img_dir, settings.HAFS_BACKUP_DIR, HAFS_tool.WORK_DIR):
        os.makedirs(d, exist_ok=True)

    wanted = {s.lower() for s in args.storms}
    cycle = args.start
    while cycle <= args.end:
        out = os.path.join(img_dir, f"hafs.{cycle}rainfall.tiff")
        if os.path.exists(out) and not args.overwrite:
            logging.info(f"HAFS {cycle}: image exists, skipped")
            cycle = shift_cycle(cycle, 6)
            continue
        storms = [s for s in list_storms(cycle) if s in wanted]
        grids = []
        for storm in storms:
            if not exists(f"{cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}.parent.swath.grb2"):
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
            logging.info(f"HAFS {cycle}: none of {' '.join(sorted(wanted))}, no image")
        cycle = shift_cycle(cycle, 6)


if __name__ == "__main__":
    main()
