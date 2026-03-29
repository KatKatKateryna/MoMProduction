#!/usr/bin/env bash
# =============================================================================
# setup_postgres.sh
# Installs PostgreSQL + PostGIS on Ubuntu, stores the database cluster under
# /root/postgres/data/, runs the schema DDL, and creates a restricted user.
#
# Safe to re-run at any time:
#   - Skips cluster creation if already set up at the correct data directory
#   - Re-applies all config, grants, and schema changes on every run
#   - Existing data is never deleted
#
# Must be run as a user with sudo privileges.
# =============================================================================

set -euo pipefail

# ---------- configuration ----------------------------------------------------
SCRIPT_DIR="$(dirname "$0")"
CONFIG_FILE="${SCRIPT_DIR}/db_config.cfg"
SQL_FILE="${SCRIPT_DIR}/create_all_tables.sql"
SQL_ID_TRIGGERS="${SCRIPT_DIR}/create_id_resolution_triggers.sql"
SQL_HISTORY_TRIGGERS="${SCRIPT_DIR}/create_history_triggers.sql"
SQL_STAGING="${SCRIPT_DIR}/create_staging_tables.sql"
SQL_QUERY_FUNCTIONS="${SCRIPT_DIR}/create_query_functions.sql"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    echo "       Copy sample_db_config.cfg to db_config.cfg and fill in the passwords."
    exit 1
fi
# shellcheck source=db_config.cfg
source "$CONFIG_FILE"

if [[ "$ADMIN_PASSWORD" == "???" ]]; then
    echo "ERROR: ADMIN_PASSWORD is not set in $CONFIG_FILE"
    echo "       Replace the ??? placeholder with a real password and re-run."
    exit 1
fi
# -----------------------------------------------------------------------------

for f in "$SQL_FILE" "$SQL_ID_TRIGGERS" "$SQL_HISTORY_TRIGGERS" "$SQL_STAGING" "$SQL_QUERY_FUNCTIONS"; do
    if [[ ! -f "$f" ]]; then
        echo "ERROR: SQL file not found: $f"
        exit 1
    fi
done

echo "=== [1/6] Installing PostgreSQL and PostGIS ==="
sudo apt-get update -y
sudo apt-get install -y \
    postgresql \
    postgresql-contrib \
    postgis \
    postgresql-postgis

PG_VERSION=$(ls /usr/lib/postgresql/ | sort -V | tail -1)
PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"
echo "    PostgreSQL version: ${PG_VERSION}"

echo "=== [2/6] Setting up database cluster at ${DATA_DIR} ==="
EXISTING_DATADIR=$(pg_lsclusters -h 2>/dev/null | awk 'NR==1{print $6}')

if [[ "$EXISTING_DATADIR" == "$DATA_DIR" ]]; then
    echo "    Cluster already at ${DATA_DIR}, skipping cluster creation."
else
    echo "    Creating new cluster at ${DATA_DIR}..."
    sudo pg_dropcluster --stop "${PG_VERSION}" main || true

    sudo mkdir -p "${DATA_DIR}"
    # Allow the postgres OS user to traverse into /root and the parent folder.
    # o+x = execute only (can enter the directory, cannot list its contents).
    sudo chmod o+x /root
    sudo chmod o+x "$(dirname "${DATA_DIR}")"
    sudo chown postgres:postgres "${DATA_DIR}"
    sudo chmod 700 "${DATA_DIR}"

    sudo pg_createcluster --datadir "${DATA_DIR}" "${PG_VERSION}" main
fi

echo "=== [3/6] Enabling remote connections ==="
sudo sed -i "s/^#*listen_addresses\s*=.*/listen_addresses = '*'/" \
    "${PG_CONF_DIR}/postgresql.conf"

REMOTE_RULE="host    ${DB_NAME}    ${ADMIN_USER}    0.0.0.0/0    scram-sha-256"
if ! sudo grep -qF "${REMOTE_RULE}" "${PG_CONF_DIR}/pg_hba.conf"; then
    echo "${REMOTE_RULE}" | sudo tee -a "${PG_CONF_DIR}/pg_hba.conf" > /dev/null
    echo "    Remote access rule added."
else
    echo "    Remote access rule already present, skipping."
fi

if command -v ufw &>/dev/null; then
    sudo ufw allow 5432/tcp
fi

echo "=== [4/6] Starting PostgreSQL service ==="
sudo systemctl enable postgresql
sudo systemctl restart postgresql

echo "=== [5/6] Running schema DDL (PostGIS extension + all tables + triggers + functions) ==="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_FILE}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_ID_TRIGGERS}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_HISTORY_TRIGGERS}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_STAGING}"
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" -f "${SQL_QUERY_FUNCTIONS}"

echo "=== [6/6] Creating/updating restricted admin user '${ADMIN_USER}' ==="
sudo -u postgres psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${ADMIN_USER}') THEN
        CREATE ROLE "${ADMIN_USER}" WITH LOGIN PASSWORD '${ADMIN_PASSWORD}';
    ELSE
        ALTER ROLE "${ADMIN_USER}" WITH LOGIN PASSWORD '${ADMIN_PASSWORD}';
    END IF;
END
\$\$;

GRANT CONNECT ON DATABASE "${DB_NAME}" TO "${ADMIN_USER}";
GRANT USAGE ON SCHEMA public TO "${ADMIN_USER}";

-- SELECT, INSERT, UPDATE on all tables; DELETE also granted on staging tables
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO "${ADMIN_USER}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE ON TABLES TO "${ADMIN_USER}";

-- DELETE on staging and latest tables
GRANT DELETE ON
    stage_gfms, stage_hwrf, stage_viirs, stage_dfo, stage_glofas, stage_final_alert,
    summary_gfms_latest, summary_hwrf_latest, summary_viirs_latest,
    summary_dfo_latest, summary_glofas_latest, summary_final_alert_latest
    TO "${ADMIN_USER}";

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "${ADMIN_USER}";
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO "${ADMIN_USER}";

ALTER ROLE "${ADMIN_USER}" SET statement_timeout = '300s';
SQL

echo ""
echo "============================================================"
echo "Setup complete."
echo "  Database     : ${DB_NAME}"
echo "  Data dir     : ${DATA_DIR}"
echo "  App user     : ${ADMIN_USER}  (remote access, restricted)"
echo "  Host         : <this machine's IP address>"
echo "  Port         : 5432"
echo ""
echo "  Change app user password with:"
echo "  sudo -u postgres psql -c \"ALTER ROLE ${ADMIN_USER} WITH PASSWORD 'newpassword';\""
echo "============================================================"
