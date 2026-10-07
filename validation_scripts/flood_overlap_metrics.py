#!/usr/bin/env python
"""Turn the two flood_overlap_pass.py outputs into report numbers.

Reads the DFO-grid pass and the VIIRS-grid pass (0.1 degree cell sums) and an
optional 0.1 degree raster of MoM watershed ids, and writes one JSON with
every statistic plus a markdown dump of the tables, which the hand-written
reports quote.

Everything pixel level is a sum over cells, so it is exact for the grid the
pass ran on. Cell level comparisons of DFO against VIIRS use each product on
its own native grid (DFO from the DFO-grid pass, VIIRS from the VIIRS-grid
pass), so neither is resampled there.
"""

from __future__ import annotations

import argparse
import json

import numpy as np

LAYER_NAMES = {"S": "DFO sudden (class 3)", "R": "DFO recurrent (class 2)",
               "A": "DFO any (class 2 or 3)", "V": "VIIRS 141-200"}
FRACTION_EDGES = [0.0, 0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 1.0]
FRACTION_LABELS = ["0%", ">0-1%", ">1-5%", ">5-10%", ">10-25%", ">25-50%", ">50-75%", ">75-100%"]
LAT_BANDS = [(60, 70), (50, 60), (40, 50), (30, 40), (20, 30), (10, 20), (0, 10),
             (-10, 0), (-20, -10), (-30, -20), (-40, -30), (-50, -40), (-60, -50)]
SCALES = [(1, "0.1 deg"), (5, "0.5 deg"), (10, "1 deg")]


def load(path):
    z = np.load(path)
    c = {k: z[k] for k in z.files if k != "info" and not k.startswith("hist_")}
    h = {k[5:]: z[k] for k in z.files if k.startswith("hist_")}
    return c, h, json.loads(str(z["info"]))


def ssum(a, sel=None):
    return float(a.sum(dtype=np.float64) if sel is None else a[sel].sum(dtype=np.float64))


def block(a, k):
    if k == 1:
        return a.astype(np.float64)
    r, c = a.shape[0] // k * k, a.shape[1] // k * k
    return a[:r, :c].reshape(r // k, k, c // k, k).sum(axis=(1, 3), dtype=np.float64)


def ratio(a, b):
    return a / b if b else float("nan")


def avg_rank(a):
    _, inverse, counts = np.unique(a, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return (((ends - counts + 1) + ends) / 2.0)[inverse]


def corr(x, y):
    if x.size < 3:
        return {"n": int(x.size), "pearson": None, "spearman": None}
    return {"n": int(x.size), "pearson": float(np.corrcoef(x, y)[0, 1]),
            "spearman": float(np.corrcoef(avg_rank(x), avg_rank(y))[0, 1])}


# --------------------------------------------------------------------------- #
def plain_overlap(c, L, sel=None):
    D, P = ssum(c["dom"], sel), ssum(c["plain"], sel)
    E, EP = ssum(c[f"ever_{L}"], sel), ssum(c[f"ever_plain_{L}"], sel)
    F, FP = ssum(c[f"freq_{L}"], sel), ssum(c[f"freq_plain_{L}"], sel)
    O, EO = D - P, E - EP
    p_on, p_off = ratio(EP, P), ratio(EO, O)
    return {
        "domain_px": D, "plain_px": P, "plain_share_of_domain": ratio(P, D),
        "flooded_px": E, "flooded_share_of_domain": ratio(E, D),
        "flooded_on_plain_px": EP,
        "share_of_flooded_on_plain": ratio(EP, E),
        "share_of_plain_flooded": p_on, "share_of_offplain_flooded": p_off,
        "risk_ratio": ratio(p_on, p_off),
        "odds_ratio": ratio(EP * (O - EO), (P - EP) * EO),
        "jaccard_plain_flood": ratio(EP, E + P - EP),
        "share_of_flood_days_on_plain": ratio(FP, F),
        "mean_freq_on_plain": ratio(FP, P), "mean_freq_off_plain": ratio(F - FP, O),
        "mean_freq_domain": ratio(F, D),
    }


def by_latitude(c, L, info):
    out = []
    lat_top = info["grid_north"] - (np.arange(c["dom"].shape[0])) * info["cell"]
    for south, north in LAT_BANDS:
        rows = (lat_top <= north + 1e-9) & (lat_top - info["cell"] >= south - 1e-9)
        sel = np.zeros_like(c["dom"], bool)
        sel[rows] = True
        r = plain_overlap(c, L, sel)
        if r["domain_px"] == 0:
            continue
        r["lat"] = f"{south}..{north}"
        out.append(r)
    E_tot = ssum(c[f"ever_{L}"])
    for r in out:
        r["share_of_all_flood"] = ratio(r["flooded_px"], E_tot)
    return out


def dose_response(c, L, min_dom):
    dom = c["dom"]
    use = dom >= min_dom
    pf = np.divide(c["plain"], dom, out=np.zeros_like(dom), where=dom > 0)
    ff = np.divide(c[f"ever_{L}"], dom, out=np.zeros_like(dom), where=dom > 0)
    out = []
    for i, label in enumerate(FRACTION_LABELS):
        if i == 0:
            s = use & (pf == 0)
        else:
            s = use & (pf > FRACTION_EDGES[i - 1]) & (pf <= FRACTION_EDGES[i])
        n = int(s.sum())
        out.append({"bin": label, "cells": n,
                     "mean_flooded_fraction": float(ff[s].mean()) if n else None,
                     "pooled_flooded_fraction": ratio(ssum(c[f"ever_{L}"], s), ssum(dom, s)),
                     "share_of_cells_with_flood": float((ff[s] > 0).mean()) if n else None})
    return out


def within_flooded_regions(c, L, k):
    """Inside regions (blocks of k x k cells) that flooded, is the water on the plain?"""
    D, P = block(c["dom"], k), block(c["plain"], k)
    E, EP = block(c[f"ever_{L}"], k), block(c[f"ever_plain_{L}"], k)
    flooded = E > 0
    usable = flooded & (P > 0) & (D - P > 0)
    E_all = E[flooded].sum()
    res = {
        "flooded_regions": int(flooded.sum()),
        "flooded_regions_with_plain": int(usable.sum()),
        "share_of_flood_px_in_regions_without_plain": ratio(E[flooded & (P == 0)].sum(), E_all),
    }
    d, p, e, ep = D[usable], P[usable], E[usable], EP[usable]
    o, eo = d - p, e - ep
    p_on, p_off = ratio(ep.sum(), p.sum()), ratio(eo.sum(), o.sum())
    # per region: share of the flood on plain vs share of land that is plain
    share_flood = ep / e
    share_land = p / d
    per_lift = (ep / p) / np.maximum(eo / o, 1e-12)
    res.update({
        "plain_share_of_land": ratio(p.sum(), d.sum()),
        "share_of_flood_on_plain": ratio(ep.sum(), e.sum()),
        "share_of_plain_flooded": p_on,
        "share_of_offplain_flooded": p_off,
        "pooled_risk_ratio": ratio(p_on, p_off),
        "regions_where_plain_floods_more": float((ep / p > eo / o).mean()) if d.size else None,
        "median_region_risk_ratio": float(np.median(per_lift)) if d.size else None,
        "median_share_flood_on_plain": float(np.median(share_flood)) if d.size else None,
        "median_share_land_plain": float(np.median(share_land)) if d.size else None,
        "flood_share_explained_if_predict_plain": ratio(ep.sum(), e.sum()),
        "precision_if_predict_plain": p_on,
    })
    return res


def cross(c, h, L):
    """DFO layer L against VIIRS on one grid, pixel level."""
    D, P = ssum(c["dom"]), ssum(c["plain"])
    EL, EV, B = ssum(c[f"ever_{L}"]), ssum(c["ever_V"]), ssum(c[f"both_{L}"])
    ELp, EVp, Bp = ssum(c[f"ever_plain_{L}"]), ssum(c["ever_plain_V"]), ssum(c[f"both_plain_{L}"])
    U, Up = EL + EV - B, ELp + EVp - Bp
    hist = h[L].astype(np.float64)  # [plain, dfo_bin, viirs_bin]
    tot = hist.sum(axis=0)
    p_v_given_d = (tot[:, 1:].sum(axis=1) / tot.sum(axis=1)).tolist()
    p_d_given_v = (tot[1:, :].sum(axis=0) / tot.sum(axis=0)).tolist()
    return {
        "dfo_px": EL, "viirs_px": EV, "both_px": B, "dfo_only_px": EL - B, "viirs_only_px": EV - B,
        "jaccard": ratio(B, U), "share_of_dfo_in_viirs": ratio(B, EL),
        "share_of_viirs_in_dfo": ratio(B, EV), "viirs_to_dfo_extent": ratio(EV, EL),
        "on_plain": {"jaccard": ratio(Bp, Up), "share_of_dfo_in_viirs": ratio(Bp, ELp),
                     "share_of_viirs_in_dfo": ratio(Bp, EVp)},
        "off_plain": {"jaccard": ratio(B - Bp, U - Up),
                      "share_of_dfo_in_viirs": ratio(B - Bp, EL - ELp),
                      "share_of_viirs_in_dfo": ratio(B - Bp, EV - EVp)},
        "viirs_rate_given_dfo_days": p_v_given_d, "dfo_rate_given_viirs_days": p_d_given_v,
        "dfo_days_px": tot.sum(axis=1).tolist(), "viirs_days_px": tot.sum(axis=0).tolist(),
        "domain_px": D, "plain_px": P,
    }


def predictors(c, L, k):
    """Inside regions flooded by both products: is VIIRS water better located by
    the DFO footprint or by the floodplain, and vice versa?"""
    D, P = block(c["dom"], k), block(c["plain"], k)
    EL, EV, B = block(c[f"ever_{L}"], k), block(c["ever_V"], k), block(c[f"both_{L}"], k)
    ELp, EVp = block(c[f"ever_plain_{L}"], k), block(c["ever_plain_V"], k)
    s = (EL > 0) & (EV > 0) & (P > 0)
    d, p, el, ev, b, elp, evp = (x[s].sum() for x in (D, P, EL, EV, B, ELp, EVp))
    return {
        "regions": int(s.sum()), "plain_share_of_land": ratio(p, d),
        "dfo_share_of_land": ratio(el, d), "viirs_share_of_land": ratio(ev, d),
        "viirs_base_rate": ratio(ev, d),
        "viirs_rate_on_dfo": ratio(b, el), "viirs_rate_on_plain": ratio(evp, p),
        "viirs_lift_dfo": ratio(ratio(b, el), ratio(ev, d)),
        "viirs_lift_plain": ratio(ratio(evp, p), ratio(ev, d)),
        "viirs_captured_by_dfo": ratio(b, ev), "viirs_captured_by_plain": ratio(evp, ev),
        "dfo_base_rate": ratio(el, d),
        "dfo_rate_on_viirs": ratio(b, ev), "dfo_rate_on_plain": ratio(elp, p),
        "dfo_lift_viirs": ratio(ratio(b, ev), ratio(el, d)),
        "dfo_lift_plain": ratio(ratio(elp, p), ratio(el, d)),
        "dfo_captured_by_viirs": ratio(b, el), "dfo_captured_by_plain": ratio(elp, el),
    }


def cell_compare(cd, cv, L, k, min_frac=0.1, nominal_d=48 * 48, nominal_v=(0.1 / 0.003372) ** 2):
    """DFO (native) vs VIIRS (native) at k x 0.1 degree cells."""
    Dd, Dv = block(cd["dom"], k), block(cv["dom"], k)
    Ed, Ev = block(cd[f"ever_{L}"], k), block(cv["ever_V"], k)
    Fd, Fv = block(cd[f"freq_{L}"], k), block(cv["freq_V"], k)
    Pd = block(cd["plain"], k)
    use = (Dd >= min_frac * nominal_d * k * k) & (Dv >= min_frac * nominal_v * k * k)
    fd, fv = Ed[use] / Dd[use], Ev[use] / Dv[use]
    md, mv = Fd[use] / Dd[use], Fv[use] / Dv[use]
    pl = Pd[use] / Dd[use]
    any_d, any_v = fd > 0, fv > 0
    out = {"cells": int(use.sum())}
    for name, (a, b) in {"any": (any_d, any_v),
                         "ge_1pct": (fd >= 0.01, fv >= 0.01),
                         "ge_5pct": (fd >= 0.05, fv >= 0.05)}.items():
        both, either = (a & b).sum(), (a | b).sum()
        out[f"flag_{name}"] = {"dfo_cells": int(a.sum()), "viirs_cells": int(b.sum()),
                               "both": int(both), "jaccard": ratio(both, either),
                               "share_of_dfo_cells_in_viirs": ratio(both, a.sum()),
                               "share_of_viirs_cells_in_dfo": ratio(both, b.sum())}
    either = any_d | any_v
    both = any_d & any_v
    out["extent_fraction_corr_either"] = corr(fd[either], fv[either])
    out["extent_fraction_corr_both"] = corr(np.log10(fd[both]), np.log10(fv[both]))
    out["mean_daily_fraction_corr_either"] = corr(md[either], mv[either])
    out["median_viirs_over_dfo_extent_both"] = float(np.median(fv[both] / fd[both])) if both.any() else None
    out["plain_fraction_corr_with_dfo"] = corr(pl, fd)
    out["plain_fraction_corr_with_viirs"] = corr(pl, fv)
    # how much of each product's flood pixels sits in cells the other also flagged
    out["share_of_dfo_px_in_viirs_cells"] = ratio((fd * Dd[use])[any_v].sum(), (fd * Dd[use]).sum())
    out["share_of_viirs_px_in_dfo_cells"] = ratio((fv * Dv[use])[any_d].sum(), (fv * Dv[use]).sum())
    return out


def watershed_compare(cd, cv, pfaf, L):
    ids = pfaf.ravel()
    keep = ids > 0
    inv = np.unique(ids[keep], return_inverse=True)[1]

    def agg(a):
        return np.bincount(inv, weights=a.ravel()[keep])

    Dd, Dv = agg(cd["dom"]), agg(cv["dom"])
    Ed, Ev = agg(cd[f"ever_{L}"]), agg(cv["ever_V"])
    Fd, Fv = agg(cd[f"freq_{L}"]), agg(cv["freq_V"])
    use = (Dd > 48 * 48) & (Dv > 880)
    fd, fv = Ed[use] / Dd[use], Ev[use] / Dv[use]
    md, mv = Fd[use] / Dd[use], Fv[use] / Dv[use]
    a, b = fd > 0, fv > 0
    either, both = a | b, a & b
    return {
        "watersheds": int(use.sum()), "dfo_flagged": int(a.sum()), "viirs_flagged": int(b.sum()),
        "both": int(both.sum()), "jaccard": ratio(both.sum(), either.sum()),
        "share_of_dfo_ws_in_viirs": ratio(both.sum(), a.sum()),
        "share_of_viirs_ws_in_dfo": ratio(both.sum(), b.sum()),
        "extent_corr_either": corr(fd[either], fv[either]),
        "mean_daily_corr_either": corr(md[either], mv[either]),
        "log_extent_corr_both": corr(np.log10(fd[both]), np.log10(fv[both])),
        "median_viirs_over_dfo_extent_both": float(np.median(fv[both] / fd[both])) if both.any() else None,
        "top100_overlap_mean_daily": (
            len(set(np.argsort(-md)[:100]) & set(np.argsort(-mv)[:100])) / 100.0),
    }


# --------------------------------------------------------------------------- #
def fmt_pct(x, d=2):
    return "n/a" if x is None or x != x else f"{x * 100:.{d}f}%"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dfo-grid", required=True)
    ap.add_argument("--viirs-grid", required=True)
    ap.add_argument("--pfaf", default=None, help="0.1 degree raster of watershed ids on the pass frame")
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()

    grids = {}
    for name, path in (("dfo_grid", args.dfo_grid), ("viirs_grid", args.viirs_grid)):
        c, h, info = load(path)
        grids[name] = (c, h, info)

    result = {"grids": {}}
    for gname, (c, h, info) in grids.items():
        nominal = (0.1 / info["res"]) ** 2
        g = {"info": info, "layers": {}, "cross": {}, "predictors": {}}
        for L in ("S", "R", "A", "V"):
            g["layers"][L] = {
                "name": LAYER_NAMES[L],
                "overall": plain_overlap(c, L),
                "by_latitude": by_latitude(c, L, info),
                "dose_response": dose_response(c, L, 0.5 * nominal),
                "within_flooded_regions": {lab: within_flooded_regions(c, L, k) for k, lab in SCALES},
            }
        for L in ("S", "R", "A"):
            g["cross"][L] = cross(c, h, L)
            g["predictors"][L] = {lab: predictors(c, L, k) for k, lab in SCALES}
        result["grids"][gname] = g

    cd, cv = grids["dfo_grid"][0], grids["viirs_grid"][0]
    result["native_cells"] = {L: {lab: cell_compare(cd, cv, L, k) for k, lab in SCALES}
                              for L in ("S", "R", "A")}
    if args.pfaf:
        import rasterio
        pf = rasterio.open(args.pfaf).read(1)
        result["watersheds"] = {L: watershed_compare(cd, cv, pf, L) for L in ("S", "R", "A")}

    with open(args.out_json, "w") as fh:
        json.dump(result, fh, indent=1, default=float)
    print("wrote", args.out_json)


if __name__ == "__main__":
    main()
