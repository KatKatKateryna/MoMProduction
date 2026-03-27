"""
Download all DFO summary CSVs from the MoM server into a local subfolder.

- Files are saved to DOWNLOAD_DIR as-is.
- Checks disk space before each file submission; stops if < MIN_FREE_DISK_GB.
  Any download already in flight is allowed to finish.
- Skips files that already exist in DOWNLOAD_DIR (resume-friendly).
"""

import re
import time
import os
import shutil
import concurrent.futures
import requests

BASE_URL = "https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/DFO/DFO_summary/"
base_dir = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(base_dir, "dfo_csv_files")
MAX_WORKERS = 10
RETRY_ATTEMPTS = 3
RETRY_DELAY = 5
MIN_FREE_DISK_GB = 1.0


def free_disk_gb():
    return shutil.disk_usage(os.path.abspath(DOWNLOAD_DIR)).free / (1024 ** 3)


def list_filenames():
    resp = requests.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    return sorted(re.findall(r'href="(DFO_\w+\.csv)"', resp.text))


def download_one(filename):
    dest = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(dest):
        return filename, True, "already exists"

    url = BASE_URL + filename
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            with open(dest, "w", encoding="utf-8", newline="") as f:
                f.write(resp.text)
            return filename, True, None
        except Exception as exc:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                return filename, False, str(exc)


def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print("Fetching file list...")
    filenames = list_filenames()
    total = len(filenames)

    already = sum(1 for f in filenames if os.path.exists(os.path.join(DOWNLOAD_DIR, f)))
    print(f"Found {total} files on server. {already} already downloaded, {total - already} to fetch.")
    print(f"Saving to: {DOWNLOAD_DIR}/")
    print(f"Disk check: {free_disk_gb():.2f} GB free. Will stop below {MIN_FREE_DISK_GB} GB.\n")

    filenames_to_download = [f for f in filenames if not os.path.exists(os.path.join(DOWNLOAD_DIR, f))]

    failed = []
    downloaded = 0
    stopped_early = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}

        for filename in filenames_to_download:
            disk = free_disk_gb()
            if disk < MIN_FREE_DISK_GB:
                print(f"\n  Disk space low ({disk:.2f} GB free) — stopping new submissions. "
                      f"Waiting for {len(futures)} in-flight downloads to finish...")
                stopped_early = True
                break

            future = executor.submit(download_one, filename)
            futures[future] = filename

        for i, future in enumerate(concurrent.futures.as_completed(futures), 1):
            fname, ok, msg = future.result()
            if not ok:
                print(f"  FAILED: {fname} — {msg}")
                failed.append(fname)
            elif msg != "already exists":
                downloaded += 1

            if i % 100 == 0 or i == len(futures):
                print(f"  {i}/{len(futures)} resolved | downloaded: {downloaded} | "
                      f"failed: {len(failed)} | disk free: {free_disk_gb():.2f} GB")

    total_on_disk = sum(1 for f in filenames if os.path.exists(os.path.join(DOWNLOAD_DIR, f)))
    print(f"\nDone. {total_on_disk}/{total} files now in {DOWNLOAD_DIR}/")

    if stopped_early:
        print(f"Stopped early — ~{total - total_on_disk} files still missing. Re-run to continue.")

    if failed:
        print(f"\nFailed files ({len(failed)}):")
        for f in failed:
            print(f"  {f}")


if __name__ == "__main__":
    main()
