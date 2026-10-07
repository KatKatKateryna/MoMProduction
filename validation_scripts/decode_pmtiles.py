#!/usr/bin/env python
"""Decode a MoM-map PMTiles value raster back to a global 30" GeoTIFF.

The archives store, per pixel, the index of the value on a 2% log scale
(scripts/build_value_pmtiles.py: index = round(log(v)/log(1.02)) + 2000, 0 = no
data), as a lossless Terrarium WebP (index = R*256 + G - 32768). River plains is
a plain colour PNG instead, so there any non-transparent pixel is floodplain.

Step 1 writes the archive's maxzoom tiles into one global Web Mercator raster,
step 2 warps that onto the 30" (1/120 degree) global lat/lon grid the Aqueduct /
AI4G rasters use, taking the maximum of the mercator pixels in each cell so a
binary layer stays binary and nothing flooded is lost.

modes:  binary  1 where the archive has data           (flood extent, river plains)
        classes round(value), 0..255                   (AI4G: 1 mask, 2 flooded)
        depth   depth in cm, UInt16, 65535 nodata      (flood_max)
"""
import argparse, io, math, os, sys
import numpy as np
from PIL import Image
from pmtiles.reader import Reader, MmapSource
from osgeo import gdal

gdal.UseExceptions()
TILE = 256
HALF = 20037508.342789244
RATIO, OFFSET = 1.02, 2000


def tile_values(data, tile_type):
    im = Image.open(io.BytesIO(data))
    a = np.asarray(im)
    if tile_type == "png":          # colour tile: any non-transparent pixel
        if a.ndim == 3 and a.shape[2] == 4:
            return (a[:, :, 3] > 0).astype(np.float32)
        return (a.reshape(a.shape[0], a.shape[1], -1).any(axis=2)).astype(np.float32)
    idx = a[:, :, 0].astype(np.int32) * 256 + a[:, :, 1].astype(np.int32) - 32768
    v = np.zeros(idx.shape, dtype=np.float32)
    m = idx > 0
    v[m] = RATIO ** (idx[m].astype(np.float64) - OFFSET)
    return v


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pm", required=True)
    p.add_argument("--out", required=True, help="30 arcsec lat/lon GeoTIFF")
    p.add_argument("--mode", choices=["binary", "classes", "depth", "fraction"], required=True)
    p.add_argument("--min-value", type=float, default=None,
                   help="only count pixels with value >= this (e.g. 2 for AI4G flooded)")
    p.add_argument("--merc", default=None, help="keep the intermediate mercator raster here")
    a = p.parse_args()

    with open(a.pm, "rb") as fh:
        r = Reader(MmapSource(fh))
        h = r.header()
        z = h["max_zoom"]
        ttype = "png" if "PNG" in str(h["tile_type"]) else "webp"
        n = 2 ** z
        size = 2 * HALF / n
        merc = a.merc or (a.out + f".merc.{os.getpid()}.tif")
        dt = gdal.GDT_UInt16 if a.mode == "depth" else gdal.GDT_Byte
        ds = gdal.GetDriverByName("GTiff").Create(
            merc, n * TILE, n * TILE, 1, dt,
            ["TILED=YES", "COMPRESS=DEFLATE", "SPARSE_OK=TRUE", "BIGTIFF=YES"])
        ds.SetGeoTransform([-HALF, size / TILE, 0, HALF, 0, -size / TILE])
        ds.SetProjection('EPSG:3857')
        band = ds.GetRasterBand(1)
        nodata = 65535 if a.mode == "depth" else 0
        frac = a.mode == "fraction"
        if not frac:
            band.SetNoDataValue(nodata)
        kept = 0
        for ty in range(n):
            strip = None
            for tx in range(n):
                data = r.get(z, tx, ty)
                if data is None:
                    continue
                v = tile_values(data, ttype)
                if strip is None:
                    strip = np.full((TILE, n * TILE), nodata,
                                    dtype=np.uint16 if a.mode == "depth" else np.uint8)
                m = v > 0
                if a.min_value is not None:
                    m &= v >= a.min_value
                if frac:
                    out = np.where(m, 100, 0).astype(np.uint8)
                elif a.mode == "binary":
                    out = np.where(m, 1, 0).astype(np.uint8)
                elif a.mode == "classes":
                    out = np.where(m, np.clip(np.rint(v), 0, 254), 0).astype(np.uint8)
                else:
                    out = np.where(m, np.clip(np.rint(v * 100), 0, 65534), nodata).astype(np.uint16)
                strip[:, tx * TILE:(tx + 1) * TILE] = out
                kept += 1
            if strip is not None:
                band.WriteArray(strip, 0, ty * TILE)
        ds = None
        print(f"{a.pm}: z{z} {ttype}, {kept} tiles with data -> {merc}", flush=True)

    # 30" global lat/lon, max over the mercator pixels of each cell
    gdal.Warp(a.out, merc, format="GTiff", dstSRS="EPSG:4326",
              outputBounds=(-180, -90, 180, 90), width=43200, height=21600,
              outputType=dt,
              srcNodata=None if frac else nodata, dstNodata=None if frac else nodata,
              resampleAlg="average" if frac else "max",
              multithread=True, warpMemoryLimit=512,
              creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2",
                               "BIGTIFF=YES", "SPARSE_OK=TRUE"])
    print(f"wrote {a.out}", flush=True)
    if a.merc is None:
        os.remove(merc)


if __name__ == "__main__":
    main()
