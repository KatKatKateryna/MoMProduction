#!/usr/bin/env python
"""Watershed level view of the same layers: does a floodplain map rank the
watersheds that flood the way the models and the satellites do?

Uses the per 0.1 degree km2 sums from joint_30s.py (g01) and MoM's pfaf_id
raster at the same 0.1 degree, so every MoM watershed gets, in km2:
domain land, floodplain, merged model extent, AI4G flooded, DFO sudden, VIIRS.
"""
import sys
import numpy as np
from scipy.stats import spearmanr
from osgeo import gdal
gdal.UseExceptions()

z = np.load(sys.argv[1], allow_pickle=True)
g01 = z["g01"]                      # (1 + n, 1800, 3600), rows from 90N
gn = [str(x) for x in z["g01_names"]]
ws = gdal.Open(sys.argv[2])         # watersheds_0p1deg.tiff, 3600x1400 from 80N
pf = ws.GetRasterBand(1).ReadAsArray()
H = pf.shape[0]
sub = g01[:, 100:100 + H, :]        # align 90N-based rows to the 80N-based raster
ids, inv = np.unique(pf.ravel(), return_inverse=True)
tab = np.zeros((g01.shape[0], len(ids)))
for k in range(g01.shape[0]):
    tab[k] = np.bincount(inv, weights=sub[k].ravel().astype(np.float64), minlength=len(ids))
keep = (ids != 0) & (tab[0] > 100)   # real watersheds with >100 km2 in the domain
ids, tab = ids[keep], tab[:, keep]
land = tab[0]
shares = {n: tab[i + 1] / land for i, n in enumerate(gn)}

out = []
P = out.append
P("## Watershed level: does the floodplain rank the watersheds that flood?")
P("")
P(f"{len(ids):,} MoM watersheds with more than 100 km2 inside the domain. For each one,")
P("the share of its land that is floodplain, that the models flood, and that each")
P("satellite product flagged.")
P("")
P("| layer | watersheds with any | median share of watershed | mean share |")
P("|---|---|---|---|")
for n in gn:
    s = shares[n]
    P(f"| {n} | {(s>0).sum():,} ({100*(s>0).mean():.0f}%) | {100*np.median(s):.2f}% | {100*s.mean():.2f}% |")
P("")
P("Rank agreement between watershed shares (Spearman, all watersheds):")
P("")
P("| | " + " | ".join(gn) + " |")
P("|---|" + "---|" * len(gn))
for a in gn:
    row = [f"{spearmanr(shares[a], shares[b]).statistic:.2f}" for b in gn]
    P(f"| {a} | " + " | ".join(row) + " |")
P("")
P("Of the 100 watersheds with the largest flooded area in each product, how many are")
P("also in the other's top 100:")
P("")
area = {n: tab[i + 1] for i, n in enumerate(gn)}
top = {n: set(np.argsort(-area[n])[:100]) for n in gn}
P("| | " + " | ".join(gn) + " |")
P("|---|" + "---|" * len(gn))
for a in gn:
    P(f"| {a} | " + " | ".join(str(len(top[a] & top[b])) for b in gn) + " |")
P("")
obs = [n for n in gn if n.startswith(("dfo", "viirs"))]
P("Watersheds where a satellite saw flood but the map says nothing:")
P("")
P("| observed layer | watersheds with observed flood | of those, no floodplain mapped | of those, outside the model extent |")
P("|---|---|---|---|")
for n in obs:
    sel = shares[n] > 0
    P(f"| {n} | {sel.sum():,} | {100*(shares['plain_maj'][sel]==0).mean():.1f}% | "
      f"{100*(shares['extent_any'][sel]==0).mean():.1f}% |")
P("")
print("\n".join(out))
