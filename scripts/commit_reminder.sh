#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/tomaasz/ocr-dashboard-v3"
LOG_TAG="ocr-dashboard-commit-reminder"
CONFIG_FILE="/etc/ocr-dashboard/telegram.env"
USER_CONFIG_FILE="${HOME}/.config/ocr-dashboard/telegram.env"

log() {
  logger -t "$LOG_TAG" "$*"
}

load_telegram_config() {
  if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
  elif [[ -f "$USER_CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$USER_CONFIG_FILE"
  fi
}

send_telegram() {
  local msg="$1"
  load_telegram_config
  if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "${TELEGRAM_CHAT_ID:-}" ]]; then
    return 0
  fi
  curl -s --max-time 5 \
    -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${msg}" >/dev/null 2>&1 || true
}

send_wall() {
  local msg="$1"
  if command -v wall >/dev/null 2>&1; then
    echo "$msg" | wall -n 2>/dev/null || true
  fi
}

if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "Repo not found at $REPO_DIR"
  exit 0
fi

CHANGES=$(git -C "$REPO_DIR" status --porcelain)
if [[ -z "$CHANGES" ]]; then
  log "Repo clean."
  exit 0
fi

if [[ "${1:-}" == "--auto-commit" ]]; then
  git -C "$REPO_DIR" add -A
  if git -C "$REPO_DIR" commit -m "chore: autosave"; then
    if git -C "$REPO_DIR" push origin HEAD; then
      log "Auto-commit + push executed."
      send_telegram "OCR Dashboard: auto-commit + push executed on ${HOSTNAME}."
      send_wall "OCR Dashboard: auto-commit + push executed on ${HOSTNAME}."
    else
      log "Auto-commit executed, push failed."
      send_telegram "OCR Dashboard: auto-commit OK, push FAILED on ${HOSTNAME}."
      send_wall "OCR Dashboard: auto-commit OK, push FAILED on ${HOSTNAME}."
    fi
  else
    log "Auto-commit failed or nothing to commit."
  fi
  exit 0
fi

log "Uncommitted changes detected in repo. Run: $REPO_DIR/scripts/commit_reminder.sh --auto-commit"
send_telegram "OCR Dashboard: uncommitted changes detected in repo on ${HOSTNAME}. Run auto-commit when ready."
send_wall "OCR Dashboard: uncommitted changes detected in repo on ${HOSTNAME}. Run auto-commit when ready."
