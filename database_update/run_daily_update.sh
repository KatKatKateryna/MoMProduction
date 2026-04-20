#!/bin/bash
# Daily cron wrapper: download data then upload yesterday's records.
# Runs at 23:30 every day via crontab.

set -euo pipefail

REPO_DIR="/root/MoMProduction"
LOG_DIR="$REPO_DIR/database_update/logs"
YESTERDAY=$(date -d "yesterday" +%Y%m%d)
LOG_FILE="$LOG_DIR/daily_update_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"

exec >> "$LOG_FILE" 2>&1

echo "=============================="
echo "Daily update started: $(date)"
echo "Processing date: $YESTERDAY"
echo "=============================="

# Activate conda environment
source /root/miniconda3/etc/profile.d/conda.sh
conda activate myenv

cd "$REPO_DIR"

echo "--- download_mom_data.py ---"
python database_update/download_mom_data.py

echo "--- upload_local_data.py -d $YESTERDAY ---"
python database_update/upload_local_data.py -d "$YESTERDAY"

echo "--- upload_local_images.py ---"
if pgrep -f "upload_local_images.py" > /dev/null; then
    echo "upload_local_images.py is already running, skipping."
else
    python database_update/upload_local_images.py
fi

echo "=============================="
echo "Daily update finished: $(date)"
echo "=============================="
