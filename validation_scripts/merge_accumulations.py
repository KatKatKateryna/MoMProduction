#!/usr/bin/env python
"""Sum several partial flood accumulations into one.

Accumulations are per-pixel counts of days, so when the inputs cover disjoint
date ranges their sum is exactly the accumulation of the union of those ranges.
That makes it safe to split a long date range across machines, run the parts
independently and add them up afterwards, which is far quicker than processing
a season on one box.

The script refuses to merge inputs that share a date, since that would double
count, and it refuses inputs on different grids or with different band
definitions. The merged metadata keeps every per-raster record from the parts,
so the result is still traceable back to individual source files.
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


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", nargs="+", required=True,
                        help="partial accumulation GeoTIFFs")
    parser.add_argument("--out", required=True)
    parser.add_argument("--window-rows", type=int, default=512)
    parser.add_argument("--allow-overlap", action="store_true",
                        help="permit inputs that share dates (they will be summed)")
    args = parser.parse_args()

    metas = []
    for path in args.inputs:
        meta_path = f"{os.path.splitext(path)[0]}_metadata.json"
        if not os.path.exists(meta_path):
            sys.exit(f"ERROR: missing metadata for {path}")
        metas.append(json.load(open(meta_path)))

    # Guard against double counting.
    seen: dict[str, str] = {}
    for path, meta in zip(args.inputs, metas):
        for entry in meta["processed"]:
            if entry["file"] in seen and not args.allow_overlap:
                sys.exit(f"ERROR: {entry['file']} appears in both "
                         f"{seen[entry['file']]} and {path}; refusing to double count")
            seen[entry["file"]] = path

    bands = [b.get("values", b.get("source_value")) for b in metas[0]["bands"]]
    for path, meta in zip(args.inputs[1:], metas[1:]):
        other = [b.get("values", b.get("source_value")) for b in meta["bands"]]
        if other != bands:
            sys.exit(f"ERROR: {path} has bands {other}, expected {bands}")

    datasets = [rasterio.open(p) for p in args.inputs]
    try:
        first = datasets[0]
        for d in datasets[1:]:
            if (d.width, d.height, d.count) != (first.width, first.height, first.count):
                sys.exit("ERROR: inputs are not on the same grid")

        profile = {
            "driver": "GTiff", "width": first.width, "height": first.height,
            "count": first.count, "dtype": "uint16", "crs": first.crs,
            "transform": first.transform, "nodata": None, "tiled": True,
            "blockxsize": 256, "blockysize": 256, "compress": "lzw", "predictor": 2,
            "BIGTIFF": "YES",
        }
        log(f"summing {len(datasets)} partials, {first.width}x{first.height}, "
            f"{first.count} bands, {len(seen)} source rasters in total")

        peaks = [0] * first.count
        tmp = f"{args.out}.tmp"
        try:
            with rasterio.open(tmp, "w", **profile) as dst:
                dst.descriptions = first.descriptions
                total = (first.height + args.window_rows - 1) // args.window_rows
                for index, row in enumerate(range(0, first.height, args.window_rows),
                                            start=1):
                    rows = min(args.window_rows, first.height - row)
                    window = Window(0, row, first.width, rows)
                    for band in range(1, first.count + 1):
                        acc = np.zeros((rows, first.width), np.uint32)
                        for d in datasets:
                            acc += d.read(band, window=window)
                        if acc.max() > 65535:
                            sys.exit("ERROR: merged counts exceed uint16")
                        out = acc.astype(np.uint16)
                        peaks[band - 1] = max(peaks[band - 1], int(out.max()))
                        dst.write(out, band, window=window)
                        del acc, out
                    if index % 25 == 0 or index == total:
                        log(f"  window {index}/{total}")
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        os.replace(tmp, args.out)
    finally:
        for d in datasets:
            d.close()

    merged = dict(metas[0])
    merged["result"] = os.path.basename(args.out)
    merged["processed"] = [e for m in metas for e in m["processed"]]
    merged["processed"].sort(key=lambda e: e["date"])
    merged["rasters_processed"] = len(merged["processed"])
    merged["max_count"] = {b: peaks[i] for i, b in enumerate(
        [str(x) for x in bands])}
    merged["merged_from"] = [
        {"file": os.path.basename(p), "rasters": m["rasters_processed"],
         "date_range": m.get("date_range_requested")}
        for p, m in zip(args.inputs, metas)
    ]
    dates = [e["date"] for e in merged["processed"]]
    merged["date_span"] = {"first": min(dates), "last": max(dates)}
    merged["result_size_bytes"] = os.path.getsize(args.out)
    merged["merged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged.pop("date_range_requested", None)

    out_meta = f"{os.path.splitext(args.out)[0]}_metadata.json"
    with open(out_meta, "w") as handle:
        json.dump(merged, handle, indent=2)
        handle.write("\n")

    log(f"wrote {os.path.basename(args.out)} "
        f"({os.path.getsize(args.out) / 1e6:.1f} MB), "
        f"{merged['rasters_processed']} rasters, "
        f"{merged['date_span']['first']}..{merged['date_span']['last']}")
    log(f"max counts {merged['max_count']}")


if __name__ == "__main__":
    main()
