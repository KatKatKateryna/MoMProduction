# Backfill guide

Scripts that regenerate products for past dates. All of them run from the
repository root with the conda environment active, e.g.

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
   - GFMS: `backfill_gfms.py` (or `run_gfms_backfill.sh`)
   - HWRF and HAFS: `rain_backfill_from_backups.py`, or
     `hwrf_summary_backfill.py` for HWRF cycles that are not backed up
   - VIIRS: `viirs_summaries_from_images.py`, then move the new summaries into
     `VIIRS_summary/` by hand
   - GloFAS and DFO have no backfill script. Their summaries must already be
     in place.
2. **MoM products** (GFMS, HWRF, DFO and VIIRS MoM, final alerts, and the HAFS
   chain): `MoM_backfill.py --overwrite`. It only reads the summaries and
   only writes MoM and final alert files, so every step 1 run must be
   finished first.
3. **Database**: re-upload the regenerated summaries and MoM products with
   the scripts in `database_update/`.

Only redo what changed, together with everything after it. For example:

| Changed | Rerun |
|---|---|
| VIIRS area calculation | VIIRS summaries, then `MoM_backfill.py` (VIIRS MoM, HWRF+DFO+VIIRS, final alerts, HAFS chain), then the database |
| HWRF / HAFS rain calculation | `rain_backfill_from_backups.py`, then `MoM_backfill.py`, then the database |
| GFMS calculation | `backfill_gfms.py`, then `MoM_backfill.py`, then the database |
| Scoring / weightage only | `MoM_backfill.py`, then the database |

The regular cron jobs keep running during a backfill. They only process new
cycles, so they do not collide with a backfill of past dates.

## Scripts

### `backfill_gfms.py` (step 1, GFMS)

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
python backfill/backfill_gfms.py --start 20260101 --end 20261013 --max-days 220
```

### `run_gfms_backfill.sh` (step 1, GFMS)

A wrapper around `backfill_gfms.py`. It activates the conda env, which the
GFMS images need for `gdal_translate`, and holds a lock
(`/var/lock/gfms_backfill.lock`) so two runs never overlap. Dates come from
the `BACKFILL_START`, `BACKFILL_END` and `BACKFILL_MAX_DAYS` environment
variables. It is not in the crontab; run it by hand:

```bash
BACKFILL_START=20260101 BACKFILL_END=20261013 backfill/run_gfms_backfill.sh \
    >> backfill/logs/gfms_backfill.log 2>&1
```

### `rain_backfill_from_backups.py` (step 1, HWRF and HAFS)

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
python backfill/rain_backfill_from_backups.py 20261011 20261013   # first and last day
python backfill/rain_backfill_from_backups.py 20261011            # up to today
```

### `hwrf_summary_backfill.py` (step 1, HWRF)

Fills in **missing** HWRF summaries and images from the HWRF host, for cycles
that are not in the backups. Use `rain_backfill_from_backups.py` when a
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
python backfill/hwrf_summary_backfill.py 20261011 20261012   # default: yesterday and today
```

### `viirs_summaries_from_images.py` (step 1, VIIRS)

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
python backfill/viirs_summaries_from_images.py /root/MoM/Products/VIIRS/VIIRS_image \
    /root/MoM/Products/VIIRS/VIIRS_summary_new --start 20260601 --end 20261012
```

### `MoM_backfill.py` (step 2)

Regenerates the MoM products of the last `--days` days, ending at `--end`,
from the summaries already in place, for both the HWRF and the HAFS chain.

- **Offline:** nothing is downloaded and no host is contacted. A cycle whose
  GFMS summary is missing is skipped, because the MoM code would download
  GFMS. A missing HWRF/HAFS rain summary is never filled in from the hosts:
  the MoM goes on without the rain once the cycle is 12 h old, as the cron
  jobs do.
- **Reads:** the GFMS, GloFAS, HWRF, HAFS, DFO and VIIRS summaries, and the
  HAFS done markers.
- **GloFAS:** chosen exactly as the cron jobs choose it, by the same MoM code.
  The 00 and 06 HWRF/HAFS cycles use the latest GloFAS before that day; the
  12 and 18 cycles, and the daily GFMS MoM, use that day's GloFAS, or the
  latest earlier one if it is missing.
- **Writes, and nothing else:** the MoM and final alert folders.
  - GFMS MoM: `GFMS/GFMS_MoM/Final_Attributes_YYYYMMDD.csv` and `Attributes_Clean_…`
  - HWRF MoM, then HWRF+DFO+VIIRS: `HWRF/HWRF_MoM/*_YYYYMMDDHHHWRF*.csv`, for each of the 4 cycles of a day
  - Final alerts: `Final_Alert/Final_Attributes_YYYYMMDDHHHWRF+MOM+DFO+VIIRSUpdated_PDC.csv`
  - DFO MoM and VIIRS MoM: the `…18MOM+DFOUpdated.csv` and `…18MOM+DFO+VIIRSUpdated.csv` files of the day
  - HAFS: the same products, tagged `HAFS`, in `HAFS/HAFS_MoM`,
    `DFO/DFO_HAFS_MoM`, `VIIRS/VIIRS_HAFS_MoM` and `Final_Alerts_hafs`

  The MoM steps' temporary files go to a temporary folder under `/tmp`,
  removed at the end, not to the processing folders. The log goes to
  `backfill/logs/MoM_backfill_<time>.log`.
- **`--overwrite`:** deletes these outputs (HWRF and HAFS) for the days in the
  window first, so they are regenerated. Without it, every step keeps an
  output that already exists.
- **HAFS:** only its MoM chain runs. HAFS summaries are never computed here: a
  cycle that HAFS has not processed yet (no done marker) stops that day's
  HAFS MoM. Use `rain_backfill_from_backups.py` to recompute HAFS summaries.
- **Previous day:** the HWRF and HAFS MoM of the day before the window is
  built first, without alerts, because each final alert pairs with the same
  cycle of the previous day. `--overwrite` does not delete that day's
  outputs, so they are kept if they already exist.
- **Skips:**
  - cycles without a GFMS summary
  - the DFO and VIIRS MoM of a day unless all 4 HWRF cycles ran
  - cycles that have not happened yet
- **Stops** if today's GloFAS is not published yet.

```bash
python backfill/MoM_backfill.py --days 7 --dry-run            # show what would run and be removed
python backfill/MoM_backfill.py --days 3 --end 20261013 --overwrite
```
