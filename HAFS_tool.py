"""
HAFS_tool.py
    -- cron job for NOAA HAFS hurricane rainfall, run alongside the HWRF
       workflow (MoM_run.py -j HAFS) and never inside it

    For every HAFS cycle, the rain of each storm is taken from its parent
    swath file (average rain rate over 0-3 h, 0-6 h, ... 0-126 h; the 3 h rain
    is the difference of consecutive windows), keeping each 3 h step only
    within WINDOW_KM of the track segment of that step: a
    storm-following window like the HWRF moving nest. The storms are merged
    into one 0-126 h total in inches on the HWRF 0.05 deg grid, and summarized
    per watershed with the HWRF calculation:
        HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff
        HAFS/HAFS_summary/hafs.YYYYMMDDHHrainfall.csv
        HAFS/HAFS_track/hafs.<storm>.YYYYMMDDHH.track.geojson

    The host data used is kept in HAFS/HAFS_host_data_backups/hfsa.YYYYMMDD/HH/
    (host layout and names): the track (trak.atcfunix) and the rain rate
    records of the swath file, copied unchanged (parent.swath.prate.grb2).
    A cycle already kept there is read from it instead of the host.

    The MoM chain is then run with the unchanged HWRF functions, reading the
    HAFS summaries (settings.HWRF_SUM_DIR / HWRF_SUM_PREFIX) and with every
    output folder pointed at the HAFS ones, so the calculations are identical
    to HWRF but nothing is mixed:
        HAFS/HAFS_summary/hafs.* <- HWRF/HWRF_summary/hwrf.*
        HAFS/HAFS_MoM            <- HWRF/HWRF_MoM
        DFO/DFO_HAFS_MoM         <- DFO/DFO_MoM
        VIIRS/VIIRS_HAFS_MoM     <- VIIRS/VIIRS_MoM
        Final_Alerts_hafs        <- Final_Alert
    The shared inputs (GFMS, GloFAS, DFO, VIIRS summaries) are only read.

    Turned on and off with ENABLED in the [hafs] section of production.cfg.

**EXAMPLE**
    python HAFS_tool.py                                  # same as the cron job
    python HAFS_tool.py --days 3 --end 20261012 --overwrite
    python HAFS_tool.py --backup                         # host data only
"""

import argparse
import contextlib
import csv
import glob
import json
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import geopandas as gpd
import numpy as np
import rasterio
import requests
from osgeo import gdal  # noqa: F401
from rasterio.transform import from_origin

# eccodes (pip wheel) must be imported after GDAL/rasterio/geopandas: imported
# first, its bundled libraries clash with the conda GDAL ones and the process
# crashes on exit
import eccodes  # isort: skip

import settings
from utilities import RAIN_SUMMARY_HEADERS, hafs_storms, write_empty_rain_summary

HOURS = ["00", "06", "12", "18"]
RES = 0.05
NODATA = -999.0
MM_PER_INCH = 25.4
# HAFS posts a cycle ~6 h after its time; a cycle with no storm by EMPTY_FINAL
# is taken as empty, and storm files still missing by STORMS_FINAL are skipped
PUBLISHED_AFTER = timedelta(hours=5)
EMPTY_FINAL = timedelta(hours=8)
STORMS_FINAL = timedelta(hours=12)

# internal folders: processed-cycle markers and temporary files
DONE_DIR = os.path.join(settings.HAFS_PROC_DIR, "done")
WORK_DIR = os.path.join(settings.HAFS_PROC_DIR, "work")

# per-watershed summaries from the precomputed cell lookup (hwrf_cells.py);
# False = the original per-watershed masks
HAFS_CELL_LOOKUP = True


def utc_now():
    return datetime.now(timezone.utc)


def cycle_time(cycle):
    return datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def shift_cycle(cycle, hours):
    return (cycle_time(cycle) + timedelta(hours=hours)).strftime("%Y%m%d%H")


def cycle_url(cycle):
    return f"{settings.HAFS_HOST.rstrip('/')}/{settings.HAFS_MODEL}/{cycle[:8]}/{cycle[8:]}/"


def make_dirs():
    for d in (
        settings.HAFS_SUM_DIR, settings.HAFS_IMG_DIR, settings.HAFS_MOM_DIR,
        settings.HAFS_TRACK_DIR, settings.DFO_HAFS_DIR, settings.VIIRS_HAFS_DIR,
        settings.FINAL_HAFS_DIR, settings.HAFS_BACKUP_DIR, DONE_DIR,
        WORK_DIR,
    ):
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# HAFS data: storms, tracks and rain
# ---------------------------------------------------------------------------


def list_storms(cycle):
    """storm ids (e.g. 20e, 09l) HAFS has for a cycle"""
    return hafs_storms(cycle)


def exists(url):
    return requests.head(url, timeout=60).status_code == 200


def backup_path(cycle, name):
    """host file kept in HAFS_host_data_backups, host layout"""
    return os.path.join(
        settings.HAFS_BACKUP_DIR, f"{settings.HAFS_MODEL}.{cycle[:8]}", cycle[8:], name
    )


def track_file(cycle, storm):
    """trak.atcfunix of a storm, from the backups or saved there from the host"""
    name = f"{storm}.{cycle}.{settings.HAFS_MODEL}.trak.atcfunix"
    path = backup_path(cycle, name)
    if not os.path.exists(path):
        part = f"{path}.{os.getpid()}.part"
        reqs = requests.get(cycle_url(cycle) + name, timeout=60)
        reqs.raise_for_status()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(part, "wb") as f:
            f.write(reqs.content)
        os.replace(part, path)
    return path


def is_rain_rate(gid):
    """0-N h average rain rate record"""
    return (eccodes.codes_get(gid, "shortName") == "prate"
            and eccodes.codes_get(gid, "typeOfStatisticalProcessing") == 0)


def rain_file(cycle, storm):
    """rain rate records of a storm's swath file, unchanged, from the backups
    or saved there from the host (the swath file is ~300 MB, these ~1/10)"""
    name = f"{storm}.{cycle}.{settings.HAFS_MODEL}.parent.swath.grb2"
    path = backup_path(cycle, name.replace(".grb2", ".prate.grb2"))
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = f"{path}.{os.getpid()}.part"
    with tempfile.TemporaryFile(dir=WORK_DIR) as tmp:
        with requests.get(cycle_url(cycle) + name, stream=True, timeout=600) as reqs:
            reqs.raise_for_status()
            for block in reqs.iter_content(chunk_size=1 << 22):
                tmp.write(block)
        tmp.seek(0)
        kept = 0
        with open(part, "wb") as out:
            while True:
                gid = eccodes.codes_grib_new_from_file(tmp)
                if gid is None:
                    break
                try:
                    if is_rain_rate(gid):
                        out.write(eccodes.codes_get_message(gid))
                        kept += 1
                finally:
                    eccodes.codes_release(gid)
    if not kept:
        os.remove(part)
        raise RuntimeError(f"no rain rates in {cycle_url(cycle)}{name}")
    os.replace(part, path)
    return path


def backup_cycle(cycle):
    """keep the host data of every storm of a cycle published so far"""
    if utc_now() - cycle_time(cycle) < PUBLISHED_AFTER:
        return
    for storm in list_storms(cycle):
        base = f"{cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}"
        if exists(base + ".parent.swath.grb2") and exists(base + ".trak.atcfunix"):
            track_file(cycle, storm)
            rain_file(cycle, storm)


def fetch_track(cycle, storm):
    """forecast track from trak.atcfunix: one point per forecast hour
    (the file repeats each hour for the 34/50/64 kt wind radii)"""
    path = track_file(cycle, storm)
    with open(path) as f:
        text = f.read()
    track = {}
    for line in text.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 10:
            continue
        fhour = int(p[5])
        if fhour in track:
            continue
        lat = int(p[6][:-1]) / 10 * (1 if p[6][-1] == "N" else -1)
        lon = int(p[7][:-1]) / 10 * (-1 if p[7][-1] == "W" else 1)
        track[fhour] = {"fhour": fhour, "lat": lat, "lon": lon,
                        "vmax_kt": int(p[8]), "mslp_hpa": int(p[9])}
    if not track:
        raise RuntimeError(f"no track positions in {path}")
    return [track[h] for h in sorted(track)]


def norm_lons(lons):
    """longitudes in -180..180, or 0..360 when the storm crosses the dateline
    (as HWRF rainfall.ascii does)"""
    west = np.where(lons > 180, lons - 360, lons)
    if west.max() - west.min() <= 180:
        return west
    return np.where(west < 0, west + 360, west)


def distance_km(lats, lons, lat0, lon0):
    la, lo = np.radians(lats), np.radians(lons)
    la0, lo0 = np.radians(lat0), np.radians(lon0)
    cosd = np.sin(la) * np.sin(la0) + np.cos(la) * np.cos(la0) * np.cos(lo - lo0)
    return 6371 * np.arccos(np.clip(cosd, -1, 1))


def unit_vectors(lats, lons):
    la, lo = np.radians(lats), np.radians(lons)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], axis=-1)


def segment_distance_km(lats, lons, start, end):
    """great-circle distance to the track segment start-end; to the nearer
    end where the closest point of the great circle is outside the segment"""
    to_ends = np.minimum(distance_km(lats, lons, *start), distance_km(lats, lons, *end))
    a, b = unit_vectors(*start), unit_vectors(*end)
    n = np.cross(a, b)
    if np.linalg.norm(n) < 1e-12:  # storm did not move
        return to_ends
    n /= np.linalg.norm(n)
    p = unit_vectors(lats, lons)
    pn = p @ n
    c = p - pn[..., None] * n  # projection onto the great circle of the segment
    inside = (np.cross(a, c) @ n >= 0) & (np.cross(c, b) @ n >= 0)
    return np.where(inside, 6371 * np.abs(np.arcsin(np.clip(pn, -1, 1))), to_ends)


def storm_rain(cycle, storm, track):
    """0-126 h storm-following rain (inches) of one storm on a 0.05 deg grid:
    (grid, top, left), grid NaN outside the window"""
    path = rain_file(cycle, storm)
    with open(path, "rb") as tmp:
        # first pass: where the 0-N h average rain rates are
        rates = []
        while True:
            gid = eccodes.codes_grib_new_from_file(tmp)
            if gid is None:
                break
            try:
                if is_rain_rate(gid):
                    eccodes.codes_set(gid, "stepUnits", "h")
                    rates.append((int(eccodes.codes_get(gid, "endStep")), int(eccodes.codes_get(gid, "offset"))))
            finally:
                eccodes.codes_release(gid)
        if not rates:
            raise RuntimeError(f"no rain rates in {path}")

        # second pass: 3 h rain, kept within the window around the track
        # segment the storm moves along during the step
        positions = {t["fhour"]: (t["lat"], t["lon"]) for t in track}
        total = window = lats = lons = None
        prev_acc, prev_hour, skipped = None, 0, 0
        for hour, offset in sorted(rates):
            tmp.seek(offset)
            gid = eccodes.codes_grib_new_from_file(tmp)
            try:
                if lats is None:
                    shape = (eccodes.codes_get(gid, "Nj"), eccodes.codes_get(gid, "Ni"))
                    lats = eccodes.codes_get_array(gid, "latitudes").reshape(shape)
                    lons = norm_lons(eccodes.codes_get_array(gid, "longitudes").reshape(shape))
                    total = np.zeros(shape)
                    window = np.zeros(shape, bool)
                    prev_acc = np.zeros(shape)
                values = eccodes.codes_get_values(gid).reshape(lats.shape)
                if eccodes.codes_get(gid, "bitmapPresent"):
                    values = np.where(values == eccodes.codes_get(gid, "missingValue"), np.nan, values)
            finally:
                eccodes.codes_release(gid)
            acc = values * hour * 3600  # kg m-2 = mm over 0-hour
            step = np.clip(acc - prev_acc, 0, None)  # GRIB packing noise can go < 0
            prev_acc = acc
            start, end = positions.get(prev_hour), positions.get(hour)
            prev_hour = hour
            if start is None:
                skipped += 1
                continue
            # past the end of the track the step keeps only its start
            near = segment_distance_km(lats, lons, start, end or start) <= settings.HAFS_WINDOW_KM
            total += np.where(near & np.isfinite(step), step, 0)
            window |= near
    if skipped:
        logging.info(f"HAFS {storm} {cycle}: {skipped} steps beyond the end of the track skipped")
    if not window.any():
        raise RuntimeError(f"HAFS {storm} {cycle}: storm window is empty")

    # nearest parent cell onto a 0.05 deg grid around the window, pixel
    # centres on multiples of RES
    top = np.ceil(lats[window].max() / RES) * RES
    left = np.floor(lons[window].min() / RES) * RES
    ny = int(round((top - np.floor(lats[window].min() / RES) * RES) / RES)) + 1
    nx = int(round((np.ceil(lons[window].max() / RES) * RES - left) / RES)) + 1
    dlat, dlon = lats[1, 0] - lats[0, 0], lons[0, 1] - lons[0, 0]
    ri = np.rint(((top - RES * np.arange(ny))[:, None] - lats[0, 0]) / dlat).astype(int)
    ci = np.rint(((left + RES * np.arange(nx))[None, :] - lons[0, 0]) / dlon).astype(int)
    ri, ci = np.broadcast_arrays(np.clip(ri, 0, lats.shape[0] - 1), np.clip(ci, 0, lats.shape[1] - 1))
    grid = np.where(window[ri, ci], total[ri, ci] / MM_PER_INCH, np.nan)
    return grid, top, left


def track_lons(track, left, ncols):
    """track longitudes in the same convention as the grid"""
    east = left + ncols * RES > 180
    return [t["lon"] + 360 if east and t["lon"] < 0 else t["lon"] for t in track]


def write_track(path, storm, cycle, track, left, ncols):
    lons = track_lons(track, left, ncols)
    features = [{
        "type": "Feature",
        "properties": {"storm": storm, "cycle": cycle},
        "geometry": {"type": "LineString", "coordinates": [[x, t["lat"]] for t, x in zip(track, lons)]},
    }]
    for t, x in zip(track, lons):
        features.append({
            "type": "Feature",
            "properties": {"storm": storm, "cycle": cycle, **t},
            "geometry": {"type": "Point", "coordinates": [x, t["lat"]]},
        })
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f)


def merge(grids):
    """merge storm grids on one canvas, keeping the larger value where they overlap"""
    top = max(g[1] for g in grids)
    left = min(g[2] for g in grids)
    bottom = min(g[1] - (g[0].shape[0] - 1) * RES for g in grids)
    right = max(g[2] + (g[0].shape[1] - 1) * RES for g in grids)
    ny = int(round((top - bottom) / RES)) + 1
    nx = int(round((right - left) / RES)) + 1
    canvas = np.full((ny, nx), np.nan)
    for grid, gtop, gleft in grids:
        r0 = int(round((top - gtop) / RES))
        c0 = int(round((gleft - left) / RES))
        view = canvas[r0 : r0 + grid.shape[0], c0 : c0 + grid.shape[1]]
        view[:] = np.fmax(view, grid)
    return canvas, top, left


def write_tiff(path, grid, top, left):
    """same layout as the HWRF images: -999 where there is no rain or no data"""
    out = np.where(np.isfinite(grid) & (grid > 0), grid, NODATA)
    transform = from_origin(left - RES / 2, top + RES / 2, RES, RES)
    with rasterio.open(
        path, "w", driver="GTiff", height=out.shape[0], width=out.shape[1], count=1,
        dtype="float64", crs="EPSG:4326", transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(out, 1)


def summarize(raintiff, output_csv, grid, top, left):
    """per-watershed summary with the HWRF calculation; True if any watershed
    has rain"""
    from HWRF_tool import HWRF_extract_by_mask
    from utilities import watersheds_gdb_reader

    watersheds = watersheds_gdb_reader()

    # fast path: precomputed watershed cells (hwrf_cells.py), same rows
    if HAFS_CELL_LOOKUP:
        from hwrf_cells import summary_rows

        try:
            rows = summary_rows(np.where(grid > 0, grid, np.nan), top, left, watersheds)
        except Exception:
            logging.exception("HAFS cell lookup failed, using the per-watershed masks")
            rows = None
        if rows is not None:
            with open(output_csv, "w") as f:
                writer = csv.writer(f)
                writer.writerow(RAIN_SUMMARY_HEADERS)
                writer.writerows(rows)
            return len(rows) > 0

    # watersheds away from the rain give no rows; skip them for speed
    rainy = np.isfinite(grid) & (grid > 0)
    rows, cols = np.nonzero(rainy)
    minx, maxx = left + cols.min() * RES - RES, left + cols.max() * RES + RES
    miny, maxy = top - rows.max() * RES - RES, top - rows.min() * RES + RES
    candidates = watersheds.cx[minx:maxx, miny:maxy].index
    if maxx > 180:
        # grid past the dateline: watersheds there are at longitude - 360
        candidates = candidates.union(watersheds.cx[minx - 360:maxx - 360, miny:maxy].index)

    has_data = False
    with open(output_csv, "w") as f:
        writer = csv.writer(f)
        writer.writerow(RAIN_SUMMARY_HEADERS)
        for the_pfafid in watersheds.index.tolist():
            if the_pfafid not in candidates:
                continue
            test_json = json.loads(
                gpd.GeoSeries([watersheds.loc[the_pfafid, "geometry"]]).to_json()
            )
            if test_json["features"][0]["geometry"] is None:
                continue
            data_points = HWRF_extract_by_mask(test_json, raintiff)
            if data_points.empty:
                continue
            total_area = data_points["area"].sum()
            writer.writerow([
                the_pfafid,
                total_area,
                total_area / watersheds.loc[the_pfafid]["area_km2"] * 100,
                data_points["intensity"].mean(),
                data_points["intensity"].max(),
            ])
            has_data = True
    return has_data


def mark_done(cycle, note):
    with open(os.path.join(DONE_DIR, cycle), "w") as f:
        f.write(note + "\n")


def is_done(cycle):
    return os.path.exists(os.path.join(DONE_DIR, cycle))


def empty_summary(cycle, note):
    """settle a cycle without rain over any watershed: header-only summary,
    no image"""
    write_empty_rain_summary(os.path.join(settings.HAFS_SUM_DIR, f"hafs.{cycle}rainfall.csv"))
    logging.info(f"HAFS {cycle}: {note}, empty summary written")
    mark_done(cycle, note)


def process_cycle(cycle):
    """HAFS summary, image and tracks of one cycle; True once the cycle is
    settled (processed, or known to have nothing; then the summary is empty)"""
    if is_done(cycle):
        return True
    age = utc_now() - cycle_time(cycle)
    if age < PUBLISHED_AFTER:
        return False

    storms = list_storms(cycle)
    if not storms:
        if age >= EMPTY_FINAL:
            empty_summary(cycle, "no storms")
            return True
        return False

    ready = []
    for storm in storms:
        base = f"{cycle_url(cycle)}{storm}.{cycle}.{settings.HAFS_MODEL}"
        if exists(base + ".parent.swath.grb2") and exists(base + ".trak.atcfunix"):
            ready.append(storm)
        elif age < STORMS_FINAL:
            logging.info(f"HAFS {cycle}: {storm} not published yet, waiting")
            return False
        else:
            logging.warning(f"HAFS {cycle}: {storm} files missing, skipped")
    logging.info(f"HAFS {cycle}: storms {' '.join(ready)}")

    grids = []
    for storm in ready:
        track = fetch_track(cycle, storm)
        grid, top, left = storm_rain(cycle, storm, track)
        write_track(
            os.path.join(settings.HAFS_TRACK_DIR, f"hafs.{storm}.{cycle}.track.geojson"),
            storm, cycle, track, left, grid.shape[1],
        )
        grids.append((grid, top, left))
        logging.info(f"HAFS {cycle}: {storm} max {np.nanmax(grid):.2f} in")
    if not grids:
        empty_summary(cycle, "no storm data")
        return True

    grid, top, left = merge(grids)
    raintiff = os.path.join(WORK_DIR, f"hafs.{cycle}rainfall.tiff")
    output_csv = os.path.join(WORK_DIR, f"hafs.{cycle}rainfall.csv")
    write_tiff(raintiff, grid, top, left)
    if np.nanmax(np.nan_to_num(grid)) > 0 and summarize(raintiff, output_csv, grid, top, left):
        shutil.move(output_csv, os.path.join(settings.HAFS_SUM_DIR, os.path.basename(output_csv)))
        shutil.move(raintiff, os.path.join(settings.HAFS_IMG_DIR, os.path.basename(raintiff)))
        logging.info(f"HAFS {cycle}: generated summary and image")
        mark_done(cycle, " ".join(ready))
    else:
        # same as HWRF: no rain over any watershed, empty summary, no image
        for f in (raintiff, output_csv):
            if os.path.exists(f):
                os.remove(f)
        empty_summary(cycle, " ".join(ready) + " (no rain over watersheds)")
    return True


# ---------------------------------------------------------------------------
# MoM chain with the unchanged HWRF functions on the HAFS folders
# ---------------------------------------------------------------------------


def _no_gfms_download(adate):
    raise RuntimeError(f"HAFS never re-downloads GFMS ({adate})")


@contextlib.contextmanager
def hafs_folders():
    """point the HWRF/DFO/VIIRS MoM and final alert folders at the HAFS ones
    for the duration of the block, and always put them back"""
    import GFMS_tool

    patched = {
        "HWRF_SUM_DIR": settings.HAFS_SUM_DIR,
        "HWRF_SUM_PREFIX": "hafs",
        "HWRF_FILE_TAG": "HAFS",
        "HWRF_MOM_DIR": settings.HAFS_MOM_DIR,
        "HWRF_PROC_DIR": WORK_DIR,
        "DFO_MOM_DIR": settings.DFO_HAFS_DIR,
        "DFO_PROC_DIR": WORK_DIR,
        "VIIRS_MOM_DIR": settings.VIIRS_HAFS_DIR,
        "VIIRS_PROC_DIR": WORK_DIR,
        "FINAL_MOM": settings.FINAL_HAFS_DIR,
        "FINAL_MOM_DIR": settings.FINAL_HAFS_DIR,
    }
    saved = {k: getattr(settings, k) for k in patched}
    saved_fixdate = GFMS_tool.GFMS_fixdate
    cwd = os.getcwd()
    try:
        for k, v in patched.items():
            setattr(settings, k, v)
        GFMS_tool.GFMS_fixdate = _no_gfms_download
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)
        GFMS_tool.GFMS_fixdate = saved_fixdate
        os.chdir(cwd)


def run_step(name, func, *args):
    try:
        func(*args)
    except Exception:
        logging.exception(f"HAFS {name} failed for {args}")


def run_cycle_mom(cycle, with_alert=True):
    """HAFS MoM, +DFO+VIIRS and final alert of a cycle; False if the cycle is
    not due yet (later cycles are not either)"""
    from HWRF_MoM import final_alert_pdc, update_HWRF_MoM, update_HWRFMoM_DFO_VIIRS

    if cycle_time(cycle) > utc_now():
        return False
    # the HWRF MoM of a cycle uses the rain of the previous cycle
    if not is_done(shift_cycle(cycle, -6)):
        logging.info(f"HAFS MoM {cycle}: previous cycle not settled yet")
        return False
    # update_HWRF_MoM would download GFMS for a missing summary: skip instead
    gfms_sum = os.path.join(settings.GFMS_SUM_DIR, f"Flood_byStor_{cycle}.csv")
    if not os.path.exists(gfms_sum):
        logging.warning(f"HAFS MoM {cycle}: no GFMS summary, skipped (retried next run)")
        return True
    with hafs_folders():
        run_step("MoM", update_HWRF_MoM, cycle)
        run_step("MoM+DFO+VIIRS", update_HWRFMoM_DFO_VIIRS, cycle)
        if with_alert:
            run_step("final alert", final_alert_pdc, cycle)
    return True


def run_day_dfo_viirs(adate):
    """HAFS-based DFO MoM and VIIRS MoM of a day, once its 4 HAFS MoM exist"""
    from DFO_MoM import update_DFO_MoM
    from VIIRS_MoM import update_VIIRS_MoM

    for hh in HOURS:
        mom = os.path.join(settings.HAFS_MOM_DIR, f"Final_Attributes_{adate}{hh}HAFSUpdated.csv")
        if not os.path.exists(mom):
            return
    with hafs_folders():
        run_step("DFO MoM", update_DFO_MoM, adate)
        run_step("VIIRS MoM", update_VIIRS_MoM, adate)


def hafs_outputs(adate):
    """HAFS MoM, DFO/VIIRS and final alert outputs of a day (YYYYMMDD)"""
    patterns = [
        os.path.join(settings.HAFS_MOM_DIR, f"*_{adate}[0-9][0-9]HAFS*.csv"),
        os.path.join(settings.DFO_HAFS_DIR, f"*_{adate}18MOM+DFOUpdated.csv"),
        os.path.join(settings.VIIRS_HAFS_DIR, f"*_{adate}18MOM+DFO+VIIRSUpdated.csv"),
        os.path.join(settings.FINAL_HAFS_DIR, f"Final_Attributes_{adate}[0-9][0-9]HAFS+MOM+DFO+VIIRSUpdated_PDC.csv"),
    ]
    return sorted(f for p in patterns for f in glob.glob(p))


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------


def run(dates, warmup=None, overwrite=False):
    """HAFS data and MoM for the given days (oldest first); the warm-up day
    only provides the previous-day pairs of the final alerts"""
    make_dirs()
    if overwrite:
        for adate in dates:
            for f in hafs_outputs(adate):
                logging.info(f"HAFS remove: {f}")
                os.remove(f)

    days = ([warmup] if warmup else []) + list(dates)
    first = days[0] + "00"
    cycles = [shift_cycle(first, -6)] + [d + hh for d in days for hh in HOURS]
    for cycle in cycles:
        if cycle_time(cycle) > utc_now():
            break
        try:
            process_cycle(cycle)
        except Exception:
            logging.exception(f"HAFS data failed for {cycle}")

    for adate in days:
        due = True
        for hh in HOURS:
            due = run_cycle_mom(adate + hh, with_alert=adate != warmup)
            if not due:
                break
        if adate != warmup:
            run_day_dfo_viirs(adate)
        if not due:
            break


def HAFS_cron():
    """cron job: catch up the last LOOKBACK_DAYS days"""
    if not settings.HAFS_ENABLED:
        logging.info("HAFS disabled in production.cfg, nothing to do")
        return
    today = utc_now().date()
    dates = [
        (today - timedelta(days=i)).strftime("%Y%m%d")
        for i in range(settings.HAFS_LOOKBACK_DAYS - 1, -1, -1)
    ]
    run(dates)


def HAFS_backup():
    """cron (with the HWRF host backup): keep the host data of the last
    LOOKBACK_DAYS days"""
    make_dirs()
    start = (utc_now() - timedelta(days=settings.HAFS_LOOKBACK_DAYS)).strftime("%Y%m%d") + "00"
    cycle = start
    while cycle_time(cycle) <= utc_now():
        try:
            backup_cycle(cycle)
        except Exception:
            logging.exception(f"HAFS backup failed for {cycle}")
        cycle = shift_cycle(cycle, 6)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=int, help="backfill this many days up to --end")
    parser.add_argument(
        "--end", default=utc_now().strftime("%Y%m%d"),
        help="last day to backfill, YYYYMMDD (default: today, UTC)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="delete the HAFS MoM, DFO/VIIRS and final alert outputs of these days first",
    )
    parser.add_argument(
        "--backup", action="store_true",
        help="only keep the host data of the last LOOKBACK_DAYS days",
    )
    args = parser.parse_args()

    settings.configure_logging("HAFS")
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    if not settings.HAFS_ENABLED:
        logging.info("HAFS disabled in production.cfg, nothing to do")
        return
    if args.backup:
        HAFS_backup()
        return
    if not args.days:
        HAFS_cron()
        return
    end = datetime.strptime(args.end, "%Y%m%d")
    dates = [(end - timedelta(days=i)).strftime("%Y%m%d") for i in range(args.days - 1, -1, -1)]
    warmup = (end - timedelta(days=args.days)).strftime("%Y%m%d")
    logging.info(f"HAFS backfill {dates[0]} - {dates[-1]}, overwrite={args.overwrite}")
    run(dates, warmup=warmup, overwrite=args.overwrite)
    logging.info("HAFS backfill finished")


if __name__ == "__main__":
    main()
