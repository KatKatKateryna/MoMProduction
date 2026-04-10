#!/usr/bin/env python3
"""
Download files from the MoM production server into database_update/downloads_mom/.
Skips files that already exist locally.

Usage:
    python database_update/download_mom_data.py [--folder FOLDER]

    --folder  Only download one top-level folder (DFO, Final_Alert, GFMS, GLOFAS, HWRF, VIIRS).
              If omitted, all folders are downloaded.
"""

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, unquote

import requests

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/"
FOLDERS = ["DFO", "Final_Alert", "GFMS", "GLOFAS", "HWRF", "VIIRS"]
LOCAL_ROOT = Path(__file__).parent / "downloads_mom" if not Path("/mnt").exists() else Path("/mnt/volume_ams3_02/downloads_mom")

LOCAL_ROOT_TABLES = Path(__file__).parent / "downloads_mom" if not Path("/mnt").exists() else Path("/mnt/volume_ams3_02/downloads_mom")
LOCAL_ROOT_IMG = Path(__file__).parent / "downloads_img" if not Path("/mnt").exists() else Path("/mnt/temp_img_download/downloads_img") 

LIMIT = None
REQUEST_DELAY = 0.5   # seconds between each HTTP request (listing or download)


def list_directory(url, session):
    """Fetch an Apache directory listing and return (subdirs, files) as relative hrefs."""
    response = session.get(url, timeout=30)
    response.raise_for_status()
    time.sleep(REQUEST_DELAY)

    dirs, files = [], []
    for href in re.findall(r'href="([^"?]+)"', response.text):
        if href.startswith("/") or href == "../":
            continue
        if href.endswith("/"):
            dirs.append(href)
        else:
            files.append(href)

    return dirs, files


def download_file(url, local_path, session):
    """Download url to local_path; skip if already present."""
    if local_path.exists():
        # print(f"  [--skip--] {local_path.name}")
        return False

    local_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [download] {local_path.parent.name}: {local_path.name}")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
    time.sleep(REQUEST_DELAY)
    return True


def crawl(url, local_dir, session):
    """Recursively crawl a directory URL and download all files."""
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        dirs, files = list_directory(url, session)
    except requests.RequestException as e:
        print(f"  [warn] Could not list {url}: {e}", file=sys.stderr)
        return

    count = 0
    for filename in files:
        file_url = urljoin(url, filename)
        local_path = local_dir / unquote(filename)
        if download_file(file_url, local_path, session):
            count += 1
            if LIMIT and count >= LIMIT:
                print(f"  [limit reached] Stopping after {LIMIT} files.")
                break

    for dirname in dirs:
        sub_url = urljoin(url, dirname)
        sub_local = local_dir / unquote(dirname.rstrip("/"))
        print(f"\n[dir] {sub_url}")
        global LOCAL_ROOT
        if sub_local.endswith("_image"):
            LOCAL_ROOT = LOCAL_ROOT_IMG
        else:
            LOCAL_ROOT = LOCAL_ROOT_TABLES
        crawl(sub_url, sub_local, session)


def main():
    parser = argparse.ArgumentParser(description="Download MoM server data locally.")
    parser.add_argument(
        "--folder",
        choices=FOLDERS,
        default=None,
        help="Download only this top-level folder (default: all).",
    )
    args = parser.parse_args()

    folders = [args.folder] if args.folder else FOLDERS

    with requests.Session() as session:
        for folder in folders:
            folder_url = urljoin(BASE_URL, folder + "/")
            local_folder = LOCAL_ROOT / folder
            print(f"\n{'='*60}\n[folder] {folder}\n{'='*60}")
            crawl(folder_url, local_folder, session)

    print("\nDone.")


if __name__ == "__main__":
    main()
