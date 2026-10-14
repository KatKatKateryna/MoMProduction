"""
offline_summaries_from_images.py
    -- all source summaries of the last N days, offline: rebuilt from the
       saved images where possible, copied otherwise, into one new folder

    Runs, in parallel, one process per source:
        GFMS        offline_gfms_summaries_from_images.py       -> OUT/gfms_summary
        VIIRS       offline_viirs_summaries_from_images.py      -> OUT/viirs_summary
        HWRF        offline_hwrf_hafs_summaries_from_images.py  -> OUT/hwrf_summary
        HAFS        offline_hwrf_hafs_summaries_from_images.py  -> OUT/hafs_summary
    and copies what cannot be rebuilt from images:
        GloFAS      threspoints_YYYYMMDD00.csv                  -> OUT/glofas
        DFO         DFO_YYYYMMDD.csv (only the 3-Day image is   -> OUT/dfo_summary
                    kept, the summary has 4 layers)
    OUT is then a flat input root for offline_mom_from_summaries.py.

    Sources: --input-root (default /root/MoM). Each folder can be flat
    (ROOT/gfms_image, ROOT/gfms_summary, ROOT/glofas, ...) or in the layout of
    /root/MoM (ROOT/Products/GFMS/GFMS_image, ...); the flat one is used if
    it exists. They are only read. The existing summaries are used to
    continue the GFMS durations, and for the header-only HWRF/HAFS summaries
    of cycles without rain (no image).

    Dates: the window (--days up to --end) and the 2 days before it, which
    the MoM needs (its warm-up day, and the previous GloFAS and rain of the
    00 and 06 cycles).

    Never deletes anything, and writes only under --out-dir (plus the logs
    in backfill/logs/, and a cell lookup in data/ if one does not exist
    yet). A file already in OUT is kept, unless --overwrite: then it is
    replaced.

**EXAMPLE**
    python backfill/offline_summaries_from_images.py --days 6 --end 20261013 --out-dir /root/MoM/rerun --dry-run
    python backfill/offline_summaries_from_images.py --days 6 --end 20261013 --out-dir /root/MoM/rerun
    python backfill/offline_mom_from_summaries.py --days 6 --end 20261013 --input-root /root/MoM/rerun \\
        --out-dir /root/MoM/rerun
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402
import folder_layout  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
DAYS_BEFORE = 2


def window(end, days):
    """first and last day (YYYYMMDD) of the window plus DAYS_BEFORE days"""
    end_date = datetime.strptime(end, "%Y%m%d")
    start = end_date - timedelta(days=days - 1 + DAYS_BEFORE)
    return start.strftime("%Y%m%d"), end


def dates(start, end):
    day, last = datetime.strptime(start, "%Y%m%d"), datetime.strptime(end, "%Y%m%d")
    while day <= last:
        yield day.strftime("%Y%m%d")
        day += timedelta(days=1)


def jobs(src, out, start, end, overwrite):
    """{name: command} of the summary scripts"""
    py = [sys.executable, "-u"]
    span = ["--start", start, "--end", end] + (["--overwrite"] if overwrite else [])
    folder = lambda name: folder_layout.folder(src, name)  # noqa: E731
    return {
        "gfms": py + [os.path.join(HERE, "offline_gfms_summaries_from_images.py"),
                      folder("gfms_image"), os.path.join(out, "gfms_summary"),
                      "--prev-dir", folder("gfms_summary")] + span,
        "viirs": py + [os.path.join(HERE, "offline_viirs_summaries_from_images.py"),
                       folder("viirs_image"), os.path.join(out, "viirs_summary")] + span,
        "hwrf": py + [os.path.join(HERE, "offline_hwrf_hafs_summaries_from_images.py"), "hwrf",
                      folder("hwrf_image"), os.path.join(out, "hwrf_summary"),
                      "--summary-dir", folder("hwrf_summary")] + span,
        "hafs": py + [os.path.join(HERE, "offline_hwrf_hafs_summaries_from_images.py"), "hafs",
                      folder("hafs_image"), os.path.join(out, "hafs_summary"),
                      "--summary-dir", folder("hafs_summary")] + span,
    }


def copy_files(src_dir, dst_dir, names, overwrite, dry_run):
    """copy the files `names` that exist in src_dir; keep those already in
    dst_dir unless overwrite; return (copied, kept, missing)"""
    copied, kept, missing = [], [], []
    for name in names:
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if not os.path.exists(src):
            missing.append(name)
            continue
        if os.path.exists(dst) and not overwrite:
            kept.append(name)
            continue
        if not dry_run:
            os.makedirs(dst_dir, exist_ok=True)
            part = f"{dst}.{os.getpid()}.part"
            shutil.copy2(src, part)
            if overwrite:
                os.replace(part, dst)
            else:
                try:
                    os.link(part, dst)  # fails if dst exists: never overwrites
                except FileExistsError:
                    kept.append(name)
                    continue
                finally:
                    os.remove(part)
        copied.append(name)
    return copied, kept, missing


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, required=True,
                        help="number of days, counted back from --end (inclusive)")
    parser.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y%m%d"),
                        help="last day, YYYYMMDD (default: today, UTC)")
    parser.add_argument("--input-root", default=folder_layout.DEFAULT_ROOT,
                        help=f"folder with the images and summaries, flat or in the layout of "
                             f"{folder_layout.DEFAULT_ROOT} (default: {folder_layout.DEFAULT_ROOT})")
    parser.add_argument("--out-dir", required=True,
                        help="new folder for gfms_summary, viirs_summary, hwrf_summary, hafs_summary, "
                             "dfo_summary, glofas")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace files already in --out-dir (default: keep them)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only report what would be run and copied, write nothing")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"offline_summaries_from_images_{stamp}.log")
    logging.basicConfig(filename=log_file, format=settings.LOG_FORMAT, level=logging.INFO, force=True)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.getLogger().addHandler(console)

    src, out = os.path.abspath(args.input_root), os.path.abspath(args.out_dir)
    if out == src:
        logging.error("--out-dir must be a separate folder, not --input-root")
        return 2
    start, end = window(args.end, args.days)
    logging.info(f"summaries {start} - {end} (window + {DAYS_BEFORE} days before): input {src}, "
                 f"output {out}, overwrite={args.overwrite}, dry_run={args.dry_run}")

    # the summary scripts, in parallel
    procs = {}
    for name, cmd in jobs(src, out, start, end, args.overwrite).items():
        job_log = os.path.join(LOG_DIR, f"offline_summaries_from_images_{stamp}_{name}.log")
        logging.info(f"{name}: {' '.join(cmd[2:])}")
        if args.dry_run:
            continue
        with open(job_log, "w") as f:
            procs[name] = (subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, cwd=settings.BASE_DIR), job_log)

    # what cannot be rebuilt from images, copied meanwhile
    days = list(dates(start, end))
    for label, folder, dst, names in (
        ("GloFAS", "glofas", "glofas", [f"threspoints_{d}00.csv" for d in days]),
        ("DFO", "dfo_summary", "dfo_summary", [f"DFO_{d}.csv" for d in days]),
    ):
        src_dir = folder_layout.folder(src, folder)
        copied, kept, missing = copy_files(src_dir, os.path.join(out, dst), names, args.overwrite, args.dry_run)
        logging.info(f"{label}: from {src_dir}: {'to copy' if args.dry_run else 'copied'} {len(copied)}, kept {len(kept)}, "
                     f"not in the source {len(missing)}{': ' + ' '.join(missing) if missing else ''}")

    failed = []
    for name, (proc, job_log) in procs.items():
        code = proc.wait()
        with open(job_log) as f:
            last = [line.strip() for line in f if " INFO : written" in line or "Traceback" in line]
        logging.info(f"{name}: exit {code}, {last[-1].split(' : ', 1)[-1] if last else 'no summary line'} "
                     f"(log {job_log})")
        if code != 0:
            failed.append(name)

    logging.info(f"finished{', failed: ' + ' '.join(failed) if failed else ''}; log {log_file}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
