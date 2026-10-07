#!/usr/bin/env python
"""Accumulate historical flood occurrence from a daily raster archive.

Folds a series of daily flood rasters into one multi-band uint16 GeoTIFF where
each band counts, per pixel, the number of rasters in which that pixel fell in
that band's class values. Every new raster added makes the result a longer
historical record of flood occurrence.

Two products are supported out of the box, both as staged by MoM production.

  --product dfo    NASA LANCE MCDWD_L3_NRT "Flood 3-Day 250m"
                   (DFO_YYYYMMDD_Flood_3-Day_250m.tiff), classes:
                     0 no water
                     1 surface water (normal / permanent water)
                     2 recurring flood (regular, seasonal inundation)
                     3 flood (unusual)
                     255 insufficient data / fill
                   Default bands: 1 = class 3, 2 = class 2.

  --product viirs  NOAA/CIMSS-GMU VIIRS flood composite
                   (VIIRS_1day_composite YYYYMMDD _flood.tiff), a single band
                   of floodwater-fraction codes with classification flags at
                   the low values (0, 1, 16, 17, 20, 27, 30, 38 masks, 99 land).
                   MoM production's own VIIRS_tool.VIIRS_extract_by_mask counts
                   a pixel as flooded when its value is above 140 and below
                   201, so that is the default for band 1, with the remaining
                   lower water-fraction codes 130-140 as band 2.

Grid handling
-------------
A product's daily rasters do not necessarily share one extent: the MCDWD files
alternate between 80N..60S and 70N..50S while keeping the same pixel size and
left edge. Each source is therefore written at an integer row/column offset
into one canonical target grid, which defaults to the product's full grid.
Rows no source ever covered simply stay 0.

Working store
-------------
Rewriting a multi-gigapixel compressed GeoTIFF for every raster costs minutes
of pure codec time, so the running counts live in a sparse uint16 memmap and
only the matching pixels of each raster are incremented. The GeoTIFF is
materialised from that store periodically and at the end of the run. Matching
pixels are a tiny fraction of the grid, so the memmap stays sparse on disk
(the MCDWD store is 46 GB nominal but well under 1 GB allocated).

Resuming
--------
The metadata file beside the result records every raster already folded in, and
a progress journal records how far through the current raster the run had got,
so an interrupted loop resumes without double counting.

Originals are never modified, moved or deleted: each raster is copied into the
working folder, accumulated, and the copy deleted before the next is brought
in. The source location is always passed in on the command line, so no host or
path specific to any machine is baked into this repository.
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

STORE_SUFFIX = "_store.u16"
STORE_META_SUFFIX = "_store.json"
JOURNAL_SUFFIX = "_progress.json"
META_SUFFIX = "_metadata.json"

PRODUCTS = {
    "dfo": {
        "name": 'NASA LANCE MCDWD_L3_NRT "Flood 3-Day 250m" (DFO staged by MoM production)',
        "pattern": r"DFO_(?P<date>\d{8})_.*\.tiff?$",
        "bands": "3;2",
        "result": "dfo_accumulater.tiff",
        "grid": {"left": -180.0, "top": 80.0, "res": 1.0 / 480.0,
                 "width": 172800, "height": 67200},
        "classes": {
            "0": "no water",
            "1": "surface water (normal / permanent water)",
            "2": "recurring flood (regular, seasonal inundation)",
            "3": "flood (unusual)",
            "255": "insufficient data / fill",
        },
        "band_labels": {"3": "flood (unusual)",
                        "2": "recurring flood (regular, seasonal inundation)"},
    },
    "viirs": {
        "name": "NOAA/CIMSS-GMU VIIRS flood composite (staged by MoM production)",
        "pattern": r"VIIRS_1day_composite(?P<date>\d{8})_flood\.tiff$",
        "bands": "141-200;130-140",
        "result": "viirs_accumulater.tiff",
        "grid": {"left": -180.0, "top": 75.0, "res": 0.0033720001,
                 "width": 106761, "height": 40035},
        "classes": {
            "0": "no data",
            "1": "no data / outside swath",
            "16,17,20,27,30,38": "classification flags (cloud, shadow, snow/ice, terrain)",
            "99": "land, no water detected",
            "130-200": "floodwater fraction codes, higher means more open water",
        },
        "band_labels": {
            "141-200": "flood (MoM production's VIIRS flood threshold, value > 140)",
            "130-140": "lower floodwater-fraction codes below that threshold",
        },
    },
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# band specs
# --------------------------------------------------------------------------- #
def parse_bands(spec: str) -> list[list[tuple[int, int]]]:
    """'3;2' or '141-200;130-140' -> per band, a list of inclusive (lo, hi) ranges."""
    bands = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        ranges = []
        for item in part.split(","):
            item = item.strip()
            if "-" in item:
                lo, hi = item.split("-", 1)
                ranges.append((int(lo), int(hi)))
            else:
                ranges.append((int(item), int(item)))
        bands.append(ranges)
    if not bands:
        sys.exit("ERROR: --bands must describe at least one band")
    return bands


def band_text(ranges: list[tuple[int, int]]) -> str:
    return ",".join(str(lo) if lo == hi else f"{lo}-{hi}" for lo, hi in ranges)


def band_mask(values: np.ndarray, ranges: list[tuple[int, int]]) -> np.ndarray:
    mask = None
    for lo, hi in ranges:
        part = (values == lo) if lo == hi else ((values >= lo) & (values <= hi))
        mask = part if mask is None else (mask | part)
    return mask


# --------------------------------------------------------------------------- #
# date range / file selection
# --------------------------------------------------------------------------- #
def month_bounds(start_month: str, end_month: str) -> tuple[int, int]:
    """'202601','202606' -> (20260101, 20260630) as YYYYMMDD ints, both inclusive."""
    start = int(start_month) * 100 + 1
    year, month = int(end_month[:4]), int(end_month[4:6])
    next_month = datetime(year + (month == 12), (month % 12) + 1, 1)
    return start, int(end_month) * 100 + (next_month - timedelta(days=1)).day


def list_candidates(source_dir, regex, lo, hi, done) -> list[tuple[int, str]]:
    found = []
    for name in os.listdir(source_dir):
        match = regex.match(name)
        if not match:
            continue
        stamp = int(match.group("date"))
        if lo <= stamp <= hi and name not in done:
            found.append((stamp, name))
    return sorted(found)


# --------------------------------------------------------------------------- #
# json helpers
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
def grid_transform(grid: dict):
    return rasterio.Affine(grid["res"], 0.0, grid["left"], 0.0, -grid["res"], grid["top"])


def source_offset(src, grid: dict) -> tuple[int, int]:
    res = grid["res"]
    if abs(src.transform.a - res) > res * 1e-4:
        sys.exit(f"ERROR: source pixel size {src.transform.a} does not match grid {res}")
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
            f"  source {src.width}x{src.height} at row {row_off}, col {col_off}\n"
            f"  target {grid['width']}x{grid['height']}"
        )
    return row_off, col_off


# --------------------------------------------------------------------------- #
# working store
# --------------------------------------------------------------------------- #
def seed_store_from_result(store: np.memmap, result_path: str, grid: dict, nbands: int,
                           window_rows: int = 512) -> None:
    """Refill a counting store from an already materialised GeoTIFF.

    The store is only a scratchpad for fast in-place increments; the GeoTIFF
    holds exactly the same uint16 counts on the same grid, losslessly. So a
    store that has been deleted to reclaim disk can be rebuilt from the result
    rather than the accumulation having to start again.
    """
    with rasterio.open(result_path) as src:
        if (src.width, src.height, src.count) != (grid["width"], grid["height"], nbands):
            sys.exit(
                f"ERROR: {os.path.basename(result_path)} is "
                f"{src.width}x{src.height}x{src.count}, expected "
                f"{grid['width']}x{grid['height']}x{nbands}; refusing to seed from it"
            )
        total = (src.height + window_rows - 1) // window_rows
        for index, row in enumerate(range(0, src.height, window_rows), start=1):
            rows = min(window_rows, src.height - row)
            window = Window(0, row, src.width, rows)
            for band in range(nbands):
                store[band, row:row + rows, :] = src.read(band + 1, window=window)
            if index % 40 == 0 or index == total:
                log(f"    seeding window {index}/{total}")
    store.flush()


def open_store(paths: dict, grid: dict, nbands: int) -> np.memmap:
    """Open the counting store, preferring the already-produced GeoTIFF.

    The materialised result is the authoritative output: it holds the same
    uint16 counts, losslessly, and unlike the scratch store it cannot have been
    left half-applied by an interrupted run. So whenever the result is in step
    with the metadata it is used to rebuild the store even if a store file is
    already present, and any mid-raster progress journal is dropped so that
    raster is redone cleanly from its first row.

    The store is trusted only when the result is absent or stale, which is the
    normal mid-run case where materialising has not happened yet.
    """
    shape = (nbands, grid["height"], grid["width"])
    meta = read_json(paths["meta"]) or {}
    processed = len(meta.get("processed", []))
    in_result = meta.get("result_rasters")
    result_usable = (processed > 0 and os.path.exists(paths["result"])
                     and in_result == processed)

    existing = read_json(paths["store_meta"])
    have_store = existing is not None and os.path.exists(paths["store"])
    if have_store and tuple(existing["shape"]) != shape:
        sys.exit(f"ERROR: store shape {tuple(existing['shape'])} != requested {shape}")

    if have_store and not result_usable:
        return np.memmap(paths["store"], dtype=np.uint16, mode="r+", shape=shape)

    if processed and not result_usable:
        detail = ("is missing" if not os.path.exists(paths["result"])
                  else f"holds only {in_result} of them")
        sys.exit(
            f"ERROR: {os.path.basename(paths['meta'])} records {processed} rasters "
            f"accumulated, there is no usable counting store, and "
            f"{os.path.basename(paths['result'])} {detail}. Those counts cannot be "
            "recovered; accumulate the new dates into a separate folder and combine "
            "them with merge_accumulations.py."
        )

    nominal = shape[0] * shape[1] * shape[2] * 2 / 1e9
    log(f"  creating counting store {shape} uint16 ({nominal:.1f} GB nominal, sparse on disk)")
    store = np.memmap(paths["store"], dtype=np.uint16, mode="w+", shape=shape)
    write_atomic_json(paths["store_meta"], {"shape": list(shape), "dtype": "uint16",
                                            "grid": grid})
    if result_usable:
        log(f"  rebuilding the store from {os.path.basename(paths['result'])} "
            f"({processed} rasters already accumulated)")
        seed_store_from_result(store, paths["result"], grid, nbands)
        if os.path.exists(paths["journal"]):
            os.remove(paths["journal"])
            log("  dropped the progress journal; any part-done raster restarts cleanly")
    return store


# --------------------------------------------------------------------------- #
def accumulate(src_path, store, grid, bands, window_rows, journal_path, key, resume_rows):
    hits = [0] * len(bands)
    unreadable = 0
    with rasterio.open(src_path) as src:
        row_off, col_off = source_offset(src, grid)
        total = (src.height - resume_rows + window_rows - 1) // window_rows
        if resume_rows:
            log(f"  resuming this raster from row {resume_rows} of {src.height}")

        # Resume from exactly where the journal stopped, rather than walking the
        # window grid from 0 and skipping. Skipping is only equivalent while the
        # window size never changes: if a run is restarted with a different
        # --window-rows, a window straddling the resume point gets partially
        # re-applied and those rows are counted twice.
        for index, row in enumerate(range(resume_rows, src.height, window_rows), start=1):
            rows = min(window_rows, src.height - row)
            try:
                values = src.read(1, window=Window(0, row, src.width, rows))
            except (rasterio.errors.RasterioError, OSError) as exc:
                # Some archived rasters have truncated tiles. Skipping the rows we
                # cannot read keeps the rest of the day usable; the damage is
                # recorded so the gap is visible rather than silently filled.
                unreadable += rows
                log(f"    rows {row}..{row + rows} unreadable, skipping ({exc})")
                store.flush()
                write_atomic_json(journal_path, {"file": key, "rows_done": row + rows})
                continue

            for band, ranges in enumerate(bands):
                flat = np.flatnonzero(band_mask(values, ranges))
                hits[band] += int(flat.size)
                if flat.size:
                    local_rows, local_cols = np.unravel_index(flat, values.shape)
                    store[band][local_rows + row_off + row, local_cols + col_off] += 1
                    del local_rows, local_cols
                del flat

            del values
            store.flush()
            write_atomic_json(journal_path, {"file": key, "rows_done": row + rows})
            if index % 20 == 0 or index == total:
                log(f"    window {index}/{total}")

        geometry = {"width": src.width, "height": src.height, "row_offset": row_off,
                    "col_offset": col_off, "top": round(src.transform.f, 8)}

    return {"pixels_matched": {band_text(r): hits[i] for i, r in enumerate(bands)},
            "source_geometry": geometry,
            "unreadable_rows": unreadable}


# --------------------------------------------------------------------------- #
def materialise(store, grid, bands, labels, result_path, window_rows) -> dict:
    tmp_path = f"{result_path}.tmp"
    profile = {
        "driver": "GTiff", "width": grid["width"], "height": grid["height"],
        "count": len(bands), "dtype": "uint16", "crs": "EPSG:4326",
        "transform": grid_transform(grid), "nodata": None, "tiled": True,
        "blockxsize": 256, "blockysize": 256, "compress": "lzw", "predictor": 2,
        "BIGTIFF": "YES",
    }
    peaks = [0] * len(bands)
    started = time.time()
    log(f"  materialising {os.path.basename(result_path)} from the counting store")
    try:
        with rasterio.open(tmp_path, "w", **profile) as dst:
            dst.descriptions = tuple(
                f"count of values {band_text(r)}"
                + (f" ({labels[band_text(r)]})" if band_text(r) in labels else "")
                for r in bands
            )
            total = (grid["height"] + window_rows - 1) // window_rows
            for index, row in enumerate(range(0, grid["height"], window_rows), start=1):
                rows = min(window_rows, grid["height"] - row)
                window = Window(0, row, grid["width"], rows)
                for band in range(len(bands)):
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
    log(f"  wrote {os.path.basename(result_path)} "
        f"({os.path.getsize(result_path) / 1e6:.1f} MB) in {time.time() - started:.0f}s")
    return {band_text(r): peaks[i] for i, r in enumerate(bands)}


# --------------------------------------------------------------------------- #
def load_meta(paths, bands, grid, product) -> dict:
    meta = read_json(paths["meta"])
    if meta is not None:
        # Older runs recorded a single "source_value" per band rather than a
        # value set, so accept either spelling when resuming one of those.
        recorded = [
            str(b.get("values", b.get("source_value", ""))) for b in meta.get("bands", [])
        ]
        wanted = [band_text(r) for r in bands]
        if recorded and recorded != wanted:
            sys.exit(f"ERROR: {paths['meta']} was built with bands {recorded}, "
                     f"but this run asks for {wanted}. Refusing to mix.")
        return meta
    return {
        "result": os.path.basename(paths["result"]),
        "product": product["name"],
        "pixel_meaning": (
            "count of processed source rasters in which this pixel's value fell in "
            "the band's value set"
        ),
        "dtype": "uint16",
        "bands": [
            {"band": i + 1, "values": band_text(r),
             "meaning": product.get("band_labels", {}).get(band_text(r), "")}
            for i, r in enumerate(bands)
        ],
        "source_class_values": product.get("classes", {}),
        "target_grid": dict(grid, crs="EPSG:4326"),
        "rasters_processed": 0,
        "processed": [],
    }


# --------------------------------------------------------------------------- #
def process_next(args, paths, bands, grid, product, regex, store_box) -> bool:
    meta = load_meta(paths, bands, grid, product)
    done = {entry["file"] for entry in meta["processed"]}
    lo, hi = month_bounds(args.start, args.end)
    # A feeder works from a snapshot of what was already accumulated, so it can
    # re-stage a raster this worker has since folded in. Clear those out, or the
    # staging queue stays full of files that will never be consumed and the
    # feeder blocks waiting for room.
    if args.delete_source_after:
        for name in os.listdir(args.source):
            if name in done and regex.match(name):
                os.remove(os.path.join(args.source, name))
                log(f"  discarded already-accumulated staged file {name}")

    pending = list_candidates(args.source, regex, lo, hi, done)
    if not pending:
        return False

    stamp, name = pending[0]
    original = os.path.join(args.source, name)
    work_copy = os.path.join(args.work, name)
    partial = f"{work_copy}.part"
    copy_needed = os.path.abspath(original) != os.path.abspath(work_copy)

    log(f"next: {name}  ({len(done)} done, {len(pending)} staged and eligible)")

    # Open the store first: it may rebuild itself from the result and drop the
    # journal, so the journal must only be read afterwards.
    if store_box.get("store") is None:
        store_box["store"] = open_store(paths, grid, len(bands))

    journal = read_json(paths["journal"]) or {}
    resume_rows = int(journal.get("rows_done", 0)) if journal.get("file") == name else 0

    for stale in os.listdir(args.work):
        if stale == name:
            continue
        if regex.match(stale) or stale.endswith(".part"):
            os.remove(os.path.join(args.work, stale))
            log(f"  removed stale copy {stale}")

    if copy_needed:
        if os.path.exists(work_copy) and resume_rows:
            log("  reusing the copy left by the interrupted run")
        else:
            log(f"  copying {os.path.getsize(original) / 1e6:.0f} MB into "
                f"{os.path.basename(args.work)}/")
            shutil.copy2(original, partial)
            os.replace(partial, work_copy)
    else:
        log("  source is already in the working folder, using it in place")

    started = time.time()
    log(f"  accumulating {[band_text(r) for r in bands]} into bands 1..{len(bands)}")
    stats = accumulate(work_copy, store_box["store"], grid, bands, args.window_rows,
                       paths["journal"], name, resume_rows)
    elapsed = time.time() - started

    meta["processed"].append({
        "file": name,
        "date": f"{str(stamp)[:4]}-{str(stamp)[4:6]}-{str(stamp)[6:]}",
        "pixels_matched": stats["pixels_matched"],
        "source_geometry": stats["source_geometry"],
        "unreadable_rows": stats["unreadable_rows"],
        "seconds": round(elapsed, 1),
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
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

    log(f"  done in {elapsed:.0f}s - matched {stats['pixels_matched']} "
        f"({meta['rasters_processed']} rasters accumulated)")
    return True


def finish(args, paths, bands, grid, product, store_box) -> None:
    if store_box.get("store") is None:
        if not os.path.exists(paths["store"]):
            log("no counting store yet, nothing to materialise")
            return
        store_box["store"] = open_store(paths, grid, len(bands))
    peaks = materialise(store_box["store"], grid, bands,
                        product.get("band_labels", {}), paths["result"], args.window_rows)
    meta = load_meta(paths, bands, grid, product)
    meta["max_count"] = peaks
    # How many rasters the GeoTIFF actually contains. A later run compares this
    # with the processed list to decide whether the result is a complete stand-in
    # for the counting store.
    meta["result_rasters"] = meta["rasters_processed"]
    meta["result_written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["result_size_bytes"] = os.path.getsize(paths["result"])
    write_atomic_json(paths["meta"], meta)


# --------------------------------------------------------------------------- #
def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Accumulate daily flood rasters into a multi-band occurrence GeoTIFF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--product", choices=sorted(PRODUCTS), default="dfo")
    parser.add_argument("--source", required=True,
                        help="directory holding the source rasters (never modified)")
    parser.add_argument("--work", default=None, help="working/output folder")
    parser.add_argument("--start", default="202601", help="first month to include, YYYYMM")
    parser.add_argument("--end", default="202606", help="last month to include, YYYYMM")
    parser.add_argument("--bands", default=None,
                        help="per-band value sets, e.g. '3;2' or '141-200;130-140'")
    parser.add_argument("--pattern", default=None,
                        help="filename regex with a (?P<date>) group")
    parser.add_argument("--result-name", default=None)
    parser.add_argument("--window-rows", type=int, default=512)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--wait", action="store_true",
                        help="keep polling for newly staged rasters")
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--expect-total", type=int, default=0,
                        help="in --wait mode, stop at this many accumulated rasters")
    parser.add_argument("--materialise-every", type=int, default=60,
                        help="write the GeoTIFF every N rasters (0 = only at the end)")
    parser.add_argument("--materialise-only", action="store_true")
    parser.add_argument("--keep-copy", action="store_true")
    parser.add_argument("--delete-source-after", action="store_true")
    parser.add_argument("--max-retries", type=int, default=5,
                        help="consecutive I/O failures tolerated before giving up")
    parser.add_argument("--retry-wait", type=int, default=30,
                        help="seconds to wait before retrying after an I/O failure")
    parser.add_argument("--grid-left", type=float, default=None)
    parser.add_argument("--grid-top", type=float, default=None)
    parser.add_argument("--grid-res", type=float, default=None)
    parser.add_argument("--grid-width", type=int, default=None)
    parser.add_argument("--grid-height", type=int, default=None)
    args = parser.parse_args()

    product = PRODUCTS[args.product]
    bands = parse_bands(args.bands or product["bands"])
    regex = re.compile(args.pattern or product["pattern"], re.IGNORECASE)
    result_name = args.result_name or product["result"]
    args.work = args.work or os.path.join(here, f"{args.product}_accumulation")

    grid = dict(product["grid"])
    for key in ("left", "top", "res", "width", "height"):
        override = getattr(args, f"grid_{key}")
        if override is not None:
            grid[key] = override

    os.makedirs(args.work, exist_ok=True)
    stem = os.path.splitext(result_name)[0]
    paths = {
        "result": os.path.join(args.work, result_name),
        "meta": os.path.join(args.work, f"{stem}{META_SUFFIX}"),
        "store": os.path.join(args.work, f".{stem}{STORE_SUFFIX}"),
        "store_meta": os.path.join(args.work, f".{stem}{STORE_META_SUFFIX}"),
        "journal": os.path.join(args.work, f".{stem}{JOURNAL_SUFFIX}"),
    }
    store_box: dict = {"store": None}

    # Refuse a working folder whose metadata holds rasters this run did not ask
    # for. Reusing a folder built for another date range or another product
    # would silently fold those counts into the result while the date range in
    # the metadata said otherwise.
    existing_meta = read_json(paths["meta"])
    if existing_meta:
        lo, hi = month_bounds(args.start, args.end)
        stray = []
        for entry in existing_meta.get("processed", []):
            stamp = int(entry["date"].replace("-", ""))
            if not (lo <= stamp <= hi) or not regex.match(entry["file"]):
                stray.append(entry["file"])
        if stray:
            shown = ", ".join(stray[:5]) + (f" and {len(stray) - 5} more"
                                            if len(stray) > 5 else "")
            sys.exit(
                f"ERROR: {paths['meta']} already records {len(stray)} raster(s) outside "
                f"the requested range {args.start}..{args.end} or not matching the "
                f"requested pattern: {shown}. Use a separate working folder for this "
                "run, or widen --start/--end to cover what is already there."
            )

    if args.materialise_only:
        finish(args, paths, bands, grid, product, store_box)
        return

    if not os.path.isdir(args.source):
        sys.exit(f"ERROR: --source is not a directory: {args.source}")

    log(f"product: {args.product} - {product['name']}")
    log(f"source : {args.source}")
    log(f"work   : {args.work}")
    log(f"months : {args.start}..{args.end} inclusive")
    log(f"grid   : {grid['width']}x{grid['height']} at left {grid['left']}, top {grid['top']}")
    log("bands  : " + ", ".join(f"{i + 1}={band_text(r)}" for i, r in enumerate(bands)))

    processed_here = 0
    failures = 0
    try:
        while True:
            try:
                made_progress = process_next(args, paths, bands, grid, product, regex,
                                             store_box)
                failures = 0
            except (rasterio.errors.RasterioError, OSError) as exc:
                # Reads can fail transiently when the box is under heavy I/O
                # pressure. The journal records how far this raster got, so a
                # retry resumes rather than restarting or double counting.
                failures += 1
                log(f"  I/O error ({type(exc).__name__}: {exc})")
                if failures >= args.max_retries:
                    log(f"  giving up after {failures} consecutive failures")
                    raise
                log(f"  retry {failures}/{args.max_retries} in {args.retry_wait}s")
                time.sleep(args.retry_wait)
                continue

            if made_progress:
                processed_here += 1
                total = len(load_meta(paths, bands, grid, product)["processed"])
                if args.materialise_every and total % args.materialise_every == 0:
                    finish(args, paths, bands, grid, product, store_box)
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

    finish(args, paths, bands, grid, product, store_box)
    log("finished")


if __name__ == "__main__":
    main()
