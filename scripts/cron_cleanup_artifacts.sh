#!/bin/bash
# Cron job script for automatic OCR artifact cleanup
# 
# This script is designed to be run periodically via cron to automatically
# clean up old debug artifacts from the database.
#
# Installation:
#   1. Make this script executable:
#      chmod +x scripts/cron_cleanup_artifacts.sh
#
#   2. Add to crontab (run every hour):
#      0 * * * * /path/to/ocr-dashboard-v3/scripts/cron_cleanup_artifacts.sh >> /var/log/ocr-cleanup.log 2>&1
#
#   3. Or run every 6 hours:
#      0 */6 * * * /path/to/ocr-dashboard-v3/scripts/cron_cleanup_artifacts.sh >> /var/log/ocr-cleanup.log 2>&1

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load environment variables if .env file exists
if [ -f "$PROJECT_DIR/.env" ]; then
    export $(grep -v '^#' "$PROJECT_DIR/.env" | xargs)
fi

# Configuration
RETENTION_HOURS="${OCR_ARTIFACT_RETENTION_HOURS:-24}"
PYTHON="${PYTHON:-python3}"

# Log start
echo "========================================="
echo "OCR Artifact Cleanup - $(date)"
echo "========================================="
echo "Retention: ${RETENTION_HOURS} hours"
echo "Database: ${OCR_PG_DSN:0:30}..." # Show only first 30 chars for security

# Check if database is configured
if [ -z "$OCR_PG_DSN" ]; then
    echo "ERROR: OCR_PG_DSN not set"
    echo "Please configure database connection in .env file"
    exit 1
fi

# Run cleanup script
cd "$PROJECT_DIR"
$PYTHON scripts/cleanup_artifacts.py --hours "$RETENTION_HOURS" --force

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Cleanup completed successfully"
else
    echo "❌ Cleanup failed with exit code $EXIT_CODE"
fi

echo "========================================="
echo ""

exit $EXIT_CODE
