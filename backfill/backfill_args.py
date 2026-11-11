"""
backfill_args.py
    -- the options every backfill script takes: --start and --end, --out-dir
       and --overwrite

    --start is required, --end optional: without it a script goes on while
    the next cycle or day has come, that is until now (UTC). A script that
    works by cycle takes YYYYMMDD[HH] for --start (a date alone is its 00
    cycle) and YYYYMMDDHH for --end; one that works by day takes YYYYMMDD.

    --out-dir is a root in the production layout: every output folder is put
    under it where settings.py puts it under the production root, the folder
    that holds PRODUCT_DIR (Products/GFMS/GFMS_image, ...). Without it the
    outputs go to the folders of production.cfg. --overwrite, off by default,
    replaces outputs that are already there; without it they are kept.
"""

import argparse
import os
from datetime import datetime, timedelta, timezone

import settings

# the folder that holds Products/, as production.cfg gives it
DEFAULT_ROOT = os.path.dirname(settings.PRODUCT_DIR)


def add_output_args(parser):
    """--out-dir and --overwrite on an argparse parser"""
    parser.add_argument(
        "--out-dir", default=DEFAULT_ROOT,
        help="root the outputs are written under, in the production layout "
        f"(default: {DEFAULT_ROOT}, the folders of production.cfg)",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="replace outputs that already exist (default: keep them)",
    )


def point_at(out_dir, names):
    """point the settings folders `names` at the same place under out_dir"""
    root = os.path.abspath(out_dir)
    for name in names:
        rel = os.path.relpath(getattr(settings, name), DEFAULT_ROOT)
        setattr(settings, name, os.path.join(root, rel))


def _cycle(text, hour_optional=False):
    """a cycle YYYYMMDDHH (YYYYMMDD too when hour_optional: hour 00)"""
    if hour_optional and len(text) == 8:
        text += "00"
    if len(text) != 10 or not text.isdigit():
        raise argparse.ArgumentTypeError(
            f"YYYYMMDD{'[HH]' if hour_optional else 'HH'} expected: {text}"
        )
    return datetime.strptime(text, "%Y%m%d%H")


def _day(text):
    """a day YYYYMMDD"""
    if len(text) != 8 or not text.isdigit():
        raise argparse.ArgumentTypeError(f"YYYYMMDD expected: {text}")
    return datetime.strptime(text, "%Y%m%d")


def add_date_args(parser, by_cycle):
    """--start (required) and --end (optional): cycles when by_cycle, else
    days"""
    if by_cycle:
        parser.add_argument(
            "--start", required=True, type=lambda t: _cycle(t, hour_optional=True),
            help="first cycle, YYYYMMDD (its 00 cycle) or YYYYMMDDHH",
        )
        parser.add_argument(
            "--end", type=_cycle, default=None,
            help="last cycle, YYYYMMDDHH (default: the last one that has come, UTC)",
        )
    else:
        parser.add_argument(
            "--start", required=True, type=_day, help="first day, YYYYMMDD"
        )
        parser.add_argument(
            "--end", type=_day, default=None,
            help="last day, YYYYMMDD (default: today, UTC)",
        )


def cycles(start, end, hours=6):
    """the cycles from start, every `hours`, up to end, or without end as long
    as they have come"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = now if end is None else end
    cycle = start
    while cycle <= last and cycle <= now:
        yield cycle
        cycle += timedelta(hours=hours)


def days(start, end):
    """the days (YYYYMMDD) from start up to end, or without end up to today"""
    today = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    last = today if end is None else end
    day = start
    while day <= last:
        yield day.strftime("%Y%m%d")
        day += timedelta(days=1)
