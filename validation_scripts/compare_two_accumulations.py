#!/usr/bin/env python
"""Compare two flood accumulations with each other, and each against floodplains.

DFO (MCDWD, 1/480 degree from 80N) and VIIRS (0.003372 degree from 75N) sit on
grids where neither divides into the other, so one has to be resampled. The
comparison is done on the finer of the two, the DFO grid, with the coarser
product upsampled by nearest neighbour: that replicates values rather than
blending them, so no counts are invented and the finer product is never
degraded.

Both products also observe different areas on different days, so raw counts are
not comparable across the grid. Each is converted to a frequency, the share of
the days that actually observed a pixel on which that pixel was flagged, using
the per-raster footprints recorded in each accumulation's metadata.

Reported for the shared domain:

  * how far the two products agree on where flooding happened at all
  * how their frequencies correlate
  * each product's enrichment on river floodplains, measured identically
  * the same split by latitude, where the products are known to behave
    differently

Outputs are written next to the first accumulation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import Window

DOWNSAMPLE = 48


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def avg_rank(a: np.ndarray) -> np.ndarray:
    values, inverse, counts = np.unique(a, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return (((ends - counts + 1) + ends) / 2.0)[inverse]


def footprints_on_target(meta: dict, target: dict) -> list[tuple[int, int, int, int, int]]:
    """Per-raster footprints, expressed in the target grid's pixels.

    Footprints are axis-aligned boxes in the source grid, and both grids are
    plain lat/lon, so the boxes stay boxes; only the edges have to be converted
    through geographic coordinates.
    """
    src = meta["target_grid"]
    tally: dict[tuple[int, int, int, int], int] = {}
    for entry in meta["processed"]:
        g = entry["source_geometry"]
        north = src["top"] - g["row_offset"] * src["res"]
        south = src["top"] - (g["row_offset"] + g["height"]) * src["res"]
        west = src["left"] + g["col_offset"] * src["res"]
        east = src["left"] + (g["col_offset"] + g["width"]) * src["res"]
        r0 = max(0, int(round((target["top"] - north) / target["res"])))
        r1 = min(target["height"], int(round((target["top"] - south) / target["res"])))
        c0 = max(0, int(round((west - target["left"]) / target["res"])))
        c1 = min(target["width"], int(round((east - target["left"]) / target["res"])))
        if r1 > r0 and c1 > c0:
            key = (r0, r1, c0, c1)
            tally[key] = tally.get(key, 0) + 1
    return [(*key, n) for key, n in tally.items()]


def observation_window(groups, row, rows, width) -> np.ndarray:
    obs = np.zeros((rows, width), dtype=np.uint16)
    for r0, r1, c0, c1, n in groups:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            obs[a - row:b - row, c0:c1] += n
    return obs


def mask_footprints(mask_meta, target):
    out = []
    for box in mask_meta["continent_footprints"]:
        r0 = max(0, int(np.floor((target["top"] - box["north"]) / target["res"])))
        r1 = min(target["height"],
                 int(np.ceil((target["top"] - box["south"]) / target["res"])))
        c0 = max(0, int(np.floor((box["west"] - target["left"]) / target["res"])))
        c1 = min(target["width"],
                 int(np.ceil((box["east"] - target["left"]) / target["res"])))
        if r1 > r0 and c1 > c0:
            out.append((r0, r1, c0, c1))
    return out


def domain_window(boxes, row, rows, width) -> np.ndarray:
    dom = np.zeros((rows, width), dtype=bool)
    for r0, r1, c0, c1 in boxes:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            dom[a - row:b - row, c0:c1] = True
    return dom


# --------------------------------------------------------------------------- #
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", required=True, help="accumulation on the target grid")
    parser.add_argument("--a-meta", default=None)
    parser.add_argument("--a-label", default="A")
    parser.add_argument("--b", required=True, help="other accumulation, already resampled")
    parser.add_argument("--b-meta", default=None, help="metadata of the ORIGINAL b grid")
    parser.add_argument("--b-label", default="B")
    parser.add_argument("--mask", required=True)
    parser.add_argument("--mask-meta", default=None)
    parser.add_argument("--band", type=int, default=1, help="band to compare in both")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prefix", default="dfo_vs_viirs")
    parser.add_argument("--window-blocks", type=int, default=2)
    parser.add_argument("--min-observations", type=float, default=0.8)
    args = parser.parse_args()

    a_meta = json.load(open(args.a_meta or f"{os.path.splitext(args.a)[0]}_metadata.json"))
    b_meta = json.load(open(args.b_meta))
    mask_meta = json.load(open(
        args.mask_meta or f"{os.path.splitext(args.mask)[0]}_metadata.json"))

    with rasterio.open(args.a) as da, rasterio.open(args.b) as db, \
            rasterio.open(args.mask) as dm:
        if not (da.shape == db.shape == dm.shape):
            sys.exit(f"ERROR: grids differ: {da.shape} {db.shape} {dm.shape}")
        height, width = da.height, da.width
        top, left, res = da.transform.f, da.transform.c, da.transform.a
        target = {"top": top, "left": left, "res": res, "width": width, "height": height}

        groups_a = footprints_on_target(a_meta, target)
        groups_b = footprints_on_target(b_meta, target)
        max_a = a_meta["rasters_processed"]
        max_b = b_meta["rasters_processed"]
        min_a = int(np.ceil(args.min_observations * max_a))
        min_b = int(np.ceil(args.min_observations * max_b))
        boxes = mask_footprints(mask_meta, target)
        row_lo = max(min(g[0] for g in groups_a), min(g[0] for g in groups_b),
                     min(b[0] for b in boxes))
        row_hi = min(max(g[1] for g in groups_a), max(g[1] for g in groups_b),
                     max(b[1] for b in boxes))
        row_lo = -(-row_lo // DOWNSAMPLE) * DOWNSAMPLE
        row_hi = (row_hi // DOWNSAMPLE) * DOWNSAMPLE

        log(f"{args.a_label}: {max_a} rasters, {len(groups_a)} footprints, min obs {min_a}")
        log(f"{args.b_label}: {max_b} rasters, {len(groups_b)} footprints, min obs {min_b}")
        log(f"target grid {width}x{height}, rows {row_lo}..{row_hi} "
            f"(lat {top - row_hi * res:.2f}..{top - row_lo * res:.2f})")

        comp_rows = (row_hi - row_lo) // DOWNSAMPLE
        comp_cols = width // DOWNSAMPLE
        keep_cols = comp_cols * DOWNSAMPLE
        comp = {k: np.zeros((comp_rows, comp_cols), np.float32)
                for k in ("domain", "plain", "a", "b")}

        stats = {"domain": 0, "plain": 0, "a_ever": 0, "b_ever": 0, "both": 0,
                 "a_only": 0, "b_only": 0, "a_on_plain": 0, "b_on_plain": 0,
                 "both_on_plain": 0}
        freq_sum = {"a": 0.0, "b": 0.0, "a_on": 0.0, "b_on": 0.0,
                    "a_off": 0.0, "b_off": 0.0}
        lat_rows = {}

        cat_profile = {
            "driver": "GTiff", "width": width, "height": height, "count": 1,
            "dtype": "uint8", "crs": da.crs, "transform": da.transform, "nodata": None,
            "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "lzw", "BIGTIFF": "YES",
        }
        cat_path = os.path.join(args.out_dir, f"{args.prefix}_agreement.tiff")

        window_rows = DOWNSAMPLE * args.window_blocks
        total = (row_hi - row_lo + window_rows - 1) // window_rows
        with rasterio.open(cat_path, "w", **cat_profile) as cat_ds:
            cat_ds.descriptions = (
                f"0 neither, 1 {args.a_label} only, 2 {args.b_label} only, 3 both",)
            for index, row in enumerate(range(row_lo, row_hi, window_rows), start=1):
                rows = min(window_rows, row_hi - row)
                window = Window(0, row, width, rows)

                obs_a = observation_window(groups_a, row, rows, width)
                obs_b = observation_window(groups_b, row, rows, width)
                dom = (domain_window(boxes, row, rows, width)
                       & (obs_a >= min_a) & (obs_b >= min_b))
                plain = (dm.read(1, window=window) == 1) & dom

                ca = da.read(args.band, window=window)
                cb = db.read(args.band, window=window)
                ever_a = (ca > 0) & dom
                ever_b = (cb > 0) & dom
                both = ever_a & ever_b

                fa = ca / np.maximum(obs_a, 1)
                fb = cb / np.maximum(obs_b, 1)
                off = dom & ~plain

                stats["domain"] += int(dom.sum())
                stats["plain"] += int(plain.sum())
                stats["a_ever"] += int(ever_a.sum())
                stats["b_ever"] += int(ever_b.sum())
                stats["both"] += int(both.sum())
                stats["a_only"] += int((ever_a & ~ever_b).sum())
                stats["b_only"] += int((ever_b & ~ever_a).sum())
                stats["a_on_plain"] += int((ever_a & plain).sum())
                stats["b_on_plain"] += int((ever_b & plain).sum())
                stats["both_on_plain"] += int((both & plain).sum())
                freq_sum["a"] += float(fa[dom].sum())
                freq_sum["b"] += float(fb[dom].sum())
                freq_sum["a_on"] += float(fa[plain].sum())
                freq_sum["b_on"] += float(fb[plain].sum())
                freq_sum["a_off"] += float(fa[off].sum())
                freq_sum["b_off"] += float(fb[off].sum())

                key = int(np.floor((top - (row + rows / 2.0) * res) / 10.0) * 10)
                entry = lat_rows.setdefault(key, {"domain": 0, "plain": 0, "a": 0,
                                                  "b": 0, "both": 0})
                entry["domain"] += int(dom.sum())
                entry["plain"] += int(plain.sum())
                entry["a"] += int(ever_a.sum())
                entry["b"] += int(ever_b.sum())
                entry["both"] += int(both.sum())

                cat_ds.write(ever_a.astype(np.uint8) + 2 * ever_b.astype(np.uint8),
                             1, window=window)

                blocks = rows // DOWNSAMPLE
                if blocks:
                    out_row = (row - row_lo) // DOWNSAMPLE
                    for name, arr in (("domain", dom), ("plain", plain),
                                      ("a", ever_a), ("b", ever_b)):
                        comp[name][out_row:out_row + blocks] = arr[
                            : blocks * DOWNSAMPLE, :keep_cols].reshape(
                            blocks, DOWNSAMPLE, comp_cols, DOWNSAMPLE).sum(axis=(1, 3))

                del obs_a, obs_b, dom, plain, ca, cb, ever_a, ever_b, both, fa, fb, off
                if index % 25 == 0 or index == total:
                    log(f"  window {index}/{total}")

    dom_n = stats["domain"]
    plain_n = stats["plain"]
    off_n = dom_n - plain_n
    union = stats["a_ever"] + stats["b_ever"] - stats["both"]

    def enrich(on_count, ever_count):
        p_on = on_count / plain_n if plain_n else 0.0
        p_off = (ever_count - on_count) / off_n if off_n else 0.0
        return p_on, p_off, (p_on / p_off if p_off else float("inf"))

    a_on, a_off, a_enr = enrich(stats["a_on_plain"], stats["a_ever"])
    b_on, b_off, b_enr = enrich(stats["b_on_plain"], stats["b_ever"])

    result = {
        "products": {
            "a": {"label": args.a_label, "rasters": max_a,
                  "date_range": a_meta.get("date_range_requested"),
                  "band": a_meta["bands"][args.band - 1]},
            "b": {"label": args.b_label, "rasters": max_b,
                  "date_range": b_meta.get("date_range_requested"),
                  "band": b_meta["bands"][args.band - 1]},
        },
        "grid": {"width": width, "height": height, "res": res,
                 "note": "the finer of the two grids; the other was upsampled by "
                         "nearest neighbour"},
        "domain": {"pixels": dom_n, "floodplain_pixels": plain_n,
                   "floodplain_fraction": plain_n / dom_n if dom_n else 0.0,
                   "lat_north": round(top - row_lo * res, 4),
                   "lat_south": round(top - row_hi * res, 4)},
        "agreement": {
            f"{args.a_label}_ever": stats["a_ever"],
            f"{args.b_label}_ever": stats["b_ever"],
            "both": stats["both"],
            f"{args.a_label}_only": stats["a_only"],
            f"{args.b_label}_only": stats["b_only"],
            "jaccard": stats["both"] / union if union else 0.0,
            f"share_of_{args.a_label}_also_in_{args.b_label}": (
                stats["both"] / stats["a_ever"] if stats["a_ever"] else 0.0),
            f"share_of_{args.b_label}_also_in_{args.a_label}": (
                stats["both"] / stats["b_ever"] if stats["b_ever"] else 0.0),
        },
        "extent": {
            f"{args.a_label}_fraction_of_domain": (
                stats["a_ever"] / dom_n if dom_n else 0.0),
            f"{args.b_label}_fraction_of_domain": (
                stats["b_ever"] / dom_n if dom_n else 0.0),
            f"{args.a_label}_mean_frequency": freq_sum["a"] / dom_n if dom_n else 0.0,
            f"{args.b_label}_mean_frequency": freq_sum["b"] / dom_n if dom_n else 0.0,
        },
        "floodplain_enrichment": {
            args.a_label: {"frequency_on": a_on, "frequency_off": a_off,
                           "enrichment": a_enr,
                           "share_on_floodplain": (
                               stats["a_on_plain"] / stats["a_ever"]
                               if stats["a_ever"] else 0.0)},
            args.b_label: {"frequency_on": b_on, "frequency_off": b_off,
                           "enrichment": b_enr,
                           "share_on_floodplain": (
                               stats["b_on_plain"] / stats["b_ever"]
                               if stats["b_ever"] else 0.0)},
        },
        "by_latitude": [],
    }

    for key in sorted(lat_rows, reverse=True):
        e = lat_rows[key]
        if not e["domain"]:
            continue
        u = e["a"] + e["b"] - e["both"]
        result["by_latitude"].append({
            "lat_south": key, "lat_north": key + 10, "pixels": e["domain"],
            f"{args.a_label}_fraction": e["a"] / e["domain"],
            f"{args.b_label}_fraction": e["b"] / e["domain"],
            "jaccard": e["both"] / u if u else 0.0,
        })

    # Cell level agreement at 0.1 degree.
    usable = comp["domain"] >= (DOWNSAMPLE * DOWNSAMPLE) * 0.5
    fa = np.divide(comp["a"], comp["domain"], out=np.zeros_like(comp["a"]),
                   where=comp["domain"] > 0)[usable]
    fb = np.divide(comp["b"], comp["domain"], out=np.zeros_like(comp["b"]),
                   where=comp["domain"] > 0)[usable]
    pick = (fa > 0) | (fb > 0)
    if pick.sum() > 1:
        result["cell_0p1deg"] = {
            "cells": int(pick.sum()),
            "pearson": float(np.corrcoef(fa[pick], fb[pick])[0, 1]),
            "spearman": float(np.corrcoef(avg_rank(fa[pick]), avg_rank(fb[pick]))[0, 1]),
        }

    np.savez_compressed(os.path.join(args.out_dir, f"{args.prefix}_cells.npz"), **comp)

    def stretch(values, reference, percentile=99.0, gamma=0.6):
        frac = np.divide(values, reference, out=np.zeros_like(values), where=reference > 0)
        positive = frac[frac > 0]
        topv = np.percentile(positive, percentile) if positive.size else 1.0
        return (np.clip(frac / (topv or 1.0), 0, 1) ** gamma * 255).astype(np.uint8)

    rgb = np.stack([stretch(comp["a"], comp["domain"]),
                    stretch(comp["plain"], comp["domain"]),
                    stretch(comp["b"], comp["domain"])])
    png = os.path.join(args.out_dir, f"{args.prefix}_global.png")
    with rasterio.open(png, "w", driver="PNG", width=rgb.shape[2], height=rgb.shape[1],
                       count=3, dtype="uint8") as dst:
        dst.write(rgb)
    log(f"wrote {os.path.basename(png)}")

    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(args.out_dir, f"{args.prefix}_report.json"), "w") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")

    ag = result["agreement"]
    log(f"{args.a_label} ever flooded: {stats['a_ever']:,}  "
        f"{args.b_label}: {stats['b_ever']:,}  both: {stats['both']:,}")
    log(f"Jaccard {ag['jaccard']:.4f}; "
        f"{ag[f'share_of_{args.a_label}_also_in_{args.b_label}'] * 100:.1f}% of "
        f"{args.a_label} is also in {args.b_label}")
    log(f"enrichment on floodplain: {args.a_label} {a_enr:.1f}x, {args.b_label} {b_enr:.1f}x")


if __name__ == "__main__":
    main()
