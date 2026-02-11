#!/bin/bash
# OCR Dashboard V2 - Start Web Server

cd "$(dirname "$0")/.."
PROJECT_DIR="$(pwd)"

# Activate virtual environment
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Load .env if exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

export OCR_RUNNING_IN_WRAPPER=1

PORT="${OCR_DASHBOARD_PORT:-9090}"

echo "🚀 Starting OCR Dashboard V2 on port $PORT..."
echo "🔄 Auto-restart enabled - server will restart automatically after shutdown"

# Auto-restart loop
while true; do
    echo "▶️  Starting uvicorn at $(date '+%Y-%m-%d %H:%M:%S')..."
    uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
    
    EXIT_CODE=$?
    echo "⏹️  Uvicorn stopped with exit code $EXIT_CODE at $(date '+%Y-%m-%d %H:%M:%S')"
    
    # Only break on Ctrl+C (exit code 130)
    if [ $EXIT_CODE -eq 130 ]; then
        echo "👋 Ctrl+C detected. Exiting."
        break
    fi
    
    # Otherwise, restart after a short delay
    echo "🔄 Restarting in 2 seconds..."
    sleep 2
done
