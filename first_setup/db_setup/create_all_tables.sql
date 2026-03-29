-- current server: https://mom.tg-ear190027.projects.jetstream-cloud.org/ModelofModels/

-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- ============================================================
-- Watershed Shapes: must be created first — all other tables
-- reference watershed_shapes(pfaf_id) as a foreign key.
-- One polygon per watershed from Watershed_pfaf_id.shp.
-- Columns mirror the shapefile schema exactly (WGS84 / EPSG:4326).
-- ============================================================
CREATE TABLE IF NOT EXISTS watershed_shapes (
    pfaf_id             INTEGER         PRIMARY KEY,
    "area_km2"          DOUBLE PRECISION,
    "ISO"               VARCHAR(8),
    "Admin0"            TEXT,
    "Admin1"            TEXT,
    "rfr_score"         DOUBLE PRECISION,
    "cfr_score"         DOUBLE PRECISION,
    geom                GEOMETRY(MultiPolygon, 4326)
);

CREATE INDEX IF NOT EXISTS idx_watershed_shapes_geom
    ON watershed_shapes USING GIST (geom);


-- ============================================================
-- Reference tables
-- ============================================================

-- All GloFAS stations: static per-station metadata
-- Lat/Lon use NUMERIC(8,3) rather than DOUBLE PRECISION so that equality
-- comparisons in the unique constraint are always exact (GloFAS coordinates
-- are always 3 decimal places; binary float can produce false mismatches).
CREATE TABLE IF NOT EXISTS all_glofas_stations (
    matching_id_station INTEGER         PRIMARY KEY,
    "Station"           TEXT,
    "Basin"             TEXT,
    "Country"           TEXT,
    "Country_code"      VARCHAR(8),
    "Continent"         TEXT,
    "Location"          TEXT,
    "Lat"               NUMERIC(8,3),
    "Lon"               NUMERIC(8,3),
    "Upstream area"     NUMERIC(15,3),
    pfaf_id             INTEGER         REFERENCES watershed_shapes(pfaf_id),
    CONSTRAINT uq_station UNIQUE ("Station", "Country", "Lat", "Lon", pfaf_id)
);

-- All Watersheds: static per-watershed metadata
-- CentroidX/CentroidY use NUMERIC(10,6) rather than DOUBLE PRECISION so that
-- equality comparisons in the unique constraint are always exact. 6 decimal
-- places matches the precision in the source data (~0.1 m resolution).
-- pfaf_id may appear more than once (one row per country slice of the watershed).
CREATE TABLE IF NOT EXISTS all_watersheds (
    matching_id_watershed INTEGER         PRIMARY KEY,
    pfaf_id               INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "name"                TEXT,
    "name_1"              TEXT,
    "CentroidX"           NUMERIC(10,6),
    "CentroidY"           NUMERIC(10,6),
    "Admin1_count"        INTEGER,
    "Admin1_names"        TEXT,
    CONSTRAINT uq_watershed UNIQUE (pfaf_id, "name", "name_1", "CentroidX", "CentroidY")
);


-- ============================================================
-- History tables: one row per (entity, timestamp) per batch
-- ============================================================

-- GFMS (8 csvs per day, every 3h)
-- image (1-4 tiffs per day (inconsistent): Flood_byStore)
CREATE TABLE IF NOT EXISTS summary_gfms (
    pfaf_id              INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "timestamp"          TIMESTAMPTZ,
    "GFMS_TotalArea_km"  DOUBLE PRECISION,
    "GFMS_perc_Area"     DOUBLE PRECISION,
    "GFMS_MeanDepth"     DOUBLE PRECISION,
    "GFMS_MaxDepth"      DOUBLE PRECISION,
    "GFMS_Duration"      INTEGER,
    PRIMARY KEY ("timestamp", pfaf_id)
);

-- HWRF (1-4 csvs per day, inconsistent)
CREATE TABLE IF NOT EXISTS summary_hwrf (
    pfaf_id              INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "timestamp"          TIMESTAMPTZ,
    "Rain_TotalArea_km"  DOUBLE PRECISION,
    "perc_Area"          DOUBLE PRECISION,
    "MeanRain"           DOUBLE PRECISION,
    "MaxRain"            DOUBLE PRECISION,
    PRIMARY KEY ("timestamp", pfaf_id)
);

-- VIIRS (1 csv per day)
-- image (2 tiffs per day: 1day, 5day)
CREATE TABLE IF NOT EXISTS summary_viirs (
    pfaf_id                  INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "timestamp"              TIMESTAMPTZ,
    "onedayFlood_Area_km"    DOUBLE PRECISION,
    "onedayperc_Area"        DOUBLE PRECISION,
    "fivedayFlood_Area_km"   DOUBLE PRECISION,
    "fivedayperc_Area"       DOUBLE PRECISION,
    PRIMARY KEY ("timestamp", pfaf_id)
);

-- DFO (1 csv per day)
-- image (1 tiff per day (inconsistent, some days are missing): Flood_3-Day_250m)
CREATE TABLE IF NOT EXISTS summary_dfo (
    pfaf_id                   INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "timestamp"               TIMESTAMPTZ,
    "1-Day_TotalArea_km2"     DOUBLE PRECISION,
    "1-Day_perc_Area"         DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"  DOUBLE PRECISION,
    "1-Day_CS_perc_Area"      DOUBLE PRECISION,
    "2-Day_TotalArea_km2"     DOUBLE PRECISION,
    "2-Day_perc_Area"         DOUBLE PRECISION,
    "3-Day_TotalArea_km2"     DOUBLE PRECISION,
    "3-Day_perc_Area"         DOUBLE PRECISION,
    PRIMARY KEY ("timestamp", pfaf_id)
);

-- GloFAS merged: dynamic per-timestamp forecast data (1 csv, 1 geojson per day)
CREATE TABLE IF NOT EXISTS summary_glofas (
    "timestamp"         TIMESTAMPTZ,
    matching_id_station INTEGER         REFERENCES all_glofas_stations(matching_id_station),
    pfaf_id             INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "ID"                TEXT,
    "Point No"          INTEGER,
    "Alert_level"       INTEGER,
    "Days_until_peak"   INTEGER,
    "GloFAS_2yr"        DOUBLE PRECISION,
    "GloFAS_5yr"        DOUBLE PRECISION,
    "GloFAS_20yr"       DOUBLE PRECISION,
    "max_EPS"           TEXT,
    "Forecast Date"     TIMESTAMP,
    PRIMARY KEY ("timestamp", matching_id_station)
);

-- Final Alert: dynamic per-timestamp alert data (4 csvs per day)
CREATE TABLE IF NOT EXISTS summary_final_alert (
    "timestamp"                 TIMESTAMPTZ,
    matching_id_watershed       INTEGER         REFERENCES all_watersheds(matching_id_watershed),
    pfaf_id                     INTEGER         REFERENCES watershed_shapes(pfaf_id),
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
    PRIMARY KEY ("timestamp", matching_id_watershed)
);


-- ============================================================
-- "Latest" tables: one row per entity, most recent snapshot
-- ============================================================

-- GFMS Latest
CREATE TABLE IF NOT EXISTS summary_gfms_latest (
    "timestamp"          TIMESTAMPTZ,
    pfaf_id              INTEGER         PRIMARY KEY REFERENCES watershed_shapes(pfaf_id),
    "GFMS_TotalArea_km"  DOUBLE PRECISION,
    "GFMS_perc_Area"     DOUBLE PRECISION,
    "GFMS_MeanDepth"     DOUBLE PRECISION,
    "GFMS_MaxDepth"      DOUBLE PRECISION,
    "GFMS_Duration"      INTEGER
);

-- HWRF Latest
CREATE TABLE IF NOT EXISTS summary_hwrf_latest (
    "timestamp"          TIMESTAMPTZ,
    pfaf_id              INTEGER         PRIMARY KEY REFERENCES watershed_shapes(pfaf_id),
    "Rain_TotalArea_km"  DOUBLE PRECISION,
    "perc_Area"          DOUBLE PRECISION,
    "MeanRain"           DOUBLE PRECISION,
    "MaxRain"            DOUBLE PRECISION
);

-- GloFAS Latest: forecast data + station metadata
-- PK is matching_id_station because multiple stations can map to the same
-- pfaf_id (watershed); pfaf_id alone is not unique within a batch.
CREATE TABLE IF NOT EXISTS summary_glofas_latest (
    matching_id_station  INTEGER         PRIMARY KEY REFERENCES all_glofas_stations(matching_id_station),
    "timestamp"          TIMESTAMPTZ,
    pfaf_id              INTEGER         REFERENCES watershed_shapes(pfaf_id),
    "ID"                 TEXT,
    "Point No"           INTEGER,
    "Alert_level"        INTEGER,
    "Days_until_peak"    INTEGER,
    "GloFAS_2yr"         DOUBLE PRECISION,
    "GloFAS_5yr"         DOUBLE PRECISION,
    "GloFAS_20yr"        DOUBLE PRECISION,
    "max_EPS"            TEXT,
    "Forecast Date"      TIMESTAMP,
    -- from all_glofas_stations
    "Station"            TEXT,
    "Basin"              TEXT,
    "Country"            TEXT,
    "Country_code"       VARCHAR(8),
    "Continent"          TEXT,
    "Location"           TEXT,
    "Lat"                NUMERIC(8,3),
    "Lon"                NUMERIC(8,3),
    "Upstream area"      NUMERIC(15,3)
);

-- VIIRS Latest
CREATE TABLE IF NOT EXISTS summary_viirs_latest (
    "timestamp"              TIMESTAMPTZ,
    pfaf_id                  INTEGER         PRIMARY KEY REFERENCES watershed_shapes(pfaf_id),
    "onedayFlood_Area_km"    DOUBLE PRECISION,
    "onedayperc_Area"        DOUBLE PRECISION,
    "fivedayFlood_Area_km"   DOUBLE PRECISION,
    "fivedayperc_Area"       DOUBLE PRECISION
);

-- DFO Latest
CREATE TABLE IF NOT EXISTS summary_dfo_latest (
    "timestamp"               TIMESTAMPTZ,
    pfaf_id                   INTEGER         PRIMARY KEY REFERENCES watershed_shapes(pfaf_id),
    "1-Day_TotalArea_km2"     DOUBLE PRECISION,
    "1-Day_perc_Area"         DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"  DOUBLE PRECISION,
    "1-Day_CS_perc_Area"      DOUBLE PRECISION,
    "2-Day_TotalArea_km2"     DOUBLE PRECISION,
    "2-Day_perc_Area"         DOUBLE PRECISION,
    "3-Day_TotalArea_km2"     DOUBLE PRECISION,
    "3-Day_perc_Area"         DOUBLE PRECISION
);

-- Final Alert Latest: alert data + watershed metadata
-- PK is matching_id_watershed because a single pfaf_id can appear multiple times
-- (one row per country slice of the watershed); pfaf_id alone is not unique.
CREATE TABLE IF NOT EXISTS summary_final_alert_latest (
    matching_id_watershed       INTEGER         PRIMARY KEY REFERENCES all_watersheds(matching_id_watershed),
    "timestamp"                 TIMESTAMPTZ,
    pfaf_id                     INTEGER         REFERENCES watershed_shapes(pfaf_id),
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
    -- from all_watersheds
    "name"                      TEXT,
    "name_1"                    TEXT,
    "CentroidX"                 NUMERIC(10,6),
    "CentroidY"                 NUMERIC(10,6),
    "Admin1_count"              INTEGER,
    "Admin1_names"              TEXT
);
