#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/tomaasz/ocr-dashboard-v3"
LOG_TAG="ocr-dashboard-push-retry"
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

if [[ -n "$(git -C "$REPO_DIR" status --porcelain)" ]]; then
  log "Repo has uncommitted changes. Skipping push retry."
  exit 0
fi

if git -C "$REPO_DIR" push origin HEAD; then
  log "Push retry succeeded."
  send_telegram "OCR Dashboard: push retry succeeded on ${HOSTNAME}."
  send_wall "OCR Dashboard: push retry succeeded on ${HOSTNAME}."
else
  log "Push retry failed."
  send_telegram "OCR Dashboard: push retry FAILED on ${HOSTNAME}."
  send_wall "OCR Dashboard: push retry FAILED on ${HOSTNAME}."
fi
