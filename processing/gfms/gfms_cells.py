"""
gfms_cells.py
    -- fast GFMS per-watershed summaries from a precomputed cell lookup

    GFMS_extract_by_watershed cuts the GFMS grid out once per watershed
    (~5,000 cut-outs, ~5 minutes per 3-hourly file). The cells each
    watershed covers never change, so they are computed once here, with the
    same masking (cell_shares: every cell the watershed overlaps, weighted by
    the exact share of the cell inside it), and saved with the covered cell
    areas to data/gfms_watershed_cells.npz. A summary then reads the grid
    once and takes each watershed's cells from the lookup.

    Every cell inside a watershed is kept in the lookup, whatever its value;
    which ones count is decided for each file exactly as before (value !=
    the file's nodata), so cells that are dry, wet or missing on a given day
    are all handled. The summary rows are identical to the original ones.

    With the switch on, the first GFMS run builds the lookup (~2 minutes) and
    rebuilds it when the watershed shapefile or MASK_METHOD changes. It is only used for a
    file with the same GFMS grid (size, position, cell size); otherwise, or if
    the build fails, GFMS falls back to the original per-watershed masks.
    The file is generated, not kept in git.

    Switch: GFMS_CELL_LOOKUP in GFMS_tool.py (True / False).

**EXAMPLE** (from the repository root)
    python -m processing.gfms.gfms_cells    # build (or rebuild) the lookup by hand
"""

import logging
import math
import os
import sys
import time

import numpy as np
import rasterio
import shapely
from rasterio import Affine

import settings
from processing.utilities import shapefile_fingerprint, watersheds_gdb_reader

CELLS_FILE = os.path.join(settings.BASE_DATA_DIR, "gfms_watershed_cells.npz")

# the GFMS grid as GFMS_download writes it in the .vrt (the bin's own grid)
GRID_WIDTH = 2458
GRID_HEIGHT = 800
GRID_TRANSFORM = Affine(0.125, 0.0, -127.25, 0.0, -0.125, 50.0)
GRID_NODATA = -9999.0

# saved in the lookup: one built with another masking is rebuilt
MASK_METHOD = "exact cell shares"

_LOOKUP = None


def grid_signature(width, height, transform):
    return [int(width), int(height)] + [float(x) for x in tuple(transform)[:6]]


def cell_shares(geometry, transform, width, height):
    """row, col and share (0-1] of every grid cell the geometry overlaps: the
    exact part of the cell's area inside the geometry, 1 for inner cells"""
    minx, miny, maxx, maxy = geometry.bounds
    col0 = max(int(math.floor((minx - transform.c) / transform.a)), 0)
    col1 = min(int(math.ceil((maxx - transform.c) / transform.a)), width)
    row0 = max(int(math.floor((maxy - transform.f) / transform.e)), 0)
    row1 = min(int(math.ceil((miny - transform.f) / transform.e)), height)
    if col0 >= col1 or row0 >= row1:
        # outside the grid
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
    rows, cols = np.mgrid[row0:row1, col0:col1]
    rows, cols = rows.ravel(), cols.ravel()
    left = transform.c + cols * transform.a
    top = transform.f + rows * transform.e
    boxes = shapely.box(left, top + transform.e, left + transform.a, top)
    shapely.prepare(geometry)
    overlap = shapely.intersects(geometry, boxes)
    rows, cols, boxes = rows[overlap], cols[overlap], boxes[overlap]
    shares = np.ones(len(boxes))
    # the intersection is only computed for the cells on the boundary
    edge = ~shapely.contains_properly(geometry, boxes)
    cell_area = transform.a * -transform.e
    shares[edge] = shapely.area(shapely.intersection(boxes[edge], geometry)) / cell_area
    # cells only touching the geometry along an edge or corner cover nothing
    keep = shares > 0
    return rows[keep], cols[keep], np.minimum(shares[keep], 1.0)


def covered_areas(rows, shares, transform):
    """km2 of the cells covered by the geometry, at the cell centre latitude"""
    lats = transform.f + (rows + 0.5) * transform.e
    return 111.111 * 111.111 * np.cos(lats * (math.pi / 180.0)) * transform.a * -transform.e * shares


def build():
    """cells and covered cell areas of every watershed (cell_shares)"""
    watersheds = watersheds_gdb_reader()
    pfaf_ids = watersheds.index.tolist()

    offsets = [0]
    cells, areas = [], []
    t0 = time.monotonic()
    for pfaf_id in pfaf_ids:
        row, col, shares = cell_shares(
            watersheds.loc[pfaf_id, "geometry"], GRID_TRANSFORM, GRID_WIDTH, GRID_HEIGHT
        )
        cells.append(row * GRID_WIDTH + col)
        areas.append(covered_areas(row, shares, GRID_TRANSFORM))
        offsets.append(offsets[-1] + len(row))

    # unique name: two GFMS processes may build at the same time
    tmp = f"{CELLS_FILE}.{os.getpid()}.part.npz"
    np.savez_compressed(
        tmp,
        pfaf_ids=np.asarray(pfaf_ids),
        offsets=np.asarray(offsets, np.int64),
        cells=np.concatenate(cells).astype(np.int32) if cells else np.zeros(0, np.int32),
        areas=np.concatenate(areas) if areas else np.zeros(0, np.float64),
        grid=np.asarray(grid_signature(GRID_WIDTH, GRID_HEIGHT, GRID_TRANSFORM)),
        shapefile=np.asarray(shapefile_fingerprint()),
        method=np.asarray(MASK_METHOD),
    )
    os.replace(tmp, CELLS_FILE)
    logging.info(f"GFMS cell lookup saved: {CELLS_FILE}, {len(pfaf_ids)} watersheds, "
                 f"{offsets[-1]} cells, {time.monotonic() - t0:.0f}s")


def read_lookup():
    """the saved lookup if it matches the current watershed shapefile"""
    if not os.path.exists(CELLS_FILE):
        return None
    with np.load(CELLS_FILE) as z:
        lookup = {k: z[k] for k in z.files}
    if str(lookup["shapefile"]) != shapefile_fingerprint():
        logging.info("GFMS cell lookup was built for another watershed shapefile")
        return None
    if str(lookup.get("method", "pixel centres")) != MASK_METHOD:
        logging.info("GFMS cell lookup was built with another masking")
        return None
    if lookup["grid"].tolist() != grid_signature(GRID_WIDTH, GRID_HEIGHT, GRID_TRANSFORM):
        logging.info("GFMS cell lookup was built for another grid")
        return None
    return lookup


def load_lookup():
    """the lookup, built first when missing or out of date; None if that
    fails (the caller then uses the per-watershed masks)"""
    global _LOOKUP
    if _LOOKUP is None:
        try:
            lookup = read_lookup()
            if lookup is None:
                logging.info(f"building the GFMS cell lookup: {CELLS_FILE}")
                build()
                lookup = read_lookup()
        except Exception:
            logging.exception("GFMS cell lookup could not be built or read")
            lookup = None
        _LOOKUP = lookup if lookup is not None else False
    return _LOOKUP or None


def summary_rows(vrt_file, watersheds):
    """the summary rows GFMS_extract_by_watershed writes for vrt_file, or
    None when the lookup cannot be used for it"""
    lookup = load_lookup()
    if lookup is None:
        return None
    if lookup["pfaf_ids"].tolist() != watersheds.index.tolist():
        logging.warning("GFMS cell lookup watershed list differs, using the per-watershed masks")
        return None
    with rasterio.open(vrt_file) as src:
        if grid_signature(src.width, src.height, src.transform) != lookup["grid"].tolist():
            logging.warning(f"GFMS grid of {vrt_file} differs from the cell lookup, using the per-watershed masks")
            return None
        no_data = src.nodata
        flat = src.read(1).ravel()

    offsets, cells, areas = lookup["offsets"], lookup["cells"], lookup["areas"]
    rows = []
    for i, pfaf_id in enumerate(lookup["pfaf_ids"].tolist()):
        s, e = offsets[i], offsets[i + 1]
        values = flat[cells[s:e]]
        valid = values != no_data
        # same as GFMS_extract_by_watershed: 0s without flooded cells
        GFMS_TotalArea = 0.0
        GFMS_Area_percent = 0.0
        GFMS_MeanDepth = 0.0
        GFMS_MaxDepth = 0.0
        GFMS_Duration = 0
        if valid.any():
            depth, area = values[valid], areas[s:e][valid]
            GFMS_TotalArea = area.sum()
            if GFMS_TotalArea > 100.0:
                GFMS_Duration = 3
            GFMS_Area_percent = (
                GFMS_TotalArea / watersheds.loc[pfaf_id]["area_km2"] * 100
            )
            # weighted by the covered area: a sliver of a cell counts as little
            GFMS_MeanDepth = np.average(depth, weights=area)
            GFMS_MaxDepth = depth.max()
        rows.append([
            pfaf_id,
            GFMS_TotalArea,
            GFMS_Area_percent,
            GFMS_MeanDepth,
            GFMS_MaxDepth,
            GFMS_Duration,
        ])
    return rows


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s", force=True)
    sys.exit(build())
