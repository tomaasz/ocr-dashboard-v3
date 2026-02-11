#!/bin/bash
# Setup standardized source mount points on a host.
# Run once per host. Requires sudo for directory creation.
#
# Usage:
#   ./scripts/setup_source_mounts.sh                    # Interactive
#   ./scripts/setup_source_mounts.sh --nas-only         # Only mount NAS
#   ./scripts/setup_source_mounts.sh --dry-run          # Preview only

set -euo pipefail

SOURCE_ROOT="/data/sources"
NAS_HOST="kosciesza.tail7319.ts.net"
NAS_USER="tomaasz"
NAS_PATH="/home/tomaasz/Genealogy/Sources"

DRY_RUN=false
NAS_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --nas-only) NAS_ONLY=true ;;
        --help) echo "Usage: $0 [--dry-run] [--nas-only]"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

log() {
    local msg="$1"
    echo "$(date +%H:%M:%S) $msg"
    return 0
}
run() {
    if $DRY_RUN; then
        echo "  [DRY-RUN] $*"
    else
        "$@"
    fi
    return $?
}

# ─── 1. Create directory structure ───

log "🔧 Setting up source mounts at ${SOURCE_ROOT}..."

if [[ ! -d "${SOURCE_ROOT}" ]]; then
    log "📁 Creating ${SOURCE_ROOT}..."
    run sudo mkdir -p "${SOURCE_ROOT}"/{nas,gdrive,local,.cache}
    run sudo chown -R "${USER}:${USER}" "${SOURCE_ROOT}"
else
    log "✅ ${SOURCE_ROOT} already exists"
    for sub in nas gdrive local .cache; do
        [[ -d "${SOURCE_ROOT}/${sub}" ]] || run mkdir -p "${SOURCE_ROOT}/${sub}"
    done
fi

# ─── 2. Mount NAS via SSHFS ───

if mountpoint -q "${SOURCE_ROOT}/nas" 2>/dev/null; then
    log "✅ NAS already mounted at ${SOURCE_ROOT}/nas"
else
    log "📂 Mounting NAS via SSHFS..."
    
    # Check if sshfs is installed
    if ! command -v sshfs &>/dev/null; then
        log "⚠️  sshfs not installed. Install with: sudo apt install sshfs"
        exit 1
    fi
    
    # Check if host is reachable
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "${NAS_USER}@${NAS_HOST}" "echo ok" &>/dev/null; then
        log "⚠️  Cannot reach ${NAS_HOST}. Check Tailscale connection."
        exit 1
    fi
    
    run sshfs "${NAS_USER}@${NAS_HOST}:${NAS_PATH}" "${SOURCE_ROOT}/nas" \
        -o allow_other,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3
    
    log "✅ NAS mounted at ${SOURCE_ROOT}/nas"
fi

# ─── 3. Mount Google Drive (optional) ───

if ! $NAS_ONLY; then
    if mountpoint -q "${SOURCE_ROOT}/gdrive" 2>/dev/null; then
        log "✅ GDrive already mounted at ${SOURCE_ROOT}/gdrive"
    elif command -v rclone &>/dev/null; then
        log "📂 Mounting Google Drive via rclone..."
        
        if rclone listremotes | grep -q "^gdrive:"; then
            run rclone mount gdrive:Genealogy "${SOURCE_ROOT}/gdrive" \
                --daemon \
                --vfs-cache-mode full \
                --vfs-cache-max-size 5G \
                --log-file /tmp/rclone-gdrive.log
            log "✅ GDrive mounted at ${SOURCE_ROOT}/gdrive"
        else
            log "⚠️  rclone remote 'gdrive' not configured. Run: rclone config"
        fi
    else
        log "ℹ️  rclone not installed — skipping GDrive mount"
    fi
fi

# ─── 4. Set OCR_SOURCE_ROOT env var ───

log ""
log "📋 Mount status:"
echo "  ${SOURCE_ROOT}/nas     — $(mountpoint -q ${SOURCE_ROOT}/nas 2>/dev/null && echo 'MOUNTED' || echo 'NOT MOUNTED')"
echo "  ${SOURCE_ROOT}/gdrive  — $(mountpoint -q ${SOURCE_ROOT}/gdrive 2>/dev/null && echo 'MOUNTED' || echo 'NOT MOUNTED')"

if mountpoint -q "${SOURCE_ROOT}/nas" 2>/dev/null; then
    COUNT=$(ls "${SOURCE_ROOT}/nas/" 2>/dev/null | head -5 | wc -l)
    log ""
    log "📂 NAS contents (first 5):"
    ls "${SOURCE_ROOT}/nas/" 2>/dev/null | head -5 || echo "  (empty or inaccessible)"
fi

log ""
log "✅ Setup complete!"
log ""
log "Next steps:"
log "  1. Set env var:  export OCR_SOURCE_ROOT=${SOURCE_ROOT}"
log "  2. Or add to service:  Environment=\"OCR_SOURCE_ROOT=${SOURCE_ROOT}\""
log "  3. Update profiles to use relative paths: nas/FolderName/SubFolder"
