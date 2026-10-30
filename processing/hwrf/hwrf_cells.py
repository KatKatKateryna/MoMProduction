"""
hwrf_cells.py
    -- fast HWRF and HAFS per-watershed summaries from a precomputed cell
       lookup

    HWRF_extract_by_watershed and HAFS_tool.summarize cut the rain image out
    once per watershed (rasterio masks, HWRF_extract_by_mask). Each image has
    its own extent, but all of them, HWRF and HAFS, have their pixel centres
    on multiples of 0.05 deg, so they are windows of one global 0.05 deg
    lattice. The lattice cells of each watershed are found once here, with
    the same masking (rasterio, crop=True, pixel centres), and saved to
    data/hwrf_watershed_cells.npz. A summary then takes each watershed's
    cells straight from the image.

    The rows are the HWRF calculation unchanged: rain cells are the ones the
    image holds (not nodata), with the HWRF cell area
    111.111^2 * cos(lat * 0.01745) * 0.05^2. Only watersheds with rain get a
    row. Images of storms crossing the dateline run past 180 deg (0..360);
    a cell there counts for the watershed at its longitude - 360, as the
    masks do with their copy of each watershed shifted by 360 deg. Where an
    image holds the same place twice (e.g. -175 and 185), the larger value
    counts (the masks count both).

    With a switch on, the first run builds the lookup (~3 minutes) and
    rebuilds it when the watershed shapefile changes. If the build fails,
    the per-watershed masks are used. The file is generated, not kept in git.

    Switches: HWRF_CELL_LOOKUP in HWRF_tool.py, HAFS_CELL_LOOKUP in
    HAFS_tool.py (True / False).

**EXAMPLE** (from the repository root)
    python -m processing.hwrf.hwrf_cells    # build (or rebuild) the lookup by hand
"""

import json
import logging
import os
import sys
import time

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.io import MemoryFile
from rasterio.mask import raster_geometry_mask
from rasterio.transform import from_origin

import settings
from processing.utilities import shapefile_fingerprint, watersheds_gdb_reader

CELLS_FILE = os.path.join(settings.BASE_DATA_DIR, "hwrf_watershed_cells.npz")

# the global lattice of the HWRF and HAFS images: pixel centres on
# multiples of RES, longitudes -180..180 - RES, latitudes 90..-90
RES = 0.05
GRID_WIDTH = 7200
GRID_HEIGHT = 3601
GRID_TRANSFORM = from_origin(-180 - RES / 2, 90 + RES / 2, RES, RES)

_LOOKUP = None


def grid_signature():
    return [GRID_WIDTH, GRID_HEIGHT] + [float(x) for x in tuple(GRID_TRANSFORM)[:6]]


def row_cell_area_km2(rows):
    """HWRF_extract_by_mask cell area of the given lattice rows"""
    lats = GRID_TRANSFORM.f + (np.asarray(rows, np.float64) + 0.5) * GRID_TRANSFORM.e
    return 111.111 * 111.111 * np.cos(lats * 0.01745) * RES * RES


def build():
    """lattice cells of every watershed, with the masking of
    HWRF_extract_by_mask (no values are read)"""
    watersheds = watersheds_gdb_reader()
    pfaf_ids = watersheds.index.tolist()
    profile = {
        "driver": "GTiff", "width": GRID_WIDTH, "height": GRID_HEIGHT, "count": 1,
        "dtype": "uint8", "crs": "EPSG:4326", "transform": GRID_TRANSFORM,
    }
    offsets = [0]
    cells = []
    t0 = time.monotonic()
    with MemoryFile() as memfile:
        with memfile.open(**profile):
            pass
        with memfile.open() as src:
            for pfaf_id in pfaf_ids:
                mask_json = json.loads(
                    gpd.GeoSeries([watersheds.loc[pfaf_id, "geometry"]]).to_json()
                )
                geometry = mask_json["features"][0]["geometry"]
                if geometry is None:
                    offsets.append(offsets[-1])
                    continue
                try:
                    outside, _, window = raster_geometry_mask(src, [geometry], crop=True)
                except ValueError:
                    # 'Input shapes do not overlap raster.'
                    offsets.append(offsets[-1])
                    continue
                row, col = np.nonzero(~outside)
                row = row + int(window.row_off)
                col = col + int(window.col_off)
                cells.append(row.astype(np.int64) * GRID_WIDTH + col)
                offsets.append(offsets[-1] + len(row))

    # unique name: two HWRF/HAFS processes may build at the same time
    tmp = f"{CELLS_FILE}.{os.getpid()}.part.npz"
    np.savez_compressed(
        tmp,
        pfaf_ids=np.asarray(pfaf_ids),
        offsets=np.asarray(offsets, np.int64),
        cells=np.concatenate(cells).astype(np.int32) if cells else np.zeros(0, np.int32),
        grid=np.asarray(grid_signature()),
        shapefile=np.asarray(shapefile_fingerprint()),
    )
    os.replace(tmp, CELLS_FILE)
    logging.info(f"HWRF cell lookup saved: {CELLS_FILE}, {len(pfaf_ids)} watersheds, "
                 f"{offsets[-1]} cells, {time.monotonic() - t0:.0f}s")


def read_lookup():
    """the saved lookup if it matches the shapefile and the lattice"""
    if not os.path.exists(CELLS_FILE):
        return None
    with np.load(CELLS_FILE) as z:
        lookup = {k: z[k] for k in z.files}
    if str(lookup["shapefile"]) != shapefile_fingerprint():
        logging.info("HWRF cell lookup was built for another watershed shapefile")
        return None
    if lookup["grid"].tolist() != grid_signature():
        logging.info("HWRF cell lookup was built for another grid")
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
                logging.info(f"building the HWRF cell lookup: {CELLS_FILE}")
                build()
                lookup = read_lookup()
        except Exception:
            logging.exception("HWRF cell lookup could not be built or read")
            lookup = None
        _LOOKUP = lookup if lookup is not None else False
    return _LOOKUP or None


def summary_rows(grid, top, left, watersheds, res=RES):
    """the rows HWRF_extract_by_watershed / HAFS_tool.summarize write for a
    rain grid (NaN where there is no rain) whose top-left pixel centre is
    (left, top), or None when the lookup cannot be used"""
    lookup = load_lookup()
    if lookup is None:
        return None
    pfaf_ids = lookup["pfaf_ids"].tolist()
    if pfaf_ids != watersheds.index.tolist():
        logging.warning("HWRF cell lookup watershed list differs, using the per-watershed masks")
        return None
    row0 = int(round((90 - top) / RES))
    col0 = int(round((left + 180) / RES))
    if (abs(res - RES) > 1e-9 or abs(90 - row0 * RES - top) > RES / 100
            or abs(col0 * RES - 180 - left) > RES / 100):
        logging.warning(f"rain grid at ({left}, {top}), {res} deg is off the 0.05 deg lattice, using the per-watershed masks")
        return None

    # every lattice cell of every watershed, in the grid if it falls there:
    # at its longitude, or at its longitude + 360 past the dateline
    offsets, cells = lookup["offsets"], lookup["cells"]
    lattice_row = cells // GRID_WIDTH
    i = lattice_row - row0
    j = (cells % GRID_WIDTH - col0) % GRID_WIDTH
    values = np.full(len(cells), np.nan)
    for jj in (j, j + GRID_WIDTH):
        inside = (i >= 0) & (i < grid.shape[0]) & (jj < grid.shape[1])
        values[inside] = np.fmax(values[inside], grid[i[inside], jj[inside]])
    rain = np.isfinite(values)

    rows = []
    areas = row_cell_area_km2(lattice_row)
    for k, pfaf_id in enumerate(pfaf_ids):
        s, e = offsets[k], offsets[k + 1]
        hit = rain[s:e]
        if not hit.any():
            continue
        intensity = pd.Series(values[s:e][hit])
        total_area = pd.Series(areas[s:e][hit]).sum()
        rows.append([
            pfaf_id,
            total_area,
            total_area / watersheds.loc[pfaf_id]["area_km2"] * 100,
            intensity.mean(),
            intensity.max(),
        ])
    return rows


def summary_rows_from_tiff(tiff, watersheds):
    """summary_rows for a rain image: its cells that are not nodata"""
    with rasterio.open(tiff) as src:
        data = src.read(1).astype(np.float64)
        no_data = src.nodata
        t = src.transform
    grid = np.where(data != no_data, data, np.nan)
    return summary_rows(grid, t.f + t.e / 2, t.c + t.a / 2, watersheds, res=t.a)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s", force=True)
    sys.exit(build())
