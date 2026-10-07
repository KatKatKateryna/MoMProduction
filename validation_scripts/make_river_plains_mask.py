#!/usr/bin/env python
"""Turn the fractional river-floodplain coverage raster into a binary mask.

GFPLAN is natively a binary floodplain map. The fractional 0..255 values in
river_plains_coverage.tiff only appear because the mask was area-averaged twice
on the way here: once when the MoM-map PMTiles were built per zoom level, and
again when those tiles were warped onto the 1/480 degree accumulator grid.

This writes river_plains_mask.tiff: uint8, 1 where at least `--threshold`/255 of
the pixel's area is floodplain and 0 elsewhere, on exactly the same grid. The
default threshold is the same >=50% rule the comparison statistics use, so the
mask and the reported numbers describe the same pixels.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import Window


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=os.path.join(here, "dfo_accumulation"))
    parser.add_argument("--coverage", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--threshold", type=int, default=128,
                        help="minimum coverage byte counted as floodplain (128 = 50%%)")
    parser.add_argument("--window-rows", type=int, default=512)
    args = parser.parse_args()

    coverage = args.coverage or os.path.join(args.dir, "river_plains_coverage.tiff")
    out = args.out or os.path.join(args.dir, "river_plains_mask.tiff")
    tmp = f"{out}.tmp"

    with rasterio.open(coverage) as src:
        profile = {
            "driver": "GTiff", "width": src.width, "height": src.height, "count": 1,
            "dtype": "uint8", "crs": src.crs, "transform": src.transform, "nodata": None,
            "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "lzw", "BIGTIFF": "YES",
        }
        log(f"{src.width}x{src.height}, threshold >= {args.threshold}/255 "
            f"({args.threshold / 255 * 100:.0f}% of pixel area)")

        floodplain = 0
        total = 0
        try:
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.descriptions = ("1 = river floodplain, 0 = not",)
                windows = (src.height + args.window_rows - 1) // args.window_rows
                for index, row in enumerate(range(0, src.height, args.window_rows), start=1):
                    rows = min(args.window_rows, src.height - row)
                    window = Window(0, row, src.width, rows)
                    mask = (src.read(1, window=window) >= args.threshold).astype(np.uint8)
                    floodplain += int(mask.sum())
                    total += mask.size
                    dst.write(mask, 1, window=window)
                    del mask
                    if index % 20 == 0 or index == windows:
                        log(f"  window {index}/{windows}")
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    os.replace(tmp, out)
    log(f"wrote {os.path.basename(out)} ({os.path.getsize(out) / 1e6:.1f} MB), "
        f"{floodplain:,} floodplain pixels = {floodplain / total * 100:.3f}% of the grid")

    sidecar = f"{os.path.splitext(out)[0]}_metadata.json"
    source_meta = os.path.join(args.dir, "river_plains_coverage_metadata.json")
    payload = {
        "result": os.path.basename(out),
        "derived_from": os.path.basename(coverage),
        "source_dataset": "GFPLAN250m river floodplains (Nardi et al.) via MoM-map PMTiles",
        "pixel_meaning": "1 = river floodplain, 0 = not",
        "threshold_byte": args.threshold,
        "threshold_percent_of_pixel_area": round(args.threshold / 255 * 100, 2),
        "floodplain_pixels": floodplain,
        "grid_pixels": total,
        "floodplain_fraction_of_grid": floodplain / total,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if os.path.exists(source_meta):
        with open(source_meta) as handle:
            parent = json.load(handle)
        payload["pmtiles_lat_bounds"] = parent.get("pmtiles_lat_bounds")
        payload["caveat"] = parent.get("caveat")
    with open(sidecar, "w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    log(f"wrote {os.path.basename(sidecar)}")


if __name__ == "__main__":
    main()
