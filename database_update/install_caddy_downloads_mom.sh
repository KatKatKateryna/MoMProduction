#!/usr/bin/env bash
# Installs a systemd service that runs a password-protected Caddy server
# (port ${CADDY_PORT} below) exposing SERVE_DIR for browsing/downloading,
# with request-rate limiting to protect the VM from overload.
#
# To change credentials, port, folder, or rate limits: edit the CONFIG
# section below, then re-run this script as root to apply the changes:
#   sudo bash database_update/install_caddy_downloads_mom.sh
#   verify: systemctl status caddy-downloads-mom
#   logs:   journalctl -u caddy-downloads-mom -f
#
# The service is set to auto-restart on crash and on VM reboot (systemd
# Restart=on-failure + WantedBy=multi-user.target), same pattern as the
# nginx services already installed on this box.

set -euo pipefail

# ============================== CONFIG ===============================
CADDY_USER="admin"
CADDY_PASSWORD="password_mom_2026"

CADDY_PORT=8091
SERVE_DIR="/mnt/volume_ams3_02/downloads_mom"

# Per-client-IP limit: max requests a single IP may make per window.
RATE_LIMIT_EVENTS=10
RATE_LIMIT_WINDOW=1s

# Aggregate limit across ALL clients combined, so even many different IPs
# hitting it at once can't overload the VM. Kept above the per-IP rate so
# it only kicks in once several clients are active simultaneously.
GLOBAL_RATE_LIMIT_EVENTS=50
GLOBAL_RATE_LIMIT_WINDOW=1s
# Note: this limits request *rate*, not download bandwidth/throughput —
# Caddy has no built-in byte-rate throttle. If large concurrent file
# transfers ever saturate the VM's bandwidth/CPU, that needs a separate
# OS-level control (e.g. tc/cgroups), not something these settings cover.
# =======================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_NAME="caddy-downloads-mom"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CADDYFILE="${SCRIPT_DIR}/Caddyfile.downloads_mom"
CADDY_BIN="/usr/local/bin/caddy"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run as root (sudo bash $0)" >&2
    exit 1
fi

if [[ ! -d "$SERVE_DIR" ]]; then
    echo "ERROR: SERVE_DIR '$SERVE_DIR' does not exist." >&2
    exit 1
fi

# --- Fetch a Caddy build that includes the rate-limit plugin (mholt/caddy-ratelimit),
# which is not part of the stock Caddy binary. Skip if already present.
if [[ ! -x "$CADDY_BIN" ]] || ! "$CADDY_BIN" list-modules 2>/dev/null | grep -q "http.handlers.rate_limit"; then
    echo "Downloading Caddy build with rate-limit plugin..."
    TMP_BIN="$(mktemp)"
    curl -fsSL -o "$TMP_BIN" \
        "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/mholt/caddy-ratelimit"
    chmod +x "$TMP_BIN"
    mv "$TMP_BIN" "$CADDY_BIN"
fi

echo "Hashing password..."
CADDY_PASSWORD_HASH="$("$CADDY_BIN" hash-password --plaintext "$CADDY_PASSWORD")"

echo "Writing Caddyfile to $CADDYFILE"
cat > "$CADDYFILE" <<EOF
{
	admin off
}

:${CADDY_PORT} {
	root * ${SERVE_DIR}

	route {
		rate_limit {
			zone per_ip {
				key    {remote_host}
				events ${RATE_LIMIT_EVENTS}
				window ${RATE_LIMIT_WINDOW}
			}
			zone global_cap {
				key    all-clients
				events ${GLOBAL_RATE_LIMIT_EVENTS}
				window ${GLOBAL_RATE_LIMIT_WINDOW}
			}
		}

		basicauth {
			${CADDY_USER} ${CADDY_PASSWORD_HASH}
		}

		file_server browse {
			hide .*
		}
	}
}
EOF

echo "Writing systemd service to $SERVICE_FILE"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Caddy downloads_mom server (port ${CADDY_PORT})
After=network.target

[Service]
ExecStart=${CADDY_BIN} run --config ${CADDYFILE} --adapter caddyfile
ExecReload=${CADDY_BIN} reload --config ${CADDYFILE} --adapter caddyfile
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120

[Install]
WantedBy=multi-user.target
EOF
chmod 600 "$SERVICE_FILE"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo ""
echo "Done. Serving ${SERVE_DIR} at http://<vm-ip>:${CADDY_PORT}/ (user: ${CADDY_USER})"
echo "Service status:"
systemctl status "$SERVICE_NAME" --no-pager -l
