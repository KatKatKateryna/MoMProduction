"""
offline_gfms_summaries_from_images.py
    -- GFMS summaries (Flood_byStor_YYYYMMDDHH.csv) from saved GFMS images,
       offline, with the current calculation

    The GFMS image (GFMS_image/Flood_byStor_YYYYMMDDHH.tiff) is a lossless
    copy of the grid the summary is computed from (the .vrt of the .bin), so
    the summaries can be rebuilt from the images alone. For every image in
    IMAGE_DIR this writes OUT_DIR/Flood_byStor_YYYYMMDDHH.csv, the file
    GFMS_extract_by_watershed + GFMS_fix_duration write:
      - area, % of the watershed, mean and max depth from the image (cell
        lookup gfms_cells.py, or the per-watershed masks);
      - GFMS_Duration chained in time order as GFMS_fix_duration does: each
        day's times continue from the previous day's 21 summary (3 h more
        where the area is > 100 km2, else 0). That 21 summary is the one
        written by this run, else the one already in OUT_DIR, else the one in
        PREV_DIR (default GFMS_summary/, only read); with none, the first
        time of the day starts from its own 3 / 0, as in the cron job.

    Offline and never overwriting: nothing is downloaded, the images and
    PREV_DIR are only read, and a summary already in OUT_DIR is kept (and
    used for the following durations); a new one is written to a temporary
    name and linked into place only if that name is still free. The cell
    lookup data/gfms_watershed_cells.npz is built only when it does not exist
    yet (a new file); if it exists for another shapefile or grid, the
    per-watershed masks are used and it is left alone. The log goes to the
    console only.

Usage:
    python backfill/offline_gfms_summaries_from_images.py IMAGE_DIR OUT_DIR
    python backfill/offline_gfms_summaries_from_images.py IMAGE_DIR OUT_DIR --start 20261008 --end 20261013
    python backfill/offline_gfms_summaries_from_images.py IMAGE_DIR OUT_DIR --prev-dir /path/to/summaries
"""

import argparse
import csv
import glob
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geopandas  # noqa: E402
import pandas as pd  # noqa: E402

import gfms_cells  # noqa: E402
import settings  # noqa: E402
from GFMS_tool import GFMS_extract_by_mask  # noqa: E402
from utilities import watersheds_gdb_reader  # noqa: E402

IMAGE_RE = re.compile(r"^Flood_byStor_(\d{10})\.tiff$")
HEADERS = [
    "pfaf_id",
    "GFMS_TotalArea_km",
    "GFMS_perc_Area",
    "GFMS_MeanDepth",
    "GFMS_MaxDepth",
    "GFMS_Duration",
]


def find_images(image_dir, start, end):
    """{YYYYMMDD: {HH: path}} of the images in image_dir, by day"""
    days = {}
    for path in glob.glob(os.path.join(image_dir, "Flood_byStor_*.tiff")):
        m = IMAGE_RE.match(os.path.basename(path))
        if not m:
            continue
        stamp = m.group(1)
        if (start and stamp[:8] < start) or (end and stamp[:8] > end):
            continue
        days.setdefault(stamp[:8], {})[stamp[8:]] = path
    return {d: dict(sorted(h.items())) for d, h in sorted(days.items())}


def use_lookup():
    """True when the cell lookup can be used; builds it only when the file
    does not exist yet (never replaces it)"""
    if gfms_cells._LOOKUP is None:
        lookup = gfms_cells.read_lookup()
        if lookup is None and not os.path.exists(gfms_cells.CELLS_FILE):
            logging.info(f"building the GFMS cell lookup: {gfms_cells.CELLS_FILE}")
            gfms_cells.build()
            lookup = gfms_cells.read_lookup()
        if lookup is None:
            logging.warning("GFMS cell lookup does not match the watershed shapefile, using the per-watershed masks")
        # summary_rows takes the lookup from here instead of building one
        gfms_cells._LOOKUP = lookup if lookup is not None else False
    return gfms_cells._LOOKUP is not False


def raw_rows(tiff, watersheds):
    """the rows GFMS_extract_by_watershed writes for tiff (before the
    duration fix)"""
    if use_lookup():
        rows = gfms_cells.summary_rows(tiff, watersheds)
        if rows is not None:
            return rows
    rows = []
    for pfaf_id in watersheds.index.tolist():
        watershed_json = json.loads(geopandas.GeoSeries([watersheds.loc[pfaf_id, "geometry"]]).to_json())
        data_points = GFMS_extract_by_mask(tiff, watershed_json)
        total, perc, mean, maxd, duration = 0.0, 0.0, 0.0, 0.0, 0
        if not data_points.empty:
            total = data_points["area"].sum()
            if total > 100.0:
                duration = 3
            perc = total / watersheds.loc[pfaf_id]["area_km2"] * 100
            mean = data_points["intensity"].mean()
            maxd = data_points["intensity"].max()
        rows.append([pfaf_id, total, perc, mean, maxd, duration])
    return rows


def as_read_back(rows):
    """the table GFMS_fix_duration reads: the rows written with csv and read
    with pandas, so the values are exactly those of the cron files"""
    text = io.StringIO()
    writer = csv.writer(text)
    writer.writerow(HEADERS)
    writer.writerows(rows)
    text.seek(0)
    return pd.read_csv(text)


def fix_duration(df, df0):
    """GFMS_fix_duration for one table, after the previous table df0"""
    df = df.copy()
    df["GFMS_Duration0"] = df["pfaf_id"].map(df0.set_index("pfaf_id")["GFMS_Duration"])
    df["GFMS_Duration"] = df.apply(
        lambda row: (3 + int(row.GFMS_Duration0) if (row.GFMS_TotalArea_km > 100.0) else 0),
        axis=1,
    )
    del df["GFMS_Duration0"]
    return df


def write_new(df, path):
    """write df to path only if path does not exist; False if it does"""
    part = f"{path}.{os.getpid()}.part"
    df.to_csv(part, index=False)
    try:
        os.link(part, path)  # fails if path exists: never overwrites
    except FileExistsError:
        return False
    finally:
        os.remove(part)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image_dir")
    parser.add_argument("out_dir")
    parser.add_argument("--start", help="first date, YYYYMMDD")
    parser.add_argument("--end", help="last date, YYYYMMDD")
    parser.add_argument(
        "--prev-dir", default=settings.GFMS_SUM_DIR,
        help="summaries to continue the durations from (only read; default: GFMS_summary/)",
    )
    args = parser.parse_args()

    # only to the console (redirected to backfill/logs/), not the processing
    # folder log that importing settings points logging at
    logging.basicConfig(stream=sys.stdout, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    image_dir, out_dir = os.path.abspath(args.image_dir), os.path.abspath(args.out_dir)
    prev_dir = os.path.abspath(args.prev_dir)
    os.makedirs(out_dir, exist_ok=True)

    days = find_images(image_dir, args.start, args.end)
    logging.info(f"GFMS summaries from {image_dir} to {out_dir}: {len(days)} days, durations continued from {prev_dir}")
    watersheds = watersheds_gdb_reader()

    done = {}  # summaries of this run (written or kept), by name
    written, kept, failed = [], [], []
    for day, hours in days.items():
        prev_name = "Flood_byStor_{}21.csv".format(
            (datetime.strptime(day, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d"))
        df0 = done.get(prev_name)
        for folder in (out_dir, prev_dir):
            if df0 is None and os.path.exists(os.path.join(folder, prev_name)):
                df0 = pd.read_csv(os.path.join(folder, prev_name))
                logging.info(f"{day}: durations continue from {os.path.join(folder, prev_name)}")
        if df0 is None:
            logging.warning(f"{day}: no {prev_name}, the first time of the day starts its own duration")

        for hh, tiff in hours.items():
            name = f"Flood_byStor_{day}{hh}.csv"
            out_csv = os.path.join(out_dir, name)
            if os.path.exists(out_csv):
                df = pd.read_csv(out_csv)
                logging.info(f"{day}{hh}: already exists, kept: {out_csv}")
                kept.append(day + hh)
            else:
                try:
                    df = as_read_back(raw_rows(tiff, watersheds))
                except Exception:
                    logging.exception(f"{day}{hh}: failed, skipped (the next time continues from the one before)")
                    failed.append(day + hh)
                    continue
                if df0 is not None:
                    df = fix_duration(df, df0)
                if write_new(df, out_csv):
                    logging.info(f"{day}{hh}: generated {out_csv}")
                    written.append(day + hh)
                else:
                    df = pd.read_csv(out_csv)
                    logging.info(f"{day}{hh}: appeared meanwhile, kept: {out_csv}")
                    kept.append(day + hh)
            done[name] = df
            df0 = df

    logging.info(f"written {len(written)}, kept {len(kept)}, failed {len(failed)}")
    if failed:
        logging.info(f"failed: {' '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
