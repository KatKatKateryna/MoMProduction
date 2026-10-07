"""
backfill_gfms.py -- regenerate missing GFMS image (tiff) products, and the
matching per-watershed summary CSVs, for historical dates.

Scope is deliberately narrow: GFMS image + GFMS summary only. It does not
touch GloFAS, HWRF, DFO, VIIRS, the Final_Alert composite, or the database.

Why this exists: image products were never generated under cron (gdal_translate
was unresolvable without an activated conda env), so GFMS_image/ is empty while
GFMS_summary/ is fully populated. GFMS_processing() only calls the extractor
when the summary CSV is absent, so the normal job will never regenerate these.

Each run walks forward from --start, finds the earliest day still missing any
of its 8 three-hourly images, and processes at most --max-days such days.
--end caps the range: nothing after it is ever looked at. Dates that are not
yet settled are excluded regardless of --end.

Summaries are recomputed and overwrite any existing file for the same date.

Usage:
    python backfill_gfms.py --start 20260201 --end 20261008 --max-days 4
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import settings
from GFMS_tool import GFMS_data_extractor, GFMS_fix_duration

BINHOURS = ["00", "03", "06", "09", "12", "15", "18", "21"]

# days with no data at the source are marked here so later runs skip them
# instead of stalling on the same gap forever
STATE_DIR = os.path.join(settings.GFMS_DIR, "GFMS_backfill_state")

def log(msg):
    """timestamped line, flushed so `tail -f` on the cron log stays live"""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {msg}", flush=True)


def tiff_path(real_date, binhour):
    return os.path.join(
        settings.GFMS_IMG_DIR, f"Flood_byStor_{real_date}{binhour}.tiff"
    )


def day_is_complete(real_date):
    """all 8 images present for this day"""
    return all(os.path.exists(tiff_path(real_date, h)) for h in BINHOURS)


def day_is_skipped(real_date):
    return os.path.exists(os.path.join(STATE_DIR, f"{real_date}.nodata"))


def mark_skipped(real_date, reason):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, f"{real_date}.nodata"), "w") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {reason}\n")


def find_days_to_process(start_date, end_date, max_days):
    """earliest-first list of days still missing images, within the range"""
    # the source publishes forecasts ahead of time, but only backfill dates
    # that are safely complete; --end caps that, it does not extend it
    settled = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=1)
    last_date = min(end_date, settled)

    days = []
    cursor = start_date
    while cursor <= last_date and len(days) < max_days:
        real_date = cursor.strftime("%Y%m%d")
        if not day_is_complete(real_date) and not day_is_skipped(real_date):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def process_day(day):
    """regenerate images + summaries for one day; returns per-file timings"""
    real_date = day.strftime("%Y%m%d")
    day_start = time.monotonic()
    log(f"DAY {real_date} start")

    produced = []
    timings = []

    for binhour in BINHOURS:
        bin_file = f"Flood_byStor_{real_date}{binhour}.bin"
        proc_csv = os.path.join(
            settings.GFMS_PROC_DIR, bin_file.replace(".bin", ".csv")
        )
        # force recomputation: extract_by_watershed returns early if this exists
        if os.path.exists(proc_csv):
            os.remove(proc_csv)

        t0 = time.monotonic()
        try:
            GFMS_data_extractor(bin_file)
        except Exception as exc:
            log(f"  {bin_file} FAILED: {exc}")
            continue
        elapsed = time.monotonic() - t0

        made_tiff = os.path.exists(tiff_path(real_date, binhour))
        if made_tiff:
            produced.append(binhour)
            timings.append(elapsed)
            log(f"  {real_date}{binhour} ok {elapsed:6.1f}s")
        else:
            log(f"  {real_date}{binhour} no data {elapsed:6.1f}s")

    if not produced:
        mark_skipped(real_date, "no images produced")
        log(f"DAY {real_date} no data at source - marked skipped")
        return timings

    # recompute summaries, overwriting any existing file for these dates
    csvlist = [
        f"Flood_byStor_{real_date}{h}.csv"
        for h in BINHOURS
        if os.path.exists(
            os.path.join(settings.GFMS_PROC_DIR, f"Flood_byStor_{real_date}{h}.csv")
        )
    ]
    prev_day = (day - timedelta(days=1)).strftime("%Y%m%d")
    base0 = f"Flood_byStor_{prev_day}21.csv"
    try:
        GFMS_fix_duration(base0, csvlist)
    except Exception as exc:
        log(f"  summary fix FAILED for {real_date}: {exc}")

    total = time.monotonic() - day_start
    per_file = (sum(timings) / len(timings)) if timings else 0.0
    log(
        f"DAY {real_date} done {total:7.1f}s  "
        f"images={len(produced)}/8  avg/file={per_file:5.1f}s  "
        f"summaries={len(csvlist)}"
    )
    return timings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start", default="20260201", help="earliest date to backfill, YYYYMMDD"
    )
    parser.add_argument(
        "--end", default="20261008", help="last date to consider, YYYYMMDD"
    )
    parser.add_argument(
        "--max-days", type=int, default=4, help="maximum days per run"
    )
    args = parser.parse_args()

    start_date = datetime.strptime(args.start, "%Y%m%d").replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(args.end, "%Y%m%d").replace(tzinfo=timezone.utc)
    if end_date < start_date:
        log(f"end {args.end} is before start {args.start} - nothing to do")
        return 0

    os.makedirs(settings.GFMS_IMG_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)

    run_start = time.monotonic()
    log(
        f"backfill start: {args.start}..{args.end}, max {args.max_days} days/run"
    )

    days = find_days_to_process(start_date, end_date, args.max_days)
    if not days:
        log("nothing to backfill - all days complete")
        return 0

    log(
        f"selected {len(days)} day(s): "
        f"{days[0].strftime('%Y%m%d')} .. {days[-1].strftime('%Y%m%d')}"
    )

    all_timings = []
    for day in days:
        all_timings.extend(process_day(day))

    total = time.monotonic() - run_start
    n = len(all_timings)
    log(
        f"RUN done {total:7.1f}s  days={len(days)}  images={n}  "
        f"avg/file={(sum(all_timings) / n if n else 0):5.1f}s  "
        f"avg/day={(total / len(days)):6.1f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
