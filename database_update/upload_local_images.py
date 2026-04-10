#!/usr/bin/env python3
"""
upload_local_images.py
======================
Converts GeoTIFF images from the downloads_img folder tree into Cloud-Optimized
GeoTIFF (COG) format using rasterio/GDAL.

Source layout (downloads_img root, same convention as download_mom_img.py):
    <SOURCE>/<SOURCE>_image/*.tiff

Output layout (cog_images root, sibling of the Postgres DATA_DIR):
    <SOURCE>/<SOURCE>_image/*.tiff   (COG, tiled, overviews, DEFLATE)

The Postgres data directory is read from first_setup/db_setup/db_config.cfg
(key DATA_DIR).  The COG root is placed as a sibling of that directory:
    <parent of DATA_DIR>/cog_images

Usage:
    python database_update/upload_local_images.py
    python database_update/upload_local_images.py --folder GFMS
    python database_update/upload_local_images.py --force   # reprocess existing
"""

import argparse
import re
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_ON_LINUX = Path("/mnt").exists()

# Raw image downloads — mirrors download_mom_img.py
DOWNLOADS_ROOT = (
    Path("/mnt/temp_img_download/downloads_img")
    if _ON_LINUX
    else Path(__file__).parent / "downloads_img"
)

# db_config.cfg that contains DATA_DIR
_DB_CFG_PATH = Path(__file__).parent.parent / "first_setup" / "db_setup" / "db_config.cfg"
# _SAMPLE_DB_CFG_PATH = Path(__file__).parent.parent / "first_setup" / "db_setup" / "sample_db_config.cfg"


def _parse_shell_cfg(path: Path) -> dict:
    """Parse a shell-style key="value" config file into a dict."""
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r'^(\w+)\s*=\s*"?([^"#]*)"?\s*(?:#.*)?$', line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _get_cog_root() -> Path:
    """Derive the COG root from DATA_DIR in db_config.cfg."""
    cfg_path = _DB_CFG_PATH  # if _DB_CFG_PATH.exists() else _SAMPLE_DB_CFG_PATH
    if not cfg_path.exists():
        print(
            "ERROR: db_config.cfg not found.\n"
            f"  Looked for: {_DB_CFG_PATH}\n",
            #f"  Fallback:   {_SAMPLE_DB_CFG_PATH}",
            file=sys.stderr,
        )
        sys.exit(1)

    cfg = _parse_shell_cfg(cfg_path)
    data_dir = cfg.get("DATA_DIR")
    if not data_dir:
        print(
            f"ERROR: DATA_DIR not found in {cfg_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    return Path(data_dir).expanduser().parent / "cog_images"


TOP_FOLDERS = ["DFO", "Final_Alert", "GFMS", "HWRF", "VIIRS"]

# Tile block size for COG internal tiling
BLOCK_SIZE = 512

# Overview levels written into the COG
OVERVIEW_LEVELS = [2, 4, 8, 16, 32]

# ---------------------------------------------------------------------------
# COG conversion
# ---------------------------------------------------------------------------

def convert_to_cog(src_path: Path, dst_path: Path) -> None:
    """Convert a GeoTIFF at *src_path* to a COG written at *dst_path*.

    Strategy:
      1. Read the source into a temporary GeoTIFF with internal tiling.
      2. Build overviews on the temporary file.
      3. Use rasterio.shutil.copy with copy_src_overviews=True to produce the
         final COG where overviews precede the image data — the defining
         property of the cloud-optimised layout.
    """
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.shutil import copy as rio_copy
    except ImportError:
        print(
            "ERROR: rasterio is required. Install it with: pip install rasterio",
            file=sys.stderr,
        )
        sys.exit(1)

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as _tmp:
        tmp_path = Path(_tmp.name)

    try:
        # --- Step 1: write a tiled intermediate copy ---
        with rasterio.open(src_path) as src:
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                tiled=True,
                blockxsize=BLOCK_SIZE,
                blockysize=BLOCK_SIZE,
                compress="deflate",
                predictor=2,        # horizontal differencing — improves ratio
                interleave="band",
            )
            with rasterio.open(tmp_path, "w", **profile) as tmp:
                tmp.write(src.read())

        # --- Step 2: build overviews on the intermediate file ---
        with rasterio.open(tmp_path, "r+") as tmp:
            tmp.build_overviews(OVERVIEW_LEVELS, Resampling.average)
            tmp.update_tags(ns="rio_overview", resampling="average")

        # --- Step 3: copy into true COG layout (overviews before image data) ---
        cog_profile = profile.copy()
        cog_profile.update(copy_src_overviews=True)
        rio_copy(tmp_path, dst_path, **cog_profile)

    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Walk helpers
# ---------------------------------------------------------------------------

def _iter_tiffs(root: Path):
    """Yield all .tiff / .tif files under *root*, sorted for reproducibility."""
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".tiff", ".tif"):
            yield path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert downloaded GeoTIFFs to Cloud-Optimized GeoTIFF (COG)."
    )
    parser.add_argument(
        "--folder",
        choices=TOP_FOLDERS,
        default=None,
        metavar="FOLDER",
        help="Process only one top-level folder (default: all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process images that already exist in the COG output folder.",
    )
    args = parser.parse_args()

    cog_root = _get_cog_root()

    if not DOWNLOADS_ROOT.exists():
        print(f"ERROR: downloads root not found: {DOWNLOADS_ROOT}", file=sys.stderr)
        print("Run download_mom_img.py first to populate it.", file=sys.stderr)
        sys.exit(1)

    cog_root.mkdir(parents=True, exist_ok=True)
    print(f"Source : {DOWNLOADS_ROOT}")
    print(f"Output : {cog_root}")

    folders = [args.folder] if args.folder else TOP_FOLDERS
    total = skipped = errors = 0

    for folder_name in folders:
        src_folder = DOWNLOADS_ROOT / folder_name
        if not src_folder.exists():
            print(f"\n[{folder_name}] not found in source, skipping.")
            continue

        tiffs = list(_iter_tiffs(src_folder))
        if not tiffs:
            print(f"\n[{folder_name}] no .tiff files found.")
            continue

        print(f"\n{'='*60}\n[{folder_name}]  {len(tiffs)} image(s) found\n{'='*60}")

        for src_path in tiffs:
            rel      = src_path.relative_to(DOWNLOADS_ROOT)
            dst_path = cog_root / rel

            if dst_path.exists() and not args.force:
                skipped += 1
                continue

            print(f"  [COG ] {rel}")
            try:
                convert_to_cog(src_path, dst_path)
                total += 1
            except Exception as exc:
                print(f"  [FAIL] {rel}: {exc}", file=sys.stderr)
                errors += 1

    print(f"\nDone.  converted={total}  skipped={skipped}  errors={errors}")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
