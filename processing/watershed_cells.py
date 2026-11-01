"""
watershed_cells.py
    -- per-watershed summaries of the GFMS, VIIRS, DFO and HWRF / HAFS rasters
       from a precomputed lookup of the grid cells each watershed covers

    Masking: every cell a watershed overlaps counts with the exact share of
    the cell inside it (1 for inner cells). Inner cells are found by their
    centres, the cells along the boundary (and one cell around it) get their
    share from the polygon intersection.

    Lookup: the cells of every watershed are saved as row runs, cells
    [c0, c1) of one grid row: runs of inner cells (share 1) and runs of
    boundary cells with a share per cell. One file per source,
    data/watershed_runs_<source>.npz, rebuilt when the watershed shapefile,
    the grid or MASK_METHOD changes; when it cannot be saved it is built in
    memory for the run. The file is generated, not kept in git.

    Cell area: the grids are latitude/longitude, so a cell's area is
    111.111^2 * cos(lat at the cell centre) * dlon * dlat (degrees, 111.111 km
    per degree), times its share.

    Grids: GFMS and VIIRS files are on one grid per source, the file's own.
    HWRF / HAFS images and DFO mosaics cover a different extent each time, so
    they are windows of one global grid of their cell size (global_grid),
    and one lookup serves them all.

    Reading: a raster is read in strips of rows, sized from the grid width so
    a strip stays under STRIP_BYTES, and summed per run with cumulative sums
    along each row. Rasters must be on a latitude/longitude grid.

    Per source only the grid and which cells count differ (SOURCES); every
    summary gives, per watershed, the area of the counted cells, its percent
    of the watershed, the area-weighted mean and the max of their values.

**EXAMPLE** (from the repository root)
    python -m processing.watershed_cells gfms <a GFMS .vrt>    # build a lookup by hand
    python -m processing.watershed_cells viirs <a VIIRS .tiff>
    python -m processing.watershed_cells dfo <a DFO mosaic .vrt>
    python -m processing.watershed_cells hwrf
"""

import logging
import math
import os
import sys
import time
from array import array

import numpy as np
import pandas as pd
import rasterio
import rasterio.features
import shapely
import shapely.affinity
from rasterio import Affine
from rasterio.transform import from_origin
from rasterio.windows import Window

import settings
from processing.utilities import shapefile_fingerprint, watersheds_gdb_reader

# saved in each lookup: one built with another masking is rebuilt
MASK_METHOD = "exact cell shares /10000, split row runs"
# boundary shares are stored as a byte, share * SHARE_SCALE (0.01 steps);
# a float32 each cost 4x as much for 7 digits nobody uses. Checked against the
# whole-cell masking it replaces: 0.01 steps differ from exact shares by 0.01%
# of the exact-vs-whole-cell gap, i.e. indistinguishable.
SHARE_SCALE = 10000
# a boundary run is split so its length fits in a uint8
EDGE_RUN_MAX = 255
# km per degree of latitude (and of longitude at the equator)
KM_PER_DEGREE = 111.111
# one strip array (rows x grid width) stays under this size
STRIP_BYTES = 16 * 2**20
# rows of a watershed whose boundary cells are intersected together
EDGE_BAND_ROWS = 16

# the global lattice of the HWRF and HAFS images: pixel centres on
# multiples of HWRF_RES, longitudes -180..180 - HWRF_RES, latitudes 90..-90
HWRF_RES = 0.05
HWRF_LATTICE = (7200, 3601, from_origin(-180 - HWRF_RES / 2, 90 + HWRF_RES / 2, HWRF_RES, HWRF_RES))


def viirs_flood(values):
    """VIIRS flood classes (141-200)"""
    return (values > 140) & (values < 201)


def dfo_flood(values):
    """DFO (MODIS MCDWD) flood class"""
    return values == 3


# per source: which cells count (values are NaN where the raster has no
# data), and whether the grid wraps around the globe in longitude
SOURCES = {
    "gfms": {"counts": np.isfinite, "wrap": False},
    "viirs": {"counts": viirs_flood, "wrap": False},
    "hwrf": {"counts": np.isfinite, "wrap": True},  # HWRF and HAFS
    "dfo": {"counts": dfo_flood, "wrap": False},
}

_LOOKUPS = {}


def grid_signature(width, height, transform):
    return [int(width), int(height)] + [float(x) for x in tuple(transform)[:6]]


def global_grid(source, transform):
    """the global grid (width, height, transform) whose windows a source's
    rasters are, for the sources whose extent varies from file to file (one
    lookup for all of them); None when each file is on the source's own grid"""
    if source == "hwrf":
        return HWRF_LATTICE
    if source == "dfo":
        # the mosaic of the day's tiles, on a grid from -180, 90 at the tiles' cell size
        res = transform.a
        return int(round(360 / res)), int(round(180 / res)), from_origin(-180, 90, res, res)
    return None


def window_offset(grid_transform, transform):
    """row and column of a raster's top-left cell in a global grid, or None
    when the raster's cells are not cells of that grid"""
    a, e = grid_transform.a, grid_transform.e
    if abs(transform.a - a) > abs(a) * 1e-6 or abs(transform.e - e) > abs(e) * 1e-6:
        return None
    col0 = int(round((transform.c - grid_transform.c) / a))
    row0 = int(round((transform.f - grid_transform.f) / e))
    if (abs(grid_transform.c + col0 * a - transform.c) > abs(a) / 100
            or abs(grid_transform.f + row0 * e - transform.f) > abs(e) / 100):
        return None
    return row0, col0


def row_cell_area_km2(transform, rows):
    """area of one cell in each of the given grid rows (km2)"""
    centre = np.clip(transform.f + (np.asarray(rows, np.float64) + 0.5) * transform.e, -90, 90)
    return KM_PER_DEGREE**2 * np.cos(np.radians(centre)) * abs(transform.a * transform.e)


def _runs(mask):
    """row, start and end column of every run of True cells of a 2D mask"""
    edges = np.diff(mask.astype(np.int8), axis=1, prepend=0, append=0)
    row, c0 = np.nonzero(edges == 1)
    _, c1 = np.nonzero(edges == -1)
    return row, c0, c1


def _dilate(mask):
    """mask grown by one cell, also diagonally"""
    grown = mask.copy()
    grown[1:] |= mask[:-1]
    grown[:-1] |= mask[1:]
    wide = grown.copy()
    wide[:, 1:] |= grown[:, :-1]
    wide[:, :-1] |= grown[:, 1:]
    return wide


def watershed_runs(geometry, width, height, transform, wrap=False):
    """the cells of one watershed: inner runs (row, c0, c1) and boundary runs
    (row, c0, c1, shares of their cells)"""
    inner, edge = [], []
    if geometry is None or geometry.is_empty:
        return inner, edge
    if not geometry.is_valid:
        geometry = shapely.make_valid(geometry)
    pieces = [geometry]
    if wrap:
        # a grid around the globe: the parts past its edges at longitude -+ 360
        pieces += [shapely.affinity.translate(geometry, xoff=x) for x in (-360, 360)]
    cell_area = abs(transform.a * transform.e)
    for piece in pieces:
        minx, miny, maxx, maxy = piece.bounds
        col0 = max(int(math.floor((minx - transform.c) / transform.a)), 0)
        col1 = min(int(math.ceil((maxx - transform.c) / transform.a)), width)
        row0 = max(int(math.floor((maxy - transform.f) / transform.e)), 0)
        row1 = min(int(math.ceil((miny - transform.f) / transform.e)), height)
        if col0 >= col1 or row0 >= row1:
            continue
        shape = (row1 - row0, col1 - col0)
        window_transform = transform * Affine.translation(col0, row0)
        centre = rasterio.features.geometry_mask(
            [piece], shape, window_transform, invert=True
        )
        boundary = rasterio.features.geometry_mask(
            [piece.boundary], shape, window_transform, invert=True, all_touched=True
        )
        # one cell more around the boundary: no partly covered cell is missed
        # where the boundary runs along cell edges
        boundary = _dilate(boundary)

        # exact shares of the boundary cells
        er, ec = np.nonzero(boundary)
        shares = np.zeros(len(er))
        band_starts = np.searchsorted(er, np.arange(0, shape[0] + EDGE_BAND_ROWS, EDGE_BAND_ROWS))
        for s, e in zip(band_starts[:-1], band_starts[1:]):
            if s == e:
                continue
            rows, cols = er[s:e] + row0, ec[s:e] + col0
            left = transform.c + cols * transform.a
            top = transform.f + rows * transform.e
            # the piece cut to the band first: each cell intersects a small part
            band = shapely.intersection(
                piece,
                shapely.box(left.min(), top.max() + transform.e * (rows.max() - rows.min() + 1),
                            left.max() + transform.a, top.max()),
            )
            if band.is_empty:
                continue
            boxes = shapely.box(left, top + transform.e, left + transform.a, top)
            shares[s:e] = shapely.area(shapely.intersection(boxes, band)) / cell_area

        # whole boundary cells join the inner runs; only partial ones keep a share
        whole = shares >= 1 - 1e-9
        inside = centre & ~boundary
        inside[er[whole], ec[whole]] = True
        r, a, b = _runs(inside)
        inner += zip(r + row0, a + col0, b + col0)

        part = (shares > 0) & ~whole
        rows, cols, shares = er[part] + row0, ec[part] + col0, shares[part]
        # consecutive cells of a row as one run
        breaks = np.flatnonzero((np.diff(rows) != 0) | (np.diff(cols) != 1)) + 1
        for rs, cs, ss in zip(np.split(rows, breaks), np.split(cols, breaks), np.split(shares, breaks)):
            if len(rs):
                edge.append((rs[0], cs[0], cs[-1] + 1, ss))
    return inner, edge


def build(source, width, height, transform, watersheds=None):
    """the lookup of every watershed on a grid (no values are read)"""
    if watersheds is None:
        watersheds = watersheds_gdb_reader()
    pfaf_ids = watersheds.index.tolist()
    wrap = SOURCES[source]["wrap"]
    # inner and boundary runs are kept apart: an inner run spans hundreds of
    # cells and needs its end column, a boundary run averages 2.3 cells and
    # only needs a length, and only it needs a share per cell
    iws, irow, ic0, ic1 = (array("i") for _ in range(4))
    ews, erow, ec0, elen = (array("i") for _ in range(4))
    shares = array("H")
    t0 = time.monotonic()
    for i, geometry in enumerate(watersheds.geometry):
        inner, edge = watershed_runs(geometry, width, height, transform, wrap)
        for r, a, b in inner:
            iws.append(i), irow.append(r), ic0.append(a), ic1.append(b)
        for r, a, b, sh in edge:
            q = np.rint(np.asarray(sh, np.float64) * SHARE_SCALE).astype(np.uint16)
            for start in range(0, len(q), EDGE_RUN_MAX):
                part = q[start : start + EDGE_RUN_MAX]
                ews.append(i), erow.append(r), ec0.append(a + start), elen.append(len(part))
                shares.frombytes(part.tobytes())
        if (i + 1) % 2000 == 0:
            logging.info(f"{source} cell lookup: {i + 1} of {len(pfaf_ids)} watersheds, {time.monotonic() - t0:.0f}s")

    def group(ws_a, row_a, c0_a, last_a, last_dtype):
        w = np.frombuffer(ws_a, np.int32)
        r = np.frombuffer(row_a, np.int32)
        order = np.argsort(r, kind="stable")
        return (w[order].astype(np.uint16), r[order],
                np.frombuffer(c0_a, np.int32)[order],
                np.frombuffer(last_a, np.int32)[order].astype(last_dtype))

    iws_v, irow_v, ic0_v, ic1_v = group(iws, irow, ic0, ic1, np.int32)
    ews_v, erow_v, ec0_v, elen_v = group(ews, erow, ec0, elen, np.uint8)
    # the shares of a run follow its own cells, so they are reordered too
    raw = np.frombuffer(shares, np.uint16)
    lengths = np.frombuffer(elen, np.int32)
    starts = np.cumsum(lengths) - lengths
    order = np.argsort(np.frombuffer(erow, np.int32), kind="stable")
    take = np.repeat(starts[order], lengths[order]) + (
        np.arange(int(lengths.sum())) - np.repeat(np.cumsum(lengths[order]) - lengths[order], lengths[order])
    )
    shares_v = raw[take] if len(raw) else np.zeros(0, np.uint16)
    eoff_v = (np.cumsum(elen_v, dtype=np.int64) - elen_v).astype(
        np.int32 if len(shares_v) < 2**31 else np.int64
    )
    lookup = {
        "pfaf_ids": np.asarray(pfaf_ids),
        "overlap": np.isin(np.arange(len(pfaf_ids)), np.concatenate([iws_v, ews_v])),
        "iws": iws_v, "irow": irow_v, "ic0": ic0_v, "ic1": ic1_v,
        "ews": ews_v, "erow": erow_v, "ec0": ec0_v, "elen": elen_v,
        "eoff": eoff_v, "shares": shares_v,
        "grid": np.asarray(grid_signature(width, height, transform)),
        "shapefile": np.asarray(shapefile_fingerprint()),
        "method": np.asarray(MASK_METHOD),
    }
    logging.info(
        f"{source} cell lookup built: {len(pfaf_ids)} watersheds, "
        f"{len(irow_v)} inner + {len(erow_v)} boundary runs, "
        f"{len(shares_v)} boundary cells, {time.monotonic() - t0:.0f}s"
    )
    return lookup


def lookup_file(source):
    return os.path.join(settings.BASE_DATA_DIR, f"watershed_runs_{source}.npz")


def read_lookup(source, grid, pfaf_ids):
    """the saved lookup if it matches the shapefile, the masking, the grid
    and the watersheds"""
    path = lookup_file(source)
    if not os.path.exists(path):
        return None
    with np.load(path) as z:
        lookup = {k: z[k] for k in z.files}
    for key, current in (
        ("shapefile", shapefile_fingerprint()),
        ("method", MASK_METHOD),
    ):
        if str(lookup.get(key, "")) != current:
            logging.info(f"{source} cell lookup was built with another {key}")
            return None
    if lookup["grid"].tolist() != grid:
        logging.info(f"{source} cell lookup was built for another grid: {lookup['grid'].tolist()}")
        return None
    if lookup["pfaf_ids"].tolist() != pfaf_ids:
        logging.info(f"{source} cell lookup was built for another watershed list")
        return None
    return lookup


def load_lookup(source, width, height, transform, watersheds):
    """the lookup of a grid, built and saved first when missing or out of
    date; built in memory only if it cannot be saved"""
    grid = grid_signature(width, height, transform)
    pfaf_ids = watersheds.index.tolist()
    cached = _LOOKUPS.get(source)
    if cached is not None and cached["grid"].tolist() == grid and cached["pfaf_ids"].tolist() == pfaf_ids:
        return cached
    lookup = read_lookup(source, grid, pfaf_ids)
    if lookup is None:
        path = lookup_file(source)
        logging.info(f"building the {source} cell lookup: {path}")
        lookup = build(source, width, height, transform, watersheds)
        # unique name: two processes may build at the same time
        tmp = f"{path}.{os.getpid()}.part.npz"
        try:
            np.savez_compressed(tmp, **lookup)
            os.replace(tmp, path)
        except Exception:
            logging.exception(f"{source} cell lookup not saved, used from memory")
            if os.path.exists(tmp):
                os.remove(tmp)
    _LOOKUPS[source] = lookup
    return lookup


def strip_rows(width):
    """rows per strip so one float64 strip stays under STRIP_BYTES"""
    return max(1, STRIP_BYTES // (8 * (width + 1)))


def summarize(source, lookup, strips, transform, watersheds, value_above=-np.inf):
    """per watershed (index pfaf_id): area (km2) and perc_area of the cells
    that count, mean (weighted by area) and max of their values; strips
    yields (first row, values of whole grid rows, NaN where no data)"""
    counts = SOURCES[source]["counts"]
    iws, irow, ic0, ic1 = lookup["iws"], lookup["irow"], lookup["ic0"], lookup["ic1"]
    ews, erow, ec0, elen = lookup["ews"], lookup["erow"], lookup["ec0"], lookup["elen"]
    eoff, shares = lookup["eoff"], lookup["shares"]
    n = len(lookup["pfaf_ids"])
    area, weighted, vmax = np.zeros(n), np.zeros(n), np.full(n, -np.inf)
    area_filtered_km2 = np.zeros(n)

    for r0, values in strips:
        k = values.shape[0]
        ilo, ihi = np.searchsorted(irow, [r0, r0 + k])
        elo, ehi = np.searchsorted(erow, [r0, r0 + k])
        has_inner, has_edge = ihi > ilo, ehi > elo
        if not (has_inner or has_edge):
            continue

        fl, fa, fb = irow[ilo:ihi] - r0, ic0[ilo:ihi], ic1[ilo:ihi]
        pl, pa = erow[elo:ehi] - r0, ec0[elo:ehi]
        length = elen[elo:ehi].astype(np.intp)

        # only the columns this strip's runs reach. The cumulative sums each
        # cost a pass over every column of the strip, and no cell outside this
        # span belongs to any watershed. Algebraically the same sums; the
        # prefix baseline moves, so mean and area can differ in the last bit.
        lows = ([int(fa.min())] if has_inner else []) + ([int(pa.min())] if has_edge else [])
        highs = ([int(fb.max())] if has_inner else []) + (
            [int((pa + length).max())] if has_edge else []
        )
        cmin, cmax = min(lows), max(highs)
        span = values[:, cmin:cmax]
        width = span.shape[1]
        fa, fb, pa = fa - cmin, fb - cmin, pa - cmin

        valid = counts(span)
        # nothing counted in the span: every contribution below is 0 or a
        # no-op, and the cumulative sums are the bulk of the per-strip cost
        if not valid.any():
            continue
        cum_n = np.zeros((k, width + 1), np.int32)
        np.cumsum(valid, axis=1, out=cum_n[:, 1:])
        cum_v = np.zeros((k, width + 1))
        np.cumsum(np.where(valid, span, 0), axis=1, out=cum_v[:, 1:])
        if value_above == -np.inf:
            cum_f = cum_n
        else:
            cum_f = np.zeros((k, width + 1), np.int32)
            np.cumsum(valid & (span > value_above), axis=1, out=cum_f[:, 1:])
        cell_km2 = row_cell_area_km2(transform, np.arange(r0, r0 + k))

        # inner runs: whole cells
        if has_inner:
            fw = iws[ilo:ihi].astype(np.intp)
            km2 = cell_km2[fl]
            area += np.bincount(fw, (cum_n[fl, fb] - cum_n[fl, fa]) * km2, n)
            area_filtered_km2 += np.bincount(fw, (cum_f[fl, fb] - cum_f[fl, fa]) * km2, n)
            weighted += np.bincount(fw, (cum_v[fl, fb] - cum_v[fl, fa]) * km2, n)
            flat_max = np.append(np.where(valid, span, -np.inf).ravel(), -np.inf)
            bounds = np.empty(2 * len(fw), np.intp)
            bounds[0::2], bounds[1::2] = fl * width + fa, fl * width + fb
            np.maximum.at(vmax, fw, np.maximum.reduceat(flat_max, bounds)[0::2])

        # boundary runs: only their cells that count. Expanding every
        # boundary run was the bulk of the cost, and on a 250 m grid almost
        # none of those cells carry data. The counted cells of the strip are
        # already sorted row-major, so one key per cell locates each run's
        # share of them with two searchsorted calls.
        if has_edge:
            pw = ews[elo:ehi].astype(np.intp)
            po = eoff[elo:ehi]
            vr, vc = np.nonzero(valid)
            if len(vr):
                stride = width + 1
                vkey = vr.astype(np.int64) * stride + vc
                base = pl.astype(np.int64) * stride
                lo_i = np.searchsorted(vkey, base + pa)
                hi_i = np.searchsorted(vkey, base + pa + length)
                nsel = hi_i - lo_i
                keep = nsel > 0
                if keep.any():
                    nsel, lo_i = nsel[keep], lo_i[keep]
                    total = int(nsel.sum())
                    starts = np.cumsum(nsel) - nsel
                    sel = np.repeat(lo_i, nsel) + (np.arange(total) - np.repeat(starts, nsel))
                    cl, cc = vr[sel], vc[sel]
                    cw = np.repeat(pw[keep], nsel)
                    cs = shares[np.repeat(po[keep], nsel) + (cc - np.repeat(pa[keep], nsel))] / SHARE_SCALE
                    cv = span[cl, cc]
                    ckm2 = cs * cell_km2[cl]
                    area += np.bincount(cw, ckm2, n)
                    area_filtered_km2 += np.bincount(cw, ckm2 * (cv > value_above), n)
                    weighted += np.bincount(cw, cv * ckm2, n)
                    np.maximum.at(vmax, cw, cv)

    has = area > 0
    result = pd.DataFrame(
        {
            "area": area_filtered_km2,
            "perc_area": area_filtered_km2 / watersheds["area_km2"].to_numpy() * 100,
            "mean": np.where(has, weighted / np.where(has, area, 1), np.nan),
            "max": np.where(has, vmax, np.nan),
            "has_data": has,
        },
        index=pd.Index(lookup["pfaf_ids"], name="pfaf_id"),
    )
    return result


def window_strips(read_rows, n_rows, n_cols, row0, col0, width, height):
    """strips of a global grid holding a raster window whose top-left cell is
    at (row0, col0); read_rows(i, k) gives k rows of the window from its row
    i. Columns past the grid's east edge wrap around (0..360); where the
    window holds the same place twice (e.g. -175 and 185), the larger value"""
    step = strip_rows(width)
    first, last = max(row0, 0), min(row0 + n_rows, height)
    for r0 in range(first, last, step):
        k = min(step, last - r0)
        values = np.full((k, width), np.nan)
        rows = read_rows(r0 - row0, k)
        for j0 in range(0, n_cols, width):
            part = rows[:, j0 : j0 + width]
            cols = (col0 + j0 + np.arange(part.shape[1])) % width
            values[:, cols] = np.fmax(values[:, cols], part)
        yield r0, values


def raster_summary(source, path, watersheds, value_above=-np.inf):
    """summarize a raster file: GFMS and VIIRS on the file's own grid, HWRF
    and DFO (extent varies from file to file) as a window of their global
    grid; read in strips"""
    with rasterio.open(path) as src:
        if src.crs is not None and not src.crs.is_geographic:
            # the cell areas assume a latitude/longitude grid
            raise ValueError(f"{path} is not on a latitude/longitude grid: {src.crs}")
        width, height, transform, nodata = src.width, src.height, src.transform, src.nodata

    def file_rows(src):
        # the native dtype: an integer raster (VIIRS, DFO classes) needs no
        # NaN, because its counts test excludes the nodata value by itself,
        # and casting it to float64 moved 8x the bytes for nothing
        floating = np.issubdtype(np.dtype(src.dtypes[0]), np.floating)

        def read(i, k):
            values = src.read(1, window=Window(0, i, width, k))
            if floating and nodata is not None:
                values = np.where(values == nodata, np.nan, values)
            return values

        return read

    grid = global_grid(source, transform)
    offset = window_offset(grid[2], transform) if grid else None
    if grid and offset is None:
        logging.warning(f"{path} is off the {source} global grid, summarized on its own grid")
    if offset is None:
        lookup = load_lookup(source, width, height, transform, watersheds)

        def strips():
            step = strip_rows(width)
            with rasterio.open(path) as src:
                read = file_rows(src)
                for r0 in range(0, height, step):
                    yield r0, read(r0, min(step, height - r0))

        return summarize(source, lookup, strips(), transform, watersheds, value_above)

    grid_width, grid_height, grid_transform = grid
    lookup = load_lookup(source, grid_width, grid_height, grid_transform, watersheds)

    def strips():
        with rasterio.open(path) as src:
            yield from window_strips(file_rows(src), height, width, *offset, grid_width, grid_height)

    return summarize(source, lookup, strips(), grid_transform, watersheds, value_above)


def lattice_summary(grid, top, left, watersheds, res=HWRF_RES, value_above=-np.inf):
    """summarize an HAFS rain grid in memory (NaN where there is no rain)
    whose top-left pixel centre is (left, top): a window of the global HWRF
    grid, past 180 deg (0..360) for storms crossing the dateline"""
    width, height, transform = HWRF_LATTICE
    offset = window_offset(transform, from_origin(left - res / 2, top + res / 2, res, res))
    if offset is None:
        raise ValueError(f"rain grid at ({left}, {top}), {res} deg is off the {HWRF_RES} deg grid")
    lookup = load_lookup("hwrf", width, height, transform, watersheds)
    strips = window_strips(
        lambda i, k: grid[i : i + k], grid.shape[0], grid.shape[1], *offset, width, height
    )
    return summarize("hwrf", lookup, strips, transform, watersheds, value_above)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s", force=True)
    source = sys.argv[1]
    ws = watersheds_gdb_reader()
    if source == "hwrf":
        load_lookup(source, *HWRF_LATTICE, ws)
    else:
        with rasterio.open(sys.argv[2]) as src:
            grid = global_grid(source, src.transform)
            if grid and window_offset(grid[2], src.transform) is not None:
                load_lookup(source, *grid, ws)
            else:
                load_lookup(source, src.width, src.height, src.transform, ws)
