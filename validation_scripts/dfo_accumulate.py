#!/usr/bin/env python
"""Accumulate historical DFO/MCDWD flood occurrence into a running GeoTIFF.

Source rasters are the NASA LANCE MCDWD_L3_NRT "Flood 3-Day 250m" products as
staged by MoM production (DFO_YYYYMMDD_Flood_3-Day_250m.tiff), whose pixel
classes are:

    0   no water
    1   surface water (normal / permanent water)
    2   recurring flood (regular, seasonal inundation)
    3   flood (unusual)
    255 insufficient data / fill

The result is a 2-band uint16 GeoTIFF, dfo_accumulater.tiff:

    band 1  number of source rasters in which the pixel was 3 (unusual flood)
    band 2  number of source rasters in which the pixel was 2 (recurring flood)

so each new raster folded in makes the file a longer historical record of
flood occurrence.

Grid handling
-------------
The source products do not all share one extent: within a single season they
appear as 172800x67200 at top latitude 80, 172800x62400 at top 80, and
172800x62400 at top 70. They do share the pixel size (1/480 deg), the left
edge (-180) and therefore the same grid lattice, so every source is written at
an integer row/column offset into one canonical target grid, which defaults to
the full MCDWD global grid (-180..180, 80N..60S, 172800x67200). Rows no source
ever covered simply stay 0.

Working store
-------------
Rewriting a 11.6-gigapixel compressed GeoTIFF for every single raster costs
about 7.5 minutes, nearly all of it LZW codec time, which is far too slow for
a season of data. Instead the running counts live in a sparse uint16 memmap
and only the handful of matching pixels per raster are incremented; the
GeoTIFF is materialised from that store periodically and at the end of the
run. Matching pixels are a tiny fraction of the grid, so the memmap stays
sparse on disk.

Resuming
--------
The metadata file next to the result records every raster already folded in,
and a small progress journal records how far through the current raster the
run had got, so an interrupted loop resumes without double counting.

Originals are never modified, moved or deleted: each raster is copied into the
working folder, accumulated, and the copy deleted before the next one is
brought in. The source location is always passed in on the command line, so no
host or path specific to any machine is baked into this repository.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import rasterio
from rasterio.windows import Window

FNAME_RE = re.compile(r"DFO_(\d{8})_.*\.tiff?$", re.IGNORECASE)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORK_DIR = os.path.join(SCRIPT_DIR, "dfo_accumulation")
RESULT_NAME = "dfo_accumulater.tiff"
META_NAME = "dfo_accumulater_metadata.json"
STORE_NAME = ".dfo_accumulater_store.u16"
STORE_META_NAME = ".dfo_accumulater_store.json"
JOURNAL_NAME = ".dfo_accumulater_progress.json"

# Full MCDWD / MODIS global flood grid: 1/480 degree, 180W..180E, 80N..60S.
DEFAULT_LEFT = -180.0
DEFAULT_TOP = 80.0
DEFAULT_RES = 1.0 / 480.0
DEFAULT_WIDTH = 172800
DEFAULT_HEIGHT = 67200

CLASS_MEANING = {
    0: "no water",
    1: "surface water (normal / permanent water)",
    2: "recurring flood (regular, seasonal inundation)",
    3: "flood (unusual)",
    255: "insufficient data / fill",
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# date range / file selection
# --------------------------------------------------------------------------- #
def month_bounds(start_month: str, end_month: str) -> tuple[int, int]:
    """'202601','202606' -> (20260101, 20260630) as YYYYMMDD ints, both inclusive."""
    start = int(start_month) * 100 + 1
    year, month = int(end_month[:4]), int(end_month[4:6])
    next_month = datetime(year + (month == 12), (month % 12) + 1, 1)
    return start, int(end_month) * 100 + (next_month - timedelta(days=1)).day


def list_candidates(source_dir: str, lo: int, hi: int, done: set[str]) -> list[tuple[int, str]]:
    """Eligible source files as (yyyymmdd, filename), oldest first."""
    found = []
    for name in os.listdir(source_dir):
        match = FNAME_RE.match(name)
        if not match:
            continue
        stamp = int(match.group(1))
        if lo <= stamp <= hi and name not in done:
            found.append((stamp, name))
    return sorted(found)


# --------------------------------------------------------------------------- #
# small json helpers
# --------------------------------------------------------------------------- #
def write_atomic_json(path: str, payload: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


def read_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)


# --------------------------------------------------------------------------- #
# target grid
# --------------------------------------------------------------------------- #
def make_grid(args) -> dict:
    return {
        "left": args.grid_left,
        "top": args.grid_top,
        "res": args.grid_res,
        "width": args.grid_width,
        "height": args.grid_height,
    }


def grid_transform(grid: dict):
    return rasterio.Affine(grid["res"], 0.0, grid["left"], 0.0, -grid["res"], grid["top"])


def source_offset(src, grid: dict) -> tuple[int, int]:
    """Integer (row_off, col_off) of a source inside the target grid."""
    res = grid["res"]
    if abs(src.transform.a - res) > res * 1e-4:
        sys.exit(
            f"ERROR: source pixel size {src.transform.a} does not match target grid {res}"
        )
    raw_col = (src.transform.c - grid["left"]) / res
    raw_row = (grid["top"] - src.transform.f) / res
    col_off, row_off = round(raw_col), round(raw_row)
    if abs(raw_col - col_off) > 0.01 or abs(raw_row - row_off) > 0.01:
        sys.exit(
            "ERROR: source does not sit on the target grid lattice "
            f"(row offset {raw_row}, col offset {raw_col})"
        )
    if row_off < 0 or col_off < 0 or (
        row_off + src.height > grid["height"] or col_off + src.width > grid["width"]
    ):
        sys.exit(
            "ERROR: source falls outside the target grid; widen it with --grid-* options.\n"
            f"  source {src.width}x{src.height} at offset row {row_off}, col {col_off}\n"
            f"  target {grid['width']}x{grid['height']}"
        )
    return row_off, col_off


# --------------------------------------------------------------------------- #
# working store
# --------------------------------------------------------------------------- #
def open_store(work: str, grid: dict, nbands: int) -> np.memmap:
    """Open (creating if needed) the sparse uint16 counting store."""
    path = os.path.join(work, STORE_NAME)
    meta_path = os.path.join(work, STORE_META_NAME)
    shape = (nbands, grid["height"], grid["width"])
    existing = read_json(meta_path)

    if existing is not None and os.path.exists(path):
        if tuple(existing["shape"]) != shape:
            sys.exit(
                f"ERROR: existing store shape {tuple(existing['shape'])} != requested {shape}"
            )
        return np.memmap(path, dtype=np.uint16, mode="r+", shape=shape)

    total_gb = shape[0] * shape[1] * shape[2] * 2 / 1e9
    log(f"  creating counting store {shape} uint16 ({total_gb:.1f} GB nominal, sparse on disk)")
    store = np.memmap(path, dtype=np.uint16, mode="w+", shape=shape)
    write_atomic_json(meta_path, {"shape": list(shape), "dtype": "uint16", "grid": grid})
    return store


# --------------------------------------------------------------------------- #
# accumulation of one raster
# --------------------------------------------------------------------------- #
def accumulate(
    src_path: str,
    store: np.memmap,
    grid: dict,
    band_values: list[int],
    window_rows: int,
    journal_path: str,
    journal_key: str,
    resume_rows: int,
) -> dict:
    hits = [0] * len(band_values)

    with rasterio.open(src_path) as src:
        row_off, col_off = source_offset(src, grid)
        total_windows = (src.height + window_rows - 1) // window_rows
        if resume_rows:
            log(f"  resuming this raster from row {resume_rows} of {src.height}")

        for index, row in enumerate(range(0, src.height, window_rows), start=1):
            rows = min(window_rows, src.height - row)
            if row + rows <= resume_rows:
                continue

            classes = src.read(1, window=Window(0, row, src.width, rows))

            for band, value in enumerate(band_values):
                flat = np.flatnonzero(classes == value)
                hits[band] += int(flat.size)
                if flat.size:
                    local_rows, local_cols = np.unravel_index(flat, classes.shape)
                    view = store[band]
                    view[local_rows + row_off + row, local_cols + col_off] += 1
                    del local_rows, local_cols
                del flat

            del classes
            store.flush()
            write_atomic_json(journal_path, {"file": journal_key, "rows_done": row + rows})

            if index % 20 == 0 or index == total_windows:
                log(f"    window {index}/{total_windows}")

        geometry = {
            "width": src.width,
            "height": src.height,
            "row_offset": row_off,
            "col_offset": col_off,
            "top": round(src.transform.f, 8),
        }

    return {
        "pixels_matched": {str(v): hits[i] for i, v in enumerate(band_values)},
        "source_geometry": geometry,
    }


# --------------------------------------------------------------------------- #
# materialise the GeoTIFF
# --------------------------------------------------------------------------- #
def materialise(store: np.memmap, grid: dict, band_values: list[int], result_path: str,
                window_rows: int) -> dict:
    tmp_path = f"{result_path}.tmp"
    profile = {
        "driver": "GTiff",
        "width": grid["width"],
        "height": grid["height"],
        "count": len(band_values),
        "dtype": "uint16",
        "crs": "EPSG:4326",
        "transform": grid_transform(grid),
        "nodata": None,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "lzw",
        "predictor": 2,
        "BIGTIFF": "YES",
    }
    peaks = [0] * len(band_values)
    started = time.time()
    log(f"  materialising {RESULT_NAME} from the counting store")

    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.descriptions = tuple(
                f"count of class {value} ({CLASS_MEANING.get(value, 'unknown')})"
                for value in band_values
            )
            total = (grid["height"] + window_rows - 1) // window_rows
            for index, row in enumerate(range(0, grid["height"], window_rows), start=1):
                rows = min(window_rows, grid["height"] - row)
                window = Window(0, row, grid["width"], rows)
                for band in range(len(band_values)):
                    chunk = np.asarray(store[band, row:row + rows, :])
                    peaks[band] = max(peaks[band], int(chunk.max()))
                    dst.write(chunk, band + 1, window=window)
                    del chunk
                if index % 40 == 0 or index == total:
                    log(f"    materialise window {index}/{total}")
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    os.replace(tmp_path, result_path)
    size_mb = os.path.getsize(result_path) / 1e6
    log(f"  wrote {RESULT_NAME} ({size_mb:.1f} MB) in {time.time() - started:.0f}s")
    return {str(v): peaks[i] for i, v in enumerate(band_values)}


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #
def load_meta(path: str, band_values: list[int], grid: dict) -> dict:
    meta = read_json(path)
    if meta is not None:
        recorded = [b["source_value"] for b in meta.get("bands", [])]
        if recorded and recorded != band_values:
            sys.exit(
                f"ERROR: {path} was built with band values {recorded}, "
                f"but this run asks for {band_values}. Refusing to mix."
            )
        return meta
    return {
        "result": RESULT_NAME,
        "product": 'NASA LANCE MCDWD_L3_NRT "Flood 3-Day 250m" (DFO staged by MoM production)',
        "pixel_meaning": (
            "count of processed source rasters in which this pixel carried the band's source value"
        ),
        "dtype": "uint16",
        "bands": [
            {
                "band": index + 1,
                "source_value": value,
                "source_class": CLASS_MEANING.get(value, "unknown"),
            }
            for index, value in enumerate(band_values)
        ],
        "source_class_values": {str(k): v for k, v in CLASS_MEANING.items()},
        "target_grid": dict(grid, crs="EPSG:4326"),
        "rasters_processed": 0,
        "processed": [],
    }


# --------------------------------------------------------------------------- #
# one iteration
# --------------------------------------------------------------------------- #
def process_next(args, paths: dict, band_values: list[int], grid: dict, store_box: dict) -> bool:
    meta = load_meta(paths["meta"], band_values, grid)
    done = {entry["file"] for entry in meta["processed"]}
    lo, hi = month_bounds(args.start, args.end)

    pending = list_candidates(args.source, lo, hi, done)
    if not pending:
        return False

    stamp, name = pending[0]
    original = os.path.join(args.source, name)
    work_copy = os.path.join(args.work, name)
    partial = f"{work_copy}.part"
    copy_needed = os.path.abspath(original) != os.path.abspath(work_copy)

    log(f"next: {name}  ({len(done)} done, {len(pending)} staged and eligible)")

    journal = read_json(paths["journal"]) or {}
    resume_rows = int(journal.get("rows_done", 0)) if journal.get("file") == name else 0

    # Clear any copy left behind by an interrupted run before bringing in a new one.
    for stale in os.listdir(args.work):
        if stale == name:
            continue
        if FNAME_RE.match(stale) or stale.endswith(".part"):
            os.remove(os.path.join(args.work, stale))
            log(f"  removed stale copy {stale}")

    if copy_needed:
        if os.path.exists(work_copy) and resume_rows:
            log("  reusing the copy left by the interrupted run")
        else:
            size_mb = os.path.getsize(original) / 1e6
            log(f"  copying {size_mb:.0f} MB into {os.path.basename(args.work)}/")
            shutil.copy2(original, partial)
            os.replace(partial, work_copy)
    else:
        log("  source is already in the working folder, using it in place")

    if store_box.get("store") is None:
        store_box["store"] = open_store(args.work, grid, len(band_values))

    started = time.time()
    log(f"  accumulating classes {band_values} into bands 1..{len(band_values)}")
    stats = accumulate(
        work_copy, store_box["store"], grid, band_values, args.window_rows,
        paths["journal"], name, resume_rows,
    )
    elapsed = time.time() - started

    meta["processed"].append(
        {
            "file": name,
            "date": f"{str(stamp)[:4]}-{str(stamp)[4:6]}-{str(stamp)[6:]}",
            "pixels_matched": stats["pixels_matched"],
            "source_geometry": stats["source_geometry"],
            "seconds": round(elapsed, 1),
            "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    meta["rasters_processed"] = len(meta["processed"])
    meta["date_range_requested"] = {"start": args.start, "end": args.end}
    meta["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_atomic_json(paths["meta"], meta)

    if os.path.exists(paths["journal"]):
        os.remove(paths["journal"])

    if copy_needed and not args.keep_copy:
        os.remove(work_copy)
        log("  deleted the working copy")
    if args.delete_source_after and os.path.exists(original):
        os.remove(original)
        log("  removed the staged source file")

    log(
        f"  done in {elapsed:.0f}s - matched {stats['pixels_matched']} "
        f"({meta['rasters_processed']} rasters accumulated)"
    )
    return True


def finish(args, paths: dict, band_values: list[int], grid: dict, store_box: dict) -> None:
    """Write the GeoTIFF and record its running maxima in the metadata."""
    if store_box.get("store") is None:
        if not os.path.exists(os.path.join(args.work, STORE_NAME)):
            log("no counting store yet, nothing to materialise")
            return
        store_box["store"] = open_store(args.work, grid, len(band_values))
    peaks = materialise(store_box["store"], grid, band_values, paths["result"], args.window_rows)
    meta = load_meta(paths["meta"], band_values, grid)
    meta["max_count"] = peaks
    meta["result_written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["result_size_bytes"] = os.path.getsize(paths["result"])
    write_atomic_json(paths["meta"], meta)


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Accumulate DFO/MCDWD flood classes into a running multi-band GeoTIFF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        required=True,
        help="directory holding the DFO_YYYYMMDD_*.tiff source rasters (never modified)",
    )
    parser.add_argument("--work", default=DEFAULT_WORK_DIR, help="working/output folder")
    parser.add_argument("--start", default="202601", help="first month to include, YYYYMM")
    parser.add_argument("--end", default="202606", help="last month to include, YYYYMM")
    parser.add_argument(
        "--band-values", default="3,2", help="source class values to accumulate, in band order"
    )
    parser.add_argument("--window-rows", type=int, default=512, help="rows read per window")
    parser.add_argument("--once", action="store_true", help="process a single raster and exit")
    parser.add_argument(
        "--wait", action="store_true",
        help="keep polling for newly staged rasters instead of exiting when none are present",
    )
    parser.add_argument(
        "--interval", type=int, default=15, help="seconds to wait between polls in --wait mode"
    )
    parser.add_argument(
        "--expect-total", type=int, default=0,
        help="in --wait mode, stop once this many rasters have been accumulated (0 = never)",
    )
    parser.add_argument(
        "--materialise-every", type=int, default=60,
        help="write the GeoTIFF every N rasters as a checkpoint (0 = only at the end)",
    )
    parser.add_argument(
        "--materialise-only", action="store_true",
        help="just write the GeoTIFF from the existing counting store and exit",
    )
    parser.add_argument(
        "--keep-copy", action="store_true", help="keep the working copy instead of deleting it"
    )
    parser.add_argument(
        "--delete-source-after", action="store_true",
        help="also delete the staged source file once accumulated (for a feeder pipeline)",
    )
    parser.add_argument("--grid-left", type=float, default=DEFAULT_LEFT)
    parser.add_argument("--grid-top", type=float, default=DEFAULT_TOP)
    parser.add_argument("--grid-res", type=float, default=DEFAULT_RES)
    parser.add_argument("--grid-width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--grid-height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    band_values = [int(v) for v in args.band_values.split(",") if v.strip()]
    if not band_values:
        sys.exit("ERROR: --band-values must list at least one class value")

    os.makedirs(args.work, exist_ok=True)
    grid = make_grid(args)
    paths = {
        "result": os.path.join(args.work, RESULT_NAME),
        "meta": os.path.join(args.work, META_NAME),
        "journal": os.path.join(args.work, JOURNAL_NAME),
    }
    store_box: dict = {"store": None}

    if args.materialise_only:
        finish(args, paths, band_values, grid, store_box)
        return

    if not os.path.isdir(args.source):
        sys.exit(f"ERROR: --source is not a directory: {args.source}")

    log(f"source : {args.source}")
    log(f"work   : {args.work}")
    log(f"months : {args.start}..{args.end} inclusive")
    log(f"grid   : {grid['width']}x{grid['height']} at left {grid['left']}, top {grid['top']}")
    log("bands  : " + ", ".join(f"{i + 1}=class {v}" for i, v in enumerate(band_values)))

    processed_here = 0
    try:
        while True:
            if process_next(args, paths, band_values, grid, store_box):
                processed_here += 1
                total = len(load_meta(paths["meta"], band_values, grid)["processed"])
                if args.materialise_every and total % args.materialise_every == 0:
                    finish(args, paths, band_values, grid, store_box)
                if args.once:
                    log(f"--once given, stopping after {processed_here} raster")
                    break
                if args.expect_total and total >= args.expect_total:
                    log(f"reached the expected total of {args.expect_total} rasters")
                    break
                continue

            if args.wait and not args.once:
                log(f"nothing staged, waiting {args.interval}s")
                time.sleep(args.interval)
                continue
            log(f"no eligible rasters left, stopping after {processed_here} this run")
            break
    finally:
        if store_box.get("store") is not None:
            store_box["store"].flush()

    finish(args, paths, band_values, grid, store_box)
    log("finished")


if __name__ == "__main__":
    main()
