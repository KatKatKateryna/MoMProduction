#!/usr/bin/env python
"""Render the flood accumulation against the river floodplains.

The first global composite was unreadable: floodplains cover about 3.5% of the
land domain while flooded pixels cover about 0.26%, so a shared stretch leaves
the flood channels invisible under the floodplain channel. Here each channel is
scaled on its own high percentile, which makes all three comparable, plus a set
of full-resolution regional crops where the relationship can actually be seen.

In every image:
    red   = band 1, count of class 3 "flood (unusual)"
    green = river floodplain coverage
    blue  = band 2, count of class 2 "recurring flood"

so floods sitting on floodplains appear yellow/white and floods away from them
stay pure red or blue.
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import rasterio
from rasterio.windows import Window

REGIONS = [
    # name, north, south, west, east
    ("ganges_brahmaputra", 28.0, 20.0, 86.0, 93.0),
    ("amazon_central", 0.0, -10.0, -70.0, -55.0),
    ("mekong_indochina", 16.5, 9.0, 102.0, 108.5),
    ("niger_inland_delta", 18.0, 11.0, -7.0, 2.0),
    ("mississippi_lower", 38.0, 29.0, -94.0, -88.0),
    ("pantanal_parana", -14.0, -24.0, -61.0, -53.0),
]

MAX_WIDTH = 1000


def log(msg: str) -> None:
    print(msg, flush=True)


def stretch(values: np.ndarray, percentile: float = 99.0, gamma: float = 0.6) -> np.ndarray:
    """Scale to 0..255 on the given percentile of the non-zero values."""
    positive = values[values > 0]
    if positive.size == 0:
        return np.zeros(values.shape, np.uint8)
    top = np.percentile(positive, percentile)
    if top <= 0:
        top = positive.max()
    scaled = np.clip(values / top, 0.0, 1.0) ** gamma
    return (scaled * 255).astype(np.uint8)


def write_png(path: str, rgb: np.ndarray) -> None:
    profile = {
        "driver": "PNG", "width": rgb.shape[2], "height": rgb.shape[1],
        "count": 3, "dtype": "uint8",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rgb)
    log(f"wrote {os.path.basename(path)}  {rgb.shape[2]}x{rgb.shape[1]}")


def render_global(npz_path: str, out_path: str) -> None:
    data = np.load(npz_path)
    cell = float(data["cell_px"])
    rgb = np.stack(
        [
            stretch(data["flood1"] / cell),
            stretch(data["plain"] / cell),
            stretch(data["flood2"] / cell),
        ]
    )
    write_png(out_path, rgb)


def render_region(acc_path, rp_path, name, north, south, west, east, out_dir, prefix):
    with rasterio.open(acc_path) as acc, rasterio.open(rp_path) as rpc:
        res = acc.transform.a
        top, left = acc.transform.f, acc.transform.c
        row0 = int(round((top - north) / res))
        row1 = int(round((top - south) / res))
        col0 = int(round((west - left) / res))
        col1 = int(round((east - left) / res))
        row0, col0 = max(row0, 0), max(col0, 0)
        row1 = min(row1, acc.height)
        col1 = min(col1, acc.width)
        if row1 <= row0 or col1 <= col0:
            log(f"skipping {name}: outside the raster")
            return

        window = Window(col0, row0, col1 - col0, row1 - row0)
        scale = max(1, int(np.ceil((col1 - col0) / MAX_WIDTH)))
        shape = ((row1 - row0) // scale, (col1 - col0) // scale)

        band1 = acc.read(1, window=window, out_shape=shape).astype(np.float32)
        band2 = acc.read(2, window=window, out_shape=shape).astype(np.float32)
        cover = rpc.read(1, window=window, out_shape=shape).astype(np.float32)

    rgb = np.stack([stretch(band1, 99.5), stretch(cover, 100.0, 1.0), stretch(band2, 99.5)])
    write_png(os.path.join(out_dir, f"{prefix}_region_{name}.png"), rgb)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=os.path.join(here, "dfo_accumulation"))
    parser.add_argument("--accum", default=None)
    parser.add_argument("--rp", default=None)
    parser.add_argument("--prefix", default="flood_vs_river_plains")
    args = parser.parse_args()

    accum = args.accum or os.path.join(args.dir, "dfo_accumulater.tiff")
    rp = args.rp or os.path.join(args.dir, "river_plains_coverage.tiff")
    npz = os.path.join(args.dir, f"{args.prefix}_aggregates_0p1deg.npz")

    if os.path.exists(npz):
        render_global(npz, os.path.join(args.dir, f"{args.prefix}_global.png"))
    else:
        log(f"no cached aggregates at {npz}, skipping the global image")

    for name, north, south, west, east in REGIONS:
        render_region(accum, rp, name, north, south, west, east, args.dir, args.prefix)


if __name__ == "__main__":
    main()
