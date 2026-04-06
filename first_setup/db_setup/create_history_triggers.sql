-- =============================================================================
-- create_history_triggers.sql
--
-- Statement-level AFTER triggers on all _latest tables.
-- Both INSERT and UPDATE paths are covered (ON CONFLICT DO UPDATE in the
-- staging flush produces UPDATE-path rows; new pfaf_ids produce INSERT-path rows).
-- Both paths run the same sync function — duplicates in history are
-- prevented by ON CONFLICT DO NOTHING on the history INSERT.
--
-- Each trigger function does two things:
--   1. CLEAR-AND-REPLACE: delete all rows in _latest whose timestamp
--      differs from the batch timestamp (atomic swap — no reader sees mixed state).
--
--   2. HISTORY COPY with filtering:
--        GFMS / DFO  — only non-zero flood rows; fallback to last row
--                      (highest pfaf_id) if the entire batch is zeros.
--        HWRF / VIIRS / GloFAS / Final Alert — all rows, no filtering.
--        For GloFAS and Final Alert, station/watershed metadata columns are
--        stripped; those already live in the reference tables.
--
-- Assumption: matching_id_station and matching_id_watershed are already
-- resolved by the caller before inserting into the _latest tables.
--
-- Run with:
--   sudo -u postgres psql -d postgres -f create_history_triggers.sql
-- =============================================================================


-- =============================================================================
-- GFMS
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

    -- Clear stale rows from _latest when a full batch arrives
    DELETE FROM summary_gfms_latest
    WHERE "timestamp" != batch_ts;

    -- Flood-row filter with last-row fallback
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
        -- Fallback: entire batch has zero flood values — write last row only
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

DROP TRIGGER IF EXISTS trg_gfms_sync_ins ON summary_gfms_latest;
DROP TRIGGER IF EXISTS trg_gfms_sync_upd ON summary_gfms_latest;

CREATE TRIGGER trg_gfms_sync_ins
AFTER INSERT ON summary_gfms_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_gfms_sync();

CREATE TRIGGER trg_gfms_sync_upd
AFTER UPDATE ON summary_gfms_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_gfms_sync();


-- =============================================================================
-- HWRF
-- =============================================================================

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

    DELETE FROM summary_hwrf_latest
    WHERE "timestamp" != batch_ts;

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

DROP TRIGGER IF EXISTS trg_hwrf_sync_ins ON summary_hwrf_latest;
DROP TRIGGER IF EXISTS trg_hwrf_sync_upd ON summary_hwrf_latest;

CREATE TRIGGER trg_hwrf_sync_ins
AFTER INSERT ON summary_hwrf_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_hwrf_sync();

CREATE TRIGGER trg_hwrf_sync_upd
AFTER UPDATE ON summary_hwrf_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_hwrf_sync();


-- =============================================================================
-- VIIRS
-- =============================================================================

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

    DELETE FROM summary_viirs_latest
    WHERE "timestamp" != batch_ts;

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

DROP TRIGGER IF EXISTS trg_viirs_sync_ins ON summary_viirs_latest;
DROP TRIGGER IF EXISTS trg_viirs_sync_upd ON summary_viirs_latest;

CREATE TRIGGER trg_viirs_sync_ins
AFTER INSERT ON summary_viirs_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_viirs_sync();

CREATE TRIGGER trg_viirs_sync_upd
AFTER UPDATE ON summary_viirs_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_viirs_sync();


-- =============================================================================
-- DFO
-- =============================================================================

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

    DELETE FROM summary_dfo_latest
    WHERE "timestamp" != batch_ts;

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

DROP TRIGGER IF EXISTS trg_dfo_sync_ins ON summary_dfo_latest;
DROP TRIGGER IF EXISTS trg_dfo_sync_upd ON summary_dfo_latest;

CREATE TRIGGER trg_dfo_sync_ins
AFTER INSERT ON summary_dfo_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_dfo_sync();

CREATE TRIGGER trg_dfo_sync_upd
AFTER UPDATE ON summary_dfo_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_dfo_sync();


-- =============================================================================
-- MoM GFMS
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_mom_gfms_sync()
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

    DELETE FROM mom_gfms_latest
    WHERE "timestamp" != batch_ts;

    INSERT INTO mom_gfms (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_mom_gfms_sync_ins ON mom_gfms_latest;
DROP TRIGGER IF EXISTS trg_mom_gfms_sync_upd ON mom_gfms_latest;

CREATE TRIGGER trg_mom_gfms_sync_ins
AFTER INSERT ON mom_gfms_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_gfms_sync();

CREATE TRIGGER trg_mom_gfms_sync_upd
AFTER UPDATE ON mom_gfms_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_gfms_sync();


-- =============================================================================
-- MoM HWRF
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_mom_hwrf_sync()
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

    DELETE FROM mom_hwrf_latest
    WHERE "timestamp" != batch_ts;

    INSERT INTO mom_hwrf (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_mom_hwrf_sync_ins ON mom_hwrf_latest;
DROP TRIGGER IF EXISTS trg_mom_hwrf_sync_upd ON mom_hwrf_latest;

CREATE TRIGGER trg_mom_hwrf_sync_ins
AFTER INSERT ON mom_hwrf_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_hwrf_sync();

CREATE TRIGGER trg_mom_hwrf_sync_upd
AFTER UPDATE ON mom_hwrf_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_hwrf_sync();


-- =============================================================================
-- MoM DFO
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_mom_dfo_sync()
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

    DELETE FROM mom_dfo_latest
    WHERE "timestamp" != batch_ts;

    INSERT INTO mom_dfo (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_mom_dfo_sync_ins ON mom_dfo_latest;
DROP TRIGGER IF EXISTS trg_mom_dfo_sync_upd ON mom_dfo_latest;

CREATE TRIGGER trg_mom_dfo_sync_ins
AFTER INSERT ON mom_dfo_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_dfo_sync();

CREATE TRIGGER trg_mom_dfo_sync_upd
AFTER UPDATE ON mom_dfo_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_dfo_sync();


-- =============================================================================
-- MoM VIIRS
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_mom_viirs_sync()
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

    DELETE FROM mom_viirs_latest
    WHERE "timestamp" != batch_ts;

    INSERT INTO mom_viirs (
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    )
    SELECT
        pfaf_id, "timestamp",
        "FID", "Resilience_Index", "NormalizedLackofResilience",
        "Alert", "Flag"
    FROM new_rows
    ON CONFLICT ("timestamp", pfaf_id) DO NOTHING;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_mom_viirs_sync_ins ON mom_viirs_latest;
DROP TRIGGER IF EXISTS trg_mom_viirs_sync_upd ON mom_viirs_latest;

CREATE TRIGGER trg_mom_viirs_sync_ins
AFTER INSERT ON mom_viirs_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_viirs_sync();

CREATE TRIGGER trg_mom_viirs_sync_upd
AFTER UPDATE ON mom_viirs_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_mom_viirs_sync();


-- =============================================================================
-- GloFAS
-- Strips station metadata columns before writing to summary_glofas.
-- matching_id_station must be resolved by the caller before inserting.
-- =============================================================================

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

    DELETE FROM summary_glofas_latest
    WHERE "timestamp" != batch_ts;

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

DROP TRIGGER IF EXISTS trg_glofas_sync_ins ON summary_glofas_latest;
DROP TRIGGER IF EXISTS trg_glofas_sync_upd ON summary_glofas_latest;

CREATE TRIGGER trg_glofas_sync_ins
AFTER INSERT ON summary_glofas_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_glofas_sync();

CREATE TRIGGER trg_glofas_sync_upd
AFTER UPDATE ON summary_glofas_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_glofas_sync();


-- =============================================================================
-- Final Alert
-- Strips watershed metadata columns before writing to summary_final_alert.
-- matching_id_watershed must be resolved by the caller before inserting.
-- =============================================================================

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

    DELETE FROM summary_final_alert_latest
    WHERE "timestamp" != batch_ts;

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

DROP TRIGGER IF EXISTS trg_final_alert_sync_ins ON summary_final_alert_latest;
DROP TRIGGER IF EXISTS trg_final_alert_sync_upd ON summary_final_alert_latest;

CREATE TRIGGER trg_final_alert_sync_ins
AFTER INSERT ON summary_final_alert_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_final_alert_sync();

CREATE TRIGGER trg_final_alert_sync_upd
AFTER UPDATE ON summary_final_alert_latest
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION fn_final_alert_sync();
