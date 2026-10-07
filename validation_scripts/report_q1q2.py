#!/usr/bin/env python
"""Turns the joint 30" contingency into the two answers asked for:

Q1  how river floodplains (GFPLAIN250m) relate to the merged model extent
    (Aqueduct riverine RP1000 + Aqueduct coastal RP1000 + GloFAS RP500)
Q2  whether the DFO and VIIRS observations fall inside the AI4G Sentinel-1
    flood layer (2014-2024)

Every number is km2 of land in the common domain, from the packed joint table:
one bit per layer per 30" cell, so any combination is a sum over the table.
"""
import json, sys
import numpy as np

z = np.load(sys.argv[1], allow_pickle=True)
names = [str(x) for x in z["names"]]
km2, cells = z["km2"], z["cells"]
meta = json.loads(str(z["meta"]))
idx = np.arange(len(km2))
BIT = {n: ((idx >> i) & 1).astype(bool) for i, n in enumerate(names)}


def A(mask):            # km2
    return float(km2[mask].sum())


def C(mask):            # cells
    return int(cells[mask].sum())


def fmt(x, d=0):
    return f"{x:,.{d}f}"


def pct(a, b):
    return "n/a" if b == 0 else f"{100.0 * a / b:.1f}%"


def pair(a, b, dom=None):
    """Overlap statistics of two layers inside dom."""
    d = np.ones(len(km2), bool) if dom is None else dom
    A_, B_ = BIT[a] & d, BIT[b] & d
    both, either = A(A_ & B_), A(A_ | B_)
    only_a, only_b = A(A_ & ~BIT[b]), A(B_ & ~BIT[a])
    tot = A(d)
    pa, pb = A(A_), A(B_)
    rest = tot - pa
    rr = ((both / pa) / ((pb - both) / rest)) if pa and rest and (pb - both) else float("nan")
    return dict(domain_km2=tot, a_km2=pa, b_km2=pb, both_km2=both, union_km2=either,
                only_a_km2=only_a, only_b_km2=only_b,
                jaccard=(both / either if either else float("nan")),
                p_b_given_a=(both / pa if pa else float("nan")),
                p_a_given_b=(both / pb if pb else float("nan")),
                risk_ratio=rr)


def row(label, d, extra=""):
    return (f"| {label} | {fmt(d['a_km2'])} | {fmt(d['b_km2'])} | {fmt(d['both_km2'])} | "
            f"{pct(d['both_km2'], d['a_km2'])} | {pct(d['both_km2'], d['b_km2'])} | "
            f"{d['jaccard']:.3f} | {d['risk_ratio']:.1f}x |{extra}")


out = []
P = out.append
dom = np.ones(len(km2), bool)
land = A(dom)
cov = BIT["ai4g_cov"]

P("# Floodplains, modelled flood extents and SAR observations")
P("")
P("All numbers are km2 of land on one common 30 arcsec (~1 km) grid, inside the")
P(f"domain used by the earlier reports: MoM watershed land between {meta['north']:.2f}N and")
P(f"{abs(meta['south']):.2f}S, where DFO, VIIRS and GFPLAIN all have coverage. A 30\" cell counts as")
P("land when at least half of it is land. Cell areas are true areas (cos lat), not cell counts.")
P("")
P("| layer | what it is | km2 | share of land |")
P("|---|---|---|---|")
DESC = {
 "plain_any": "GFPLAIN250m floodplain, any 250 m pixel in the cell",
 "plain_maj": "GFPLAIN250m floodplain over at least half the cell",
 "plain_dec": "floodplain from the PMTiles archive, >=50% of the cell (decode check)",
 "ai4g_dec": "AI4G flooded from the PMTiles archive, >=50% of the cell (decode check)",
 "extent_any": "flooded in Aqueduct riverine RP1000, Aqueduct coastal RP1000 or GloFAS RP500 (>=1% of cell)",
 "extent_maj": "the same merged model extent over at least half the cell",
 "ai4g_flood": "AI4G: flooded at least once in Sentinel-1, 2014-2024",
 "ai4g_mask": "AI4G exclusion mask (rough terrain, arid, urban): not assessed",
 "ai4g_cov": "AI4G has a tile at all (0, 1 or 2, not 255)",
 "dfo_sudden": "DFO class 3 sudden flood, flagged on >=1 day (Jan-Aug 2026)",
 "dfo_sud5": "DFO class 3 flagged on >=5 days",
 "dfo_recur": "DFO class 2 recurrent flood, >=1 day",
 "viirs1d": "VIIRS 1-day composite 141-200, >=1 day (full range)",
 "viirs1d5": "VIIRS 1-day, >=5 days",
 "viirs5d": "VIIRS 5-day composite 141-200, >=1 day (full range)",
 "viirs5d5": "VIIRS 5-day, >=5 days",
}
P(f"| (domain) | land in the domain | {fmt(land)} | 100% |")
for n in names:
    P(f"| {n} | {DESC.get(n,'')} | {fmt(A(BIT[n]))} | {pct(A(BIT[n]), land)} |")
P("")

P("## Decode check")
P("")
P("The floodplain and AI4G layers exist both as a raster and as PMTiles, so decoding")
P("the archives can be checked against the real thing. Area ratio decoded/exact:")
P("")
P(f"- floodplain: {A(BIT['plain_dec']):,.0f} vs {A(BIT['plain_maj']):,.0f} km2 "
  f"({A(BIT['plain_dec'])/A(BIT['plain_maj']):.2f}x), "
  f"{pct(A(BIT['plain_dec'] & BIT['plain_maj']), A(BIT['plain_maj']))} of the exact floodplain recovered")
P(f"- AI4G flooded: {A(BIT['ai4g_dec']):,.0f} vs {A(BIT['ai4g_flood']):,.0f} km2 "
  f"({A(BIT['ai4g_dec'])/A(BIT['ai4g_flood']):.2f}x), "
  f"{pct(A(BIT['ai4g_dec'] & BIT['ai4g_flood']), A(BIT['ai4g_flood']))} of the exact flooded area recovered")
P("")
P("The merged model extent only exists as PMTiles, so its area carries the same bias.")
P("")

# ── AI4G coverage ────────────────────────────────────────────────────────────
P("## AI4G coverage, and what actually limits the test")
P("")
P(f"- AI4G has a tile over {pct(A(cov), land)} of the land domain ({fmt(A(cov))} km2).")
P(f"- Of the covered land, {pct(A(cov & BIT['ai4g_mask']), A(cov))} is the exclusion mask "
  "(rough terrain, arid or urban: Sentinel-1 was not trusted there), and")
P(f"  {pct(A(cov & BIT['ai4g_flood']), A(cov))} was seen flooded at least once in 2014-2024.")
P(f"- {pct(land - A(cov), land)} of the land domain has no AI4G tile at all.")
P(f"- So the layer has a usable verdict (flooded or never flooded) on only "
  f"{pct(A(cov & ~BIT['ai4g_mask']), land)} of the land domain. That, not missing tiles, is")
P("  what limits the comparison: the exclusion mask is where Sentinel-1 flood detection was")
P("  not trusted (rough terrain, arid, urban), not where nothing flooded.")
P("")

# ── Q1 ───────────────────────────────────────────────────────────────────────
P("## Q1. River floodplains against the merged model extent")
P("")
P("Merged model extent = Aqueduct riverine RP1000 OR Aqueduct coastal RP1000 OR GloFAS RP500,")
P("i.e. your \"flood extent, any model\" layer. Both are static maps, so this is a pure")
P("map-to-map comparison: no time, no observation.")
P("")
P("| floodplain / model extent | floodplain km2 | extent km2 | both | of floodplain in extent | of extent on floodplain | Jaccard | risk ratio |")
P("|---|---|---|---|---|---|---|---|")
for a_ in ("plain_any", "plain_maj"):
    for b_ in ("extent_any", "extent_maj"):
        P(row(f"{a_} / {b_}", pair(a_, b_)))
P("")

lat_bands, lat_cells = z["lat_bands"], z["lat_cells"]
P("### By latitude (floodplain over half the cell, model extent >=1%)")
P("")
P("| latitude | land km2-ish (cells) | floodplain | model extent | both | of floodplain in extent | of extent on floodplain |")
P("|---|---|---|---|---|---|---|")
bp, be = names.index("plain_maj"), names.index("extent_any")
for i in range(len(lat_bands) - 1):
    tot = lat_cells[i].sum()
    if tot == 0:
        continue
    pl = lat_cells[i][BIT["plain_maj"]].sum()
    ex = lat_cells[i][BIT["extent_any"]].sum()
    bo = lat_cells[i][BIT["plain_maj"] & BIT["extent_any"]].sum()
    P(f"| {lat_bands[i]}..{lat_bands[i+1]} | {fmt(tot)} | {pct(pl,tot)} | {pct(ex,tot)} | "
      f"{pct(bo,tot)} | {pct(bo,pl)} | {pct(bo,ex)} |")
P("")

# depth of the model flood, on and off the floodplain
dh, dbins = z["depth_hist"], z["depth_bins"]
P("### Does the model put its deep water on the floodplain?")
P("")
P("Cells of the merged model extent, by the deepest of the three models (flood_max).")
P("")
P("| model depth | cells | on floodplain (half-cell) | on floodplain (any) |")
P("|---|---|---|---|")
ext = BIT["extent_any"]
for k in range(len(dbins) - 1):
    col = dh[:, k]
    tot = col[ext].sum()
    if tot == 0:
        continue
    on_m = col[ext & BIT["plain_maj"]].sum()
    on_a = col[ext & BIT["plain_any"]].sum()
    lo, hi = dbins[k] / 100.0, dbins[k + 1] / 100.0
    P(f"| {lo:g}-{hi:g} m | {fmt(tot)} | {pct(on_m,tot)} | {pct(on_a,tot)} |")
P("")

# ── Q2 ───────────────────────────────────────────────────────────────────────
P("## Q2. Do the DFO and VIIRS observations fall inside AI4G?")
P("")
P("Only cells where AI4G has a tile are counted, and the AI4G classes are kept apart:")
P("flooded (2), exclusion mask (1, not assessed) and observed-but-never-flooded (0).")
P("")
P("| observed layer | km2 in AI4G-covered land | in AI4G flooded | in AI4G exclusion mask | in AI4G never flooded | outside AI4G coverage |")
P("|---|---|---|---|---|---|")
OBS = ["dfo_sudden", "dfo_sud5", "dfo_recur", "viirs1d", "viirs1d5", "viirs5d", "viirs5d5"]
for n in OBS:
    b = BIT[n]
    tot_all = A(b)
    inc = A(b & cov)
    P(f"| {n} | {fmt(inc)} | {pct(A(b & BIT['ai4g_flood']), inc)} | "
      f"{pct(A(b & cov & BIT['ai4g_mask']), inc)} | "
      f"{pct(A(b & cov & ~BIT['ai4g_flood'] & ~BIT['ai4g_mask']), inc)} | "
      f"{pct(tot_all - inc, tot_all)} |")
P("")
P("The exclusion-mask column is land AI4G did not assess, so the honest containment")
P("test is the one that drops it:")
P("")
P("| observed layer | km2 on land AI4G assessed (tile, not masked) | also AI4G flooded | AI4G never flooded |")
P("|---|---|---|---|")
assessed = cov & ~BIT["ai4g_mask"]
for n in OBS:
    b = BIT[n] & assessed
    t = A(b)
    P(f"| {n} | {fmt(t)} | {pct(A(b & BIT['ai4g_flood']), t)} | "
      f"{pct(A(b & ~BIT['ai4g_flood']), t)} |")
P("")
P("Reverse direction, inside AI4G-covered land only:")
P("")
P("| observed layer | of AI4G flooded also flagged | Jaccard with AI4G flooded | risk ratio |")
P("|---|---|---|---|")
for n in OBS:
    d = pair(n, "ai4g_flood", dom=cov)
    P(f"| {n} | {pct(d['both_km2'], d['b_km2'])} | {d['jaccard']:.3f} | {d['risk_ratio']:.1f}x |")
P("")

# ── predictors head to head ──────────────────────────────────────────────────
P("## Which map best says where the water will be?")
P("")
P("Lift = P(observed flood | predictor) / P(observed flood | all land in the domain).")
P("")
P("| predictor | share of land | " + " | ".join(f"lift, {n}" for n in ["dfo_sudden", "dfo_recur", "viirs1d", "viirs5d"]) + " |")
P("|---|---|" + "---|" * 4)
for pr in ["plain_maj", "plain_any", "extent_any", "extent_maj", "ai4g_flood"]:
    cells_pr = A(BIT[pr])
    lifts = []
    for n in ["dfo_sudden", "dfo_recur", "viirs1d", "viirs5d"]:
        base = A(BIT[n]) / land
        p = A(BIT[pr] & BIT[n]) / cells_pr if cells_pr else float("nan")
        lifts.append(f"{p/base:.1f}x" if base else "n/a")
    P(f"| {pr} | {pct(cells_pr, land)} | " + " | ".join(lifts) + " |")
P("")
P("AI4G does not judge the land under its exclusion mask, so the row above mixes land it")
P("assessed with land it skipped. The same table on the land it did assess:")
P("")
P("| predictor | share of that land | " + " | ".join(f"lift, {n}" for n in ["dfo_sudden", "dfo_recur", "viirs1d", "viirs5d"]) + " |")
P("|---|---|" + "---|" * 4)
asd = cov & ~BIT["ai4g_mask"]
land_a = A(asd)
for pr in ["plain_maj", "extent_any", "extent_maj", "ai4g_flood"]:
    pr_a = A(BIT[pr] & asd)
    lifts = []
    for n in ["dfo_sudden", "dfo_recur", "viirs1d", "viirs5d"]:
        base = A(BIT[n] & asd) / land_a
        p = A(BIT[pr] & BIT[n] & asd) / pr_a if pr_a else float("nan")
        lifts.append(f"{p/base:.1f}x" if base else "n/a")
    P(f"| {pr} | {pct(pr_a, land_a)} | " + " | ".join(lifts) + " |")
P("")

print("\n".join(out))
