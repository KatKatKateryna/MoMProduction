-- Drop all MoM tables in dependency order.
-- CASCADE handles any remaining FK references automatically.
-- ⚠ This permanently destroys ALL data AND schema objects.

DROP TABLE IF EXISTS
    -- staging
    stage_gfms,
    stage_hwrf,
    stage_viirs,
    stage_dfo,
    stage_glofas,
    stage_final_alert,

    -- latest snapshots
    summary_gfms_latest,
    summary_hwrf_latest,
    summary_viirs_latest,
    summary_dfo_latest,
    summary_glofas_latest,
    summary_final_alert_latest,

    -- history
    summary_gfms,
    summary_hwrf,
    summary_viirs,
    summary_dfo,
    summary_glofas,
    summary_final_alert,

    -- reference (depend on watershed_shapes)
    all_glofas_stations,
    all_watersheds,

    -- parent (last)
    watershed_shapes

CASCADE;
