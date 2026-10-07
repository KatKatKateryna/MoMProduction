#!/usr/bin/env python
"""Full joint contingency of the 30" flood layers, with real cell areas.

Every layer is reduced to one bit per 30" cell, the bits are packed into a code,
and the codes are counted (by cells and by km2) with np.bincount. From that one
table every pair's intersection, union, Jaccard and conditional probabilities
follow, as do all higher-order combinations, so nothing has to be re-read.

Also written out: the same counts per 10 degree latitude band, per-layer km2 per
0.1 degree cell (for watershed statistics), and a histogram of the merged model
flood depth by layer combination.

Domain: land (MoM watershed polygons) between --south and --north, the latitude
range where the DFO/VIIRS accumulations and GFPLAIN all have coverage.
"""
import argparse, json, sys, time
import numpy as np
from osgeo import gdal

gdal.UseExceptions()
W, H, CELL = 43200, 21600, 1.0 / 120.0
R = 6371.0072  # km
DEPTH_BINS = [0, 10, 50, 100, 200, 400, 800, 1600, 65534]  # cm


def band(path, idx=1):
    ds = gdal.Open(path)
    return ds, ds.GetRasterBand(idx)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--layers", required=True,
                   help="name=path:band:op:value,... op in gt,ge,eq,ne,lt")
    p.add_argument("--land", required=True, help="path:band:op:value")
    p.add_argument("--depth", default=None)
    p.add_argument("--g01", default="", help="layer names to sum per 0.1 degree cell")
    p.add_argument("--out", required=True)
    p.add_argument("--north", type=float, default=64.0729166666)
    p.add_argument("--south", type=float, default=-54.73125)
    p.add_argument("--rows", type=int, default=120)
    a = p.parse_args()

    specs = []
    for item in a.layers.split(","):
        name, rest = item.split("=")
        path, bidx, op, val = rest.split(":")
        specs.append((name, path, int(bidx), op, float(val)))
    names = [s[0] for s in specs]
    nl = len(names)
    assert nl <= 20
    handles = [band(s[1], s[2]) for s in specs]
    lpath, lb_, lop, lval = a.land.split(":")
    land_ds, land_b = band(lpath, int(lb_))
    lval = float(lval)
    g01_idx = [names.index(n) for n in a.g01.split(",") if n]
    depth_ds, depth_b = band(a.depth) if a.depth else (None, None)

    j0 = max(0, int(np.floor((90.0 - a.north) / CELL)))
    j1 = min(H, int(np.ceil((90.0 - a.south) / CELL)))
    ncomb = 1 << nl
    cells = np.zeros(ncomb, dtype=np.int64)
    km2 = np.zeros(ncomb, dtype=np.float64)
    lat_bands = np.arange(-90, 100, 10)
    lat_cells = np.zeros((len(lat_bands) - 1, ncomb), dtype=np.int64)
    depth_hist = np.zeros((ncomb, len(DEPTH_BINS) - 1), dtype=np.int64)
    # per 0.1 degree cell: km2 of domain and of each layer
    g01 = np.zeros((len(a.g01.split(',')) + 1 if a.g01 else 1, 1800, 3600), dtype=np.float32)
    t0 = time.time()
    for r0 in range(j0, j1, a.rows):
        r1 = min(r0 + a.rows, j1)
        n = r1 - r0
        land = land_b.ReadAsArray(0, r0, W, n)
        dom = (land > lval) if lop == "gt" else ((land == lval) if lop == "eq" else (land >= lval))
        if not dom.any():
            continue
        lat_top = 90.0 - np.arange(r0, r1) * CELL
        row_km2 = (R * R * np.radians(CELL) *
                   (np.sin(np.radians(lat_top)) - np.sin(np.radians(lat_top - CELL))))
        area = np.repeat(row_km2[:, None], W, axis=1)
        code = np.zeros((n, W), dtype=np.int32)
        for b, (name, path, bidx, op, val) in enumerate(specs):
            arr = handles[b][1].ReadAsArray(0, r0, W, n)
            if op == "gt":
                m = arr > val
            elif op == "eq":
                m = arr == val
            elif op == "ne":
                m = arr != val
            elif op == "lt":
                m = arr < val
            else:
                m = arr >= val
            code |= (m.astype(np.int32) << b)
        c = code[dom]
        w = area[dom]
        cells += np.bincount(c, minlength=ncomb)
        km2 += np.bincount(c, weights=w, minlength=ncomb)
        lb = np.digitize((lat_top - CELL / 2), lat_bands) - 1
        lbf = np.repeat(lb[:, None], W, axis=1)[dom]
        nb_ = lat_cells.shape[0]
        lat_cells += np.bincount(lbf * ncomb + c,
                                 minlength=nb_ * ncomb).reshape(nb_, ncomb)
        if depth_b is not None:
            d = depth_b.ReadAsArray(0, r0, W, n)[dom]
            d = np.where(d >= 65535, 0, d)   # nodata -> lowest bin, not the deepest
            nd_ = depth_hist.shape[1]
            db = np.clip(np.digitize(d, DEPTH_BINS) - 1, 0, nd_ - 1)
            depth_hist += np.bincount(c * nd_ + db,
                                      minlength=ncomb * nd_).reshape(ncomb, nd_)
        # 0.1 degree sums
        rr = ((np.arange(r0, r1)) // 12)
        rr0, nrr = int(rr[0]), int(rr[-1] - rr[0] + 1)
        cc = np.arange(W) // 12
        rrf = np.repeat(rr[:, None], W, axis=1)[dom] - rr0
        ccf = np.repeat(cc[None, :], n, axis=0)[dom]
        flat = rrf * 3600 + ccf
        ml = nrr * 3600
        g01[0, rr0:rr0 + nrr] += np.bincount(flat, weights=w, minlength=ml
                                             ).reshape(nrr, 3600).astype(np.float32)
        for q, b in enumerate(g01_idx):
            sel = ((c >> b) & 1) > 0
            if sel.any():
                g01[q + 1, rr0:rr0 + nrr] += np.bincount(
                    flat[sel], weights=w[sel], minlength=ml
                    ).reshape(nrr, 3600).astype(np.float32)
        if (r0 - j0) % (a.rows * 10) == 0:
            print(f"  row {r0}/{j1} {time.time()-t0:.0f}s", flush=True)

    np.savez_compressed(a.out, names=np.array(names), cells=cells, km2=km2,
                        lat_bands=lat_bands, lat_cells=lat_cells,
                        depth_bins=np.array(DEPTH_BINS), depth_hist=depth_hist,
                        g01=g01, g01_names=np.array([n for n in a.g01.split(",") if n]),
                        meta=np.array(json.dumps(
                            {"north": a.north, "south": a.south,
                             "layers": [list(map(str, s)) for s in specs]})))
    print(f"wrote {a.out} in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
