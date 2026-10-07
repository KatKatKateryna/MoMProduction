#!/usr/bin/env python
"""Aggregate a flood accumulation onto a fixed global grid of equal cells.

DFO and VIIRS are accumulated on different native grids (1/480 degree from
80N, and 0.003372 degree from 75N), and neither divides into the other, so they
cannot be compared pixel to pixel without resampling one of them. Instead both
are reduced here onto the same fixed global lat/lon frame, assigning every
source pixel to the cell containing its centre. That is exact, cheap, and
symmetric: neither product is interpolated or given the other's geometry.

For every cell it records

    pixels          source pixels whose centre falls in the cell
    observations    sum over those pixels of how many rasters observed them
    floodplain      how many of them are river floodplain
    flooded_N       how many were flagged at least once in band N
    counts_N        total flagged days in band N

from which both a "what share of the cell ever flooded" and an observation
normalised "what share of observed days flooded" can be derived, the latter
being the one that stays comparable where the source footprints differ.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import Window

# Fixed global frame both products are reduced onto.
GRID_NORTH = 80.0
GRID_SOUTH = -60.0
GRID_WEST = -180.0


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def observation_groups(meta: dict):
    tally: dict[tuple[int, int, int, int], int] = {}
    for entry in meta["processed"]:
        g = entry["source_geometry"]
        key = (g["row_offset"], g["row_offset"] + g["height"],
               g["col_offset"], g["col_offset"] + g["width"])
        tally[key] = tally.get(key, 0) + 1
    return [(*key, n) for key, n in tally.items()]


def observation_window(groups, row, rows, width) -> np.ndarray:
    obs = np.zeros((rows, width), dtype=np.uint16)
    for r0, r1, c0, c1, n in groups:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            obs[a - row:b - row, c0:c1] += n
    return obs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accum", required=True)
    parser.add_argument("--accum-meta", default=None)
    parser.add_argument("--mask", default=None, help="binary floodplain mask on the same grid")
    parser.add_argument("--out", required=True, help="output .npz")
    parser.add_argument("--cell-deg", type=float, default=0.1)
    parser.add_argument("--window-rows", type=int, default=256)
    parser.add_argument("--label", default=None)
    args = parser.parse_args()

    meta_path = args.accum_meta or f"{os.path.splitext(args.accum)[0]}_metadata.json"
    meta = json.load(open(meta_path))
    groups = observation_groups(meta)

    nrows = int(round((GRID_NORTH - GRID_SOUTH) / args.cell_deg))
    ncols = int(round(360.0 / args.cell_deg))

    with rasterio.open(args.accum) as acc:
        nbands = acc.count
        top, left, res = acc.transform.f, acc.transform.c, acc.transform.a
        width, height = acc.width, acc.height

        # Cell index of each source column, from the pixel centre. Non-decreasing,
        # so whole blocks of columns can be reduced in one call.
        lon = left + (np.arange(width) + 0.5) * res
        out_col = np.floor((lon - GRID_WEST) / args.cell_deg).astype(np.int64)
        np.clip(out_col, 0, ncols - 1, out=out_col)
        uniq_cols, col_starts = np.unique(out_col, return_index=True)

        acc_arrays = {
            "pixels": np.zeros((nrows, ncols), np.float64),
            "observations": np.zeros((nrows, ncols), np.float64),
            "floodplain": np.zeros((nrows, ncols), np.float64),
        }
        for band in range(nbands):
            acc_arrays[f"flooded_{band + 1}"] = np.zeros((nrows, ncols), np.float64)
            acc_arrays[f"counts_{band + 1}"] = np.zeros((nrows, ncols), np.float64)

        mask_ds = rasterio.open(args.mask) if args.mask else None
        if mask_ds is not None and (mask_ds.width, mask_ds.height) != (width, height):
            sys.exit("ERROR: mask grid does not match the accumulation grid")

        log(f"{args.accum}: {width}x{height}, {nbands} bands, {len(groups)} footprints")
        log(f"reducing onto {nrows}x{ncols} cells of {args.cell_deg} degrees")

        def add(name, values, out_rows, row_starts):
            colsum = np.add.reduceat(values, col_starts, axis=1)
            cellsum = np.add.reduceat(colsum, row_starts, axis=0)
            acc_arrays[name][np.ix_(out_rows, uniq_cols)] += cellsum

        total = (height + args.window_rows - 1) // args.window_rows
        for index, row in enumerate(range(0, height, args.window_rows), start=1):
            rows = min(args.window_rows, height - row)
            lat = top - (np.arange(row, row + rows) + 0.5) * res
            out_row_all = np.floor((GRID_NORTH - lat) / args.cell_deg).astype(np.int64)
            inside = (out_row_all >= 0) & (out_row_all < nrows)
            if not inside.any():
                continue
            first, last = int(np.argmax(inside)), rows - int(np.argmax(inside[::-1]))
            window = Window(0, row + first, width, last - first)
            out_rows_win = out_row_all[first:last]
            uniq_rows, row_starts = np.unique(out_rows_win, return_index=True)

            obs = observation_window(groups, row + first, last - first, width)
            add("pixels", np.ones(obs.shape, np.float32), uniq_rows, row_starts)
            add("observations", obs.astype(np.float32), uniq_rows, row_starts)
            del obs

            if mask_ds is not None:
                plain = (mask_ds.read(1, window=window) == 1).astype(np.float32)
                add("floodplain", plain, uniq_rows, row_starts)
                del plain

            for band in range(nbands):
                counts = acc.read(band + 1, window=window)
                add(f"counts_{band + 1}", counts.astype(np.float32), uniq_rows, row_starts)
                add(f"flooded_{band + 1}", (counts > 0).astype(np.float32),
                    uniq_rows, row_starts)
                del counts

            if index % 25 == 0 or index == total:
                log(f"  window {index}/{total}")

        if mask_ds is not None:
            mask_ds.close()

    payload = {k: v.astype(np.float32) for k, v in acc_arrays.items()}
    payload["cell_deg"] = args.cell_deg
    payload["grid_north"] = GRID_NORTH
    payload["grid_west"] = GRID_WEST
    payload["nbands"] = nbands
    payload["rasters"] = meta["rasters_processed"]
    np.savez_compressed(args.out, **payload)

    info = {
        "label": args.label or os.path.basename(args.accum),
        "accumulation": os.path.basename(args.accum),
        "rasters": meta["rasters_processed"],
        "date_range": meta.get("date_range_requested"),
        "bands": meta.get("bands"),
        "cell_deg": args.cell_deg,
        "grid": {"north": GRID_NORTH, "south": GRID_SOUTH, "west": GRID_WEST,
                 "rows": nrows, "cols": ncols},
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(f"{os.path.splitext(args.out)[0]}_info.json", "w") as handle:
        json.dump(info, handle, indent=2)
        handle.write("\n")
    log(f"wrote {os.path.basename(args.out)} "
        f"({os.path.getsize(args.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
