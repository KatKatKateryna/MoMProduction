"""
Settings for MoM Production
    -- production.cfg
    -- define folder structure
        -- folders for data processing
        -- folders for data products

"""

import configparser
import logging
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DATA_DIR = os.path.join(BASE_DIR, "data")

config = configparser.ConfigParser(allow_no_value=True)
config.read(os.path.join(BASE_DIR, "production.cfg"))

# config directory
# base directory for downloading and processing data
WORKING_DIR = os.path.expanduser(
    config.get("general", "WORKING_DIR").replace("/", os.sep)
)
# base directory for the data products
PRODUCT_DIR = os.path.expanduser(
    config.get("general", "PRODUCT_DIR").replace("/", os.sep)
)

# config GLOFAS directory
GLOFAS_PROC_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "glofas"))
GLOFAS_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "GLOFAS"))
GLOFAS_SUM_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "GLOFAS"))

# config GFMS directory
GFMS_PROC_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "gfms"))
GFMS_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "GFMS"))
GFMS_SUM_DIR = os.path.join(GFMS_DIR, "GFMS_summary")
GFMS_IMG_DIR = os.path.join(GFMS_DIR, "GFMS_image")
GFMS_MOM_DIR = os.path.join(GFMS_DIR, "GFMS_MoM")

# config DFO directory
DFO_PROC_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "dfo"))
DFO_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "DFO"))
DFO_SUM_DIR = os.path.join(DFO_DIR, "DFO_summary")
DFO_IMG_DIR = os.path.join(DFO_DIR, "DFO_image")
DFO_MOM_DIR = os.path.join(DFO_DIR, "DFO_MoM")

# config VIIRS directory
VIIRS_PROC_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "VIIRS"))
VIIRS_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "VIIRS"))
VIIRS_SUM_DIR = os.path.join(VIIRS_DIR, "VIIRS_summary")
VIIRS_IMG_DIR = os.path.join(VIIRS_DIR, "VIIRS_image")
VIIRS_MOM_DIR = os.path.join(VIIRS_DIR, "VIIRS_MoM")

# config HWRF directory
HWRF_PROC_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "hwrf"))
HWRF_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "HWRF"))
HWRF_SUM_DIR = os.path.join(HWRF_DIR, "HWRF_summary")
HWRF_IMG_DIR = os.path.join(HWRF_DIR, "HWRF_image")
HWRF_MOM_DIR = os.path.join(HWRF_DIR, "HWRF_MoM")
# HWRF time_delay: 6 hours
HWRF_TIME_DELAY = 6

# final product
FINAL_MOM = os.path.join(PRODUCT_DIR, config.get("products_dir", "FINAL"))
FINAL_MOM_DIR = os.path.join(PRODUCT_DIR, config.get("products_dir", "FINAL"))

# config watershed shp file
WATERSHED_DIR = os.path.join(BASE_DIR, "data", "watershed_shp")
WATERSHED_SHP = os.path.join(WATERSHED_DIR, "Watershed_pfaf_id.shp")

# setup logging
# a new log for each month, and a separate file per job when the job is known
LOG_DIR = os.path.join(WORKING_DIR, config.get("processing_dir", "logs"))
LOG_FORMAT = "%(asctime)s - %(module)s - %(levelname)s : %(message)s"


def logfile_path(job=None):
    """monthly log file path, suffixed with the job name when one is given"""
    todays_date = datetime.now(timezone.utc)
    name = "{year}_{month}".format(year=todays_date.year, month=todays_date.month)
    if job:
        name = "{}_{}".format(name, str(job).upper())
    return os.path.join(LOG_DIR, "{}.log".format(name))


def configure_logging(job=None):
    """(re)point the root logger at this job's log file"""
    try:
        logging.basicConfig(
            filename=logfile_path(job),
            format=LOG_FORMAT,
            level=logging.INFO,
            force=True,
        )
    except Exception:
        pass


# default target until the job name is known: MoM_run calls this again with it
logfile = logfile_path()
configure_logging()
