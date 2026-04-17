#!/bin/bash
# Installs the daily 23:30 cron job for download + upload.
# Run once to register the job; cron persists across reboots automatically.
#
# This installs a USER-LEVEL crontab for the root user (via `crontab -`).
# It is NOT a system-wide job — it is stored in /var/spool/cron/crontabs/root
# and only visible with `crontab -l` (not in /etc/crontab or /etc/cron.d/).
#
# Usage (run from repo root /root/MoMProduction):
#   chmod +x database_update/run_daily_update.sh   # make the daily job executable so cron can run it
#   bash database_update/install_cron.sh            # register the cron entry
#
# No arguments needed.
# Safe to re-run — will not add a duplicate entry if already installed.
#
# To check all installed jobs for the current user:
#   crontab -l
#
# To check for duplicate entries specifically:
#   crontab -l | grep run_daily_update
#   (should print exactly one line; if more, open crontab -e and remove duplicates)
#
# To disable the job temporarily (comment it out):
#   crontab -e   (add # at the start of the line)
#
# To remove the job permanently:
#   crontab -e   (delete the line)
#   — or to remove ALL jobs for this user:
#   crontab -r
#
# Logs are written to:
#   database_update/logs/daily_update_YYYYMMDD.log

set -euo pipefail

SCRIPT="/root/MoMProduction/database_update/run_daily_update.sh"
CRON_LINE="30 23 * * * $SCRIPT"

# Make the wrapper executable
chmod +x "$SCRIPT"

# Add the cron entry only if it isn't already present
if crontab -l 2>/dev/null | grep -qF "$SCRIPT"; then
    echo "Cron job already installed — no changes made."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "Cron job installed:"
    echo "  $CRON_LINE"
fi

echo ""
echo "Current crontab:"
crontab -l
