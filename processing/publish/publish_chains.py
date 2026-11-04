"""
Update STATS_CHAINS, the prediction chain records of every watershed, on the
branch STATS_BRANCH from the stats csv files of publish_stats in STATS_FOLDER:
those newer than the latest latest_record of the file are read in order, and
one commit writes the updated file. Without the file, it is created with every
watershed of data/Attributes.csv (the watershed shapefile's pfaf_ids) and
filled from all the stats csv files.

publish_stats calls it after its push; it also runs on its own:
    python -m processing.publish.publish_chains

STATS_CHAINS (Alerts/stats_summaries/stats_chains.json), one watershed per line:

    {"<pfaf_id>": {"latest_record": <YYMMDDHH of the latest stats csv that has
                                    the watershed; null before>,
                   "chains": {"<start>": {<key>: <value>}}}}

One record per chain of the watershed's Stats_Chains_history, however long
ago, added when the chain starts and updated at each alert of the chain
(chain_records). All times in hours; timestamps YYMMDDHH; models as the
source letters of publish_stats (l GloFAS, g GFMS, h HAFS, d DFO, v VIIRS),
in the order of Stats_Sources_history. The correct lead time of an alert is
STEP_H per alert until the section's reference alert. Keys:

    <start>          the record's key: chain start
    start_to_lead    relative to the flood start (the chain's first lead
                     time 0, the reference alert); null values until then:
        wait         time from the start until then
        model_0      the model(s) with the smallest lead time at the start
                     (that raised the alert first); [src_0] when the start
                     is before the history
        lead_0_err   lead_0 - wait: error of the first lead time (> 0: the
                     flood came sooner, < 0: later)
        dev          median of lead - correct lead over the chain's alerts in
                     the history before then; when the chain starts there
                     (wait 0), over the start alert (dev = lead_0_err); null
                     without such alerts
        dev_l        the same for the GloFAS lead time
        dev_g        the same for the GFMS lead time
        dev_h        the same for the HAFS lead time
    start_to_obs     the same keys relative to the first alert at which DFO or
                     VIIRS observes the flood (the reference alert); null
                     values until then
    flood_start      the flood start; null until then
    flood_end        the chain's latest alert
    flood_total_dur  time from the flood start until the chain's latest
                     alert; null until the flood start
    model_flood_start
                     the model(s) with lead time 0 at the flood start; null
                     until then
    obs              1 once DFO or VIIRS observed the flood in the chain, else 0
    flood_st_to_obs  time from the flood start until the first observation;
                     null until observed
    obs_dur          time from the first to the latest observation; null
                     until observed

e.g. "413017": {"latest_record": "26110418", "chains": {"26110400": {
     "start_to_lead": {"wait": 6, "lead_0_err": -3, "dev": -3, "dev_l": null,
     "dev_g": 12, "dev_h": -3}, "start_to_obs": {"wait": null, ...},
     "flood_start": "26110406", "flood_end": "26110418", "flood_total_dur": 12,
     "model_flood_start": ["h"], "obs": 0, "flood_st_to_obs": null, "obs_dur": null}}}

Needs GITHUB_REPO and GITHUB_ALERTS_TOKEN (see publish_utils).

"""

import io
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests

from processing.publish.publish_stats import (
    ENCODING,
    FORECAST,
    STAMP,
    STATS_BRANCH,
    STATS_FOLDER,
    WINDOWED,
    fields,
    history_index,
    hours,
    source_leads,
)
from processing.publish.publish_utils import (
    REPO,
    RETRIES,
    alert_cycle,
    bytes_entry,
    commit,
    commit_message,
    folder_files,
    github_session,
    head_tree,
    read_blob,
)

# next to STATS_FOLDER: the files of all watersheds
STATS_SUMMARIES = "Alerts/stats_summaries"
STATS_CHAINS = f"{STATS_SUMMARIES}/stats_chains.json"
# hours between two alerts of the windowed history columns
STEP_H = 6
# the values of start_to_lead and start_to_obs of a record
RELATIVE = ["wait", "model_0", "lead_0_err", "dev"] + [f"dev_{letter}" for letter in FORECAST]


def median(values):
    """the median of values (None for none), an int when whole"""
    if not values:
        return None
    m = statistics.median(values)
    return int(m) if m == int(m) else m


def new_record():
    """a record of a chain that just got into STATS_CHAINS"""
    return {
        "start_to_lead": dict.fromkeys(RELATIVE),
        "start_to_obs": dict.fromkeys(RELATIVE),
        "flood_start": None,
        "flood_end": None,
        "flood_total_dur": None,
        "model_flood_start": None,
        "obs": 0,
        "flood_st_to_obs": None,
        "obs_dur": None,
    }


def chain_records(records, sources, chains, now):
    """update records, the chain records of a watershed ({start: record}, keys:
    module docstring), with the alert at now; sources, chains: its
    Stats_Sources_history and Stats_Chains_history values (one per alert,
    STEP_H apart, this alert last). The record of this alert's chain is added
    when missing, from all the chain's alerts in the history, else updated
    with this alert"""
    entries = [fields(e) for e in chains]
    cur = entries[-1]
    if not cur:
        return
    start = cur["start"]
    start_t = datetime.strptime(start, STAMP)
    # the chain's alerts in the history
    chain = [len(entries) - 1]
    while chain[0] > 0 and entries[chain[0] - 1].get("start") == start:
        chain.insert(0, chain[0] - 1)

    def time(k):
        return now - timedelta(hours=STEP_H * (len(entries) - 1 - k))

    # the models of the start, when it is in the history
    leads_0 = source_leads(sources[chain[0]]) if time(chain[0]) == start_t else {}
    model_0 = models(leads_0, min(leads_0.values())) if leads_0 else [cur["src_0"]]

    def relative(event, k):
        """start_to_lead / start_to_obs for the reference alert k (time
        event): the correct lead time of the alerts before it is STEP_H per
        alert until k; the start alert itself when it is k"""
        wait = hours(event, start_t)
        before = [j for j in chain if j < k]
        if not before and time(k) == start_t:
            before = [k]
        dev, per_source = [], {letter: [] for letter in FORECAST}
        for j in before:
            correct = STEP_H * (k - j)
            dev.append(int(entries[j]["lead"]) - correct)
            for letter, lead in source_leads(sources[j]).items():
                if letter in per_source:
                    per_source[letter].append(lead - correct)
        return dict(
            zip(
                RELATIVE,
                [wait, model_0, int(cur["lead_0"]) - wait, median(dev)]
                + [median(per_source[letter]) for letter in FORECAST],
            )
        )

    new = start not in records
    rec = records.setdefault(start, new_record())
    for k in chain if new else chain[-1:]:
        e, t = entries[k], time(k)
        rec["flood_end"] = stamp(t)
        if e["lead"] != "0":
            continue
        # wait stays at the flood start in the chain entries
        flood_t = start_t + timedelta(hours=int(e["wait"]))
        if rec["flood_start"] is None:
            rec["flood_start"] = stamp(flood_t)
            rec["start_to_lead"] = relative(flood_t, k)
            rec["model_flood_start"] = models(source_leads(sources[k]), 0)
        rec["flood_total_dur"] = hours(t, flood_t)
        if e["obs"] != "1":
            continue
        if not rec["obs"]:
            rec["obs"] = 1
            rec["flood_st_to_obs"] = hours(t, flood_t)
            rec["start_to_obs"] = relative(t, k)
        first_obs = flood_t + timedelta(hours=rec["flood_st_to_obs"])
        rec["obs_dur"] = hours(t, first_obs)


def models(leads, lead):
    """the models of leads ({letter: lead time}, in their order) at lead"""
    return [letter for letter, h in leads.items() if h == lead]


def stamp(t):
    return t.strftime(STAMP)


def update_chains(chains, cycle, table):
    """update chains (STATS_CHAINS) with the stats csv of the YYYYMMDDHH cycle
    (history_index of it)"""
    now = datetime.strptime(cycle, "%Y%m%d%H")
    for p, row in table.iterrows():
        entry = chains.setdefault(str(int(p)), {"latest_record": None, "chains": {}})
        chain_records(
            entry["chains"],
            row["Stats_Sources_history"].split(","),
            row["Stats_Chains_history"].split(","),
            now,
        )
        entry["latest_record"] = stamp(now)


def initial_chains():
    """STATS_CHAINS with no records: every watershed of data/Attributes.csv
    (those of the watershed shapefile)"""
    import settings

    ids = pd.read_csv(
        os.path.join(settings.BASE_DATA_DIR, "Attributes.csv"),
        usecols=["pfaf_id"],
        encoding="latin1",
    )["pfaf_id"]
    return {str(int(p)): {"latest_record": None, "chains": {}} for p in ids.dropna()}


def chains_json(chains):
    """the STATS_CHAINS content of chains, one watershed per line"""
    lines = [
        f"{json.dumps(p)}: {json.dumps(v, separators=(',', ':'))}"
        for p, v in sorted(chains.items(), key=lambda kv: int(kv[0]))
    ]
    return ("{\n" + ",\n".join(lines) + "\n}\n").encode("utf-8")


def publish_chains(session):
    """update STATS_CHAINS on STATS_BRANCH from the stats csv files newer than
    its latest latest_record (all of them when it does not exist yet), in one
    commit"""
    try:
        head_tree(session, STATS_BRANCH)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            logging.info(f"publish chains: no branch {STATS_BRANCH} in {REPO}")
            return
        raise
    tables = {}
    for _ in range(RETRIES):
        head, tree = head_tree(session, STATS_BRANCH)
        remote = folder_files(session, tree, STATS_FOLDER, ".csv", "publish chains")
        sha = folder_files(session, tree, STATS_SUMMARIES, ".json", "publish chains").get(
            os.path.basename(STATS_CHAINS)
        )
        if sha:
            chains = json.loads(read_blob(session, sha))
        else:
            logging.info(f"publish chains: no {STATS_CHAINS}, created")
            chains = initial_chains()
        latest = max(
            (v["latest_record"] for v in chains.values() if v["latest_record"]),
            default="",
        )
        names = sorted(
            (n for n in remote if alert_cycle(n)[2:] > latest), key=alert_cycle
        )
        if not names:
            logging.info(f"publish chains: {REPO}/{STATS_CHAINS} is up to date")
            return
        for name in names:
            if remote[name] not in tables:
                tables[remote[name]] = history_index(
                    pd.read_csv(
                        io.BytesIO(read_blob(session, remote[name])),
                        encoding=ENCODING,
                        dtype=str,
                        keep_default_na=False,
                    )
                )
            table = tables[remote[name]]
            if table is None:
                logging.warning(f"publish chains: {name} has no {WINDOWED}, skipped")
                continue
            update_chains(chains, alert_cycle(name), table)
        entry = bytes_entry(session, STATS_CHAINS, chains_json(chains))
        if commit(session, head, tree, [entry], commit_message("chains", names), STATS_BRANCH):
            logging.info(
                f"publish chains: {REPO}/{STATS_CHAINS} on {STATS_BRANCH} updated "
                f"from {len(names)} stats file(s), last {names[-1]}"
            )
            return
        logging.info("publish chains: branch moved during the push, retrying")
    logging.warning(f"publish chains: gave up after {RETRIES} attempts")


if __name__ == "__main__":
    import settings

    # stdout, as publish_alerts
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    session = github_session("publish chains")
    if session is not None:
        publish_chains(session)
