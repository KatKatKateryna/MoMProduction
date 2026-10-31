"""
Push the new final alert csv files (settings.FINAL_MOM_DIR) to a GitHub repo
through the GitHub API, without a local clone.

Every local csv whose name sorts after the last csv existing on remote,
is pushed all in one commit. With no csv in the repo folder yet, only the newest
local one is pushed.

Needs in the environment or .env (without them, nothing is pushed):
    GITHUB_REPO              the target repo as owner/name
    GITHUB_ALERTS_TOKEN   fine-grained token with Contents read/write on it

"""

import argparse
import base64
import glob
import logging
import os
import re
import sys

import requests
from dotenv import load_dotenv

import settings

load_dotenv()

# setting GITHUB_REPO / GITHUB_ALERTS_TOKEN on Linux:
#   - for cron: add them to .env in the repository root, which
#     load_dotenv() above reads; cron jobs do not load ~/.bashrc or ~/.profile
#         sudo nano /etc/environment
#         GITHUB_REPO=owner/name
#         GITHUB_ALERTS_TOKEN=github_pat_...
#   - for the current shell only (e.g. a manual --dry-run):
#         export GITHUB_REPO=owner/name
#         export GITHUB_ALERTS_TOKEN=github_pat_...

API = "https://api.github.com"
REPO = os.getenv("GITHUB_REPO")
BRANCH = "main"
FOLDER = "Alerts/csv"
# a concurrent job may move the branch between our read and our update
RETRIES = 3


def gh(session, method, path, **kwargs):
    """call the GitHub API, raise on error, return the json"""
    r = session.request(method, f"{API}/repos/{REPO}/{path}", timeout=60, **kwargs)
    r.raise_for_status()
    return r.json()


def remote_state(session):
    """head commit, its tree and the csv names in the repo folder"""
    head = gh(session, "GET", f"git/ref/heads/{BRANCH}")["object"]["sha"]
    tree = gh(session, "GET", f"git/commits/{head}")["tree"]["sha"]
    listing = gh(session, "GET", f"git/trees/{tree}", params={"recursive": 1})
    if listing.get("truncated"):
        logging.warning(f"push alerts: {REPO} tree listing truncated")
    prefix = FOLDER + "/"
    names = [
        e["path"][len(prefix) :]
        for e in listing["tree"]
        if e["type"] == "blob"
        and e["path"].startswith(prefix)
        and e["path"].endswith(".csv")
    ]
    return head, tree, names


def new_alerts(remote_names):
    """local csv files later than the newest one in the repo"""
    local = sorted(glob.glob(os.path.join(settings.FINAL_MOM_DIR, "*.csv")))
    if not remote_names:
        return local[-1:]
    last = max(remote_names)
    return [f for f in local if os.path.basename(f) > last]


CYCLE = re.compile(r"_(\d{10})")


def alert_cycle(name):
    """the YYYYMMDDHH cycle of an alert file name, else the name itself"""
    m = CYCLE.search(name)
    return m.group(1) if m else name


def commit_files(session, head, tree, files):
    """one commit adding files to the repo folder; False if the branch moved"""
    entries = []
    for f in files:
        with open(f, "rb") as fh:
            content = base64.b64encode(fh.read()).decode("ascii")
        blob = gh(
            session,
            "POST",
            "git/blobs",
            json={"content": content, "encoding": "base64"},
        )
        entries.append(
            {
                "path": f"{FOLDER}/{os.path.basename(f)}",
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            }
        )
    new_tree = gh(
        session, "POST", "git/trees", json={"base_tree": tree, "tree": entries}
    )["sha"]
    cycles = [alert_cycle(os.path.basename(f)) for f in files]
    message = (
        f"alert for {cycles[0]}"
        if len(cycles) == 1
        else f"alerts for {cycles[0]} .. {cycles[-1]}"
    )
    commit = gh(
        session,
        "POST",
        "git/commits",
        json={"message": message, "tree": new_tree, "parents": [head]},
    )["sha"]
    r = session.patch(
        f"{API}/repos/{REPO}/git/refs/heads/{BRANCH}",
        json={"sha": commit, "force": False},
        timeout=60,
    )
    if r.status_code == 422:  # not a fast-forward: someone pushed meanwhile
        return False
    r.raise_for_status()
    return True


def push_alerts(dry_run=False):
    """push the final alerts newer than the newest one in the repo"""
    if not REPO:
        logging.info("push alerts: no GITHUB_REPO configured, skipped")
        return
    token = os.getenv("GITHUB_ALERTS_TOKEN")
    if not token and not dry_run:
        logging.info("push alerts: no GitHub token configured, skipped")
        return
    session = requests.Session()
    session.headers.update(
        {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    )
    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    for _ in range(RETRIES):
        head, tree, remote_names = remote_state(session)
        files = new_alerts(remote_names)
        if not files:
            logging.info(f"push alerts: {REPO}/{FOLDER} is up to date")
            return
        if dry_run:
            logging.info(
                f"push alerts (dry run): newest in repo: {max(remote_names, default='none')}"
            )
            for f in files:
                logging.info(f"push alerts (dry run): would push {os.path.basename(f)}")
            return
        if commit_files(session, head, tree, files):
            logging.info(
                f"push alerts: pushed {len(files)} file(s) to {REPO}/{FOLDER}, last {os.path.basename(files[-1])}"
            )
            return
        logging.info("push alerts: branch moved during the push, retrying")
    logging.warning(f"push alerts: gave up after {RETRIES} attempts")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="push new final alerts to GitHub")
    parser.add_argument(
        "--dry-run", action="store_true", help="only list what would be pushed"
    )
    # stdout: run_mom_job.sh's cron entry appends it to the job's cron log
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO, format=settings.LOG_FORMAT, force=True
    )
    push_alerts(parser.parse_args().dry_run)
