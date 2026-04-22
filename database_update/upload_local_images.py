#!/usr/bin/env python3
"""
upload_local_images.py
======================
Converts GeoTIFF images from the downloads_img folder tree into Cloud-Optimized
GeoTIFF (COG) format using rasterio/GDAL.

Source layout (downloads_img root, same convention as download_mom_img.py):
    <SOURCE>/<SOURCE>_image/*.tiff

Output layout (cog_images root, sibling of the Postgres DATA_DIR):
    <SOURCE>/*.tiff   (COG, tiled, overviews, DEFLATE)

The Postgres data directory is read from first_setup/db_setup/db_config.cfg
(key DATA_DIR).  The COG root is placed as a sibling of that directory:
    <parent of DATA_DIR>/cog_images

Usage:
    python database_update/upload_local_images.py
    python database_update/upload_local_images.py --folder GFMS
    python database_update/upload_local_images.py --force   # reprocess existing
    python database_update/upload_local_images.py -q max    # larger file, faster online loading
"""

import argparse
import re
import sys
import tempfile
import time
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

    return Path(data_dir).expanduser().parent.parent / "cog_images"


TOP_FOLDERS = ["DFO", "Final_Alert", "GFMS", "HWRF", "VIIRS"]

# CRS to assign when the source file lacks one (EPSG code or None to leave as-is)
FOLDER_CRS = {
    "GFMS": 4326,
}

# Quality presets:
# "nano" — absolute minimum file size: no overviews, ZSTD max compression, 2048-px tiles (lossless)
# "min"  — small file, some overviews for zoom-out speed
# "max"  — fastest tile loading, largest file
QUALITY_PRESETS = {
    "nano": {"block_size": 2048, "overview_levels": [],                "compress": "zstd", "zlevel": 22},
    "min":  {"block_size": 1024, "overview_levels": [4, 16],           "compress": "deflate", "zlevel": 9},
    "max":  {"block_size": 256,  "overview_levels": [2, 4, 8, 16, 32], "compress": "deflate", "zlevel": 6},
}

# ---------------------------------------------------------------------------
# COG conversion
# ---------------------------------------------------------------------------

def convert_to_cog(src_path: Path, dst_path: Path, crs=None, quality: str = "nano") -> None:
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

    preset = QUALITY_PRESETS[quality]
    block_size = preset["block_size"]
    overview_levels = preset["overview_levels"]
    compress = preset["compress"]
    zlevel = preset["zlevel"]

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as _tmp:
        tmp_path = Path(_tmp.name)

    try:
        # --- Step 1: write a tiled intermediate copy (windowed to avoid OOM) ---
        with rasterio.open(src_path) as src:
            profile = src.profile.copy()
            profile.update(
                driver="GTiff",
                tiled=True,
                blockxsize=block_size,
                blockysize=block_size,
                compress=compress,
                predictor=2,        # horizontal differencing — improves ratio
                zlevel=zlevel,
                interleave="band",
            )
            if crs is not None:
                from rasterio.crs import CRS
                profile["crs"] = CRS.from_epsg(crs)
            with rasterio.open(tmp_path, "w", **profile) as tmp:
                for _, window in src.block_windows(1):
                    tmp.write(src.read(window=window), window=window)

        # --- Step 2: build overviews on the intermediate file ---
        with rasterio.open(tmp_path, "r+") as tmp:
            tmp.build_overviews(overview_levels, Resampling.average)
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
    parser.add_argument(
        "-q", "--quality",
        choices=["nano", "min", "max"],
        default="min",
        help=(
            "Output quality preset. "
            "'nano' (default): smallest file, no overviews, larger tiles, max compression. "
            "'min': smaller file, fewer overviews, large tiles, max compression. "
            "'max': faster tile loading, more overviews, smaller tiles, standard compression."
        ),
    )
    args = parser.parse_args()

    # Force line-buffering so every print() flushes immediately when stdout is
    # redirected to a file (e.g. nohup). Without this, output accumulates in an
    # 8 KB buffer and only appears at program exit.
    sys.stdout.reconfigure(line_buffering=True)

    cog_root = _get_cog_root()
    print(f"Quality: {args.quality}", flush=True)

    if not DOWNLOADS_ROOT.exists():
        print(f"ERROR: downloads root not found: {DOWNLOADS_ROOT}", file=sys.stderr)
        print("Run download_mom_img.py first to populate it.", file=sys.stderr)
        sys.exit(1)

    cog_root.mkdir(parents=True, exist_ok=True)
    print(f"Source : {DOWNLOADS_ROOT}", flush=True)
    print(f"Output : {cog_root}", flush=True)

    folders = [args.folder] if args.folder else TOP_FOLDERS
    total = skipped = errors = 0

    for folder_name in folders:
        src_folder = DOWNLOADS_ROOT / folder_name
        if not src_folder.exists():
            print(f"\n[{folder_name}] not found in source, skipping.", flush=True)
            continue

        tiffs = list(_iter_tiffs(src_folder))
        if not tiffs:
            print(f"\n[{folder_name}] no .tiff files found.", flush=True)
            continue

        print(f"\n{'='*60}\n[{folder_name}]  {len(tiffs)} image(s) found\n{'='*60}", flush=True)

        for src_path in tiffs:
            rel      = src_path.relative_to(DOWNLOADS_ROOT)
            dst_path = cog_root / folder_name / src_path.name

            if dst_path.exists() and not args.force:
                skipped += 1
                continue

            try:
                t0 = time.monotonic()
                convert_to_cog(src_path, dst_path, crs=FOLDER_CRS.get(folder_name), quality=args.quality)
                elapsed = time.monotonic() - t0
                print(f"  [COG ] {rel}  ({elapsed:.1f}s)", flush=True)
                total += 1
            except Exception as exc:
                print(f"  [FAIL] {rel}: {exc}", flush=True, file=sys.stderr)
                errors += 1

    print(f"\nDone.  converted={total}  skipped={skipped}  errors={errors}", flush=True)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
