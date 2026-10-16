"""
folder_layout.py
    -- where the summary and image folders of a data root are, for the
       backfill scripts

    A root can have either layout, folder by folder:
      - flat:   ROOT/gfms_summary, ROOT/viirs_image, ROOT/glofas, ...
                (what offline_summaries_from_images.py writes)
      - common: the production layout, e.g. ROOT/Products/GFMS/GFMS_summary,
                ROOT/Products/GLOFAS
    The flat folder is used when it exists, otherwise the common one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings  # noqa: E402

# the root of the common layout (the folder holding Products/)
DEFAULT_ROOT = os.path.dirname(settings.PRODUCT_DIR)

# flat folder name -> settings folder of the common layout
FOLDERS = {
    "gfms_summary": "GFMS_SUM_DIR",
    "gfms_image": "GFMS_IMG_DIR",
    "glofas": "GLOFAS_DIR",
    "dfo_summary": "DFO_SUM_DIR",
    "viirs_summary": "VIIRS_SUM_DIR",
    "viirs_image": "VIIRS_IMG_DIR",
    "hwrf_summary": "HWRF_SUM_DIR",
    "hwrf_image": "HWRF_IMG_DIR",
    "hafs_summary": "HAFS_SUM_DIR",
    "hafs_image": "HAFS_IMG_DIR",
}
# read once: the backfill scripts change some of these settings while running
_COMMON = {name: getattr(settings, attr) for name, attr in FOLDERS.items()}


def folder(root, name):
    """folder `name` (a FOLDERS key) of root: flat if it exists, else common"""
    root = os.path.abspath(root)
    flat = os.path.join(root, name)
    if os.path.isdir(flat):
        return flat
    return os.path.join(root, os.path.relpath(_COMMON[name], DEFAULT_ROOT))
