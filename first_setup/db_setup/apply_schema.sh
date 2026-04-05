#!/usr/bin/env bash
# =============================================================================
# apply_schema.sh
#
# Re-applies all schema DDL (tables, triggers, functions) against an already-
# running PostgreSQL instance.  Safe to re-run at any time:
#   - Tables use CREATE TABLE IF NOT EXISTS
#   - Functions/triggers use CREATE OR REPLACE / DROP IF EXISTS
#   - No data is deleted
#
# Use this instead of setup_postgres.sh when PostgreSQL is already configured
# and you only need to create new tables or update triggers/functions.
#
# Run with:
#   bash first_setup/db_setup/apply_schema.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/db_config.cfg"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    exit 1
fi
source "$CONFIG_FILE"

SQL_FILES=(
    "${SCRIPT_DIR}/create_all_tables.sql"
    "${SCRIPT_DIR}/create_id_resolution_triggers.sql"
    "${SCRIPT_DIR}/create_history_triggers.sql"
    "${SCRIPT_DIR}/create_staging_tables.sql"
    "${SCRIPT_DIR}/create_query_functions.sql"
)

for f in "${SQL_FILES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: SQL file not found: $f"
        exit 1
    fi
done

echo "Applying schema to database '${DB_NAME}' on localhost..."
echo ""

for f in "${SQL_FILES[@]}"; do
    echo "--- $(basename "$f")"
    sudo -u postgres psql \
        -d "$DB_NAME" \
        -v ON_ERROR_STOP=1 \
        -f "$f"
    echo ""
done

echo "--- Granting permissions to '${ADMIN_USER}' on all tables"
sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 <<SQL
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "${ADMIN_USER}";

GRANT DELETE ON
    stage_gfms, stage_hwrf, stage_viirs, stage_dfo, stage_glofas, stage_final_alert,
    stage_mom_gfms, stage_mom_hwrf, stage_mom_dfo, stage_mom_viirs,
    summary_gfms_latest, summary_hwrf_latest, summary_viirs_latest,
    summary_dfo_latest, summary_glofas_latest, summary_final_alert_latest,
    summary_mom_gfms_latest, summary_mom_hwrf_latest,
    summary_mom_dfo_latest, summary_mom_viirs_latest
    TO "${ADMIN_USER}";
SQL

echo ""
echo "Schema applied successfully."
