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

-- GloFAS stations (static per-station metadata)
CREATE TABLE IF NOT EXISTS glofas_stations (
    station_id          INTEGER         PRIMARY KEY,
    "Station"           TEXT,
    "Basin"             TEXT,
    "Country"           TEXT,
    "Country_code"      VARCHAR(8),
    "Continent"         TEXT,
    "ISO"               VARCHAR(8),
    "Admin0"            TEXT,
    "Admin1"            TEXT,
    "Location"          TEXT,
    "Lat"               DOUBLE PRECISION,
    "Lon"               DOUBLE PRECISION,
    "Upstream area"     DOUBLE PRECISION,
    "area_km2"          DOUBLE PRECISION,
    pfaf_id             INTEGER,
    "rfr_score"         DOUBLE PRECISION,
    "cfr_score"         DOUBLE PRECISION
);

-- GloFAS merged (dynamic per-timestamp forecast data)
CREATE TABLE IF NOT EXISTS glofas_merged (
    "timestamp"         VARCHAR(64),
    station_id          INTEGER         REFERENCES glofas_stations(station_id),
    pfaf_id             INTEGER,
    "ID"                TEXT,
    "Point No"          INTEGER,
    "Alert_level"       INTEGER,
    "Days_until_peak"   INTEGER,
    "GloFAS_2yr"        DOUBLE PRECISION,
    "GloFAS_5yr"        DOUBLE PRECISION,
    "GloFAS_20yr"       DOUBLE PRECISION,
    "max_EPS"           TEXT,
    "Forecast Date"     TIMESTAMP,
    PRIMARY KEY ("timestamp", station_id)
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

-- Watershed lookup (static per-watershed metadata)
CREATE TABLE IF NOT EXISTS watershed_lookup (
    watershed_id        INTEGER         PRIMARY KEY,
    pfaf_id             INTEGER,
    "name"              TEXT,
    "name_1"            TEXT,
    "CentroidX"         DOUBLE PRECISION,
    "CentroidY"         DOUBLE PRECISION,
    "Admin1_count"      INTEGER,
    "Admin1_names"      TEXT,
    "area_km2"          DOUBLE PRECISION
);

-- Final Alert (dynamic per-timestamp alert data)
CREATE TABLE IF NOT EXISTS final_alert (
    "timestamp"                 VARCHAR(64),
    watershed_id                INTEGER         REFERENCES watershed_lookup(watershed_id),
    pfaf_id                     INTEGER,
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
    PRIMARY KEY ("timestamp", watershed_id)
);
