#!/bin/bash
# Sync CSV files from WAGO controller and import new data
# Only downloads files that are newer or don't exist locally

# Configuration (can be overridden by environment variables)
REMOTE_HOST="${REMOTE_HOST:-192.168.1.10}"
REMOTE_USER="${REMOTE_USER:-admin}"
REMOTE_PATH="${REMOTE_PATH:-/media/sd/CSV_Files/}"
LOCAL_DATA="${DATA_DIR:-/data}"
SSH_KEY="${SSH_KEY:-/ssh/wago_sync}"
LAST_SYNC_FILE="${LOCAL_DATA}/.last_sync"

echo "========================================"
echo "WAGO Data Sync - $(date -Iseconds)"
echo "========================================"

# Check if SSH key exists
if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    echo "Please generate SSH key first. See ssh/README.md for instructions."
    exit 1
fi

# Ensure data directory exists
mkdir -p "$LOCAL_DATA"

echo "Syncing from ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}"
echo "  -> ${LOCAL_DATA}/"

# SSH options for dropbear compatibility
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o PubkeyAcceptedAlgorithms=+ssh-rsa -o HostKeyAlgorithms=+ssh-rsa"

# Get list of remote CSV files with their sizes
echo "Checking for new/changed files..."
REMOTE_FILES=$(ssh $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "ls -l ${REMOTE_PATH}*.csv 2>/dev/null" | awk '{print $5, $NF}')

if [ -z "$REMOTE_FILES" ]; then
    echo "No CSV files found on remote server"
    exit 0
fi

# Count files to download
DOWNLOAD_COUNT=0
DOWNLOAD_LIST=""

while read -r size filepath; do
    [ -z "$filepath" ] && continue
    filename=$(basename "$filepath")
    local_file="${LOCAL_DATA}/${filename}"

    # Check if file exists locally and has same size
    if [ -f "$local_file" ]; then
        local_size=$(stat -f%z "$local_file" 2>/dev/null || stat -c%s "$local_file" 2>/dev/null)
        if [ "$local_size" = "$size" ]; then
            # File exists with same size, skip
            continue
        fi
    fi

    # File is new or different size, add to download list
    DOWNLOAD_COUNT=$((DOWNLOAD_COUNT + 1))
    DOWNLOAD_LIST="${DOWNLOAD_LIST} ${filepath}"
done <<< "$REMOTE_FILES"

if [ $DOWNLOAD_COUNT -eq 0 ]; then
    echo "All files are up to date (no changes detected)"
else
    echo "Downloading $DOWNLOAD_COUNT new/changed files..."

    # Download each file
    for filepath in $DOWNLOAD_LIST; do
        filename=$(basename "$filepath")
        echo "  -> $filename"
        scp $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}:${filepath}" "${LOCAL_DATA}/" 2>/dev/null
    done

    echo "Download complete"
fi

echo ""
echo "Running incremental import..."

# Run incremental import
python3 /scripts/import_data.py --incremental

# Update last sync timestamp
date -Iseconds > "$LAST_SYNC_FILE"

echo ""
echo "========================================"
echo "Sync and import completed successfully"
echo "Next sync in ${SYNC_INTERVAL:-300} seconds"
echo "========================================"
