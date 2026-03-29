-- =============================================================================
-- create_query_functions.sql
--
-- Query helper functions for GFMS and DFO — the two history tables that
-- filter out zero-value rows before storing.
--
-- Because zero-value rows are not stored, a plain SELECT returns 0 rows for
-- a watershed that had no flood activity. These functions implement the
-- correct three-case semantics:
--
--   Case 1 — timestamp not in the table
--            Batch was never ingested. Returns 0 rows.
--
--   Case 2 — timestamp exists, pfaf_id absent
--            Batch was ingested but this watershed had all-zero flood values.
--            Returns 1 row with all flood columns = 0.
--
--   Case 3 — both timestamp and pfaf_id exist
--            Returns the real stored row.
--
-- Functions:
--   fn_get_gfms(p_ts, p_pfaf_id)  — single watershed lookup
--   fn_get_gfms_batch(p_ts)       — all watersheds for a timestamp
--   fn_get_dfo(p_ts, p_pfaf_id)   — single watershed lookup
--   fn_get_dfo_batch(p_ts)        — all watersheds for a timestamp
--
-- The batch functions use all_watersheds UNION the real rows as the universe
-- of pfaf_ids, so they work correctly whether or not all_watersheds is loaded.
--
-- Run AFTER create_all_tables.sql.
-- Run with:
--   sudo -u postgres psql -d postgres -f create_query_functions.sql
-- =============================================================================


-- =============================================================================
-- GFMS — single watershed lookup
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_get_gfms(p_ts TIMESTAMPTZ, p_pfaf_id INTEGER)
RETURNS TABLE (
    pfaf_id             INTEGER,
    "timestamp"         TIMESTAMPTZ,
    "GFMS_TotalArea_km" DOUBLE PRECISION,
    "GFMS_perc_Area"    DOUBLE PRECISION,
    "GFMS_MeanDepth"    DOUBLE PRECISION,
    "GFMS_MaxDepth"     DOUBLE PRECISION,
    "GFMS_Duration"     DOUBLE PRECISION
) LANGUAGE plpgsql AS $$
BEGIN
    -- Case 1: timestamp not in table — return nothing
    IF NOT EXISTS (
        SELECT 1 FROM summary_gfms WHERE "timestamp" = p_ts
    ) THEN
        RETURN;
    END IF;

    -- Cases 2 & 3: timestamp exists — real row or synthesised zero row
    RETURN QUERY
    SELECT
        p_pfaf_id,
        p_ts,
        COALESCE(g."GFMS_TotalArea_km", 0.0),
        COALESCE(g."GFMS_perc_Area",    0.0),
        COALESCE(g."GFMS_MeanDepth",    0.0),
        COALESCE(g."GFMS_MaxDepth",     0.0),
        COALESCE(g."GFMS_Duration",     0.0)
    FROM (SELECT 1) dummy
    LEFT JOIN summary_gfms g
        ON g."timestamp" = p_ts
       AND g.pfaf_id     = p_pfaf_id;
END;
$$;


-- =============================================================================
-- GFMS — all watersheds for a given timestamp
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_get_gfms_batch(p_ts TIMESTAMPTZ)
RETURNS TABLE (
    pfaf_id             INTEGER,
    "timestamp"         TIMESTAMPTZ,
    "GFMS_TotalArea_km" DOUBLE PRECISION,
    "GFMS_perc_Area"    DOUBLE PRECISION,
    "GFMS_MeanDepth"    DOUBLE PRECISION,
    "GFMS_MaxDepth"     DOUBLE PRECISION,
    "GFMS_Duration"     DOUBLE PRECISION
) LANGUAGE plpgsql AS $$
BEGIN
    -- Case 1: timestamp not in table — return nothing
    IF NOT EXISTS (
        SELECT 1 FROM summary_gfms WHERE "timestamp" = p_ts
    ) THEN
        RETURN;
    END IF;

    -- Universe: all known watersheds plus any real rows for this timestamp.
    -- This ensures correct results whether or not all_watersheds is loaded.
    RETURN QUERY
    SELECT
        u.pfaf_id,
        p_ts,
        COALESCE(g."GFMS_TotalArea_km", 0.0),
        COALESCE(g."GFMS_perc_Area",    0.0),
        COALESCE(g."GFMS_MeanDepth",    0.0),
        COALESCE(g."GFMS_MaxDepth",     0.0),
        COALESCE(g."GFMS_Duration",     0.0)
    FROM (
        SELECT pfaf_id FROM all_watersheds
        UNION
        SELECT pfaf_id FROM summary_gfms WHERE "timestamp" = p_ts
    ) u
    LEFT JOIN summary_gfms g
        ON g.pfaf_id     = u.pfaf_id
       AND g."timestamp" = p_ts
    ORDER BY u.pfaf_id;
END;
$$;


-- =============================================================================
-- DFO — single watershed lookup
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_get_dfo(p_ts TIMESTAMPTZ, p_pfaf_id INTEGER)
RETURNS TABLE (
    pfaf_id                    INTEGER,
    "timestamp"                TIMESTAMPTZ,
    "1-Day_TotalArea_km2"      DOUBLE PRECISION,
    "1-Day_perc_Area"          DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"   DOUBLE PRECISION,
    "1-Day_CS_perc_Area"       DOUBLE PRECISION,
    "2-Day_TotalArea_km2"      DOUBLE PRECISION,
    "2-Day_perc_Area"          DOUBLE PRECISION,
    "3-Day_TotalArea_km2"      DOUBLE PRECISION,
    "3-Day_perc_Area"          DOUBLE PRECISION
) LANGUAGE plpgsql AS $$
BEGIN
    -- Case 1: timestamp not in table — return nothing
    IF NOT EXISTS (
        SELECT 1 FROM summary_dfo WHERE "timestamp" = p_ts
    ) THEN
        RETURN;
    END IF;

    -- Cases 2 & 3: timestamp exists — real row or synthesised zero row
    RETURN QUERY
    SELECT
        p_pfaf_id,
        p_ts,
        COALESCE(d."1-Day_TotalArea_km2",    0.0),
        COALESCE(d."1-Day_perc_Area",        0.0),
        COALESCE(d."1-Day_CS_TotalArea_km2", 0.0),
        COALESCE(d."1-Day_CS_perc_Area",     0.0),
        COALESCE(d."2-Day_TotalArea_km2",    0.0),
        COALESCE(d."2-Day_perc_Area",        0.0),
        COALESCE(d."3-Day_TotalArea_km2",    0.0),
        COALESCE(d."3-Day_perc_Area",        0.0)
    FROM (SELECT 1) dummy
    LEFT JOIN summary_dfo d
        ON d."timestamp" = p_ts
       AND d.pfaf_id     = p_pfaf_id;
END;
$$;


-- =============================================================================
-- DFO — all watersheds for a given timestamp
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_get_dfo_batch(p_ts TIMESTAMPTZ)
RETURNS TABLE (
    pfaf_id                    INTEGER,
    "timestamp"                TIMESTAMPTZ,
    "1-Day_TotalArea_km2"      DOUBLE PRECISION,
    "1-Day_perc_Area"          DOUBLE PRECISION,
    "1-Day_CS_TotalArea_km2"   DOUBLE PRECISION,
    "1-Day_CS_perc_Area"       DOUBLE PRECISION,
    "2-Day_TotalArea_km2"      DOUBLE PRECISION,
    "2-Day_perc_Area"          DOUBLE PRECISION,
    "3-Day_TotalArea_km2"      DOUBLE PRECISION,
    "3-Day_perc_Area"          DOUBLE PRECISION
) LANGUAGE plpgsql AS $$
BEGIN
    -- Case 1: timestamp not in table — return nothing
    IF NOT EXISTS (
        SELECT 1 FROM summary_dfo WHERE "timestamp" = p_ts
    ) THEN
        RETURN;
    END IF;

    -- Universe: all known watersheds plus any real rows for this timestamp.
    RETURN QUERY
    SELECT
        u.pfaf_id,
        p_ts,
        COALESCE(d."1-Day_TotalArea_km2",    0.0),
        COALESCE(d."1-Day_perc_Area",        0.0),
        COALESCE(d."1-Day_CS_TotalArea_km2", 0.0),
        COALESCE(d."1-Day_CS_perc_Area",     0.0),
        COALESCE(d."2-Day_TotalArea_km2",    0.0),
        COALESCE(d."2-Day_perc_Area",        0.0),
        COALESCE(d."3-Day_TotalArea_km2",    0.0),
        COALESCE(d."3-Day_perc_Area",        0.0)
    FROM (
        SELECT pfaf_id FROM all_watersheds
        UNION
        SELECT pfaf_id FROM summary_dfo WHERE "timestamp" = p_ts
    ) u
    LEFT JOIN summary_dfo d
        ON d.pfaf_id     = u.pfaf_id
       AND d."timestamp" = p_ts
    ORDER BY u.pfaf_id;
END;
$$;
