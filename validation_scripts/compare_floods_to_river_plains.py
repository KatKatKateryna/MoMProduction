#!/usr/bin/env python
"""Do river floodplains coincide with where floods actually happen?

Compares the bands of a flood occurrence accumulation (for DFO: band 1 = count
of class 3 "unusual flood", band 2 = count of class 2 "recurring flood") with
the binary GFPLAIN250m river-floodplain mask, both on the same grid.

Analysis domain
---------------
Three things would bias a naive global comparison and are excluded:

  * areas GFPLAIN never examined. The source encodes only floodplain (0)
    against one "everything else" value (255), so non-floodplain land cannot be
    told from ocean; what it does give is the six continental tile footprints,
    and only pixels inside those boxes are counted.
  * rows only some source rasters observed, because the MCDWD products
    alternate between 80N..60S and 70N..50S extents, so a pixel's flood count is
    only comparable where every raster looked.
  * nothing else: the domain is the intersection of those two.

Because the floodplain mask is binary there is no per-pixel "coverage" to bin
on. The dose-response is therefore measured where it is actually meaningful,
at 0.1 degree cells: what fraction of a cell is floodplain against what
fraction of it flooded.

Outputs, all written next to the inputs:

  flood_vs_river_plains_report.json   every statistic computed
  flood_vs_river_plains_report.md     the readable answer
  flood_vs_river_plains_response.csv  flood extent per floodplain-fraction bin
  flood_vs_river_plains_categories.tiff
                                      uint8, 0 neither / 1 floodplain only /
                                      2 flood only / 3 both, for band 1
  flood_vs_river_plains_global.png     0.1 degree RGB composite
  flood_vs_river_plains_aggregates_0p1deg.npz   the cell aggregates
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import Window

DOWNSAMPLE = 48  # 1/480 deg -> 0.1 deg cells

# Bins of "what fraction of this 0.1 degree cell is floodplain".
FRACTION_EDGES = [0.0, 0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 1.01]
FRACTION_LABELS = ["0%", ">0-1%", ">1-5%", ">5-10%", ">10-25%", ">25-50%",
                   ">50-75%", ">75-100%"]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
def observed_rows(accum_meta: dict, height: int) -> tuple[int, int]:
    lo, hi = 0, height
    for entry in accum_meta["processed"]:
        geom = entry["source_geometry"]
        lo = max(lo, geom["row_offset"])
        hi = min(hi, geom["row_offset"] + geom["height"])
    return lo, hi


def observation_groups(accum_meta: dict) -> list[tuple[int, int, int, int, int]]:
    """Distinct source footprints and how many rasters had each.

    The products do not all cover the same area: MCDWD alternates between two
    latitude extents and a quarter of the VIIRS files are truncated in width.
    A pixel's raw count is therefore not comparable across the grid unless it
    is divided by how many rasters actually looked at it.
    """
    tally: dict[tuple[int, int, int, int], int] = {}
    for entry in accum_meta["processed"]:
        geom = entry["source_geometry"]
        key = (geom["row_offset"], geom["row_offset"] + geom["height"],
               geom["col_offset"], geom["col_offset"] + geom["width"])
        tally[key] = tally.get(key, 0) + 1
    return [(*key, n) for key, n in tally.items()]


def observation_window(groups, row, rows, width) -> np.ndarray:
    obs = np.zeros((rows, width), dtype=np.uint16)
    for r0, r1, c0, c1, n in groups:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            obs[a - row:b - row, c0:c1] += n
    return obs


def footprint_pixels(boxes, top, left, res, width, height):
    """Continental footprints as inclusive-exclusive pixel boxes on the grid."""
    out = []
    for box in boxes:
        r0 = max(0, int(np.floor((top - box["north"]) / res)))
        r1 = min(height, int(np.ceil((top - box["south"]) / res)))
        c0 = max(0, int(np.floor((box["west"] - left) / res)))
        c1 = min(width, int(np.ceil((box["east"] - left) / res)))
        if r1 > r0 and c1 > c0:
            out.append((r0, r1, c0, c1, box["continent"]))
    return out


def domain_window(boxes_px, row, rows, width) -> np.ndarray:
    """Boolean mask of the pixels inside any continental footprint."""
    dom = np.zeros((rows, width), dtype=bool)
    for r0, r1, c0, c1, _ in boxes_px:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            dom[a - row:b - row, c0:c1] = True
    return dom


# --------------------------------------------------------------------------- #
def analyse(args) -> dict:
    accum_meta = json.load(open(args.accum_meta))
    mask_meta = json.load(open(args.mask_meta))

    with rasterio.open(args.accum) as acc, rasterio.open(args.mask) as msk:
        if (acc.width, acc.height) != (msk.width, msk.height):
            sys.exit(f"ERROR: grids differ: {acc.shape} vs {msk.shape}")
        height, width, nbands = acc.height, acc.width, acc.count
        top, left, res = acc.transform.f, acc.transform.c, acc.transform.a

        boxes_px = footprint_pixels(mask_meta["continent_footprints"], top, left, res,
                                    width, height)
        fp_lo = min(b[0] for b in boxes_px)
        fp_hi = max(b[1] for b in boxes_px)
        groups = observation_groups(accum_meta)
        max_obs = max(
            sum(n for r0, r1, c0, c1, n in groups if r0 <= r < r1 and c0 <= 0 < c1)
            for r in {g[0] for g in groups}
        ) if groups else 0
        max_obs = max(max_obs, accum_meta["rasters_processed"])
        min_obs = int(np.ceil(args.min_observations * max_obs))
        log(f"observation footprints: {len(groups)} distinct, max {max_obs} rasters, "
            f"requiring at least {min_obs} per pixel")
        obs_lo = min(g[0] for g in groups)
        obs_hi = max(g[1] for g in groups)
        row_lo, row_hi = max(obs_lo, fp_lo), min(obs_hi, fp_hi)
        row_lo = -(-row_lo // DOWNSAMPLE) * DOWNSAMPLE
        row_hi = (row_hi // DOWNSAMPLE) * DOWNSAMPLE
        n_rasters = accum_meta["rasters_processed"]

        log(f"grid {width}x{height}, {n_rasters} rasters accumulated")
        log(f"rows observed by every raster : {obs_lo}..{obs_hi}")
        log(f"rows inside GFPLAIN footprints: {fp_lo}..{fp_hi}")
        log(f"analysis rows                 : {row_lo}..{row_hi} "
            f"(lat {top - row_hi * res:.2f}..{top - row_lo * res:.2f})")

        comp_rows = (row_hi - row_lo) // DOWNSAMPLE
        comp_cols = width // DOWNSAMPLE
        comp = {key: np.zeros((comp_rows, comp_cols), np.float32)
                for key in ("domain", "plain", "flood1", "flood2")}

        domain_px = 0
        plain_px = 0
        flood_px = [0] * nbands
        flood_on_plain = [0] * nbands
        count_sum_on = [0.0] * nbands
        count_sum_off = [0.0] * nbands
        freq_sum_on = [0.0] * nbands
        freq_sum_off = [0.0] * nbands
        lat_stats: dict[int, dict] = {}

        cat_profile = {
            "driver": "GTiff", "width": width, "height": height, "count": 1,
            "dtype": "uint8", "crs": acc.crs, "transform": acc.transform, "nodata": None,
            "tiled": True, "blockxsize": 256, "blockysize": 256,
            "compress": "lzw", "BIGTIFF": "YES",
        }

        window_rows = DOWNSAMPLE * args.window_blocks
        with rasterio.open(args.categories, "w", **cat_profile) as cat_ds:
            cat_ds.descriptions = ("0 neither, 1 floodplain only, 2 flood only, 3 both",)
            total_windows = (row_hi - row_lo + window_rows - 1) // window_rows
            for index, row in enumerate(range(row_lo, row_hi, window_rows), start=1):
                rows = min(window_rows, row_hi - row)
                window = Window(0, row, width, rows)

                obs = observation_window(groups, row, rows, width)
                dom = domain_window(boxes_px, row, rows, width) & (obs >= min_obs)
                safe_obs = np.maximum(obs, 1)
                is_plain = (msk.read(1, window=window) == 1) & dom
                domain_px += int(dom.sum())
                plain_px += int(is_plain.sum())

                centre_lat = top - (row + rows / 2.0) * res
                lat_key = int(np.floor(centre_lat / 10.0) * 10)
                entry = lat_stats.setdefault(
                    lat_key,
                    {"pixels": 0, "floodplain": 0,
                     "flood": [0] * nbands, "flood_on_floodplain": [0] * nbands},
                )
                entry["pixels"] += int(dom.sum())
                entry["floodplain"] += int(is_plain.sum())

                blocks = rows // DOWNSAMPLE
                out_row = (row - row_lo) // DOWNSAMPLE
                # The grid width need not divide by the block size (VIIRS is
                # 106761 wide), so drop the ragged remainder columns.
                keep_cols = comp_cols * DOWNSAMPLE
                if blocks:
                    comp["domain"][out_row:out_row + blocks] = dom[
                        : blocks * DOWNSAMPLE, :keep_cols].reshape(
                        blocks, DOWNSAMPLE, comp_cols, DOWNSAMPLE).sum(axis=(1, 3))
                    comp["plain"][out_row:out_row + blocks] = is_plain[
                        : blocks * DOWNSAMPLE, :keep_cols].reshape(
                        blocks, DOWNSAMPLE, comp_cols, DOWNSAMPLE).sum(axis=(1, 3))

                for band in range(nbands):
                    counts = acc.read(band + 1, window=window)
                    has = (counts > 0) & dom
                    on = has & is_plain
                    flood_px[band] += int(has.sum())
                    flood_on_plain[band] += int(on.sum())
                    off_mask = dom & ~is_plain
                    count_sum_on[band] += float(counts[is_plain].sum())
                    count_sum_off[band] += float(counts[off_mask].sum())
                    # Frequency = days flooded / days observed, which stays
                    # comparable where the source footprints differ.
                    freq = counts / safe_obs
                    freq_sum_on[band] += float(freq[is_plain].sum())
                    freq_sum_off[band] += float(freq[off_mask].sum())
                    del freq, off_mask
                    entry["flood"][band] += int(has.sum())
                    entry["flood_on_floodplain"][band] += int(on.sum())

                    if band == 0:
                        cat_ds.write(
                            (is_plain.astype(np.uint8) + 2 * has.astype(np.uint8)),
                            1, window=window,
                        )
                    if blocks:
                        comp[f"flood{band + 1}"][out_row:out_row + blocks] = has[
                            : blocks * DOWNSAMPLE, :keep_cols].reshape(
                            blocks, DOWNSAMPLE, comp_cols, DOWNSAMPLE).sum(axis=(1, 3))
                    del counts, has, on

                del dom, is_plain
                if index % 25 == 0 or index == total_windows:
                    log(f"  window {index}/{total_windows}")

    off_px = domain_px - plain_px
    result = {
        "inputs": {
            "accumulation": os.path.basename(args.accum),
            "floodplain_mask": os.path.basename(args.mask),
            "floodplain_source": mask_meta.get("source_dataset"),
            "floodplain_doi": mask_meta.get("source_doi"),
            "rasters_accumulated": n_rasters,
            "date_range": accum_meta.get("date_range_requested"),
        },
        "domain": {
            "rows": [row_lo, row_hi],
            "lat_north": round(top - row_lo * res, 4),
            "lat_south": round(top - row_hi * res, 4),
            "pixels": domain_px,
            "note": ("pixels inside the GFPLAIN continental footprints and inside the "
                     "rows every source raster observed"),
        },
        "floodplain": {
            "definition": "GFPLAIN250m floodplain, binary",
            "pixels": plain_px,
            "fraction_of_domain": plain_px / domain_px if domain_px else 0.0,
        },
        "bands": [],
    }

    for band in range(nbands):
        info = accum_meta["bands"][band]
        on_n, all_n = flood_on_plain[band], flood_px[band]
        off_n = all_n - on_n
        p_in = on_n / plain_px if plain_px else 0.0
        p_out = off_n / off_px if off_px else 0.0
        odds_in = p_in / (1 - p_in) if 0 < p_in < 1 else float("nan")
        odds_out = p_out / (1 - p_out) if 0 < p_out < 1 else float("nan")
        mean_on = count_sum_on[band] / plain_px if plain_px else 0.0
        mean_off = count_sum_off[band] / off_px if off_px else 0.0
        union = plain_px + all_n - on_n
        result["bands"].append({
            "band": band + 1,
            "source_value": info.get("source_value", info.get("values")),
            "source_class": info.get("source_class", info.get("meaning", "")),
            "flood_pixels_in_domain": all_n,
            "flood_fraction_of_domain": all_n / domain_px if domain_px else 0.0,
            "flood_pixels_on_floodplain": on_n,
            "share_of_flood_on_floodplain": on_n / all_n if all_n else 0.0,
            "frequency_on_floodplain": p_in,
            "frequency_off_floodplain": p_out,
            "enrichment_ratio": (p_in / p_out) if p_out else float("inf"),
            "odds_ratio": (odds_in / odds_out) if odds_out else float("inf"),
            "jaccard_with_floodplain": on_n / union if union else 0.0,
            "mean_count_on_floodplain": mean_on,
            "mean_count_off_floodplain": mean_off,
            "mean_count_ratio": (mean_on / mean_off) if mean_off else float("inf"),
            "mean_frequency_on_floodplain": (
                freq_sum_on[band] / plain_px if plain_px else 0.0),
            "mean_frequency_off_floodplain": (
                freq_sum_off[band] / off_px if off_px else 0.0),
            "mean_frequency_ratio": (
                (freq_sum_on[band] / plain_px) / (freq_sum_off[band] / off_px)
                if plain_px and off_px and freq_sum_off[band] else float("inf")),
        })

    result["by_latitude"] = []
    for key in sorted(lat_stats, reverse=True):
        entry = lat_stats[key]
        px, plain_n = entry["pixels"], entry["floodplain"]
        if px == 0:
            continue
        off_n = px - plain_n
        row = {"lat_south": key, "lat_north": key + 10, "pixels": px,
               "floodplain_fraction": plain_n / px, "bands": []}
        for band in range(nbands):
            fn, on_n = entry["flood"][band], entry["flood_on_floodplain"][band]
            freq_on = on_n / plain_n if plain_n else 0.0
            freq_off = (fn - on_n) / off_n if off_n else 0.0
            row["bands"].append({
                "band": band + 1,
                "flood_pixels": fn,
                "share_of_global_flood": fn / flood_px[band] if flood_px[band] else 0.0,
                "share_on_floodplain": on_n / fn if fn else 0.0,
                "frequency_on_floodplain": freq_on,
                "frequency_off_floodplain": freq_off,
                "enrichment_ratio": (freq_on / freq_off) if freq_off else float("inf"),
            })
        result["by_latitude"].append(row)

    # Cell-level dose-response and correlation.
    np.savez_compressed(args.aggregates, row_lo=row_lo, row_hi=row_hi,
                        downsample=DOWNSAMPLE, **comp)
    log(f"cached 0.1 degree aggregates to {os.path.basename(args.aggregates)}")

    dom_cell = comp["domain"]
    usable = dom_cell >= (DOWNSAMPLE * DOWNSAMPLE) * args.min_cell_domain
    plain_frac = np.divide(comp["plain"], dom_cell, out=np.zeros_like(dom_cell),
                           where=dom_cell > 0)
    bin_index = np.digitize(plain_frac, FRACTION_EDGES[1:-1], right=True)

    for band in range(nbands):
        flood_frac = np.divide(comp[f"flood{band + 1}"], dom_cell,
                               out=np.zeros_like(dom_cell), where=dom_cell > 0)
        response = []
        for b, label in enumerate(FRACTION_LABELS):
            sel = usable & (bin_index == b)
            n = int(sel.sum())
            response.append({
                "floodplain_fraction_bin": label,
                "cells": n,
                "mean_flooded_fraction": float(flood_frac[sel].mean()) if n else 0.0,
                "cells_with_any_flood": int((flood_frac[sel] > 0).sum()) if n else 0,
            })
        result["bands"][band]["cell_response"] = response

        x = plain_frac[usable].ravel()
        y = flood_frac[usable].ravel()
        pick = (x > 0) | (y > 0)
        if pick.sum() > 1:
            xs, ys = x[pick], y[pick]
            pearson = float(np.corrcoef(xs, ys)[0, 1])
            spearman = float(np.corrcoef(avg_rank(xs), avg_rank(ys))[0, 1])
        else:
            pearson = spearman = float("nan")
        result["bands"][band]["cell_0p1deg_correlation"] = {
            "pearson": pearson, "spearman": spearman, "cells_considered": int(pick.sum()),
            "note": ("floodplain fraction vs flooded fraction of each 0.1 degree cell, "
                     "over in-domain cells where either occurs"),
        }

    write_composite(comp, args.composite)
    return result


def avg_rank(a: np.ndarray) -> np.ndarray:
    """Ranks with ties averaged; plain argsort would order ties arbitrarily."""
    values, inverse, counts = np.unique(a, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return (((ends - counts + 1) + ends) / 2.0)[inverse]


# --------------------------------------------------------------------------- #
def write_composite(comp: dict, path: str) -> None:
    def stretch(values, reference, percentile=99.0, gamma=0.6):
        frac = np.divide(values, reference, out=np.zeros_like(values), where=reference > 0)
        positive = frac[frac > 0]
        top = np.percentile(positive, percentile) if positive.size else 1.0
        if top <= 0:
            top = 1.0
        return (np.clip(frac / top, 0, 1) ** gamma * 255).astype(np.uint8)

    dom = comp["domain"]
    rgb = np.stack([stretch(comp["flood1"], dom), stretch(comp["plain"], dom),
                    stretch(comp["flood2"], dom)])
    with rasterio.open(path, "w", driver="PNG", width=rgb.shape[2], height=rgb.shape[1],
                       count=3, dtype="uint8") as dst:
        dst.write(rgb)
    log(f"wrote {os.path.basename(path)} ({rgb.shape[2]}x{rgb.shape[1]})")


# --------------------------------------------------------------------------- #
def write_report(result: dict, md_path: str, csv_path: str) -> None:
    with open(csv_path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["band", "source_value", "floodplain_fraction_bin", "cells",
                         "mean_flooded_fraction", "cells_with_any_flood"])
        for band in result["bands"]:
            for row in band["cell_response"]:
                writer.writerow([band["band"], band["source_value"],
                                 row["floodplain_fraction_bin"], row["cells"],
                                 f"{row['mean_flooded_fraction']:.6g}",
                                 row["cells_with_any_flood"]])

    dom, fp = result["domain"], result["floodplain"]
    lines = [
        "# Do river floodplains coincide with observed floods?",
        "",
        f"Accumulation: {result['inputs']['rasters_accumulated']} daily rasters "
        f"({result['inputs']['date_range']['start']}..{result['inputs']['date_range']['end']}), "
        f"{result['inputs']['accumulation']}.",
        f"Floodplains: {result['inputs']['floodplain_source']}, binary mask "
        f"({result['inputs']['floodplain_doi']}).",
        "",
        f"Domain: {dom['pixels']:,} pixels, latitude {dom['lat_south']} to "
        f"{dom['lat_north']}. {dom['note']}.",
        f"Floodplain is {fp['fraction_of_domain'] * 100:.2f}% of that domain "
        f"({fp['pixels']:,} pixels).",
        "",
    ]

    for band in result["bands"]:
        lines += [
            f"## Band {band['band']}: {band['source_value']} - {band['source_class']}",
            "",
            f"- flooded at least once: {band['flood_pixels_in_domain']:,} pixels "
            f"({band['flood_fraction_of_domain'] * 100:.3f}% of the domain)",
            f"- of those, {band['share_of_flood_on_floodplain'] * 100:.1f}% are on floodplain",
            f"- frequency on floodplain: {band['frequency_on_floodplain'] * 100:.3f}%, "
            f"off floodplain: {band['frequency_off_floodplain'] * 100:.3f}%",
            f"- enrichment {band['enrichment_ratio']:.1f}x, "
            f"odds ratio {band['odds_ratio']:.1f}",
            f"- mean flood count {band['mean_count_on_floodplain']:.4f} on vs "
            f"{band['mean_count_off_floodplain']:.4f} off "
            f"({band['mean_count_ratio']:.1f}x)",
            f"- mean flooded fraction of observed days "
            f"{band['mean_frequency_on_floodplain'] * 100:.4f}% on vs "
            f"{band['mean_frequency_off_floodplain'] * 100:.4f}% off "
            f"({band['mean_frequency_ratio']:.1f}x)",
            f"- Jaccard overlap {band['jaccard_with_floodplain']:.4f}",
            f"- 0.1 degree cell correlation: Pearson "
            f"{band['cell_0p1deg_correlation']['pearson']:.3f}, Spearman "
            f"{band['cell_0p1deg_correlation']['spearman']:.3f}",
            "",
            "Flooded fraction of a 0.1 degree cell, by how much of the cell is floodplain:",
            "",
            "| floodplain of cell | cells | mean flooded fraction | cells with any flood |",
            "| --- | --- | --- | --- |",
        ]
        for row in band["cell_response"]:
            lines.append(
                f"| {row['floodplain_fraction_bin']} | {row['cells']:,} | "
                f"{row['mean_flooded_fraction'] * 100:.4f}% | "
                f"{row['cells_with_any_flood']:,} |"
            )
        lines.append("")

    for band_index in range(len(result["bands"])):
        value = result["bands"][band_index]["source_value"]
        lines += [
            f"## Band {band_index + 1} ({value}) by latitude",
            "",
            "| latitude | floodplain of band | share of all flood here | on floodplain "
            "| freq on | freq off | enrichment |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in result["by_latitude"]:
            b = row["bands"][band_index]
            lines.append(
                f"| {row['lat_south']}..{row['lat_north']} | "
                f"{row['floodplain_fraction'] * 100:.2f}% | "
                f"{b['share_of_global_flood'] * 100:.1f}% | "
                f"{b['share_on_floodplain'] * 100:.1f}% | "
                f"{b['frequency_on_floodplain'] * 100:.3f}% | "
                f"{b['frequency_off_floodplain'] * 100:.3f}% | "
                f"{b['enrichment_ratio']:.1f}x |"
            )
        lines.append("")

    with open(md_path, "w") as handle:
        handle.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        description="Compare accumulated flood occurrence with river floodplains.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--dir", default=os.path.join(here, "dfo_accumulation"))
    parser.add_argument("--accum", default=None)
    parser.add_argument("--accum-meta", default=None)
    parser.add_argument("--mask", default=None)
    parser.add_argument("--mask-meta", default=None)
    parser.add_argument("--prefix", default="flood_vs_river_plains")
    parser.add_argument("--window-blocks", type=int, default=2,
                        help=f"window height in units of {DOWNSAMPLE} rows")
    parser.add_argument("--min-cell-domain", type=float, default=0.5,
                        help="minimum share of a 0.1 degree cell inside the domain to use it")
    parser.add_argument("--min-observations", type=float, default=0.8,
                        help="minimum share of the maximum raster count a pixel must have "
                             "been observed by to enter the domain")
    args = parser.parse_args()

    args.accum = args.accum or os.path.join(args.dir, "dfo_accumulater.tiff")
    args.accum_meta = args.accum_meta or f"{os.path.splitext(args.accum)[0]}_metadata.json"
    args.mask = args.mask or os.path.join(args.dir, "river_plains_mask.tiff")
    args.mask_meta = args.mask_meta or f"{os.path.splitext(args.mask)[0]}_metadata.json"
    args.categories = os.path.join(args.dir, f"{args.prefix}_categories.tiff")
    args.composite = os.path.join(args.dir, f"{args.prefix}_global.png")
    args.aggregates = os.path.join(args.dir, f"{args.prefix}_aggregates_0p1deg.npz")

    for path in (args.accum, args.accum_meta, args.mask, args.mask_meta):
        if not os.path.exists(path):
            sys.exit(f"ERROR: missing input {path}")

    result = analyse(args)
    result["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with open(os.path.join(args.dir, f"{args.prefix}_report.json"), "w") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    write_report(result, os.path.join(args.dir, f"{args.prefix}_report.md"),
                 os.path.join(args.dir, f"{args.prefix}_response.csv"))
    log("wrote report json, md and csv")

    for band in result["bands"]:
        log(f"band {band['band']} ({band['source_value']}): "
            f"{band['share_of_flood_on_floodplain'] * 100:.1f}% of flooded pixels on "
            f"floodplain, enrichment {band['enrichment_ratio']:.1f}x, "
            f"Spearman {band['cell_0p1deg_correlation']['spearman']:.3f}")


if __name__ == "__main__":
    main()
