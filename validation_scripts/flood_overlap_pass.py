#!/usr/bin/env python
"""One pass over a grid: DFO and VIIRS flood counts against floodplains and land.

Run once on the DFO grid (DFO native, VIIRS resampled onto it) and once on the
VIIRS grid (VIIRS native, DFO resampled onto it). Every pixel is reduced to the
0.1 degree cell containing its centre, so the outputs are cell sums from which
both pixel level totals (sum over cells) and cell level comparisons follow.

Layers
------
    S  DFO band 1, class 3 "flood (unusual)"       -- sudden flood
    R  DFO band 2, class 2 "recurring flood"        -- recurrent flood
    A  S or R                                        -- any DFO flood
    V  VIIRS band 1, values 141-200                  -- MoM's VIIRS flood

Analysis domain (identical for every layer)
-------------------------------------------
    land (rasterised MoM watershed polygons)
    AND inside the GFPLAIN continental footprints
    AND observed by >= 80% of the DFO rasters AND >= 80% of the VIIRS rasters

GFPLAIN codes floodplain against one "everything else" value that includes
the ocean, so without the land mask "off floodplain" would be mostly sea.

Per 0.1 degree cell the .npz holds
    dom, plain                          domain / floodplain pixels
    ever_L, ever_plain_L                pixels flagged at least once (L = S R A V)
    freq_L, freq_plain_L                sum of per-pixel flagged share of observed rasters
    both_L, both_plain_L                pixels flagged by DFO layer L and by V (L = S R A)
plus a pixel histogram hist[L][plain, dfo_days_bin, viirs_days_bin].
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np
import rasterio
from rasterio.windows import Window

GRID_NORTH, GRID_SOUTH, GRID_WEST, CELL = 80.0, -60.0, -180.0, 0.1
NROWS = int(round((GRID_NORTH - GRID_SOUTH) / CELL))
NCOLS = int(round(360.0 / CELL))

# Day-count bins for the DFO x VIIRS pixel histogram.
DAY_EDGES = [0, 1, 2, 5, 10, 30]
DAY_LABELS = ["0", "1", "2-4", "5-9", "10-29", "30+"]
NBIN = len(DAY_EDGES)
DAY_LUT = (np.searchsorted(DAY_EDGES, np.arange(65536), side="right") - 1).astype(np.uint8)

LAYERS = ("S", "R", "A", "V")
DFO_LAYERS = ("S", "R", "A")


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def footprints_on_target(meta: dict, target: dict):
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
            tally[(r0, r1, c0, c1)] = tally.get((r0, r1, c0, c1), 0) + 1
    return [(*k, n) for k, n in tally.items()]


def observation_window(groups, row, rows, width) -> np.ndarray:
    obs = np.zeros((rows, width), dtype=np.uint16)
    for r0, r1, c0, c1, n in groups:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            obs[a - row:b - row, c0:c1] += n
    return obs


def mask_boxes(mask_meta, target):
    out = []
    for box in mask_meta["continent_footprints"]:
        r0 = max(0, int(np.floor((target["top"] - box["north"]) / target["res"])))
        r1 = min(target["height"], int(np.ceil((target["top"] - box["south"]) / target["res"])))
        c0 = max(0, int(np.floor((box["west"] - target["left"]) / target["res"])))
        c1 = min(target["width"], int(np.ceil((box["east"] - target["left"]) / target["res"])))
        if r1 > r0 and c1 > c0:
            out.append((r0, r1, c0, c1))
    return out


def box_window(boxes, row, rows, width) -> np.ndarray:
    dom = np.zeros((rows, width), dtype=bool)
    for r0, r1, c0, c1 in boxes:
        a, b = max(r0, row), min(r1, row + rows)
        if b > a:
            dom[a - row:b - row, c0:c1] = True
    return dom


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--grid-name", required=True)
    p.add_argument("--dfo", required=True, help="DFO counts on this grid (band 1 class 3, band 2 class 2)")
    p.add_argument("--viirs", required=True, help="VIIRS counts on this grid (band 1 = 141-200)")
    p.add_argument("--dfo-meta", required=True, help="metadata of the native DFO accumulation")
    p.add_argument("--viirs-meta", required=True, help="metadata of the native VIIRS accumulation")
    p.add_argument("--plain", required=True, help="GFPLAIN binary mask on this grid")
    p.add_argument("--plain-meta", required=True)
    p.add_argument("--land", required=True, help="land mask on this grid")
    p.add_argument("--out", required=True)
    p.add_argument("--window-rows", type=int, default=48)
    p.add_argument("--min-observations", type=float, default=0.8)
    p.add_argument("--start-row", type=int, default=None, help="testing: first row")
    p.add_argument("--max-windows", type=int, default=None, help="testing: stop early")
    args = p.parse_args()

    dfo_meta = json.load(open(args.dfo_meta))
    viirs_meta = json.load(open(args.viirs_meta))
    plain_meta = json.load(open(args.plain_meta))

    ds = {k: rasterio.open(v) for k, v in
          (("dfo", args.dfo), ("viirs", args.viirs), ("plain", args.plain), ("land", args.land))}
    shapes = {k: d.shape for k, d in ds.items()}
    if len(set(shapes.values())) != 1:
        raise SystemExit(f"grids differ: {shapes}")
    ref = ds["dfo"]
    height, width = ref.height, ref.width
    top, left, res = ref.transform.f, ref.transform.c, ref.transform.a
    for k, d in ds.items():
        if abs(d.transform.f - top) > res / 10 or abs(d.transform.c - left) > res / 10:
            raise SystemExit(f"{k} is not aligned with the DFO input")
    target = {"top": top, "left": left, "res": res, "width": width, "height": height}

    groups_d = footprints_on_target(dfo_meta, target)
    groups_v = footprints_on_target(viirs_meta, target)
    max_d, max_v = dfo_meta["rasters_processed"], viirs_meta["rasters_processed"]
    min_d = int(np.ceil(args.min_observations * max_d))
    min_v = int(np.ceil(args.min_observations * max_v))
    boxes = mask_boxes(plain_meta, target)
    row_lo = max(min(b[0] for b in boxes), min(g[0] for g in groups_d), min(g[0] for g in groups_v))
    row_hi = min(max(b[1] for b in boxes), max(g[1] for g in groups_d), max(g[1] for g in groups_v))
    log(f"{args.grid_name}: {width}x{height} res {res}; rows {row_lo}..{row_hi}; "
        f"DFO {max_d} rasters (min {min_d}), VIIRS {max_v} (min {min_v})")

    lon = left + (np.arange(width) + 0.5) * res
    out_col = np.clip(np.floor((lon - GRID_WEST) / CELL).astype(np.int64), 0, NCOLS - 1)
    uniq_cols, col_starts = np.unique(out_col, return_index=True)

    names = ["dom", "plain"]
    for L in LAYERS:
        names += [f"ever_{L}", f"ever_plain_{L}", f"freq_{L}", f"freq_plain_{L}"]
    for L in DFO_LAYERS:
        names += [f"both_{L}", f"both_plain_{L}"]
    cells = {n: np.zeros((NROWS, NCOLS), np.float32) for n in names}
    hist = {L: np.zeros(2 * NBIN * NBIN, np.int64) for L in DFO_LAYERS}

    def add(name, values, out_rows, row_starts):
        colsum = np.add.reduceat(values, col_starts, axis=1)
        cells[name][np.ix_(out_rows, uniq_cols)] += np.add.reduceat(colsum, row_starts, axis=0)

    wr = args.window_rows
    if args.start_row is not None:
        row_lo = args.start_row
    if args.max_windows is not None:
        row_hi = min(row_hi, row_lo + wr * args.max_windows)
    total = (row_hi - row_lo + wr - 1) // wr
    for index, row in enumerate(range(row_lo, row_hi, wr), start=1):
        rows = min(wr, row_hi - row)
        win = Window(0, row, width, rows)
        lat = top - (np.arange(row, row + rows) + 0.5) * res
        out_rows_all = np.floor((GRID_NORTH - lat) / CELL).astype(np.int64)
        out_rows, row_starts = np.unique(out_rows_all, return_index=True)

        obs_d = observation_window(groups_d, row, rows, width)
        obs_v = observation_window(groups_v, row, rows, width)
        dom = box_window(boxes, row, rows, width)
        dom &= obs_d >= min_d
        dom &= obs_v >= min_v
        dom &= ds["land"].read(1, window=win) == 1
        if not dom.any():
            continue
        plain = (ds["plain"].read(1, window=win) == 1) & dom
        f32 = np.float32
        add("dom", dom.astype(f32), out_rows, row_starts)
        add("plain", plain.astype(f32), out_rows, row_starts)

        cs = ds["dfo"].read(1, window=win)
        cr = ds["dfo"].read(2, window=win)
        cv = ds["viirs"].read(1, window=win)
        ca = cs.astype(np.uint16) + cr
        inv_d = np.where(dom, 1.0 / np.maximum(obs_d, 1), 0).astype(f32)
        inv_v = np.where(dom, 1.0 / np.maximum(obs_v, 1), 0).astype(f32)
        del obs_d, obs_v
        counts = {"S": cs, "R": cr, "A": ca, "V": cv}
        ever = {}
        for L in LAYERS:
            e = (counts[L] > 0) & dom
            ever[L] = e
            ep = e & plain
            fr = counts[L] * (inv_v if L == "V" else inv_d)
            add(f"ever_{L}", e.astype(f32), out_rows, row_starts)
            add(f"ever_plain_{L}", ep.astype(f32), out_rows, row_starts)
            add(f"freq_{L}", fr, out_rows, row_starts)
            add(f"freq_plain_{L}", np.where(plain, fr, 0).astype(f32), out_rows, row_starts)
            del ep, fr
        for L in DFO_LAYERS:
            b = ever[L] & ever["V"]
            add(f"both_{L}", b.astype(f32), out_rows, row_starts)
            add(f"both_plain_{L}", (b & plain).astype(f32), out_rows, row_starts)
            idx = (plain[dom].astype(np.int64) * NBIN * NBIN
                   + DAY_LUT[counts[L][dom]].astype(np.int64) * NBIN
                   + DAY_LUT[cv[dom]])
            hist[L] += np.bincount(idx, minlength=2 * NBIN * NBIN)
            del b, idx
        del dom, plain, cs, cr, cv, ca, inv_d, inv_v, ever, counts
        if index % 50 == 0 or index == total:
            log(f"  window {index}/{total}")

    for d in ds.values():
        d.close()
    payload = dict(cells)
    for L in DFO_LAYERS:
        payload[f"hist_{L}"] = hist[L].reshape(2, NBIN, NBIN)
    info = {
        "grid_name": args.grid_name, "res": res, "width": width, "height": height,
        "inputs": {"dfo": args.dfo, "viirs": args.viirs, "plain": args.plain, "land": args.land},
        "dfo_rasters": max_d, "viirs_rasters": max_v,
        "dfo_dates": [dfo_meta["processed"][0]["date"], dfo_meta["processed"][-1]["date"]],
        "viirs_dates": [viirs_meta["processed"][0]["date"], viirs_meta["processed"][-1]["date"]],
        "min_observations": args.min_observations,
        "lat_north": top - row_lo * res, "lat_south": top - row_hi * res,
        "cell": CELL, "grid_north": GRID_NORTH, "grid_west": GRID_WEST,
        "day_bins": DAY_LABELS,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload["info"] = json.dumps(info)
    np.savez_compressed(args.out, **payload)
    log(f"wrote {args.out}; domain {cells['dom'].sum(dtype=np.float64):,.0f} px, plain {cells['plain'].sum(dtype=np.float64):,.0f} px")


if __name__ == "__main__":
    main()
