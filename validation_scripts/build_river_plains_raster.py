#!/usr/bin/env python
"""Rasterise the river-floodplain PMTiles onto the flood accumulator's grid.

The MoM-map river plains layer ships as raster PMTiles built from the GFPLAN
250m floodplain GeoTIFFs (Nardi et al.): floodplain cells were value 0 in the
source and were encoded as a 4-bit palette PNG where the palette index is the
area-averaged floodplain coverage of that tile pixel (0 = none, 15 = full).
Tiles containing no floodplain at all were skipped.

To compare them with the DFO flood accumulation they have to live on the same
grid, so this runs in two steps:

  1. every tile at the chosen zoom (default the PMTiles max zoom, 10, which is
     about 153 m at the equator and therefore finer than the 1/480 degree
     target) is decoded back to its palette index, scaled to 0..255 coverage,
     and written into one sparse world-wide EPSG:3857 mosaic aligned exactly to
     that zoom's tile grid. Where the two continent files overlap the larger
     coverage wins.

  2. the mosaic is warped to the target EPSG:4326 grid with area averaging,
     giving river_plains_coverage.tiff: uint8 percent-of-pixel floodplain
     coverage, pixel-aligned with dfo_accumulater.tiff.

Note on extent: an absent tile means "no floodplain here", which cannot be
told apart from "outside the GFPLAN domain". The PMTiles bounds (about 54.7S
to 64.1N) are written into the sidecar metadata so the analysis can restrict
itself to the latitudes the floodplain dataset actually covers.
"""

from __future__ import annotations

import argparse
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

from osgeo import gdal, osr  # noqa: E402
from pmtiles.reader import MmapSource, Reader, all_tiles  # noqa: E402
from pmtiles.tile import tileid_to_zxy  # noqa: E402

gdal.UseExceptions()

HALF_WORLD = 20037508.342789244
TILE = 256
LEVELS = 16  # palette entries used by build_river_plains.py

DEFAULT_LEFT = -180.0
DEFAULT_TOP = 80.0
DEFAULT_RES = 1.0 / 480.0
DEFAULT_WIDTH = 172800
DEFAULT_HEIGHT = 67200


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def decode_tile(blob: bytes) -> np.ndarray:
    """PNG palette tile -> uint8 coverage 0..255."""
    name = f"/vsimem/rp_{os.getpid()}.png"
    gdal.FileFromMemBuffer(name, blob)
    try:
        ds = gdal.Open(name)
        index = ds.GetRasterBand(1).ReadAsArray()
        ds = None
    finally:
        gdal.Unlink(name)
    # Palette index i means coverage i/(LEVELS-1); scale to the 0..255 byte range.
    return np.rint(index.astype(np.float32) * 255.0 / (LEVELS - 1)).astype(np.uint8)


def iter_tiles(path: str, zoom: int):
    """Yield (x, y, blob) for every stored tile at `zoom`."""
    with open(path, "rb") as handle:
        source = MmapSource(handle)
        reader = Reader(source)
        header = reader.header()
        if zoom > header["max_zoom"]:
            sys.exit(f"ERROR: {path} only goes to zoom {header['max_zoom']}")
        for item in all_tiles(source):
            key, blob = item
            z, x, y = tileid_to_zxy(key) if isinstance(key, int) else key
            if z == zoom:
                yield x, y, blob


def build_mosaic(pmtiles: list[str], zoom: int, path: str) -> dict:
    side = TILE * 2 ** zoom
    res = 2 * HALF_WORLD / side
    log(f"mosaic: {side}x{side} px at {res:.3f} m, sparse EPSG:3857")

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(
        path, side, side, 1, gdal.GDT_Byte,
        options=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                 "COMPRESS=DEFLATE", "ZLEVEL=6", "BIGTIFF=YES", "SPARSE_OK=TRUE"],
    )
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(3857)
    ds.SetProjection(srs.ExportToWkt())
    ds.SetGeoTransform([-HALF_WORLD, res, 0.0, HALF_WORLD, 0.0, -res])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(0)

    written: set[tuple[int, int]] = set()
    bounds = None
    stats = {}

    for path_index, source in enumerate(pmtiles):
        with open(source, "rb") as handle:
            header = Reader(MmapSource(handle)).header()
        box = (header["min_lon_e7"] / 1e7, header["min_lat_e7"] / 1e7,
               header["max_lon_e7"] / 1e7, header["max_lat_e7"] / 1e7)
        bounds = box if bounds is None else (
            min(bounds[0], box[0]), min(bounds[1], box[1]),
            max(bounds[2], box[2]), max(bounds[3], box[3]),
        )

        count = merged = 0
        started = time.time()
        for x, y, blob in iter_tiles(source, zoom):
            coverage = decode_tile(blob)
            if (x, y) in written:
                existing = band.ReadAsArray(x * TILE, y * TILE, TILE, TILE)
                coverage = np.maximum(coverage, existing)
                merged += 1
            band.WriteArray(coverage, x * TILE, y * TILE)
            written.add((x, y))
            count += 1
            if count % 10000 == 0:
                log(f"  {os.path.basename(source)}: {count} tiles")
        stats[os.path.basename(source)] = {
            "tiles": count, "overlapping_tiles_merged": merged,
            "seconds": round(time.time() - started, 1),
        }
        log(f"  {os.path.basename(source)}: {count} tiles ({merged} merged) "
            f"in {time.time() - started:.0f}s")

    band.FlushCache()
    ds = None
    log(f"mosaic written, {len(written)} tiles, {os.path.getsize(path) / 1e6:.0f} MB on disk")
    return {"per_file": stats, "tiles_total": len(written), "pmtiles_bounds": bounds}


def warp_progress(complete: float, _message, state: list) -> int:
    step = int(complete * 10)
    if step > state[0]:
        state[0] = step
        log(f"  warp {step * 10}%")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rasterise river floodplain PMTiles onto the flood accumulator grid.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pmtiles", nargs="+", required=True, help="input PMTiles files")
    parser.add_argument("--out", required=True, help="output coverage GeoTIFF")
    parser.add_argument("--zoom", type=int, default=10, help="PMTiles zoom level to read")
    parser.add_argument("--mosaic", default=None, help="intermediate mosaic path")
    parser.add_argument("--keep-mosaic", action="store_true")
    parser.add_argument("--grid-left", type=float, default=DEFAULT_LEFT)
    parser.add_argument("--grid-top", type=float, default=DEFAULT_TOP)
    parser.add_argument("--grid-res", type=float, default=DEFAULT_RES)
    parser.add_argument("--grid-width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--grid-height", type=int, default=DEFAULT_HEIGHT)
    args = parser.parse_args()

    mosaic = args.mosaic or f"{args.out}.mercator.tif"
    info = build_mosaic(args.pmtiles, args.zoom, mosaic)

    left, top, res = args.grid_left, args.grid_top, args.grid_res
    right = left + args.grid_width * res
    bottom = top - args.grid_height * res
    log(f"warping to EPSG:4326 {args.grid_width}x{args.grid_height} "
        f"({left}..{right}, {bottom}..{top}), average resampling")

    started = time.time()
    gdal.Warp(
        args.out, mosaic,
        dstSRS="EPSG:4326",
        outputBounds=(left, bottom, right, top),
        width=args.grid_width, height=args.grid_height,
        resampleAlg="average",
        srcNodata=None, dstNodata=None,
        outputType=gdal.GDT_Byte,
        multithread=True,
        warpMemoryLimit=400 * 1024 * 1024,
        creationOptions=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                         "COMPRESS=LZW", "PREDICTOR=2", "BIGTIFF=YES"],
        callback=warp_progress,
        callback_data=[0],
    )
    log(f"warp finished in {time.time() - started:.0f}s, "
        f"{os.path.getsize(args.out) / 1e6:.1f} MB")

    sidecar = f"{os.path.splitext(args.out)[0]}_metadata.json"
    with open(sidecar, "w") as handle:
        json.dump(
            {
                "result": os.path.basename(args.out),
                "source": "MoM-map data/persistent/river_plains/*.pmtiles",
                "source_dataset": "GFPLAN250m river floodplains (Nardi et al.)",
                "pixel_meaning": "floodplain coverage of the pixel, 0 = none, 255 = full",
                "zoom_read": args.zoom,
                "target_grid": {
                    "crs": "EPSG:4326", "left": left, "top": top, "res": res,
                    "width": args.grid_width, "height": args.grid_height,
                },
                "pmtiles_lat_bounds": [info["pmtiles_bounds"][1], info["pmtiles_bounds"][3]],
                "pmtiles_lon_bounds": [info["pmtiles_bounds"][0], info["pmtiles_bounds"][2]],
                "caveat": (
                    "an absent tile means no floodplain, which is indistinguishable from "
                    "outside the GFPLAN domain; restrict analysis to pmtiles_lat_bounds"
                ),
                "tiles": info,
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            handle, indent=2,
        )
        handle.write("\n")
    log(f"wrote {os.path.basename(sidecar)}")

    if not args.keep_mosaic:
        os.remove(mosaic)
        for extra in (f"{mosaic}.aux.xml",):
            if os.path.exists(extra):
                os.remove(extra)
        log("removed the intermediate mercator mosaic")


if __name__ == "__main__":
    main()
