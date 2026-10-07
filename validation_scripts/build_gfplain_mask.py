#!/usr/bin/env python
"""Build a binary river-floodplain mask from the original GFPLAIN250m rasters.

Source: Nardi, F. & Annis, A., GFPLAIN250m, figshare,
https://doi.org/10.6084/m9.figshare.6665165.v1 (CC BY 4.0), the six continental
GeoTIFFs AF, AS, EU, NA, OC and SA. They are genuinely binary:

    0    river floodplain
    255  everything else (non-floodplain land, ocean, outside the tile)

so there is no fractional "coverage" anywhere in the source. An earlier version
of this analysis derived floodplains from the MoM-map raster PMTiles, which had
been area-averaged once per zoom level when the tiles were built and again when
they were warped onto the analysis grid; that produced fractional values that
are an artefact of the tiling, not a property of the data. This script goes
back to the published rasters instead.

Grids do not line up: GFPLAIN250m is 1/432 degree (about 257 m) with a
different, non-aligned origin per continent, while the flood accumulation is on
1/480 degree (about 232 m). The mosaic is therefore warped with nearest
neighbour, which resamples without inventing intermediate values, so the output
stays strictly binary.

Outputs, on exactly the accumulation grid:

    river_plains_mask.tiff           uint8, 1 = floodplain, 0 = not
    river_plains_mask_metadata.json  counts plus the per-continent footprints,
                                     which define the area GFPLAIN actually
                                     examined and which the comparison uses as
                                     its analysis domain
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

# GDAL finds its proj.db through the conda activation scripts, which are not run
# when the environment's python is invoked by absolute path, so point at it here.
for _proj_dir in (
    os.path.join(sys.prefix, "share", "proj"),
    os.path.join(sys.prefix, "share", "gdal", "proj"),
):
    if os.path.exists(os.path.join(_proj_dir, "proj.db")):
        os.environ.setdefault("PROJ_DATA", _proj_dir)
        os.environ.setdefault("PROJ_LIB", _proj_dir)
        break

import rasterio  # noqa: E402
from osgeo import gdal  # noqa: E402
from rasterio.windows import Window  # noqa: E402

gdal.UseExceptions()

FLOODPLAIN_VALUE = 0
SOURCE_NODATA = 255

DEFAULT_LEFT = -180.0
DEFAULT_TOP = 80.0
DEFAULT_RES = 1.0 / 480.0
DEFAULT_WIDTH = 172800
DEFAULT_HEIGHT = 67200


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def warp_progress(complete: float, _message, state: list) -> int:
    step = int(complete * 10)
    if step > state[0]:
        state[0] = step
        log(f"  warp {step * 10}%")
    return 1


def footprints(paths: list[str]) -> list[dict]:
    out = []
    for path in paths:
        with rasterio.open(path) as src:
            t = src.transform
            out.append({
                "continent": os.path.splitext(os.path.basename(path))[0],
                "width": src.width,
                "height": src.height,
                "res": t.a,
                "west": t.c,
                "north": t.f,
                "east": t.c + src.width * t.a,
                "south": t.f - src.height * t.a,
            })
    return out


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True,
                        help="directory holding AF.TIF, AS.TIF, EU.TIF, NA.TIF, OC.TIF, SA.TIF")
    parser.add_argument("--dir", default=os.path.join(here, "dfo_accumulation"),
                        help="output folder")
    parser.add_argument("--out", default=None)
    parser.add_argument("--work", default="/root", help="where to keep the temporary warp")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--window-rows", type=int, default=512)
    parser.add_argument("--grid-left", type=float, default=DEFAULT_LEFT)
    parser.add_argument("--grid-top", type=float, default=DEFAULT_TOP)
    parser.add_argument("--grid-res", type=float, default=DEFAULT_RES)
    parser.add_argument("--grid-width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--grid-height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    tifs = sorted(glob.glob(os.path.join(args.src, "*.TIF")) +
                  glob.glob(os.path.join(args.src, "*.tif")))
    if not tifs:
        sys.exit(f"ERROR: no GeoTIFFs found in {args.src}")
    log(f"sources: {', '.join(os.path.basename(t) for t in tifs)}")

    out = args.out or os.path.join(args.dir, "river_plains_mask.tiff")
    os.makedirs(args.dir, exist_ok=True)
    vrt_path = os.path.join(args.work, "gfplain_mosaic.vrt")
    warped = os.path.join(args.work, "gfplain_warped.tif")

    boxes = footprints(tifs)

    # Mosaic. Treating 255 as no-data matters: the continental tiles overlap, and
    # without it one tile's "everything else" would erase a neighbour's floodplain.
    log("building mosaic VRT")
    gdal.BuildVRT(vrt_path, tifs, srcNodata=SOURCE_NODATA, VRTNodata=SOURCE_NODATA)

    left, top, res = args.grid_left, args.grid_top, args.grid_res
    right = left + args.grid_width * res
    bottom = top - args.grid_height * res
    log(f"warping to {args.grid_width}x{args.grid_height} at 1/{round(1 / res)} degree, "
        "nearest neighbour")
    started = time.time()
    gdal.Warp(
        warped, vrt_path,
        dstSRS="EPSG:4326",
        outputBounds=(left, bottom, right, top),
        width=args.grid_width, height=args.grid_height,
        resampleAlg="near",
        srcNodata=SOURCE_NODATA, dstNodata=SOURCE_NODATA,
        outputType=gdal.GDT_Byte,
        multithread=True,
        warpMemoryLimit=256 * 1024 * 1024,
        creationOptions=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                         "COMPRESS=LZW", "BIGTIFF=YES", "SPARSE_OK=TRUE"],
        callback=warp_progress, callback_data=[0],
    )
    log(f"warp finished in {time.time() - started:.0f}s")

    # Collapse to a strict 0/1 mask.
    log("writing binary mask")
    tmp_out = f"{out}.tmp"
    floodplain = 0
    with rasterio.open(warped) as src:
        profile = {
            "driver": "GTiff", "width": src.width, "height": src.height, "count": 1,
            "dtype": "uint8", "crs": src.crs, "transform": src.transform, "nodata": None,
            "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "lzw", "BIGTIFF": "YES",
        }
        try:
            with rasterio.open(tmp_out, "w", **profile) as dst:
                dst.descriptions = ("1 = river floodplain (GFPLAIN250m), 0 = not",)
                total = (src.height + args.window_rows - 1) // args.window_rows
                for index, row in enumerate(range(0, src.height, args.window_rows), start=1):
                    rows = min(args.window_rows, src.height - row)
                    window = Window(0, row, src.width, rows)
                    mask = (src.read(1, window=window) == FLOODPLAIN_VALUE).astype(np.uint8)
                    floodplain += int(mask.sum())
                    dst.write(mask, 1, window=window)
                    del mask
                    if index % 25 == 0 or index == total:
                        log(f"  window {index}/{total}")
        except BaseException:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
            raise
    os.replace(tmp_out, out)

    grid_px = args.grid_width * args.grid_height
    log(f"wrote {os.path.basename(out)} ({os.path.getsize(out) / 1e6:.1f} MB), "
        f"{floodplain:,} floodplain pixels = {floodplain / grid_px * 100:.3f}% of the grid")

    sidecar = f"{os.path.splitext(out)[0]}_metadata.json"
    with open(sidecar, "w") as handle:
        json.dump({
            "result": os.path.basename(out),
            "source_dataset": "GFPLAIN250m (Nardi & Annis)",
            "source_doi": "https://doi.org/10.6084/m9.figshare.6665165.v1",
            "source_licence": "CC BY 4.0",
            "source_coding": {"0": "river floodplain", "255": "everything else"},
            "pixel_meaning": "1 = river floodplain, 0 = not",
            "resampling": "nearest neighbour (source 1/432 deg -> target 1/480 deg)",
            "target_grid": {"crs": "EPSG:4326", "left": left, "top": top, "res": res,
                            "width": args.grid_width, "height": args.grid_height},
            "floodplain_pixels": floodplain,
            "grid_pixels": grid_px,
            "floodplain_fraction_of_grid": floodplain / grid_px,
            "continent_footprints": boxes,
            "domain_note": (
                "GFPLAIN encodes only floodplain (0) against a single 'everything else' "
                "value (255), so non-floodplain land cannot be told from ocean or from "
                "outside the tile. continent_footprints give the boxes the dataset "
                "actually covers and should be used as the analysis domain."
            ),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, handle, indent=2)
        handle.write("\n")
    log(f"wrote {os.path.basename(sidecar)}")

    if not args.keep_temp:
        for path in (warped, vrt_path):
            if os.path.exists(path):
                os.remove(path)
        log("removed the temporary mosaic and warp")


if __name__ == "__main__":
    main()
