#!/usr/bin/env bash
# Installs a systemd service that starts the COG nginx server (port 8090) on
# every system reboot.  Run once as root:
#   sudo bash database_update/install_nginx_service.sh
#   verify: systemctl status nginx-cog

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CONDA="$(which conda)"
CONDA_RUN="${CONDA} run -n myenv"

SERVICE_NAME="nginx-cog"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Nginx COG image server (port 8090)
After=network.target

[Service]
Type=forking
PIDFile=${SCRIPT_DIR}/nginx_cog.pid
ExecStartPre=${CONDA_RUN} python ${SCRIPT_DIR}/serve_cog_nginx.py stop
ExecStart=${CONDA_RUN} python ${SCRIPT_DIR}/serve_cog_nginx.py start
ExecStop=${CONDA_RUN} python ${SCRIPT_DIR}/serve_cog_nginx.py stop
RemainAfterExit=no
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo "Service file written to $SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo ""
echo "Done. Service status:"
systemctl status "$SERVICE_NAME" --no-pager -l
