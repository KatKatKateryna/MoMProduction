"""
viirs_cells.py
    -- VIIRS flood area per watershed: true cell areas, and a fast path from
       a precomputed lookup of the cells each watershed covers

    Cell area: the VIIRS composites are on a latitude/longitude grid, so a
    cell covers less ground away from the equator. Each flood cell counts
    with its true area, R^2 * dlon * (sin(lat_top) - sin(lat_bottom)) on the
    sphere of the Earth's surface area, instead of a fixed 0.375 x 0.375 km.
    Both the per-watershed masks (VIIRS_extract_by_mask) and the fast path
    use flood_area_km2, so they give identical results.

    Fast path: VIIRS_extract_by_watershed cuts the 375 m grid out once per
    watershed (~5 minutes per image). The cells of every watershed are found
    once here with the same masking (rasterio, crop=True, pixel centres) and
    saved as row runs to data/viirs_watershed_runs.npz; an image is then read
    once in strips and its flood cells counted per run. Every cell of a
    watershed is kept whatever its value, and a cell can belong to several
    watersheds, as with the masks.

    The lookup is built by the first run that needs it (~6 minutes) and
    rebuilt when the watershed shapefile or the VIIRS grid changes. If the
    build fails, VIIRS falls back to the per-watershed masks. The file is
    generated, not kept in git.

    Switch: CELL_LOOKUP in the [viirs] section of production.cfg
    (True / False; missing = False).

**EXAMPLE**
    python viirs_cells.py VIIRS_1day_composite20261012_flood.tiff   # build by hand
"""

import json
import logging
import math
import os
import sys
import time

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.mask import raster_geometry_mask
from rasterio.windows import Window

import settings
from utilities import shapefile_fingerprint, watersheds_gdb_reader

CELLS_FILE = os.path.join(settings.BASE_DATA_DIR, "viirs_watershed_runs.npz")

# radius of the sphere with the Earth's surface area (authalic), km
EARTH_RADIUS_KM = 6371.0072
# image rows read at a time
STRIP_ROWS = 512

_LOOKUP = None


def is_flood(data):
    """VIIRS flood classes (141-200)"""
    return (data > 140) & (data < 201)


def row_cell_area_km2(transform, rows):
    """area of one cell in each of the given grid rows"""
    top = transform.f + np.asarray(rows, np.float64) * transform.e
    bottom = top + transform.e
    return (
        EARTH_RADIUS_KM ** 2
        * abs(math.radians(transform.a))
        * np.abs(np.sin(np.radians(top)) - np.sin(np.radians(bottom)))
    )


def flood_area_km2(rows, counts, transform):
    """total area of `counts` flood cells in each grid row of `rows`; summed
    exactly, so the order of the rows does not matter"""
    if len(rows) == 0:
        return 0.0
    areas = np.asarray(counts, np.float64) * row_cell_area_km2(transform, rows)
    return math.fsum(areas.tolist())


def grid_signature(src):
    return [int(src.width), int(src.height)] + [float(x) for x in tuple(src.transform)[:6]]


def build(tiff):
    """row runs of every watershed on the grid of `tiff`, with the masking
    of VIIRS_extract_by_mask (the image values are not read)"""
    watersheds = watersheds_gdb_reader()
    pfaf_ids = watersheds.index.tolist()
    ws_runs, row_runs, c0_runs, c1_runs = [], [], [], []
    overlap = np.zeros(len(pfaf_ids), bool)
    t0 = time.monotonic()
    with rasterio.open(tiff) as src:
        grid = grid_signature(src)
        for i, pfaf_id in enumerate(pfaf_ids):
            mask_json = json.loads(
                gpd.GeoSeries([watersheds.loc[pfaf_id, "geometry"]]).to_json()
            )
            try:
                outside, _, window = raster_geometry_mask(
                    src, [mask_json["features"][0]["geometry"]], crop=True
                )
            except ValueError:
                # 'Input shapes do not overlap raster.'
                continue
            overlap[i] = True
            inside = (~outside).astype(np.int8)
            edges = np.diff(inside, axis=1, prepend=0, append=0)
            r_start, c_start = np.nonzero(edges == 1)
            _, c_end = np.nonzero(edges == -1)
            if len(r_start) == 0:
                continue
            ws_runs.append(np.full(len(r_start), i, np.int32))
            row_runs.append((r_start + int(window.row_off)).astype(np.int32))
            c0_runs.append((c_start + int(window.col_off)).astype(np.int32))
            c1_runs.append((c_end + int(window.col_off)).astype(np.int32))

    ws = np.concatenate(ws_runs) if ws_runs else np.zeros(0, np.int32)
    row = np.concatenate(row_runs) if row_runs else np.zeros(0, np.int32)
    c0 = np.concatenate(c0_runs) if c0_runs else np.zeros(0, np.int32)
    c1 = np.concatenate(c1_runs) if c1_runs else np.zeros(0, np.int32)
    order = np.argsort(row, kind="stable")

    # unique name: two VIIRS processes may build at the same time
    tmp = f"{CELLS_FILE}.{os.getpid()}.part.npz"
    np.savez_compressed(
        tmp,
        pfaf_ids=np.asarray(pfaf_ids),
        overlap=overlap,
        ws=ws[order], row=row[order], c0=c0[order], c1=c1[order],
        grid=np.asarray(grid),
        shapefile=np.asarray(shapefile_fingerprint()),
    )
    os.replace(tmp, CELLS_FILE)
    logging.info(
        f"VIIRS cell lookup saved: {CELLS_FILE}, {len(pfaf_ids)} watersheds, "
        f"{len(ws)} runs, {time.monotonic() - t0:.0f}s"
    )


def read_lookup(grid):
    """the saved lookup if it matches the shapefile and the grid"""
    if not os.path.exists(CELLS_FILE):
        return None
    with np.load(CELLS_FILE) as z:
        lookup = {k: z[k] for k in z.files}
    if str(lookup["shapefile"]) != shapefile_fingerprint():
        logging.info("VIIRS cell lookup was built for another watershed shapefile")
        return None
    if lookup["grid"].tolist() != grid:
        logging.info(f"VIIRS cell lookup was built for another grid: {lookup['grid'].tolist()}")
        return None
    return lookup


def load_lookup(tiff):
    """the lookup for the grid of `tiff`, built first when missing or out of
    date; None if that fails (the caller then uses the per-watershed masks)"""
    global _LOOKUP
    with rasterio.open(tiff) as src:
        grid = grid_signature(src)
    if _LOOKUP is not None and _LOOKUP["grid"].tolist() == grid:
        return _LOOKUP
    try:
        lookup = read_lookup(grid)
        if lookup is None:
            logging.info(f"building the VIIRS cell lookup: {CELLS_FILE}")
            build(tiff)
            lookup = read_lookup(grid)
    except Exception:
        logging.exception("VIIRS cell lookup could not be built or read")
        lookup = None
    _LOOKUP = lookup
    return lookup


def summary_rows(tiff, watersheds):
    """the rows [pfaf_id, area, perc_area] VIIRS_extract_by_watershed writes
    for `tiff`, or None when the lookup cannot be used"""
    lookup = load_lookup(tiff)
    if lookup is None:
        return None
    pfaf_ids = lookup["pfaf_ids"].tolist()
    if pfaf_ids != watersheds.index.tolist():
        logging.warning("VIIRS cell lookup watershed list differs, using the per-watershed masks")
        return None
    ws, row, c0, c1 = lookup["ws"], lookup["row"], lookup["c0"], lookup["c1"]

    # flood cells per run, strip by strip; only runs with flooding are kept
    hit_ws, hit_row, hit_count = [], [], []
    with rasterio.open(tiff) as src:
        transform = src.transform
        for r0 in range(0, src.height, STRIP_ROWS):
            n = min(STRIP_ROWS, src.height - r0)
            lo, hi = np.searchsorted(row, [r0, r0 + n])
            if lo == hi:
                continue
            flood = is_flood(src.read(1, window=Window(0, r0, src.width, n)))
            cumulative = np.zeros((n, src.width + 1), np.int32)
            np.cumsum(flood, axis=1, out=cumulative[:, 1:])
            local = row[lo:hi] - r0
            count = cumulative[local, c1[lo:hi]] - cumulative[local, c0[lo:hi]]
            keep = count > 0
            hit_ws.append(ws[lo:hi][keep])
            hit_row.append(row[lo:hi][keep])
            hit_count.append(count[keep])

    area = np.zeros(len(pfaf_ids))
    if hit_ws:
        hit_ws = np.concatenate(hit_ws)
        hit_row = np.concatenate(hit_row)
        hit_count = np.concatenate(hit_count)
        order = np.lexsort((hit_row, hit_ws))
        hit_ws, hit_row, hit_count = hit_ws[order], hit_row[order], hit_count[order]
        # one integer count per watershed and row, as the masks count them
        first = np.flatnonzero(np.r_[True, (np.diff(hit_ws) != 0) | (np.diff(hit_row) != 0)])
        hit_count = np.add.reduceat(hit_count, first)
        hit_ws, hit_row = hit_ws[first], hit_row[first]
        starts = np.flatnonzero(np.r_[True, np.diff(hit_ws) != 0])
        ends = np.r_[starts[1:], len(hit_ws)]
        for s, e in zip(starts, ends):
            area[hit_ws[s]] = flood_area_km2(hit_row[s:e], hit_count[s:e], transform)

    rows = []
    for i, pfaf_id in enumerate(pfaf_ids):
        # a watershed outside the image counts 0, as in VIIRS_extract_by_mask
        flood_area = float(area[i]) if lookup["overlap"][i] else 0
        perc_Area = flood_area / watersheds.loc[pfaf_id]["area_km2"] * 100
        rows.append([pfaf_id, flood_area, perc_Area])
    return rows


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s", force=True)
    sys.exit(build(sys.argv[1]))
