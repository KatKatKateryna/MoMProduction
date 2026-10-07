#!/usr/bin/env python
"""Reduce a native-grid accumulation (or binary mask) onto the global 30" grid.

The new layers (AI4G, Aqueduct/GloFAS extent, floodplains) are all on the 30"
(1/120 degree) grid from 90N/180W. DFO (1/480 degree from 80N) and VIIRS
(0.003372 degree from 75N) are put on it here by assigning every native pixel to
the cell containing its centre -- exact, and neither product is interpolated.

Output GeoTIFF, 43200 x 21600, UInt16, on that grid:
    band 1  npx        native pixels whose centre falls in the cell
    band 2  ever_1     of those, how many had band 1 > 0
    band 3  maxdays_1  the largest band 1 count in the cell
    band 4  ever_2     } only if the source has a second band
    band 5  maxdays_2  }
For a binary mask (--binary) band 1 > 0 means the mask is set, so band 2 is the
number of masked pixels in the cell.
"""
import argparse, sys, time
import numpy as np
from osgeo import gdal

gdal.UseExceptions()
OUT_W, OUT_H, CELL = 43200, 21600, 1.0 / 120.0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--rows-per-chunk", type=int, default=16, help="output rows per read")
    a = p.parse_args()

    src = gdal.Open(a.src)
    gt = src.GetGeoTransform()
    top, left, res = gt[3], gt[0], gt[1]
    W, H = src.RasterXSize, src.RasterYSize
    nb = min(src.RasterCount, 2)

    # native column -> output column (monotone), and the run starts
    lon_c = left + (np.arange(W) + 0.5) * res
    col_map = np.clip(((lon_c + 180.0) / CELL).astype(np.int64), 0, OUT_W - 1)
    starts = np.flatnonzero(np.r_[True, np.diff(col_map) > 0])
    out_cols = col_map[starts]                      # output column of each run
    nruns = len(starts)

    # native row -> output row
    lat_c = top - (np.arange(H) + 0.5) * res
    row_map = np.clip(((90.0 - lat_c) / CELL).astype(np.int64), 0, OUT_H - 1)
    first_out, last_out = row_map[0], row_map[-1]

    nbands = 1 + 2 * nb
    drv = gdal.GetDriverByName("GTiff")
    # Strips as tall as one write, so each chunk is compressed once instead of being
    # read back and recompressed for every partially written tile row.
    dst = drv.Create(a.out, OUT_W, OUT_H, nbands, gdal.GDT_UInt16,
                     ["TILED=NO", f"BLOCKYSIZE={a.rows_per_chunk}", "COMPRESS=DEFLATE",
                      "PREDICTOR=2", "BIGTIFF=YES", "SPARSE_OK=TRUE", "INTERLEAVE=BAND"])
    dst.SetGeoTransform([-180.0, CELL, 0, 90.0, 0, -CELL])
    dst.SetProjection(src.GetProjection())
    names = ["npx", "ever_1", "maxdays_1", "ever_2", "maxdays_2"]
    for b in range(nbands):
        dst.GetRasterBand(b + 1).SetDescription(names[b])

    runlen = np.add.reduceat(np.ones(len(col_map), dtype=np.int64), starts)
    t0 = time.time()
    chunk = a.rows_per_chunk
    for j0 in range(first_out, last_out + 1, chunk):
        j1 = min(j0 + chunk, last_out + 1)
        i0, i1 = np.searchsorted(row_map, j0), np.searchsorted(row_map, j1)
        if i1 <= i0:
            continue
        bands = [src.GetRasterBand(b + 1).ReadAsArray(0, int(i0), W, int(i1 - i0))
                 for b in range(nb)]
        out = np.zeros((nbands, j1 - j0, OUT_W), dtype=np.uint16)
        rows = row_map[i0:i1]
        for j in range(j0, j1):
            sel = rows == j
            if not sel.any():
                continue
            k = j - j0
            n_native_rows = int(sel.sum())
            out[0, k, out_cols] = np.minimum(runlen * n_native_rows, 65535)
            for b in range(nb):
                blk = bands[b][sel]
                flagged = (blk > 0).astype(np.uint32)
                ever = np.add.reduceat(flagged, starts, axis=1).sum(axis=0)
                mx = np.maximum.reduceat(blk, starts, axis=1).max(axis=0).astype(np.int64)
                out[1 + 2 * b, k, out_cols] = np.minimum(ever, 65535)
                out[2 + 2 * b, k, out_cols] = np.minimum(mx, 65535)
        for b in range(nbands):
            dst.GetRasterBand(b + 1).WriteArray(out[b], 0, j0)
        if (j0 - first_out) % (chunk * 40) == 0:
            print(f"  row {j0}/{last_out} {time.time()-t0:.0f}s", flush=True)
    dst = None
    print(f"wrote {a.out} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
