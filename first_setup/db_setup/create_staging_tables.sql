-- =============================================================================
-- create_staging_tables.sql
--
-- Staging tables are the user-facing entry point for all data ingestion.
-- Insert data here — the trigger handles everything else:
--
--   < 1 row      → batch is discarded, nothing touches _latest or history
--   timestamp already in history → batch is discarded (idempotent re-run guard)
--   otherwise    → data is pushed to the corresponding _latest table,
--                  which fires the history and ID-resolution triggers
--
-- Staging tables have no PK/FK constraints so inserts never fail on conflicts.
-- For GloFAS and Final Alert, omit matching_id — it is resolved automatically
-- by the BEFORE trigger on the _latest table.
--
-- Run AFTER create_all_tables.sql, create_id_resolution_triggers.sql,
-- and create_history_triggers.sql.
--
-- Run with:
--   sudo -u postgres psql -d postgres -f create_staging_tables.sql
-- =============================================================================


-- =============================================================================
-- GFMS
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_gfms (
    pfaf_id              INTEGER,
    "timestamp"          TIMESTAMPTZ,
    "GFMS_TotalArea_km"  DOUBLE PRECISION,
    "GFMS_perc_Area"     DOUBLE PRECISION,
    "GFMS_MeanDepth"     DOUBLE PRECISION,
    "GFMS_MaxDepth"      DOUBLE PRECISION,
    "GFMS_Duration"      INTEGER
);

CREATE OR REPLACE FUNCTION fn_stage_gfms_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_gfms;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_gfms WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_gfms;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_gfms WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO summary_gfms_latest (
        pfaf_id, "timestamp",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
    )
    SELECT
        pfaf_id, "timestamp",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
    FROM stage_gfms
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"         = EXCLUDED."timestamp",
        "GFMS_TotalArea_km" = EXCLUDED."GFMS_TotalArea_km",
        "GFMS_perc_Area"    = EXCLUDED."GFMS_perc_Area",
        "GFMS_MeanDepth"    = EXCLUDED."GFMS_MeanDepth",
        "GFMS_MaxDepth"     = EXCLUDED."GFMS_MaxDepth",
        "GFMS_Duration"     = EXCLUDED."GFMS_Duration";

    DELETE FROM stage_gfms;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_gfms_flush ON stage_gfms;
CREATE TRIGGER trg_stage_gfms_flush
AFTER INSERT ON stage_gfms
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_gfms_flush();


-- =============================================================================
-- HWRF
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_hwrf (
    pfaf_id              INTEGER,
    "timestamp"          TIMESTAMPTZ,
    "Rain_TotalArea_km"  DOUBLE PRECISION,
    "perc_Area"          DOUBLE PRECISION,
    "MeanRain"           DOUBLE PRECISION,
    "MaxRain"            DOUBLE PRECISION
);

CREATE OR REPLACE FUNCTION fn_stage_hwrf_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_hwrf;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_hwrf WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_hwrf;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_hwrf WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO summary_hwrf_latest (
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    )
    SELECT
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    FROM stage_hwrf
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"        = EXCLUDED."timestamp",
        "Rain_TotalArea_km"= EXCLUDED."Rain_TotalArea_km",
        "perc_Area"        = EXCLUDED."perc_Area",
        "MeanRain"         = EXCLUDED."MeanRain",
        "MaxRain"          = EXCLUDED."MaxRain";

    DELETE FROM stage_hwrf;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_hwrf_flush ON stage_hwrf;
CREATE TRIGGER trg_stage_hwrf_flush
AFTER INSERT ON stage_hwrf
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_hwrf_flush();


-- =============================================================================
-- VIIRS
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_viirs (
    pfaf_id                  INTEGER,
    "timestamp"              TIMESTAMPTZ,
    "onedayFlood_Area_km"    DOUBLE PRECISION,
    "onedayperc_Area"        DOUBLE PRECISION,
    "fivedayFlood_Area_km"   DOUBLE PRECISION,
    "fivedayperc_Area"       DOUBLE PRECISION
);

CREATE OR REPLACE FUNCTION fn_stage_viirs_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_viirs;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_viirs WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_viirs;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_viirs WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO summary_viirs_latest (
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    )
    SELECT
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    FROM stage_viirs
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"           = EXCLUDED."timestamp",
        "onedayFlood_Area_km" = EXCLUDED."onedayFlood_Area_km",
        "onedayperc_Area"     = EXCLUDED."onedayperc_Area",
        "fivedayFlood_Area_km"= EXCLUDED."fivedayFlood_Area_km",
        "fivedayperc_Area"    = EXCLUDED."fivedayperc_Area";

    DELETE FROM stage_viirs;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_viirs_flush ON stage_viirs;
CREATE TRIGGER trg_stage_viirs_flush
AFTER INSERT ON stage_viirs
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_viirs_flush();


-- =============================================================================
-- DFO
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_dfo (
    pfaf_id                   INTEGER,
    "timestamp"               TIMESTAMPTZ,
    "1-Day_TotalArea_km2"     DOUBLE PRECISION,
    "1-Day_perc_Area"         DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"  DOUBLE PRECISION,
    "1-Day_CS_perc_Area"      DOUBLE PRECISION,
    "2-Day_TotalArea_km2"     DOUBLE PRECISION,
    "2-Day_perc_Area"         DOUBLE PRECISION,
    "3-Day_TotalArea_km2"     DOUBLE PRECISION,
    "3-Day_perc_Area"         DOUBLE PRECISION
);

CREATE OR REPLACE FUNCTION fn_stage_dfo_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_dfo;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_dfo WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_dfo;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_dfo WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO summary_dfo_latest (
        pfaf_id, "timestamp",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area"
    )
    SELECT
        pfaf_id, "timestamp",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area"
    FROM stage_dfo
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"              = EXCLUDED."timestamp",
        "1-Day_TotalArea_km2"    = EXCLUDED."1-Day_TotalArea_km2",
        "1-Day_perc_Area"        = EXCLUDED."1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2" = EXCLUDED."1-Day_CS_TotalArea_km2",
        "1-Day_CS_perc_Area"     = EXCLUDED."1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2"    = EXCLUDED."2-Day_TotalArea_km2",
        "2-Day_perc_Area"        = EXCLUDED."2-Day_perc_Area",
        "3-Day_TotalArea_km2"    = EXCLUDED."3-Day_TotalArea_km2",
        "3-Day_perc_Area"        = EXCLUDED."3-Day_perc_Area";

    DELETE FROM stage_dfo;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_dfo_flush ON stage_dfo;
CREATE TRIGGER trg_stage_dfo_flush
AFTER INSERT ON stage_dfo
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_dfo_flush();


-- =============================================================================
-- GloFAS
-- Omit matching_id_station — resolved automatically by the BEFORE trigger
-- on summary_glofas_latest when the staging flush pushes data through.
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_glofas (
    "timestamp"          TIMESTAMPTZ,
    pfaf_id              INTEGER,
    "ID"                 TEXT,
    "Point No"           INTEGER,
    "Alert_level"        INTEGER,
    "Days_until_peak"    INTEGER,
    "GloFAS_2yr"         DOUBLE PRECISION,
    "GloFAS_5yr"         DOUBLE PRECISION,
    "GloFAS_20yr"        DOUBLE PRECISION,
    "max_EPS"            TEXT,
    "Forecast Date"      TIMESTAMP,
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

CREATE OR REPLACE FUNCTION fn_stage_glofas_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_glofas;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_glofas WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_glofas;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_glofas WHERE "timestamp" = batch_ts;
    END IF;

    -- matching_id_station is passed as NULL; the BEFORE trigger on
    -- summary_glofas_latest resolves it from (Station, Country, Lat, Lon, pfaf_id)
    -- and writes the resolved value back onto the row before it is inserted.
    INSERT INTO summary_glofas_latest (
        matching_id_station,
        "timestamp", pfaf_id,
        "ID", "Point No",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "max_EPS", "Forecast Date",
        "Station", "Basin", "Country", "Country_code",
        "Continent", "Location",
        "Lat", "Lon", "Upstream area"
    )
    SELECT
        NULL,
        "timestamp", pfaf_id,
        "ID", "Point No",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "max_EPS", "Forecast Date",
        "Station", "Basin", "Country", "Country_code",
        "Continent", "Location",
        "Lat", "Lon", "Upstream area"
    FROM stage_glofas
    ON CONFLICT (matching_id_station) DO UPDATE SET
        "timestamp"      = EXCLUDED."timestamp",
        pfaf_id          = EXCLUDED.pfaf_id,
        "ID"             = EXCLUDED."ID",
        "Point No"       = EXCLUDED."Point No",
        "Alert_level"    = EXCLUDED."Alert_level",
        "Days_until_peak"= EXCLUDED."Days_until_peak",
        "GloFAS_2yr"     = EXCLUDED."GloFAS_2yr",
        "GloFAS_5yr"     = EXCLUDED."GloFAS_5yr",
        "GloFAS_20yr"    = EXCLUDED."GloFAS_20yr",
        "max_EPS"        = EXCLUDED."max_EPS",
        "Forecast Date"  = EXCLUDED."Forecast Date",
        "Station"        = EXCLUDED."Station",
        "Basin"          = EXCLUDED."Basin",
        "Country"        = EXCLUDED."Country",
        "Country_code"   = EXCLUDED."Country_code",
        "Continent"      = EXCLUDED."Continent",
        "Location"       = EXCLUDED."Location",
        "Lat"            = EXCLUDED."Lat",
        "Lon"            = EXCLUDED."Lon",
        "Upstream area"  = EXCLUDED."Upstream area";

    DELETE FROM stage_glofas;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_glofas_flush ON stage_glofas;
CREATE TRIGGER trg_stage_glofas_flush
AFTER INSERT ON stage_glofas
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_glofas_flush();


-- =============================================================================
-- MoM GFMS
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_mom_gfms (
    pfaf_id                      INTEGER,
    "timestamp"                  TIMESTAMPTZ,
    "FID"                        DOUBLE PRECISION,
    "Resilience_Index"           DOUBLE PRECISION,
    "NormalizedLackofResilience" DOUBLE PRECISION,
    "Alert"                      TEXT,
    "Flag"                       TEXT
);

CREATE OR REPLACE FUNCTION fn_stage_mom_gfms_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_gfms;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM mom_gfms WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_mom_gfms;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM mom_gfms WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO mom_gfms_latest (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM stage_mom_gfms
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"                  = EXCLUDED."timestamp",
        "FID"                        = EXCLUDED."FID",
        "Resilience_Index"           = EXCLUDED."Resilience_Index",
        "NormalizedLackofResilience" = EXCLUDED."NormalizedLackofResilience",
        "Alert"                      = EXCLUDED."Alert",
        "Flag"                       = EXCLUDED."Flag";

    DELETE FROM stage_mom_gfms;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_mom_gfms_flush ON stage_mom_gfms;
CREATE TRIGGER trg_stage_mom_gfms_flush
AFTER INSERT ON stage_mom_gfms
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_mom_gfms_flush();


-- =============================================================================
-- MoM HWRF
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_mom_hwrf (
    pfaf_id                      INTEGER,
    "timestamp"                  TIMESTAMPTZ,
    "FID"                        DOUBLE PRECISION,
    "Resilience_Index"           DOUBLE PRECISION,
    "NormalizedLackofResilience" DOUBLE PRECISION,
    "Alert"                      TEXT,
    "Flag"                       TEXT
);

CREATE OR REPLACE FUNCTION fn_stage_mom_hwrf_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_hwrf;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM mom_hwrf WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_mom_hwrf;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM mom_hwrf WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO mom_hwrf_latest (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM stage_mom_hwrf
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"                  = EXCLUDED."timestamp",
        "FID"                        = EXCLUDED."FID",
        "Resilience_Index"           = EXCLUDED."Resilience_Index",
        "NormalizedLackofResilience" = EXCLUDED."NormalizedLackofResilience",
        "Alert"                      = EXCLUDED."Alert",
        "Flag"                       = EXCLUDED."Flag";

    DELETE FROM stage_mom_hwrf;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_mom_hwrf_flush ON stage_mom_hwrf;
CREATE TRIGGER trg_stage_mom_hwrf_flush
AFTER INSERT ON stage_mom_hwrf
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_mom_hwrf_flush();


-- =============================================================================
-- MoM DFO
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_mom_dfo (
    pfaf_id                      INTEGER,
    "timestamp"                  TIMESTAMPTZ,
    "FID"                        DOUBLE PRECISION,
    "Resilience_Index"           DOUBLE PRECISION,
    "NormalizedLackofResilience" DOUBLE PRECISION,
    "Alert"                      TEXT,
    "Flag"                       TEXT
);

CREATE OR REPLACE FUNCTION fn_stage_mom_dfo_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_dfo;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM mom_dfo WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_mom_dfo;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM mom_dfo WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO mom_dfo_latest (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM stage_mom_dfo
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"                  = EXCLUDED."timestamp",
        "FID"                        = EXCLUDED."FID",
        "Resilience_Index"           = EXCLUDED."Resilience_Index",
        "NormalizedLackofResilience" = EXCLUDED."NormalizedLackofResilience",
        "Alert"                      = EXCLUDED."Alert",
        "Flag"                       = EXCLUDED."Flag";

    DELETE FROM stage_mom_dfo;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_mom_dfo_flush ON stage_mom_dfo;
CREATE TRIGGER trg_stage_mom_dfo_flush
AFTER INSERT ON stage_mom_dfo
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_mom_dfo_flush();


-- =============================================================================
-- MoM VIIRS
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_mom_viirs (
    pfaf_id                      INTEGER,
    "timestamp"                  TIMESTAMPTZ,
    "FID"                        DOUBLE PRECISION,
    "Resilience_Index"           DOUBLE PRECISION,
    "NormalizedLackofResilience" DOUBLE PRECISION,
    "Alert"                      TEXT,
    "Flag"                       TEXT
);

CREATE OR REPLACE FUNCTION fn_stage_mom_viirs_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_viirs;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM mom_viirs WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_mom_viirs;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM mom_viirs WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO mom_viirs_latest (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM stage_mom_viirs
    ON CONFLICT (pfaf_id) DO UPDATE SET
        "timestamp"                  = EXCLUDED."timestamp",
        "FID"                        = EXCLUDED."FID",
        "Resilience_Index"           = EXCLUDED."Resilience_Index",
        "NormalizedLackofResilience" = EXCLUDED."NormalizedLackofResilience",
        "Alert"                      = EXCLUDED."Alert",
        "Flag"                       = EXCLUDED."Flag";

    DELETE FROM stage_mom_viirs;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_mom_viirs_flush ON stage_mom_viirs;
CREATE TRIGGER trg_stage_mom_viirs_flush
AFTER INSERT ON stage_mom_viirs
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_mom_viirs_flush();


-- =============================================================================
-- Final Alert
-- Omit matching_id_watershed — resolved automatically by the BEFORE trigger
-- on summary_final_alert_latest when the staging flush pushes data through.
-- =============================================================================

CREATE TABLE IF NOT EXISTS stage_final_alert (
    "timestamp"                 TIMESTAMPTZ,
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
    "name"                      TEXT,
    "name_1"                    TEXT,
    "CentroidX"                 NUMERIC(10,6),
    "CentroidY"                 NUMERIC(10,6),
    "Admin1_count"              INTEGER,
    "Admin1_names"              TEXT
);

CREATE OR REPLACE FUNCTION fn_stage_final_alert_flush()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    rc             INTEGER;
    batch_ts       TIMESTAMPTZ;
    hist_count     INTEGER;
    expected_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_final_alert;
        RETURN NULL;
    END IF;

    expected_count := COALESCE(current_setting('mom.expected_rows', true)::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_final_alert WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_final_alert;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_final_alert WHERE "timestamp" = batch_ts;
    END IF;

    -- matching_id_watershed is passed as NULL; the BEFORE trigger on
    -- summary_final_alert_latest resolves it from
    -- (pfaf_id, name, name_1, CentroidX, CentroidY) and writes the resolved
    -- value back onto the row before it is inserted.
    INSERT INTO summary_final_alert_latest (
        matching_id_watershed,
        "timestamp", pfaf_id,
        "rfr_score", "cfr_score",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "Alert_Score", "PeakArrivalScore",
        "TwoYScore", "FiveYScore", "TwtyYScore", "Sum_Score_x",
        "GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth",
        "GFMS_MaxDepth", "GFMS_Duration",
        "GFMS_area_score", "GFMS_perc_area_score",
        "MeanD_Score", "MaxD_Score", "Duration_Score",
        "Sum_Score_y", "MOM_Score", "Hazard_Score",
        "Scaled_Riverine_Risk", "Scaled_Coastal_Risk", "Flag",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area",
        "DFO_area_1day_score", "DFO_percarea_1day_score",
        "DFO_area_2day_score", "DFO_percarea_2day_score",
        "DFO_area_3day_score", "DFO_percarea_3day_score",
        "DFOTotal_Score",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area",
        "VIIRS_area_1day_score", "VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score", "VIIRS_percarea_5day_score",
        "VIIRSTotal_Score",
        "Severity", "Alert", "Status",
        "name", "name_1", "CentroidX", "CentroidY",
        "Admin1_count", "Admin1_names"
    )
    SELECT
        NULL,
        "timestamp", pfaf_id,
        "rfr_score", "cfr_score",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "Alert_Score", "PeakArrivalScore",
        "TwoYScore", "FiveYScore", "TwtyYScore", "Sum_Score_x",
        "GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth",
        "GFMS_MaxDepth", "GFMS_Duration",
        "GFMS_area_score", "GFMS_perc_area_score",
        "MeanD_Score", "MaxD_Score", "Duration_Score",
        "Sum_Score_y", "MOM_Score", "Hazard_Score",
        "Scaled_Riverine_Risk", "Scaled_Coastal_Risk", "Flag",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area",
        "DFO_area_1day_score", "DFO_percarea_1day_score",
        "DFO_area_2day_score", "DFO_percarea_2day_score",
        "DFO_area_3day_score", "DFO_percarea_3day_score",
        "DFOTotal_Score",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area",
        "VIIRS_area_1day_score", "VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score", "VIIRS_percarea_5day_score",
        "VIIRSTotal_Score",
        "Severity", "Alert", "Status",
        "name", "name_1", "CentroidX", "CentroidY",
        "Admin1_count", "Admin1_names"
    FROM stage_final_alert
    ON CONFLICT (matching_id_watershed) DO UPDATE SET
        "timestamp"                 = EXCLUDED."timestamp",
        pfaf_id                     = EXCLUDED.pfaf_id,
        "rfr_score"                 = EXCLUDED."rfr_score",
        "cfr_score"                 = EXCLUDED."cfr_score",
        "Alert_level"               = EXCLUDED."Alert_level",
        "Days_until_peak"           = EXCLUDED."Days_until_peak",
        "GloFAS_2yr"                = EXCLUDED."GloFAS_2yr",
        "GloFAS_5yr"                = EXCLUDED."GloFAS_5yr",
        "GloFAS_20yr"               = EXCLUDED."GloFAS_20yr",
        "Alert_Score"               = EXCLUDED."Alert_Score",
        "PeakArrivalScore"          = EXCLUDED."PeakArrivalScore",
        "TwoYScore"                 = EXCLUDED."TwoYScore",
        "FiveYScore"                = EXCLUDED."FiveYScore",
        "TwtyYScore"                = EXCLUDED."TwtyYScore",
        "Sum_Score_x"               = EXCLUDED."Sum_Score_x",
        "GFMS_TotalArea_km"         = EXCLUDED."GFMS_TotalArea_km",
        "GFMS_perc_Area"            = EXCLUDED."GFMS_perc_Area",
        "GFMS_MeanDepth"            = EXCLUDED."GFMS_MeanDepth",
        "GFMS_MaxDepth"             = EXCLUDED."GFMS_MaxDepth",
        "GFMS_Duration"             = EXCLUDED."GFMS_Duration",
        "GFMS_area_score"           = EXCLUDED."GFMS_area_score",
        "GFMS_perc_area_score"      = EXCLUDED."GFMS_perc_area_score",
        "MeanD_Score"               = EXCLUDED."MeanD_Score",
        "MaxD_Score"                = EXCLUDED."MaxD_Score",
        "Duration_Score"            = EXCLUDED."Duration_Score",
        "Sum_Score_y"               = EXCLUDED."Sum_Score_y",
        "MOM_Score"                 = EXCLUDED."MOM_Score",
        "Hazard_Score"              = EXCLUDED."Hazard_Score",
        "Scaled_Riverine_Risk"      = EXCLUDED."Scaled_Riverine_Risk",
        "Scaled_Coastal_Risk"       = EXCLUDED."Scaled_Coastal_Risk",
        "Flag"                      = EXCLUDED."Flag",
        "1-Day_TotalArea_km2"       = EXCLUDED."1-Day_TotalArea_km2",
        "1-Day_perc_Area"           = EXCLUDED."1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2"    = EXCLUDED."1-Day_CS_TotalArea_km2",
        "1-Day_CS_perc_Area"        = EXCLUDED."1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2"       = EXCLUDED."2-Day_TotalArea_km2",
        "2-Day_perc_Area"           = EXCLUDED."2-Day_perc_Area",
        "3-Day_TotalArea_km2"       = EXCLUDED."3-Day_TotalArea_km2",
        "3-Day_perc_Area"           = EXCLUDED."3-Day_perc_Area",
        "DFO_area_1day_score"       = EXCLUDED."DFO_area_1day_score",
        "DFO_percarea_1day_score"   = EXCLUDED."DFO_percarea_1day_score",
        "DFO_area_2day_score"       = EXCLUDED."DFO_area_2day_score",
        "DFO_percarea_2day_score"   = EXCLUDED."DFO_percarea_2day_score",
        "DFO_area_3day_score"       = EXCLUDED."DFO_area_3day_score",
        "DFO_percarea_3day_score"   = EXCLUDED."DFO_percarea_3day_score",
        "DFOTotal_Score"            = EXCLUDED."DFOTotal_Score",
        "onedayFlood_Area_km"       = EXCLUDED."onedayFlood_Area_km",
        "onedayperc_Area"           = EXCLUDED."onedayperc_Area",
        "fivedayFlood_Area_km"      = EXCLUDED."fivedayFlood_Area_km",
        "fivedayperc_Area"          = EXCLUDED."fivedayperc_Area",
        "VIIRS_area_1day_score"     = EXCLUDED."VIIRS_area_1day_score",
        "VIIRS_percarea_1day_score" = EXCLUDED."VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score"     = EXCLUDED."VIIRS_area_5day_score",
        "VIIRS_percarea_5day_score" = EXCLUDED."VIIRS_percarea_5day_score",
        "VIIRSTotal_Score"          = EXCLUDED."VIIRSTotal_Score",
        "Severity"                  = EXCLUDED."Severity",
        "Alert"                     = EXCLUDED."Alert",
        "Status"                    = EXCLUDED."Status",
        "name"                      = EXCLUDED."name",
        "name_1"                    = EXCLUDED."name_1",
        "CentroidX"                 = EXCLUDED."CentroidX",
        "CentroidY"                 = EXCLUDED."CentroidY",
        "Admin1_count"              = EXCLUDED."Admin1_count",
        "Admin1_names"              = EXCLUDED."Admin1_names";

    DELETE FROM stage_final_alert;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_stage_final_alert_flush ON stage_final_alert;
CREATE TRIGGER trg_stage_final_alert_flush
AFTER INSERT ON stage_final_alert
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_stage_final_alert_flush();
