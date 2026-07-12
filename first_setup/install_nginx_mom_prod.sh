#!/usr/bin/env bash
# Installs a systemd service that starts the nginx server (port 8070) on
# every system reboot. Run once as root:
#   sudo bash first_setup/install_nginx_mom_prod.sh
#   verify: systemctl status nginx-mom-prod

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_NAME="nginx-mom-prod"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

CFG_FILE="${SCRIPT_DIR}/../production.cfg"
PRODUCT_DIR=$(awk -F': *' '/^PRODUCT_DIR:/ {print $2}' "$CFG_FILE")
PRODUCT_DIR="${PRODUCT_DIR/#\~/$HOME}"

NGINX_CONF="${SCRIPT_DIR}/nginx.conf"
cat > "$NGINX_CONF" <<EOF
pid /run/nginx.pid;
events {}
http {
    server {
        listen 8070;

        location / {
            alias ${PRODUCT_DIR}/;
            autoindex on;
        }
    }
}
EOF

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Nginx MoM Prod server (port 8070)
After=network.target

[Service]
Type=forking
PIDFile=/run/nginx.pid
ExecStartPre=-/usr/bin/fuser -k 8070/tcp
ExecStart=/usr/sbin/nginx -c ${SCRIPT_DIR}/nginx.conf
ExecReload=/usr/sbin/nginx -s reload
ExecStop=/usr/sbin/nginx -s quit

Restart=on-failure
RestartSec=10
StartLimitIntervalSec=120

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
