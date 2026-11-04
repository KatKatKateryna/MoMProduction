"""
Push the stats of the final alert csv files to the repo of publish_alerts
(publish_alerts calls publish_stats after its push when PUBLISH_CSV_STATS is
true, in any case; environment or .env).

The files are chosen as publish_tiles chooses them: the local final alerts
newer than the newest stats csv in STATS_FOLDER of the branch STATS_BRANCH;
with none there, only the newest local one, whose history then starts with it.
Each stats csv is the final alert csv with one row per watershed (as the
tiles, publish_utils.watersheds), only the Advisory and above ones, and the
stats columns (stats_frame); the history columns extend those of the newest
stats csv in that folder. The branch is created from main when missing, main
is merged into it, then one commit adds the files. publish_stats then calls
publish_chains, which updates the chain records of all watersheds from them.

Stats columns, after the final alert columns (all times in hours):

    The three hold one value per alert, STATS_HISTORY of them joined
    with ",", oldest first, this alert last; "" for an alert that does not
    have the watershed (not Advisory or above, or no stats csv):

    Stats_Lead_history     the lead time of each alert: 0 when DFO or VIIRS
                           observes the flood (Lead_h_DFO or Lead_h_VIIRS
                           0, set by the MoM), else Lead_h with -1 counted as
                           0 (the watershed has the alert); "" without Lead_h.
                           Lead_h itself is kept as it is.
    Stats_Sources_history  the sources with a lead time of 0 or more at each
                           alert, as <source>_<lead time> joined with "/",
                           smallest lead time first; on equal lead times the
                           source of the Flag column first (1 h, 2 d, 3 v),
                           then in the order l g h d v. Source letters:
                               l  GloFAS  (Lead_h_GloFAS)
                               g  GFMS    (Lead_h_GFMS)
                               h  HAFS    (Lead_h_HAFS)
                               d  DFO     (Lead_h_DFO)
                               v  VIIRS   (Lead_h_VIIRS)
                           e.g. "h_12/g_18": HAFS 12 h, GFMS 18 h
    Stats_Chains_history   the prediction chain of each alert (chain_entry).
                           Chains split the lead history: an empty value ends
                           the chain; a 0 continues it; the next non-zero
                           value after a 0 (or after an empty value) starts a
                           new one. Keys:
                               start   chain start, YYMMDDHH
                               lead_0  first lead time of the chain
                               src_0   source of that first lead time (letter)
                               wait    time from the start to this alert,
                                       until the lead time first gets to 0
                                       (then it stays: the wait for the flood)
                               lead    lead time of this alert
                               obs     1 when DFO or VIIRS observes the flood
                                       at this alert, else 0
                           e.g. "start:26110400/lead_0:12/src_0:h/wait:6/
                                 lead:3/obs:0"

Needs GITHUB_REPO and GITHUB_ALERTS_TOKEN (see publish_utils).

"""

import io
import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

from processing.publish.publish_utils import (
    REPO,
    RETRIES,
    alert_cycle,
    bytes_entry,
    commit,
    commit_message,
    ensure_branch,
    folder_files,
    head_tree,
    merge_into,
    newer_files,
    read_blob,
    watersheds,
)

STATS_BRANCH = "stats"
STATS_FOLDER = "Alerts/stats"
# alerts in each WINDOWED column, the current one last
STATS_HISTORY = 56
# the final alerts are written in this encoding (HWRF_MoM.final_alert_pdc)
ENCODING = "Windows-1252"
ABOVE_INFORMATION = ["Advisory", "Watch", "Warning"]
# letter of each source in Stats_Sources_history ("<letter>_<lead h>" joined
# with "/"): the source is listed when its lead time column is 0 or more, by
# the smallest lead time first; on equal lead times the source of the Flag
# column first, then in this order
SOURCES = [
    ("l", "Lead_h_GloFAS"),
    ("g", "Lead_h_GFMS"),
    ("h", "Lead_h_HAFS"),
    ("d", "Lead_h_DFO"),
    ("v", "Lead_h_VIIRS"),
]
# the forecast sources, with their own deviation in publish_chains
FORECAST = ["l", "g", "h"]
# Flag of the final alert -> the source that set its Hazard_Score
# (HWRF_MoM: 1 HWRF/HAFS, 2 DFO, 3 VIIRS; none for GloFAS + GFMS, and blank
# for Information and Advisory alerts)
FLAG_SOURCE = {1: "h", 2: "d", 3: "v"}
# one comma separated value per alert, STATS_HISTORY of them, carried from
# each stats csv to the next
WINDOWED = ["Stats_Lead_history", "Stats_Sources_history", "Stats_Chains_history"]
STAMP = "%y%m%d%H"


def history_index(df):
    """pfaf_id -> WINDOWED columns of a stats table; None without those columns"""
    if any(c not in df for c in ["pfaf_id"] + WINDOWED):
        return None
    df = df[["pfaf_id"] + WINDOWED].copy()
    df["pfaf_id"] = pd.to_numeric(df["pfaf_id"], errors="coerce")
    df = df.dropna(subset=["pfaf_id"]).drop_duplicates("pfaf_id")
    return df.set_index("pfaf_id")


def keyed(*pairs):
    """'key:value/key:value' of the (key, value) pairs"""
    return "/".join(f"{k}:{v}" for k, v in pairs)


def fields(entry):
    """{key: value} of a keyed entry; {} for an empty one"""
    return dict(part.split(":", 1) for part in entry.split("/")) if entry else {}


def source_entry(leads, flagged):
    """Stats_Sources_history entry of one alert: '<letter>_<lead h>' of the
    sources with a lead time of 0 or more (leads: letter -> lead time),
    smallest lead time first; on equal lead times the flagged source
    (FLAG_SOURCE, or None) first, then in SOURCES order; joined with '/'"""
    order = [letter for letter, _ in SOURCES]
    present = sorted(
        (leads[letter], letter != flagged, order.index(letter), letter)
        for letter in order
        if leads[letter] >= 0
    )
    return "/".join(f"{letter}_{int(lead)}" for lead, _, _, letter in present)


def source_leads(entry):
    """{letter: lead time} of a Stats_Sources_history entry"""
    return {
        part.split("_")[0]: int(part.split("_")[1]) for part in entry.split("/") if part
    }


def hours(later, earlier):
    return int((later - earlier).total_seconds() // 3600)


def chain_entry(prev_entry, prev_lead, lead, now, observed, source):
    """Stats_Chains_history entry of one alert (keys: module docstring) after
    the previous alert's entry and lead. An empty lead is no chain; a lead of
    0 continues the previous chain; any other lead continues it unless the
    previous lead was 0; otherwise (or without a previous chain) a new chain
    starts with this alert, its first source the first of this alert's
    sources. wait stays at the previous entry's once the chain's lead time
    was 0"""
    if lead == "":
        return ""
    prev = fields(prev_entry)
    if prev and (lead == "0" or prev_lead != "0"):
        start, lead_0, src_0 = prev["start"], prev["lead_0"], prev["src_0"]
        if prev_lead == "0":
            wait = prev["wait"]
        else:
            wait = hours(now, datetime.strptime(start, STAMP))
    else:
        start, lead_0, src_0, wait = now.strftime(STAMP), lead, source, 0
    return keyed(
        ("start", start), ("lead_0", lead_0), ("src_0", src_0), ("wait", wait),
        ("lead", lead), ("obs", int(observed)),
    )


def stats_frame(path, local, last):
    """the final alert csv at path with one row per watershed
    (publish_utils.watersheds), only the Advisory and above ones, values
    unchanged, with the WINDOWED columns, which extend those of last, (YYYYMMDDHH, history_index) of the
    newest previous stats, or None: one "" per local alert between them, ""
    for a watershed it does not have; STATS_HISTORY comma separated values
    each, oldest first, this alert last.
    Stats_Lead_history: 0 when DFO or VIIRS observes the flood (Lead_h_DFO
    or Lead_h_VIIRS 0), else Lead_h, -1 counted as 0 (the watershed has the alert); "" without
    Lead_h. Stats_Sources_history: source_entry. Stats_Chains_history:
    chain_entry"""
    df = pd.read_csv(path, encoding=ENCODING, dtype=str, keep_default_na=False)
    # the index column the MoM writes has no name
    df.columns = ["" if c.startswith("Unnamed: ") else c for c in df.columns]
    df = watersheds(df)
    df = df[df["Alert"].isin(ABOVE_INFORMATION)].reset_index(drop=True)
    now = datetime.strptime(alert_cycle(os.path.basename(path)), "%Y%m%d%H")

    observed = pd.Series(False, index=df.index)
    for column in ["Lead_h_DFO", "Lead_h_VIIRS"]:
        if column in df:
            observed |= pd.to_numeric(df[column], errors="coerce") == 0
    lead_h = (
        pd.to_numeric(df["Lead_h"], errors="coerce")
        if "Lead_h" in df
        else pd.Series(np.nan, index=df.index)
    )
    leads = [
        "0" if obs else ("" if pd.isna(v) else str(max(int(v), 0)))
        for v, obs in zip(lead_h, observed)
    ]
    by_source = pd.DataFrame(
        {
            letter: pd.to_numeric(df[column], errors="coerce") if column in df else np.nan
            for letter, column in SOURCES
        },
        index=df.index,
    )
    flagged = (
        pd.to_numeric(df["Flag"], errors="coerce").map(FLAG_SOURCE)
        if "Flag" in df
        else pd.Series(None, index=df.index)
    )
    sources = [
        source_entry(row, flag if isinstance(flag, str) else None)
        for row, flag in zip(by_source.to_dict("records"), flagged)
    ]

    # the previous values, and how many local alerts back
    pfaf = pd.to_numeric(df["pfaf_id"], errors="coerce")
    cycle = alert_cycle(os.path.basename(path))
    cycles = [alert_cycle(os.path.basename(f)) for f in local]
    prev_cycle, prev = last if last and last[1] is not None else ("", None)
    back = 1 + sum(1 for c in cycles if prev_cycle < c < cycle)

    def earlier(value):
        """the STATS_HISTORY - 1 values before this alert"""
        values = [""] * STATS_HISTORY + (value.split(",") if value else []) + [""] * (back - 1)
        return values[-(STATS_HISTORY - 1) :]

    out = {c: [] for c in WINDOWED}
    for p, lead, source, obs in zip(pfaf, leads, sources, observed):
        found = prev is not None and p in prev.index
        h, s, c = (earlier(prev.at[p, col] if found else "") for col in WINDOWED)
        h, s = h + [lead], s + [source]
        c = c + [chain_entry(c[-1], h[-2], lead, now, obs, source.split("_")[0])]
        for col, values in zip(WINDOWED, (h, s, c)):
            out[col].append(",".join(values))
    for col in WINDOWED:
        df[col] = out[col]
    return df


def build_stats(files, local, last):
    """[(file, stats table)] of files in order, each one extending the
    previous one (the first one extends last, as in stats_frame)"""
    tables = []
    for f in files:
        df = stats_frame(f, local, last)
        last = (alert_cycle(os.path.basename(f)), history_index(df))
        tables.append((f, df))
    return tables


def publish_stats(session, local):
    """push_stats, then publish_chains (also when the stats are up to date
    or their push failed; its failure is logged)"""
    try:
        push_stats(session, local)
    finally:
        from processing.publish.publish_chains import publish_chains

        try:
            publish_chains(session)
        except Exception:
            logging.exception("publish chains failed")


def push_stats(session, local):
    """push the stats csv of the local final alerts newer than the newest
    stats csv in STATS_FOLDER of STATS_BRANCH (only the newest local one when
    there is none), after creating the branch from main if needed and merging
    main into it; the histories extend that newest stats csv"""
    ensure_branch(session, STATS_BRANCH)
    merge_into(session, STATS_BRANCH)
    _, tree = head_tree(session, STATS_BRANCH)
    remote = folder_files(session, tree, STATS_FOLDER, ".csv", "publish stats")
    files = newer_files(list(remote), local)
    if not files:
        logging.info(f"publish stats: {REPO}/{STATS_FOLDER} is up to date")
        return
    last = None
    if remote:
        name = max(remote)
        data = read_blob(session, remote[name])
        last = (
            alert_cycle(name),
            history_index(
                pd.read_csv(
                    io.BytesIO(data), encoding=ENCODING, dtype=str, keep_default_na=False
                )
            ),
        )

    entries = [
        bytes_entry(
            session,
            f"{STATS_FOLDER}/{os.path.basename(f)}",
            df.to_csv(index=False).encode(ENCODING),
        )
        for f, df in build_stats(files, local, last)
    ]
    for attempt in range(RETRIES):
        if attempt:
            merge_into(session, STATS_BRANCH)
        head, tree = head_tree(session, STATS_BRANCH)
        if commit(session, head, tree, entries, commit_message("stats", files), STATS_BRANCH):
            logging.info(
                f"publish stats: pushed {len(files)} file(s) to {REPO}/{STATS_FOLDER} "
                f"on {STATS_BRANCH}, last {os.path.basename(files[-1])}"
            )
            return
        logging.info("publish stats: branch moved during the push, retrying")
    logging.warning(f"publish stats: gave up after {RETRIES} attempts")
