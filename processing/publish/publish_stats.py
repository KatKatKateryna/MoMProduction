"""
Push the stats of the final alert csv files to the repo of publish_alerts
(publish_alerts calls publish_stats after its push when PUBLISH_CSV_STATS is
true, in any case; environment or .env).

The files are chosen as publish_tiles chooses them: the local final alerts
newer than the newest stats csv in STATS_FOLDER of the branch STATS_BRANCH;
with none there, only the newest local one, whose history then starts with it.
Each stats csv is the final alert csv with one row per watershed (as the
tiles, publish_utils.watersheds), only the Advisory and above ones, and the
stats columns (stats_frame); each watershed's history columns extend those of
the latest stats csv in that folder that has it, however far back, found
through STATS_INDEX (updated in the same commit). The branch is created from
main when missing, main is merged into it, then one commit adds the files.

Stats columns, after the final alert columns (all times in hours):

    Stats_Lead_h_DFO    0 when the DFO score alone (DFOTotal_Score as the
                        Hazard_Score, with the watershed's risk) gives an
                        alert above Information: DFO observes the flood; else -1
    Stats_Lead_h_VIIRS  the same for the VIIRS score (VIIRSTotal_Score)

    The next three hold one value per alert, STATS_HISTORY of them joined
    with ",", oldest first, this alert last; "" for an alert that does not
    have the watershed (not Advisory or above, or no stats csv):

    Stats_Lead_history     the lead time of each alert: 0 when DFO or VIIRS
                           observes the flood, else Lead_h with -1 counted as
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
                               d  DFO     (Stats_Lead_h_DFO)
                               v  VIIRS   (Stats_Lead_h_VIIRS)
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

    Stats_Chains           one record per chain whose lead time got to 0,
                           joined with ",", oldest first (chain_records); kept
                           however long ago; the record of the current chain
                           gets h_flood_st_to_obs and flood_dur updated
                           while it stays at 0. Keys:
                               start        chain start, YYMMDDHH
                               wait         time from the start until the
                                            lead time got to 0
                               lead_0_err   lead_0 - wait: error of the first
                                            lead time (> 0: the flood came
                                            sooner, < 0: later)
                               dev          median of lead - correct lead
                                            (the record's wait - the alert's
                                            wait) over the chain's alerts in
                                            the history before the lead time
                                            got to 0; "" without such alerts
                               dev_l        the same for the GloFAS lead time
                               dev_g        the same for the GFMS lead time
                               dev_h        the same for the HAFS lead time
                               h_flood_st_to_obs
                                            time from the lead time 0 (the
                                            flood start) until DFO or VIIRS
                                            observed the flood; -1 if not
                                            (yet)
                               flood_dur    flood duration: time from the
                                            lead time 0 until the chain's
                                            last alert
                           e.g. "start:26110400/wait:6/lead_0_err:-3/dev:-3/
                                 dev_l:/dev_g:12/dev_h:-3/h_flood_st_to_obs:-1/
                                 flood_dur:6"

STATS_INDEX, next to STATS_FOLDER: {"<pfaf_id>": "<YYYYMMDDHH>"}, the alert
of the latest stats csv that has each watershed.

Needs GITHUB_REPO and GITHUB_ALERTS_TOKEN (see publish_utils).

"""

import io
import json
import logging
import os
import statistics
from datetime import datetime, timedelta

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
# next to STATS_FOLDER: {pfaf_id: YYYYMMDDHH of the latest stats csv that has
# the watershed}, so its previous values are found however far back
STATS_INDEX = "Alerts/stats_index.json"
# alerts in each WINDOWED column, the current one last
STATS_HISTORY = 56
# the final alerts are written in this encoding (HWRF_MoM.final_alert_pdc)
ENCODING = "Windows-1252"
RISK = ["Scaled_Riverine_Risk", "Scaled_Coastal_Risk"]
ABOVE_INFORMATION = ["Advisory", "Watch", "Warning"]
# letter of each source in Stats_Sources_history ("<letter>_<lead h>" joined
# with "/"): the source is listed when its lead time column is 0 or more, by
# the smallest lead time first; on equal lead times the source of the Flag
# column first, then in this order
SOURCES = [
    ("l", "Lead_h_GloFAS"),
    ("g", "Lead_h_GFMS"),
    ("h", "Lead_h_HAFS"),
    ("d", "Stats_Lead_h_DFO"),
    ("v", "Stats_Lead_h_VIIRS"),
]
# the forecast sources, with their own deviation in Stats_Chains
FORECAST = ["l", "g", "h"]
# Flag of the final alert -> the source that set its Hazard_Score
# (HWRF_MoM: 1 HWRF/HAFS, 2 DFO, 3 VIIRS; none for GloFAS + GFMS, and blank
# for Information and Advisory alerts)
FLAG_SOURCE = {1: "h", 2: "d", 3: "v"}
# one comma separated value per alert, STATS_HISTORY of them
WINDOWED = ["Stats_Lead_history", "Stats_Sources_history", "Stats_Chains_history"]
# carried from each stats csv to the next
CARRIED = WINDOWED + ["Stats_Chains"]
STAMP = "%y%m%d%H"


def alone_lead(df, score, alert_func):
    """per row of df: 0 where the score column alone, as the Hazard_Score with
    the row's risk, gives an alert above Information (alert_func, the mofunc_*
    of the source's MoM step); -1 otherwise or without the column"""
    if score not in df or df.empty:
        return pd.Series(-1, index=df.index)
    w = pd.DataFrame(
        {"Hazard_Score": pd.to_numeric(df[score], errors="coerce").fillna(0)}
    )
    for c in RISK:
        w[c] = pd.to_numeric(df[c], errors="coerce") if c in df else np.nan
    from processing.utilities import severity_from_hazard_and_risk_scores

    with np.errstate(divide="ignore", invalid="ignore"):
        w["Severity"] = severity_from_hazard_and_risk_scores(w)
    alert = w.apply(alert_func, axis=1)
    return pd.Series(np.where(alert.isin(ABOVE_INFORMATION), 0, -1), index=df.index)


def add_alone_leads(df):
    """Stats_Lead_h_DFO and Stats_Lead_h_VIIRS (alone_lead) added to df"""
    from processing.hwrf.HWRF_MoM import mofunc_dfo, mofunc_viirs

    df["Stats_Lead_h_DFO"] = alone_lead(df, "DFOTotal_Score", mofunc_dfo)
    df["Stats_Lead_h_VIIRS"] = alone_lead(df, "VIIRSTotal_Score", mofunc_viirs)


def history_index(df):
    """pfaf_id -> CARRIED columns of a stats table; None without those columns"""
    if any(c not in df for c in ["pfaf_id"] + CARRIED):
        return None
    df = df[["pfaf_id"] + CARRIED].copy()
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


def median(values):
    """the median of values ('' for none), as a short number"""
    return f"{statistics.median(values):g}" if values else ""


def chain_records(records, leads, sources, chains, now, observed):
    """Stats_Chains records of a watershed after this alert (keys: module
    docstring): records, the previous ones; leads, sources, chains, its
    windowed history columns with this alert last. When this alert's chain
    gets to lead time 0, a record is added; while it stays at 0, the record of
    the chain gets h_flood_st_to_obs (once observed) and flood_dur updated"""
    cur = fields(chains[-1])
    if not cur or cur["lead"] != "0":
        return records
    start, wait = cur["start"], int(cur["wait"])
    zero = datetime.strptime(start, STAMP) + timedelta(hours=wait)
    prev = fields(chains[-2]) if len(chains) > 1 else {}
    if prev.get("start") == start and prev.get("lead") == "0":
        # the chain was already at 0: update its record
        for i in range(len(records) - 1, -1, -1):
            rec = fields(records[i])
            if rec.get("start") != start:
                continue
            if rec["h_flood_st_to_obs"] == "-1" and observed:
                rec["h_flood_st_to_obs"] = str(hours(now, zero))
            rec["flood_dur"] = str(hours(now, zero))
            return records[:i] + [keyed(*rec.items())] + records[i + 1 :]
        return records

    # first lead time 0 of the chain: deviations over its earlier alerts
    dev, per_source = [], {letter: [] for letter in FORECAST}
    for k in range(len(chains) - 2, -1, -1):
        entry = fields(chains[k])
        if entry.get("start") != start:
            break
        correct = wait - int(entry["wait"])
        dev.append(int(leads[k]) - correct)
        for letter, lead in source_leads(sources[k]).items():
            if letter in per_source:
                per_source[letter].append(lead - correct)
    record = keyed(
        ("start", start),
        ("wait", wait),
        ("lead_0_err", int(cur["lead_0"]) - wait),
        ("dev", median(dev)),
        *((f"dev_{letter}", median(per_source[letter])) for letter in FORECAST),
        ("h_flood_st_to_obs", 0 if observed else -1),
        ("flood_dur", 0),
    )
    return records + [record]


def stats_frame(path, local, previous, last_seen):
    """the final alert csv at path with one row per watershed
    (publish_utils.watersheds), only the Advisory and above ones, values
    unchanged, with Stats_Lead_h_DFO, Stats_Lead_h_VIIRS and the CARRIED
    columns. Each watershed's previous values come from the stats of the
    cycle last_seen gives for it (pfaf_id as text -> YYYYMMDDHH; previous(cycle)
    -> history_index or None), however far back, so its Stats_Chains records
    are kept; the WINDOWED columns get one "" per local alert since, and ""
    for the alerts before them; STATS_HISTORY comma separated values each, oldest first, this
    alert last.
    Stats_Lead_history: 0 when DFO or VIIRS alone gives the alert (observed),
    else Lead_h, -1 counted as 0 (the watershed has the alert); "" without
    Lead_h. Stats_Sources_history: source_entry. Stats_Chains_history:
    chain_entry. Stats_Chains: chain_records, comma separated"""
    df = pd.read_csv(path, encoding=ENCODING, dtype=str, keep_default_na=False)
    # the index column the MoM writes has no name
    df.columns = ["" if c.startswith("Unnamed: ") else c for c in df.columns]
    df = watersheds(df)
    df = df[df["Alert"].isin(ABOVE_INFORMATION)].reset_index(drop=True)
    add_alone_leads(df)
    now = datetime.strptime(alert_cycle(os.path.basename(path)), "%Y%m%d%H")

    observed = (df["Stats_Lead_h_DFO"] == 0) | (df["Stats_Lead_h_VIIRS"] == 0)
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

    # each watershed's values in the latest previous stats that have it
    # (last_seen), and how many local alerts back
    pfaf = pd.to_numeric(df["pfaf_id"], errors="coerce")
    cycle = alert_cycle(os.path.basename(path))
    cycles = [alert_cycle(os.path.basename(f)) for f in local]
    by_cycle = {}
    for p in set(pfaf.dropna()):
        seen = last_seen.get(str(int(p)))
        if seen and seen < cycle:
            by_cycle.setdefault(seen, []).append(p)
    found = {}
    for seen, ps in by_cycle.items():
        prev = previous(seen)
        if prev is None:
            continue
        back = 1 + sum(1 for c in cycles if seen < c < cycle)
        for p in ps:
            if p in prev.index:
                found[p] = (back, {c: prev.at[p, c] for c in CARRIED})

    def earlier(value, back):
        """the STATS_HISTORY - 1 values before this alert"""
        values = [""] * STATS_HISTORY + (value.split(",") if value else []) + [""] * (back - 1)
        return values[-(STATS_HISTORY - 1) :]

    out = {c: [] for c in CARRIED}
    for p, lead, source, obs in zip(pfaf, leads, sources, observed):
        back, prev = found.get(p, (1, {c: "" for c in CARRIED}))
        h, s, c = (earlier(prev[col], back) for col in WINDOWED)
        h, s = h + [lead], s + [source]
        c = c + [chain_entry(c[-1], h[-2], lead, now, obs, source.split("_")[0])]
        records = prev["Stats_Chains"].split(",") if prev["Stats_Chains"] else []
        records = chain_records(records, h, s, c, now, obs)
        for col, values in zip(CARRIED, (h, s, c, records)):
            out[col].append(",".join(values))
    for col in CARRIED:
        df[col] = out[col]
    return df


def build_stats(files, local, previous, last_seen):
    """[(file, stats table)] of files in order, each one's stats available to
    the next ones (previous(cycle) is asked for the others); last_seen
    (STATS_INDEX) is updated with each one's watersheds"""
    built, tables = {}, []

    def lookup(cycle):
        return built[cycle] if cycle in built else previous(cycle)

    for f in files:
        df = stats_frame(f, local, lookup, last_seen)
        cycle = alert_cycle(os.path.basename(f))
        built[cycle] = history_index(df)
        for p in pd.to_numeric(df["pfaf_id"], errors="coerce").dropna():
            last_seen[str(int(p))] = cycle
        tables.append((f, df))
    return tables


def index_json(last_seen):
    """the STATS_INDEX content of last_seen, one watershed per line"""
    ordered = dict(sorted(last_seen.items(), key=lambda kv: int(kv[0])))
    return (json.dumps(ordered, indent=0) + "\n").encode("utf-8")


def publish_stats(session, local):
    """push the stats csv of the local final alerts newer than the newest
    stats csv in STATS_FOLDER of STATS_BRANCH (only the newest local one when
    there is none), after creating the branch from main if needed and merging
    main into it; the previous stats are read from that folder through
    STATS_INDEX, which the same commit updates"""
    ensure_branch(session, STATS_BRANCH)
    merge_into(session, STATS_BRANCH)
    _, tree = head_tree(session, STATS_BRANCH)
    remote = folder_files(session, tree, STATS_FOLDER, ".csv", "publish stats")
    files = newer_files(list(remote), local)
    if not files:
        logging.info(f"publish stats: {REPO}/{STATS_FOLDER} is up to date")
        return
    by_cycle = {alert_cycle(name): name for name in remote}
    index = folder_files(
        session, tree, os.path.dirname(STATS_INDEX), ".json", "publish stats"
    ).get(os.path.basename(STATS_INDEX))
    last_seen = json.loads(read_blob(session, index)) if index else {}
    if not index and remote:
        logging.warning(f"publish stats: no {STATS_INDEX}, histories start again")
    cache = {}

    def previous(cycle):
        if cycle not in by_cycle:
            return None
        if cycle not in cache:
            data = read_blob(session, remote[by_cycle[cycle]])
            cache[cycle] = history_index(
                pd.read_csv(
                    io.BytesIO(data), encoding=ENCODING, dtype=str, keep_default_na=False
                )
            )
        return cache[cycle]

    entries = [
        bytes_entry(
            session,
            f"{STATS_FOLDER}/{os.path.basename(f)}",
            df.to_csv(index=False).encode(ENCODING),
        )
        for f, df in build_stats(files, local, previous, last_seen)
    ]
    entries.append(bytes_entry(session, STATS_INDEX, index_json(last_seen)))
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
