#!/usr/bin/env bash
set -euo pipefail

RUNTIME_DIR="/home/tomaasz/ocr-dashboard-v3"
LOG_TAG="ocr-dashboard-auto-sync"
GIT="git -C $RUNTIME_DIR"

log() {
  logger -t "$LOG_TAG" "$*"
}

log_error() {
  logger -p user.err -t "$LOG_TAG" "$*"
}

if [[ ! -d "$RUNTIME_DIR/.git" ]]; then
  log_error "Repository not found at $RUNTIME_DIR"
  exit 1
fi

# Ensure user config is set (fallback if not present in global config, though it should be)
# We assume 'tomaasz' user has global git config, but setting local just in case if missing is safer,
# OR we rely on existing config. Failing if no identity is better than committing as 'root' or unknown.

# 1. AUTO-COMMIT: Check for uncommitted changes
if [[ -n "$($GIT status --porcelain)" ]]; then
  log "Uncommitted changes detected. Committing..."
  $GIT add .
  if $GIT commit -m "Auto-save: uncommitted changes on VPS ($(date +%Y-%m-%d_%H:%M))"; then
    log "Changes auto-committed."
  else
    log_error "Failed to commit changes."
    exit 1
  fi
fi

# 2. FETCH
log "Fetching origin..."
if ! $GIT fetch origin main; then
  log_error "Git fetch failed. Check network/auth."
  exit 1
fi

# 3. CHECK STATUS & SYNC
LOCAL_REV=$($GIT rev-parse HEAD)
REMOTE_REV=$($GIT rev-parse origin/main)
BASE_REV=$($GIT merge-base HEAD origin/main)

if [[ "$LOCAL_REV" == "$REMOTE_REV" ]]; then
  log "Already up-to-date."
  exit 0
fi

if [[ "$LOCAL_REV" == "$BASE_REV" ]]; then
  # Behind: fast-forward
  log "Behind remote. Pulling..."
  if $GIT pull origin main; then
    log "Updated to $REMOTE_REV."
    # Restart service if updated
    systemctl restart ocr-dashboard.service
  else
    log_error "Failed to pull changes."
    exit 1
  fi
elif [[ "$REMOTE_REV" == "$BASE_REV" ]]; then
  # Ahead: push
  log "Ahead of remote. Pushing..."
  if $GIT push origin main; then
    log "Pushed local changes to remote."
  else
    log_error "Failed to push changes."
    exit 1
  fi
else
  # Diverged: Changes on both sides. Rebase needed.
  log "Diverged (changes on both sides). Attempting rebase..."
  if $GIT pull --rebase origin main; then
    log "Rebase successful."
    # Push back the rebased commits
    if $GIT push origin main; then
       log "Pushed rebased changes."
    else
       log_error "Failed to push after rebase."
       exit 1
    fi
    # Restart service since we pulled changes
    systemctl restart ocr-dashboard.service
  else
    log_error "Rebase failed (conflict?). Aborting rebase."
    $GIT rebase --abort
    exit 1
  fi
fi
