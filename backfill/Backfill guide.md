# Backfill guide

Scripts that regenerate products for past dates. The prefix says whether a
script contacts any host:

- `offline_…` scripts only read local files (summaries, images, host data
  backups) and never download anything.
- `online_…` scripts download from the data hosts.

All of them run from the repository root with the conda environment active, e.g.

```bash
cd /root/MoMProduction
conda activate myenv
python backfill/<script>.py ...
```

Send the output of long runs to `backfill/logs/` (not kept in git), and run
them detached so they survive the session:

```bash
setsid nohup python -u backfill/<script>.py ... \
    > backfill/logs/<script>_$(date -u +%Y%m%dT%H%M).log 2>&1 < /dev/null &
```

## Order

The products build on each other:

```
source summaries / images  ->  MoM products  ->  database upload
(step 1, any order)            (step 2)           (step 3)
```

1. **Source summaries and images**, one script per source. These are
   independent of each other and can run in any order or in parallel:
   - GFMS: `offline_gfms_summaries_from_images.py` (summaries from the saved
     images), then move the new summaries into `GFMS_summary/` by hand; or
     `online_gfms_from_host.py` (or `online_gfms_from_host.sh`) to download
     the source files again and regenerate both images and summaries
   - HWRF and HAFS: `offline_hwrf_hafs_summaries_from_backups.py`, or
     `online_hwrf_summaries_from_host.py` for HWRF cycles that are not backed up
   - VIIRS: `offline_viirs_summaries_from_images.py`, then move the new summaries into
     `VIIRS_summary/` by hand
   - GloFAS and DFO have no backfill script. Their summaries must already be
     in place.
2. **MoM products** (GFMS, HWRF, DFO and VIIRS MoM, final alerts, and the HAFS
   chain): `offline_mom_from_summaries.py --out-dir <folder>`. It only reads the
   summaries and only writes into that folder, so every step 1 run must be
   finished first.
3. **Database**: re-upload the regenerated summaries and MoM products with
   the scripts in `database_update/`.

Only redo what changed, together with everything after it. For example:

| Changed | Rerun |
|---|---|
| VIIRS area calculation | VIIRS summaries, then `offline_mom_from_summaries.py` (VIIRS MoM, HWRF+DFO+VIIRS, final alerts, HAFS chain), then the database |
| HWRF / HAFS rain calculation | `offline_hwrf_hafs_summaries_from_backups.py`, then `offline_mom_from_summaries.py`, then the database |
| GFMS calculation | `offline_gfms_summaries_from_images.py` (or `online_gfms_from_host.py`), then `offline_mom_from_summaries.py`, then the database |
| Scoring / weightage only | `offline_mom_from_summaries.py`, then the database |

The regular cron jobs keep running during a backfill. They only process new
cycles, so they do not collide with a backfill of past dates.

## Scripts

### `offline_gfms_summaries_from_images.py` (step 1, GFMS)

Recomputes the GFMS summaries from the saved GFMS images with the current
calculation. Each image is a lossless copy of the grid its summary was
computed from, so the result is the same as a fresh run (checked: 16
summaries of 2026-10-10/11 came out byte-identical).

- **Reads, never changes:** `Flood_byStor_YYYYMMDDHH.tiff` in IMAGE_DIR,
  normally `GFMS/GFMS_image/`, and the summaries in `--prev-dir`, by default
  `GFMS/GFMS_summary/`, to continue the durations from.
- **Durations:** chained in time order, as the cron job does. Each day
  continues from the previous day's 21:00 summary: the one this run wrote,
  else the one already in OUT_DIR, else the one in `--prev-dir`. With none,
  the first time of the day starts its own duration (3 h or 0). So run whole
  periods oldest first, or start the period right after a day whose 21:00
  summary is good.
- **Writes, never overwrites:** `OUT_DIR/Flood_byStor_YYYYMMDDHH.csv`. A
  summary already in OUT_DIR is kept, and used for the following durations.
- **Lookup file:** `data/gfms_watershed_cells.npz` is created only if it does
  not exist. An existing one is never replaced.
- **Not touched:** `GFMS/GFMS_summary/`, the images, the MoM products, the
  database.
- Write to a new folder, check the results, then move the files into
  `GFMS/GFMS_summary/` yourself (replacing the old ones) before step 2.

```bash
python backfill/offline_gfms_summaries_from_images.py /root/MoM/Products/GFMS/GFMS_image \
    /root/MoM/Products/GFMS/GFMS_summary_regenerated --start 20261008 --end 20261013
```

### `online_gfms_from_host.py` (step 1, GFMS)

Regenerates the GFMS images and per-watershed summaries of past days.

- **Reads:** the GFMS `.bin` files, downloaded again from the GFMS host for
  each 3-hourly time.
- **Writes, overwriting:** `GFMS/GFMS_image/Flood_byStor_YYYYMMDDHH.tiff` and
  `GFMS/GFMS_summary/Flood_byStor_YYYYMMDDHH.csv`.
- **Its own state:** `GFMS/GFMS_backfill_state/`. A day gets `YYYYMMDD.done`
  when it is finished, or `.nodata` when the source has nothing for it. Days
  with a marker are never redone, so delete the markers of the days to run
  again.
- **Order of days:** oldest first, because each day's flood duration builds
  on the previous day's 21:00 summary. Only days up to yesterday are done
  (today's files include forecasts), and at most `--max-days` per run.
- **Temporary files:** the downloaded files in the GFMS processing folder are
  deleted after each day.
- **Not touched:** GloFAS, the MoM products, the database.

```bash
python backfill/online_gfms_from_host.py --start 20260101 --end 20261013 --max-days 220
```

### `online_gfms_from_host.sh` (step 1, GFMS)

A wrapper around `online_gfms_from_host.py`. It activates the conda env, which the
GFMS images need for `gdal_translate`, and holds a lock
(`/var/lock/gfms_backfill.lock`) so two runs never overlap. Dates come from
the `BACKFILL_START`, `BACKFILL_END` and `BACKFILL_MAX_DAYS` environment
variables. It is not in the crontab; run it by hand:

```bash
BACKFILL_START=20260101 BACKFILL_END=20261013 backfill/online_gfms_from_host.sh \
    >> backfill/logs/gfms_backfill.log 2>&1
```

### `offline_hwrf_hafs_summaries_from_backups.py` (step 1, HWRF and HAFS)

Regenerates the HWRF and HAFS rainfall summaries and images with the current
code (cell lookup, dateline handling, HAFS track window `WINDOW_KM`).

- **Reads, never changes:** only the host data backups,
  `HWRF/HWRF_host_data_backups/hwrf.YYYYMMDD/HH/*.rainfall.ascii.gz` and
  `HAFS/HAFS_host_data_backups/hfsa.YYYYMMDD/HH/` (storm tracks and rain
  rates). Cycles without a backup are skipped. The hosts are never contacted.
- **Writes, overwriting:**
  - `HWRF/HWRF_summary/hwrf.YYYYMMDDHHrainfall.csv` and
    `HWRF/HWRF_image/hwrf.YYYYMMDDHHrainfall.tiff`
  - `HAFS/HAFS_summary/hafs.YYYYMMDDHHrainfall.csv` and
    `HAFS/HAFS_image/hafs.YYYYMMDDHHrainfall.tiff`
- A cycle with no rain over any watershed gets a summary with only the header
  row, and its old image is removed.
- A cycle that fails keeps its old summary and image.
- **Temporary files:** in `hwrf/backfill_work/` and the HAFS `work/` folder,
  under the processing directory; removed at the end.
- **Not touched:** the backups, the HAFS tracks and done markers, the MoM
  products, the database.

```bash
python backfill/offline_hwrf_hafs_summaries_from_backups.py 20261011 20261013   # first and last day
python backfill/offline_hwrf_hafs_summaries_from_backups.py 20261011            # up to today
```

### `online_hwrf_summaries_from_host.py` (step 1, HWRF)

Fills in **missing** HWRF summaries and images from the HWRF host, for cycles
that are not in the backups. Use `offline_hwrf_hafs_summaries_from_backups.py` when a
backup exists.

- **Reads:** the HWRF host's `rainfall.ascii` files, downloaded into the HWRF
  processing folder.
- **Writes:** `HWRF_summary/hwrf.YYYYMMDDHHrainfall.csv` and
  `HWRF_image/hwrf.YYYYMMDDHHrainfall.tiff`, only for cycles that have no
  summary, or only a zero-value placeholder. A cycle that already has a real
  summary is skipped, so this script cannot be used to recompute.
- **Not touched:** the MoM products, the database.
- The host keeps only the last few days, so older cycles cannot be filled.

```bash
python backfill/online_hwrf_summaries_from_host.py 20261011 20261012   # default: yesterday and today
```

### `offline_viirs_summaries_from_images.py` (step 1, VIIRS)

Recomputes the VIIRS summaries from the saved VIIRS images with the current
calculation (true cell areas; only pixels coded 141–200 count as flooded).

- **Reads, never changes:** `VIIRS_1day_compositeYYYYMMDD_flood.tiff` and
  `VIIRS_5day_compositeYYYYMMDD_flood.tiff` in IMAGE_DIR, normally
  `VIIRS/VIIRS_image/`.
- **Writes, never overwrites:** `OUT_DIR/VIIRS_Flood_YYYYMMDD.csv`. A summary
  already in OUT_DIR is skipped. A date with only one of the two images is
  skipped.
- **Lookup file:** `data/viirs_watershed_runs.npz` is created by the first
  run if it does not exist (about 6 minutes). An existing one is never
  replaced.
- **Not touched:** `VIIRS/VIIRS_summary/`, the MoM products, the database.
- Write to a new folder, check the results, then move the files into
  `VIIRS/VIIRS_summary/` yourself (replacing the old ones) before step 2.

```bash
python backfill/offline_viirs_summaries_from_images.py /root/MoM/Products/VIIRS/VIIRS_image \
    /root/MoM/Products/VIIRS/VIIRS_summary_new --start 20260601 --end 20261012
```

### `offline_mom_from_summaries.py` (step 2)

Regenerates the MoM products of the last `--days` days, ending at `--end`,
from the summaries already in place, for both the HWRF and the HAFS chain,
into a separate output folder.

- **Offline:** nothing is downloaded and no host is contacted. A cycle whose
  GFMS summary is missing is skipped, because the MoM code would download
  GFMS. A missing rain summary is never filled in from the hosts.
- **Reads, never changes:** the GFMS, GloFAS, HWRF, HAFS, DFO and VIIRS
  summaries under `--input-root` (default `/root/MoM`), in the same layout
  as `/root/MoM` (`Products/GFMS/GFMS_summary/`, …).
- **Writes, and nothing else:** the output folder `--out-dir` (required):

  | Folder | Contents |
  |---|---|
  | `gfms_mom/` | GFMS MoM: `Final_Attributes_YYYYMMDD.csv`, `Attributes_Clean_…` |
  | `hwrf_mom/` | HWRF MoM and HWRF+DFO+VIIRS, for each of the 4 cycles of a day (tag `HWRF`) |
  | `dfo_mom/`, `viirs_mom/` | DFO and VIIRS MoM of the HWRF chain (the `…18MOM+DFOUpdated` / `…18MOM+DFO+VIIRSUpdated` files) |
  | `hafs_mom/` | HAFS MoM and HAFS+DFO+VIIRS (tag `HAFS`) |
  | `dfo_hafs_mom/`, `viirs_hafs_mom/` | DFO and VIIRS MoM of the HAFS chain |
  | `Final_Alerts/` | final alerts of both chains (`…HWRF+MOM+DFO+VIIRSUpdated_PDC.csv`, `…HAFS+MOM+…`) |

  The MoM steps' temporary files go to a temporary folder under `/tmp`,
  removed at the end. The log goes to
  `backfill/logs/offline_mom_from_summaries_<time>.log`.
- **Rain:** the MoM of a cycle uses the rain summary of the previous cycle.
  - HWRF chain: the HWRF summary. With `--hafs-fallback`, a cycle that has
    no HWRF summary uses the HAFS summary of that cycle instead; the outputs
    are still tagged `HWRF`, and the log says which source was used. Without
    either, the MoM goes on without rain once the cycle is 12 h old, as the
    cron job does.
  - HAFS chain: a cycle runs only when the HAFS summary of the previous
    cycle exists.
- **GloFAS:** chosen exactly as the cron jobs choose it, by the same MoM code.
  The 00 and 06 cycles use the latest GloFAS before that day; the 12 and 18
  cycles, and the daily GFMS MoM, use that day's GloFAS, or the latest
  earlier one if it is missing. Today's GFMS MoM is skipped until today's
  GloFAS is published.
- **`--overwrite`** (off by default): deletes the outputs of the days in the
  window from the output folder first, so they are regenerated. Without it,
  every step keeps an output that is already there.
- **Previous day:** the MoM of the day before the window is built first,
  without alerts, because each final alert pairs with the same cycle of the
  previous day. `--overwrite` does not delete that day's outputs.
- **Skips:**
  - cycles without a GFMS summary
  - the DFO and VIIRS MoM of a day unless all 4 cycles of that chain ran
  - cycles that have not happened yet

```bash
python backfill/offline_mom_from_summaries.py --days 7 --out-dir /root/MoM/mom_rerun --dry-run
python backfill/offline_mom_from_summaries.py --days 3 --end 20261013 --out-dir /root/MoM/mom_rerun
python backfill/offline_mom_from_summaries.py --days 3 --end 20261013 --out-dir /root/MoM/mom_rerun \
    --hafs-fallback --overwrite --input-root /data/MoM_copy
```
