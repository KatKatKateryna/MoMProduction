-- =============================================================================
-- apply_trigger_fix.sql
--
-- Applies all schema and trigger changes to the running database:
--   1. Add created_at / updated_at columns to all history and _latest tables
--   2. Create fn_set_timestamps() and per-table triggers
--   3. Replace all staging flush functions with DELETE+INSERT versions
--   4. Replace all *_sync history trigger functions (remove stale DELETE)
--   5. Drop all _upd triggers on _latest tables
--   6. Fix the corrupted summary_gfms row for 2025-01-07 12:00:00+00
--
-- Run with:
--   sudo -u postgres psql -d postgres -f apply_trigger_fix.sql
-- =============================================================================

BEGIN;

-- =============================================================================
-- 1. Add created_at / updated_at to all history tables
-- =============================================================================

ALTER TABLE summary_gfms
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_hwrf
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_viirs
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_dfo
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_glofas
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_final_alert
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_gfms
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_hwrf
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_dfo
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_viirs
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

-- =============================================================================
-- 2. Add created_at / updated_at to all _latest tables
-- =============================================================================

ALTER TABLE summary_gfms_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_hwrf_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_viirs_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_dfo_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_glofas_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE summary_final_alert_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_gfms_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_hwrf_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_dfo_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

ALTER TABLE mom_viirs_latest
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NULL;

-- =============================================================================
-- 3. Create fn_set_timestamps() and per-table triggers
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_set_timestamps()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_at = NOW();
    ELSIF TG_OP = 'UPDATE' THEN
        NEW.updated_at = NOW();
    END IF;
    RETURN NEW;
END;
$$;

-- History tables
CREATE TRIGGER trg_summary_gfms_ts           BEFORE INSERT OR UPDATE ON summary_gfms           FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_hwrf_ts           BEFORE INSERT OR UPDATE ON summary_hwrf           FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_viirs_ts          BEFORE INSERT OR UPDATE ON summary_viirs          FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_dfo_ts            BEFORE INSERT OR UPDATE ON summary_dfo            FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_glofas_ts         BEFORE INSERT OR UPDATE ON summary_glofas         FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_final_alert_ts    BEFORE INSERT OR UPDATE ON summary_final_alert    FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_gfms_ts               BEFORE INSERT OR UPDATE ON mom_gfms               FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_hwrf_ts               BEFORE INSERT OR UPDATE ON mom_hwrf               FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_dfo_ts                BEFORE INSERT OR UPDATE ON mom_dfo                FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_viirs_ts              BEFORE INSERT OR UPDATE ON mom_viirs              FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();

-- Latest tables
CREATE TRIGGER trg_summary_gfms_latest_ts         BEFORE INSERT OR UPDATE ON summary_gfms_latest         FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_hwrf_latest_ts         BEFORE INSERT OR UPDATE ON summary_hwrf_latest         FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_viirs_latest_ts        BEFORE INSERT OR UPDATE ON summary_viirs_latest        FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_dfo_latest_ts          BEFORE INSERT OR UPDATE ON summary_dfo_latest          FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_glofas_latest_ts       BEFORE INSERT OR UPDATE ON summary_glofas_latest       FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_summary_final_alert_latest_ts  BEFORE INSERT OR UPDATE ON summary_final_alert_latest  FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_gfms_latest_ts             BEFORE INSERT OR UPDATE ON mom_gfms_latest             FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_hwrf_latest_ts             BEFORE INSERT OR UPDATE ON mom_hwrf_latest             FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_dfo_latest_ts              BEFORE INSERT OR UPDATE ON mom_dfo_latest              FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();
CREATE TRIGGER trg_mom_viirs_latest_ts            BEFORE INSERT OR UPDATE ON mom_viirs_latest            FOR EACH ROW EXECUTE FUNCTION fn_set_timestamps();

-- =============================================================================
-- 4. Replace staging flush functions (DELETE+INSERT instead of UPSERT)
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_stage_gfms_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_gfms WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_gfms;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_gfms WHERE "timestamp" = batch_ts;
    END IF;

    DELETE FROM summary_gfms_latest;

    INSERT INTO summary_gfms_latest (
        pfaf_id, "timestamp",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
    )
    SELECT
        pfaf_id, "timestamp",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
    FROM stage_gfms;

    DELETE FROM stage_gfms;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_hwrf_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_hwrf WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_hwrf;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_hwrf WHERE "timestamp" = batch_ts;
    END IF;

    DELETE FROM summary_hwrf_latest;

    INSERT INTO summary_hwrf_latest (
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    )
    SELECT
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    FROM stage_hwrf;

    DELETE FROM stage_hwrf;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_viirs_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_viirs WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_viirs;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_viirs WHERE "timestamp" = batch_ts;
    END IF;

    DELETE FROM summary_viirs_latest;

    INSERT INTO summary_viirs_latest (
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    )
    SELECT
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    FROM stage_viirs;

    DELETE FROM stage_viirs;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_dfo_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_dfo WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_dfo;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_dfo WHERE "timestamp" = batch_ts;
    END IF;

    DELETE FROM summary_dfo_latest;

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
    FROM stage_dfo;

    DELETE FROM stage_dfo;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_glofas_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_glofas WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_glofas;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_glofas WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO all_glofas_stations (
        matching_id_station,
        "Station", "Basin", "Country", "Country_code",
        "Continent", "Location",
        "Lat", "Lon", "Upstream area",
        pfaf_id
    )
    SELECT DISTINCT ON (s."Station", s."Country", s."Lat", s."Lon", s.pfaf_id)
        nextval('seq_glofas_station_id'),
        s."Station", s."Basin", s."Country", s."Country_code",
        s."Continent", s."Location",
        s."Lat", s."Lon", s."Upstream area",
        s.pfaf_id
    FROM stage_glofas s
    ON CONFLICT ("Station", "Country", "Lat", "Lon", pfaf_id) DO NOTHING;

    DELETE FROM summary_glofas_latest;

    INSERT INTO summary_glofas_latest (
        matching_id_station,
        "timestamp", pfaf_id,
        "ID", "Point No",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "max_EPS", "Forecast Date"
    )
    SELECT
        (SELECT MIN(st."matching_id_station")
         FROM all_glofas_stations st
         WHERE st."Station" = s."Station"
           AND st."Country" IS NOT DISTINCT FROM s."Country"
           AND st."Lat"     = s."Lat"
           AND st."Lon"     = s."Lon"
           AND st.pfaf_id   = s.pfaf_id),
        s."timestamp", s.pfaf_id,
        s."ID", s."Point No",
        s."Alert_level", s."Days_until_peak",
        s."GloFAS_2yr", s."GloFAS_5yr", s."GloFAS_20yr",
        s."max_EPS", s."Forecast Date"
    FROM stage_glofas s;

    DELETE FROM stage_glofas;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_final_alert_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
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

    expected_count := COALESCE(NULLIF(current_setting('mom.expected_rows', true), '')::INTEGER, rc);

    SELECT COUNT(*) INTO hist_count FROM summary_final_alert WHERE "timestamp" = batch_ts;

    IF hist_count >= expected_count THEN
        DELETE FROM stage_final_alert;
        RETURN NULL;
    END IF;

    IF hist_count > 0 THEN
        DELETE FROM summary_final_alert WHERE "timestamp" = batch_ts;
    END IF;

    INSERT INTO all_watersheds (
        matching_id_watershed,
        pfaf_id,
        "name", "name_1",
        "CentroidX", "CentroidY",
        "Admin1_count", "Admin1_names"
    )
    SELECT DISTINCT ON (s.pfaf_id, s."name", s."name_1", s."CentroidX", s."CentroidY")
        nextval('seq_watershed_id'),
        s.pfaf_id,
        s."name", s."name_1",
        s."CentroidX"::NUMERIC(10,6), s."CentroidY"::NUMERIC(10,6),
        s."Admin1_count", s."Admin1_names"
    FROM stage_final_alert s
    ON CONFLICT (pfaf_id, "name", "name_1", "CentroidX", "CentroidY") DO NOTHING;

    DELETE FROM summary_final_alert_latest;

    INSERT INTO summary_final_alert_latest (
        matching_id_watershed,
        "timestamp", pfaf_id,
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
        "Severity", "Alert", "Status"
    )
    SELECT
        (SELECT MIN(w."matching_id_watershed")
         FROM all_watersheds w
         WHERE w.pfaf_id      = s.pfaf_id
           AND w."name"       = s."name"
           AND w."name_1"     = s."name_1"
           AND w."CentroidX"  = s."CentroidX"::NUMERIC(10,6)
           AND w."CentroidY"  = s."CentroidY"::NUMERIC(10,6)),
        s."timestamp", s.pfaf_id,
        s."Alert_level", s."Days_until_peak",
        s."GloFAS_2yr", s."GloFAS_5yr", s."GloFAS_20yr",
        s."Alert_Score", s."PeakArrivalScore",
        s."TwoYScore", s."FiveYScore", s."TwtyYScore", s."Sum_Score_x",
        s."GFMS_TotalArea_km", s."GFMS_perc_Area", s."GFMS_MeanDepth",
        s."GFMS_MaxDepth", s."GFMS_Duration",
        s."GFMS_area_score", s."GFMS_perc_area_score",
        s."MeanD_Score", s."MaxD_Score", s."Duration_Score",
        s."Sum_Score_y", s."MOM_Score", s."Hazard_Score",
        s."Scaled_Riverine_Risk", s."Scaled_Coastal_Risk", s."Flag",
        s."1-Day_TotalArea_km2", s."1-Day_perc_Area",
        s."1-Day_CS_TotalArea_km2", s."1-Day_CS_perc_Area",
        s."2-Day_TotalArea_km2", s."2-Day_perc_Area",
        s."3-Day_TotalArea_km2", s."3-Day_perc_Area",
        s."DFO_area_1day_score", s."DFO_percarea_1day_score",
        s."DFO_area_2day_score", s."DFO_percarea_2day_score",
        s."DFO_area_3day_score", s."DFO_percarea_3day_score",
        s."DFOTotal_Score",
        s."onedayFlood_Area_km", s."onedayperc_Area",
        s."fivedayFlood_Area_km", s."fivedayperc_Area",
        s."VIIRS_area_1day_score", s."VIIRS_percarea_1day_score",
        s."VIIRS_area_5day_score", s."VIIRS_percarea_5day_score",
        s."VIIRSTotal_Score",
        s."Severity", s."Alert", s."Status"
    FROM stage_final_alert s;

    DELETE FROM stage_final_alert;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_mom_gfms_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    rc       INTEGER;
    batch_ts TIMESTAMPTZ;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_gfms;
        RETURN NULL;
    END IF;

    UPDATE watershed_shapes ws
    SET
        "Resilience_Index"           = s."Resilience_Index",
        "NormalizedLackofResilience" = s."NormalizedLackofResilience"
    FROM stage_mom_gfms s
    WHERE ws.pfaf_id = s.pfaf_id
      AND s."Resilience_Index" IS NOT NULL
      AND ws."Resilience_Index" IS NULL;

    DELETE FROM mom_gfms_latest;

    INSERT INTO mom_gfms_latest (
        pfaf_id, "timestamp",
        "FID", "Alert",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "Alert_Score", "PeakArrivalScore",
        "TwoYScore", "FiveYScore", "TwtyYScore", "Sum_Score_x",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration",
        "GFMS_area_score", "GFMS_perc_area_score",
        "MeanD_Score", "MaxD_Score", "Duration_Score", "Sum_Score_y",
        "Hazard_Score", "Scaled_Riverine_Risk", "Scaled_Coastal_Risk", "Severity"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Alert",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "Alert_Score", "PeakArrivalScore",
        "TwoYScore", "FiveYScore", "TwtyYScore", "Sum_Score_x",
        "GFMS_TotalArea_km", "GFMS_perc_Area",
        "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration",
        "GFMS_area_score", "GFMS_perc_area_score",
        "MeanD_Score", "MaxD_Score", "Duration_Score", "Sum_Score_y",
        "Hazard_Score", "Scaled_Riverine_Risk", "Scaled_Coastal_Risk", "Severity"
    FROM stage_mom_gfms;

    DELETE FROM stage_mom_gfms;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_mom_hwrf_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    rc       INTEGER;
    batch_ts TIMESTAMPTZ;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_hwrf;
        RETURN NULL;
    END IF;

    UPDATE watershed_shapes ws
    SET
        "Resilience_Index"           = s."Resilience_Index",
        "NormalizedLackofResilience" = s."NormalizedLackofResilience"
    FROM stage_mom_hwrf s
    WHERE ws.pfaf_id = s.pfaf_id
      AND s."Resilience_Index" IS NOT NULL
      AND ws."Resilience_Index" IS NULL;

    DELETE FROM mom_hwrf_latest;

    INSERT INTO mom_hwrf_latest (
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain",
        "HWRF_area_score", "HWRF_percarea_score",
        "MeanRain_Score", "MaxRain_Score", "HWRFTot_Score",
        "MOM_Score", "Hazard_Score", "Severity"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain",
        "HWRF_area_score", "HWRF_percarea_score",
        "MeanRain_Score", "MaxRain_Score", "HWRFTot_Score",
        "MOM_Score", "Hazard_Score", "Severity"
    FROM stage_mom_hwrf;

    DELETE FROM stage_mom_hwrf;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_mom_dfo_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    rc       INTEGER;
    batch_ts TIMESTAMPTZ;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_dfo;
        RETURN NULL;
    END IF;

    UPDATE watershed_shapes ws
    SET
        "Resilience_Index"           = s."Resilience_Index",
        "NormalizedLackofResilience" = s."NormalizedLackofResilience"
    FROM stage_mom_dfo s
    WHERE ws.pfaf_id = s.pfaf_id
      AND s."Resilience_Index" IS NOT NULL
      AND ws."Resilience_Index" IS NULL;

    DELETE FROM mom_dfo_latest;

    INSERT INTO mom_dfo_latest (
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area",
        "DFO_area_1day_score", "DFO_percarea_1day_score",
        "DFO_area_2day_score", "DFO_percarea_2day_score",
        "DFO_area_3day_score", "DFO_percarea_3day_score",
        "DFOTotal_Score", "Hazard_Score", "Severity"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "1-Day_TotalArea_km2", "1-Day_perc_Area",
        "1-Day_CS_TotalArea_km2", "1-Day_CS_perc_Area",
        "2-Day_TotalArea_km2", "2-Day_perc_Area",
        "3-Day_TotalArea_km2", "3-Day_perc_Area",
        "DFO_area_1day_score", "DFO_percarea_1day_score",
        "DFO_area_2day_score", "DFO_percarea_2day_score",
        "DFO_area_3day_score", "DFO_percarea_3day_score",
        "DFOTotal_Score", "Hazard_Score", "Severity"
    FROM stage_mom_dfo;

    DELETE FROM stage_mom_dfo;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_stage_mom_viirs_flush()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    rc       INTEGER;
    batch_ts TIMESTAMPTZ;
BEGIN
    SELECT COUNT(*), MAX("timestamp") INTO rc, batch_ts FROM new_rows;

    IF rc < 1 THEN
        DELETE FROM stage_mom_viirs;
        RETURN NULL;
    END IF;

    UPDATE watershed_shapes ws
    SET
        "Resilience_Index"           = s."Resilience_Index",
        "NormalizedLackofResilience" = s."NormalizedLackofResilience"
    FROM stage_mom_viirs s
    WHERE ws.pfaf_id = s.pfaf_id
      AND s."Resilience_Index" IS NOT NULL
      AND ws."Resilience_Index" IS NULL;

    DELETE FROM mom_viirs_latest;

    INSERT INTO mom_viirs_latest (
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area",
        "VIIRS_area_1day_score", "VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score", "VIIRS_percarea_5day_score",
        "VIIRSTotal_Score", "Hazard_Score", "Severity"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Alert", "Flag",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area",
        "VIIRS_area_1day_score", "VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score", "VIIRS_percarea_5day_score",
        "VIIRSTotal_Score", "Hazard_Score", "Severity"
    FROM stage_mom_viirs;

    DELETE FROM stage_mom_viirs;
    RETURN NULL;
END;
$$;

-- =============================================================================
-- 5. Replace history sync functions (remove DELETE stale-timestamp logic)
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_gfms_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts       TIMESTAMPTZ;
    row_count      INTEGER;
    has_flood_rows BOOLEAN;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM new_rows
        WHERE COALESCE("GFMS_TotalArea_km", 0) != 0
           OR COALESCE("GFMS_perc_Area",    0) != 0
           OR COALESCE("GFMS_MeanDepth",    0) != 0
           OR COALESCE("GFMS_MaxDepth",     0) != 0
           OR COALESCE("GFMS_Duration",     0) != 0
    ) INTO has_flood_rows;

    IF has_flood_rows THEN
        INSERT INTO summary_gfms (
            pfaf_id, "timestamp",
            "GFMS_TotalArea_km", "GFMS_perc_Area",
            "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
        )
        SELECT
            pfaf_id, "timestamp",
            "GFMS_TotalArea_km", "GFMS_perc_Area",
            "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
        FROM new_rows
        WHERE COALESCE("GFMS_TotalArea_km", 0) != 0
           OR COALESCE("GFMS_perc_Area",    0) != 0
           OR COALESCE("GFMS_MeanDepth",    0) != 0
           OR COALESCE("GFMS_MaxDepth",     0) != 0
           OR COALESCE("GFMS_Duration",     0) != 0
        ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;
    ELSE
        INSERT INTO summary_gfms (
            pfaf_id, "timestamp",
            "GFMS_TotalArea_km", "GFMS_perc_Area",
            "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
        )
        SELECT
            pfaf_id, "timestamp",
            "GFMS_TotalArea_km", "GFMS_perc_Area",
            "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration"
        FROM new_rows
        ORDER BY pfaf_id DESC
        LIMIT 1
        ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_hwrf_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts  TIMESTAMPTZ;
    row_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    INSERT INTO summary_hwrf (
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    )
    SELECT
        pfaf_id, "timestamp",
        "Rain_TotalArea_km", "perc_Area", "MeanRain", "MaxRain"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_viirs_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts  TIMESTAMPTZ;
    row_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    INSERT INTO summary_viirs (
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    )
    SELECT
        pfaf_id, "timestamp",
        "onedayFlood_Area_km", "onedayperc_Area",
        "fivedayFlood_Area_km", "fivedayperc_Area"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_dfo_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts       TIMESTAMPTZ;
    row_count      INTEGER;
    has_flood_rows BOOLEAN;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM new_rows
        WHERE COALESCE("1-Day_TotalArea_km2",    0) != 0
           OR COALESCE("1-Day_perc_Area",        0) != 0
           OR COALESCE("1-Day_CS_TotalArea_km2", 0) != 0
           OR COALESCE("1-Day_CS_perc_Area",     0) != 0
           OR COALESCE("2-Day_TotalArea_km2",    0) != 0
           OR COALESCE("2-Day_perc_Area",        0) != 0
           OR COALESCE("3-Day_TotalArea_km2",    0) != 0
           OR COALESCE("3-Day_perc_Area",        0) != 0
    ) INTO has_flood_rows;

    IF has_flood_rows THEN
        INSERT INTO summary_dfo (
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
        FROM new_rows
        WHERE COALESCE("1-Day_TotalArea_km2",    0) != 0
           OR COALESCE("1-Day_perc_Area",        0) != 0
           OR COALESCE("1-Day_CS_TotalArea_km2", 0) != 0
           OR COALESCE("1-Day_CS_perc_Area",     0) != 0
           OR COALESCE("2-Day_TotalArea_km2",    0) != 0
           OR COALESCE("2-Day_perc_Area",        0) != 0
           OR COALESCE("3-Day_TotalArea_km2",    0) != 0
           OR COALESCE("3-Day_perc_Area",        0) != 0
        ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;
    ELSE
        INSERT INTO summary_dfo (
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
        FROM new_rows
        ORDER BY pfaf_id DESC
        LIMIT 1
        ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_glofas_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts  TIMESTAMPTZ;
    row_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    INSERT INTO summary_glofas (
        "timestamp", matching_id_station, pfaf_id,
        "ID", "Point No",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "max_EPS", "Forecast Date"
    )
    SELECT
        "timestamp", matching_id_station, pfaf_id,
        "ID", "Point No",
        "Alert_level", "Days_until_peak",
        "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr",
        "max_EPS", "Forecast Date"
    FROM new_rows
    ON CONFLICT ("timestamp", matching_id_station) DO NOTHING;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_final_alert_sync()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    batch_ts  TIMESTAMPTZ;
    row_count INTEGER;
BEGIN
    SELECT COUNT(*), MAX("timestamp")
    INTO row_count, batch_ts
    FROM new_rows;

    IF row_count < 1 THEN
        RETURN NULL;
    END IF;

    INSERT INTO summary_final_alert (
        "timestamp", matching_id_watershed, pfaf_id,
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
        "Severity", "Alert", "Status"
    )
    SELECT
        "timestamp", matching_id_watershed, pfaf_id,
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
        "Severity", "Alert", "Status"
    FROM new_rows
    ON CONFLICT ("timestamp", matching_id_watershed) DO NOTHING;

    RETURN NULL;
END;
$$;

-- MoM sync functions are unchanged (ON CONFLICT DO UPDATE preserved for Phase-2)
-- fn_mom_gfms_sync, fn_mom_hwrf_sync, fn_mom_dfo_sync, fn_mom_viirs_sync
-- already have no DELETE FROM *_latest lines (they were removed above).

-- =============================================================================
-- 6. Drop all _upd triggers on _latest tables
-- =============================================================================

DROP TRIGGER IF EXISTS trg_gfms_sync_upd         ON summary_gfms_latest;
DROP TRIGGER IF EXISTS trg_hwrf_sync_upd          ON summary_hwrf_latest;
DROP TRIGGER IF EXISTS trg_viirs_sync_upd         ON summary_viirs_latest;
DROP TRIGGER IF EXISTS trg_dfo_sync_upd           ON summary_dfo_latest;
DROP TRIGGER IF EXISTS trg_glofas_sync_upd        ON summary_glofas_latest;
DROP TRIGGER IF EXISTS trg_final_alert_sync_upd   ON summary_final_alert_latest;
DROP TRIGGER IF EXISTS trg_mom_gfms_sync_upd      ON mom_gfms_latest;
DROP TRIGGER IF EXISTS trg_mom_hwrf_sync_upd      ON mom_hwrf_latest;
DROP TRIGGER IF EXISTS trg_mom_dfo_sync_upd       ON mom_dfo_latest;
DROP TRIGGER IF EXISTS trg_mom_viirs_sync_upd     ON mom_viirs_latest;

-- =============================================================================
-- 7. Fix corrupted summary_gfms data for 2025-01-07 12:00:00+00
--    Remove the spurious zero-value fallback row (pfaf_id 914900) that was
--    inserted by the INSERT trigger during the UPSERT split.
--    After fix: count for this timestamp should be 1221 (matching the CSV).
-- =============================================================================

DELETE FROM summary_gfms
WHERE "timestamp" = '2025-01-07 12:00:00+00'
  AND pfaf_id = 914900
  AND COALESCE("GFMS_TotalArea_km", 0) = 0
  AND COALESCE("GFMS_perc_Area",    0) = 0
  AND COALESCE("GFMS_MeanDepth",    0) = 0
  AND COALESCE("GFMS_MaxDepth",     0) = 0
  AND COALESCE("GFMS_Duration",     0) = 0;

-- Verify the fix:
DO $$
DECLARE
    cnt INTEGER;
BEGIN
    SELECT COUNT(*) INTO cnt
    FROM summary_gfms
    WHERE "timestamp" = '2025-01-07 12:00:00+00';

    RAISE NOTICE 'summary_gfms rows for 2025-01-07 12:00+00: % (expected 1221)', cnt;
END;
$$;

COMMIT;
