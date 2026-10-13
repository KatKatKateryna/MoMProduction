"""
gfms_cells.py
    -- fast GFMS per-watershed summaries from a precomputed cell lookup

    GFMS_extract_by_watershed cuts the GFMS grid out once per watershed
    (~5,000 rasterio masks, ~5 minutes per 3-hourly file). The cells each
    watershed covers never change, so they are computed once here, with the
    same masking (rasterio.mask, crop=True, pixel centres), and saved with the
    cell areas to data/gfms_watershed_cells.npz. A summary then reads the grid
    once and takes each watershed's cells from the lookup.

    Every cell inside a watershed is kept in the lookup, whatever its value;
    which ones count is decided for each file exactly as before (value !=
    the file's nodata), so cells that are dry, wet or missing on a given day
    are all handled. The summary rows are identical to the original ones.

    The lookup is only used when it matches: same GFMS grid (size, position,
    cell size) and same watershed shapefile. Otherwise, or when the file is
    missing, GFMS falls back to the original per-watershed masks.

    Switch: CELL_LOOKUP in the [gfms] section of production.cfg
    (True / False; missing = False).

**EXAMPLE**
    python gfms_cells.py            # build (or rebuild) the lookup
"""

import hashlib
import json
import logging
import math
import os
import sys
import time

import geopandas
import numpy as np
import pandas as pd
import rasterio
from rasterio import Affine
from rasterio.io import MemoryFile
from rasterio.mask import mask

import settings
from utilities import watersheds_gdb_reader

CELLS_FILE = os.path.join(settings.BASE_DATA_DIR, "gfms_watershed_cells.npz")

# the GFMS grid as GFMS_download writes it in the .vrt
GRID_WIDTH = 4916
GRID_HEIGHT = 1600
GRID_TRANSFORM = Affine(0.0625, 0.0, -127.25, 0.0, -0.0625, 50.0)
GRID_NODATA = -9999.0

_LOOKUP = None


def shapefile_fingerprint():
    """sha256 of the watershed shapefile geometry and attribute files"""
    sha = hashlib.sha256()
    base = os.path.splitext(settings.WATERSHED_SHP)[0]
    for ext in (".shp", ".dbf"):
        with open(base + ext, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                sha.update(block)
    return sha.hexdigest()


def grid_signature(width, height, transform):
    return [int(width), int(height)] + [float(x) for x in tuple(transform)[:6]]


def build():
    """cells and cell areas of every watershed, with the original masking"""
    watersheds = watersheds_gdb_reader()
    pfaf_ids = watersheds.index.tolist()
    profile = {
        "driver": "GTiff", "width": GRID_WIDTH, "height": GRID_HEIGHT, "count": 1,
        "dtype": "float32", "crs": "EPSG:4326", "transform": GRID_TRANSFORM,
        "nodata": GRID_NODATA,
    }
    # every cell valid, so the mask alone decides which cells are kept
    full = np.ones((GRID_HEIGHT, GRID_WIDTH), np.float32)

    offsets = [0]
    cells, areas = [], []
    t0 = time.monotonic()
    with MemoryFile() as memfile:
        with memfile.open(**profile) as dst:
            dst.write(full, 1)
        with memfile.open() as src:
            px, py = src.res
            for n, pfaf_id in enumerate(pfaf_ids, 1):
                # same geometry hand-over and mask call as GFMS_extract_by_mask
                mask_json = json.loads(
                    geopandas.GeoSeries([watersheds.loc[pfaf_id, "geometry"]]).to_json()
                )
                try:
                    out_image, out_transform = mask(
                        src, [mask_json["features"][0]["geometry"]], crop=True
                    )
                except ValueError:
                    # 'Input shapes do not overlap raster.'
                    offsets.append(offsets[-1])
                    continue
                data = out_image[0]
                row, col = np.where(data != src.nodata)
                T1 = out_transform * Affine.translation(0.5, 0.5)
                lons, lats = T1 * (col, row)
                area = 111.111 * 111.111 * np.cos(lats * (math.pi / 180.0)) * px * py
                row_off = int(round((out_transform.f - GRID_TRANSFORM.f) / GRID_TRANSFORM.e))
                col_off = int(round((out_transform.c - GRID_TRANSFORM.c) / GRID_TRANSFORM.a))
                cells.append((row + row_off).astype(np.int64) * GRID_WIDTH + (col + col_off))
                areas.append(np.asarray(area, np.float64))
                offsets.append(offsets[-1] + len(row))
                if n % 500 == 0:
                    print(f"{n}/{len(pfaf_ids)} watersheds, {time.monotonic() - t0:.0f}s", flush=True)

    tmp = CELLS_FILE + ".part.npz"
    np.savez_compressed(
        tmp,
        pfaf_ids=np.asarray(pfaf_ids),
        offsets=np.asarray(offsets, np.int64),
        cells=np.concatenate(cells).astype(np.int32) if cells else np.zeros(0, np.int32),
        areas=np.concatenate(areas) if areas else np.zeros(0, np.float64),
        grid=np.asarray(grid_signature(GRID_WIDTH, GRID_HEIGHT, GRID_TRANSFORM)),
        shapefile=np.asarray(shapefile_fingerprint()),
    )
    os.replace(tmp, CELLS_FILE)
    print(f"saved {CELLS_FILE}: {len(pfaf_ids)} watersheds, {offsets[-1]} cells, "
          f"{time.monotonic() - t0:.0f}s")


def load_lookup():
    """the lookup if it matches the current watershed shapefile, else None"""
    global _LOOKUP
    if _LOOKUP is None:
        if not os.path.exists(CELLS_FILE):
            logging.warning(f"GFMS cell lookup missing ({CELLS_FILE}), build it with gfms_cells.py")
            _LOOKUP = False
            return None
        with np.load(CELLS_FILE) as z:
            lookup = {k: z[k] for k in z.files}
        if str(lookup["shapefile"]) != shapefile_fingerprint():
            logging.warning("GFMS cell lookup was built for another watershed shapefile, rebuild it with gfms_cells.py")
            _LOOKUP = False
            return None
        _LOOKUP = lookup
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
            data_points = pd.DataFrame(
                {"intensity": values[valid], "area": areas[s:e][valid]}
            )
            GFMS_TotalArea = data_points["area"].sum()
            if GFMS_TotalArea > 100.0:
                GFMS_Duration = 3
            GFMS_Area_percent = (
                GFMS_TotalArea / watersheds.loc[pfaf_id]["area_km2"] * 100
            )
            GFMS_MeanDepth = data_points["intensity"].mean()
            GFMS_MaxDepth = data_points["intensity"].max()
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
    sys.exit(build())
