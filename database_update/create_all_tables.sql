-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- GFMS
CREATE TABLE IF NOT EXISTS gfms_summary (
    pfaf_id              INTEGER,
    "timestamp"          VARCHAR(64),
    "GFMS_TotalArea_km"  DOUBLE PRECISION,
    "GFMS_perc_Area"     DOUBLE PRECISION,
    "GFMS_MeanDepth"     DOUBLE PRECISION,
    "GFMS_MaxDepth"      DOUBLE PRECISION,
    "GFMS_Duration"      INTEGER,
    PRIMARY KEY (pfaf_id, "timestamp")
);

-- HWRF
CREATE TABLE IF NOT EXISTS hwrf_summary (
    pfaf_id              INTEGER,
    "timestamp"          VARCHAR(64),
    "Rain_TotalArea_km"  DOUBLE PRECISION,
    "perc_Area"          DOUBLE PRECISION,
    "MeanRain"           DOUBLE PRECISION,
    "MaxRain"            DOUBLE PRECISION,
    PRIMARY KEY (pfaf_id, "timestamp")
);

-- GloFAS
CREATE TABLE IF NOT EXISTS glofas_summary (
    pfaf_id             INTEGER,
    "timestamp"         VARCHAR(64),
    "Point No"          INTEGER,
    "ID"                TEXT,
    "Basin"             TEXT,
    "Location"          TEXT,
    "Station"           TEXT,
    "Country"           TEXT,
    "Continent"         TEXT,
    "Country_code"      VARCHAR(3),
    "Upstream area"     DOUBLE PRECISION,
    "Lon"               DOUBLE PRECISION,
    "Lat"               DOUBLE PRECISION,
    "unknown_2"         INTEGER,
    "Days_until_peak"   INTEGER,
    "GloFAS_2yr"        DOUBLE PRECISION,
    "GloFAS_5yr"        DOUBLE PRECISION,
    "GloFAS_20yr"       DOUBLE PRECISION,
    "Alert_level"       INTEGER,
    "area_km2"          DOUBLE PRECISION,
    "ISO"               VARCHAR(3),
    "Admin0"            TEXT,
    "Admin1"            TEXT,
    "rfr_score"         DOUBLE PRECISION,
    "cfr_score"         DOUBLE PRECISION,
    "Forecast Date"     TIMESTAMP,
    "max_EPS"           TEXT,
    PRIMARY KEY (pfaf_id, "timestamp")
);

-- VIIRS
CREATE TABLE IF NOT EXISTS viirs_summary (
    pfaf_id                  INTEGER,
    "timestamp"              VARCHAR(64),
    "onedayFlood_Area_km"    DOUBLE PRECISION,
    "onedayperc_Area"        DOUBLE PRECISION,
    "fivedayFlood_Area_km"   DOUBLE PRECISION,
    "fivedayperc_Area"       DOUBLE PRECISION,
    PRIMARY KEY (pfaf_id, "timestamp")
);

-- DFO
CREATE TABLE IF NOT EXISTS dfo_summary (
    pfaf_id                   INTEGER,
    "timestamp"               VARCHAR(64),
    "1-Day_TotalArea_km2"     DOUBLE PRECISION,
    "1-Day_perc_Area"         DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"  DOUBLE PRECISION,
    "1-Day_CS_perc_Area"      DOUBLE PRECISION,
    "2-Day_TotalArea_km2"     DOUBLE PRECISION,
    "2-Day_perc_Area"         DOUBLE PRECISION,
    "3-Day_TotalArea_km2"     DOUBLE PRECISION,
    "3-Day_perc_Area"         DOUBLE PRECISION,
    PRIMARY KEY (pfaf_id, "timestamp")
);

-- Final Alert
CREATE TABLE IF NOT EXISTS final_alert (
    pfaf_id                     INTEGER,
    "timestamp"                 VARCHAR(64),
    "name"                      TEXT,
    "name_1"                    TEXT,
    "CentroidX"                 DOUBLE PRECISION,
    "CentroidY"                 DOUBLE PRECISION,
    "Admin1_count"              DOUBLE PRECISION,
    "Admin1_names"              TEXT,
    "area_km2"                  DOUBLE PRECISION,
    "rfr_score"                 DOUBLE PRECISION,
    "cfr_score"                 DOUBLE PRECISION,
    "Alert_level"               DOUBLE PRECISION,
    "Days_until_peak"           DOUBLE PRECISION,
    "GloFAS_2yr"                DOUBLE PRECISION,
    "GloFAS_5yr"                DOUBLE PRECISION,
    "GloFAS_20yr"               DOUBLE PRECISION,
    "Alert_Score"               DOUBLE PRECISION,
    "PeakArrivalScore"          DOUBLE PRECISION,
    "TwoYScore"                 DOUBLE PRECISION,
    "FiveYScore"                DOUBLE PRECISION,
    "TwtyYScore"                DOUBLE PRECISION,
    "Sum_Score_x"               DOUBLE PRECISION,
    "GFMS_TotalArea_km"         DOUBLE PRECISION,
    "GFMS_perc_Area"            DOUBLE PRECISION,
    "GFMS_MeanDepth"            DOUBLE PRECISION,
    "GFMS_MaxDepth"             DOUBLE PRECISION,
    "GFMS_Duration"             DOUBLE PRECISION,
    "GFMS_area_score"           DOUBLE PRECISION,
    "GFMS_perc_area_score"      DOUBLE PRECISION,
    "MeanD_Score"               DOUBLE PRECISION,
    "MaxD_Score"                DOUBLE PRECISION,
    "Duration_Score"            DOUBLE PRECISION,
    "Sum_Score_y"               DOUBLE PRECISION,
    "MOM_Score"                 DOUBLE PRECISION,
    "Hazard_Score"              DOUBLE PRECISION,
    "Scaled_Riverine_Risk"      DOUBLE PRECISION,
    "Scaled_Coastal_Risk"       DOUBLE PRECISION,
    "Flag"                      DOUBLE PRECISION,
    "1-Day_TotalArea_km2"       DOUBLE PRECISION,
    "1-Day_perc_Area"           DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"    DOUBLE PRECISION,
    "1-Day_CS_perc_Area"        DOUBLE PRECISION,
    "2-Day_TotalArea_km2"       DOUBLE PRECISION,
    "2-Day_perc_Area"           DOUBLE PRECISION,
    "3-Day_TotalArea_km2"       DOUBLE PRECISION,
    "3-Day_perc_Area"           DOUBLE PRECISION,
    "DFO_area_1day_score"       DOUBLE PRECISION,
    "DFO_percarea_1day_score"   DOUBLE PRECISION,
    "DFO_area_2day_score"       DOUBLE PRECISION,
    "DFO_percarea_2day_score"   DOUBLE PRECISION,
    "DFO_area_3day_score"       DOUBLE PRECISION,
    "DFO_percarea_3day_score"   DOUBLE PRECISION,
    "DFOTotal_Score"            DOUBLE PRECISION,
    "onedayFlood_Area_km"       DOUBLE PRECISION,
    "onedayperc_Area"           DOUBLE PRECISION,
    "fivedayFlood_Area_km"      DOUBLE PRECISION,
    "fivedayperc_Area"          DOUBLE PRECISION,
    "VIIRS_area_1day_score"     DOUBLE PRECISION,
    "VIIRS_percarea_1day_score" DOUBLE PRECISION,
    "VIIRS_area_5day_score"     DOUBLE PRECISION,
    "VIIRS_percarea_5day_score" DOUBLE PRECISION,
    "VIIRSTotal_Score"          DOUBLE PRECISION,
    "Severity"                  DOUBLE PRECISION,
    "Alert"                     TEXT,
    "Status"                    TEXT,
    PRIMARY KEY (pfaf_id, "timestamp")
);
