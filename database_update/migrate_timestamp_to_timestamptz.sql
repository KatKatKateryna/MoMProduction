-- Migration: change all "timestamp" columns from VARCHAR(64) to TIMESTAMPTZ
-- Safe to run on an empty database — no data conversion needed.
-- Run with: psql -U postgres -d postgres -f migrate_timestamp_to_timestamptz.sql

-- Main tables (timestamp is part of the primary key)
ALTER TABLE summary_gfms         ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_hwrf         ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_glofas       ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_viirs        ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_dfo          ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_final_alert  ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;

-- Latest tables
ALTER TABLE summary_gfms_latest         ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_hwrf_latest         ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_glofas_latest       ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_viirs_latest        ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_dfo_latest          ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
ALTER TABLE summary_final_alert_latest  ALTER COLUMN "timestamp" TYPE TIMESTAMPTZ USING "timestamp"::TIMESTAMPTZ;
