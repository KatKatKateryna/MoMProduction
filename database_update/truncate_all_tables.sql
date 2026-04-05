-- Truncate all tables in dependency order.
-- CASCADE handles FK constraints automatically.
-- ⚠ This permanently deletes ALL data — tables and schema are preserved.

TRUNCATE TABLE
    -- staging (no FK constraints, but clear first)
    stage_gfms,
    stage_hwrf,
    stage_viirs,
    stage_dfo,
    stage_glofas,
    stage_final_alert,

    -- history tables
    summary_gfms,
    summary_hwrf,
    summary_viirs,
    summary_dfo,
    summary_glofas,
    summary_final_alert,

    -- latest snapshot tables
    summary_gfms_latest,
    summary_hwrf_latest,
    summary_viirs_latest,
    summary_dfo_latest,
    summary_glofas_latest,
    summary_final_alert_latest,

    -- reference tables
    all_glofas_stations,
    all_watersheds,

    -- parent (must come last in the list, CASCADE covers child references)
    watershed_shapes

CASCADE;
