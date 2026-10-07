#!/usr/bin/env python
"""Unbiased area of a percent-cover layer: sum of (percent/100 * cell area) on land.

The >=1% and >=50% thresholds used elsewhere count a whole cell as flooded or not,
so they over- or under-state area. This sums the cover fractions instead, which is
what the layer really covers, and is the yardstick for how much the PMTiles
archives' own max-resampling inflates a layer.
"""
import sys
import numpy as np
from osgeo import gdal
gdal.UseExceptions()
W, CELL, R = 43200, 1 / 120.0, 6371.0072
land_ds = gdal.Open("work/land_30s.tif"); land_b = land_ds.GetRasterBand(2)
srcs = []
for spec in sys.argv[1:]:
    path, b, scale = spec.split(":")
    ds = gdal.Open(path); srcs.append((spec, ds, ds.GetRasterBand(int(b)), float(scale)))
tot = np.zeros(len(srcs)); land_km2 = 0.0
j0, j1 = int((90 - 64.0729166666) / CELL), int((90 + 54.73125) / CELL)
for r0 in range(j0, j1, 240):
    n = min(240, j1 - r0)
    dom = land_b.ReadAsArray(0, r0, W, n) >= 8
    if not dom.any():
        continue
    lat = 90.0 - np.arange(r0, r0 + n) * CELL
    km2 = (R * R * np.radians(CELL) * (np.sin(np.radians(lat)) - np.sin(np.radians(lat - CELL))))
    a = np.repeat(km2[:, None], W, axis=1)[dom]
    land_km2 += a.sum()
    for i, (_, _, band, scale) in enumerate(srcs):
        v = band.ReadAsArray(0, r0, W, n)[dom].astype(np.float64)
        v = np.where(v >= 65535, 0, v)
        tot[i] += (np.clip(v * scale, 0, 1) * a).sum()
print(f"land {land_km2:,.0f} km2")
for i, (spec, _, _, _) in enumerate(srcs):
    print(f"{spec}: {tot[i]:,.0f} km2  ({100*tot[i]/land_km2:.2f}% of land)")
