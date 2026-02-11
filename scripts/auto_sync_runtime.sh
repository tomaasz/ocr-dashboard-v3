#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="/home/tomaasz/ocr-dashboard-v3"
LOG_TAG="ocr-dashboard-auto-sync"

log() {
  logger -t "$LOG_TAG" "$*"
}

if [[ ! -d "$RUNTIME_DIR/.git" ]]; then
  log "Runtime repo not found at $RUNTIME_DIR"
  exit 0
fi

if [[ -n "$(git -C "$RUNTIME_DIR" status --porcelain)" ]]; then
  log "Runtime repo has uncommitted changes. Skipping sync."
  exit 0
fi

# Fetch and check if update is needed
if ! git -C "$RUNTIME_DIR" fetch --quiet origin main; then
  log "Git fetch failed."
  exit 1
fi

LOCAL_REV=$(git -C "$RUNTIME_DIR" rev-parse HEAD)
REMOTE_REV=$(git -C "$RUNTIME_DIR" rev-parse origin/main)

if [[ "$LOCAL_REV" == "$REMOTE_REV" ]]; then
  log "Runtime already up to date."
  exit 0
fi

# Hard reset to origin/main and restart service
if git -C "$RUNTIME_DIR" reset --hard --quiet origin/main; then
  log "Runtime updated to origin/main: $REMOTE_REV"
  systemctl restart ocr-dashboard.service
  log "Service restarted."
else
  log "Git reset failed."
  exit 1
fi
