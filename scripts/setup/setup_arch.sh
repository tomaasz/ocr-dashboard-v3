#!/bin/bash
#
# OCR Dashboard V2 - Arch Linux Host Setup Script
# ================================================
# This script configures a new Arch Linux host for running OCR workers
#
# Usage:
#   ./setup_arch.sh
#
# What it does:
# 1. Installs required system packages (pacman/yay)
# 2. Mounts NAS folder with scans (CIFS/SMB)
# 3. Clones repository from GitHub
# 4. Sets up Python virtual environment
# 5. Installs Python dependencies
# 6. Installs Playwright browsers
# 7. Configures systemd service (optional)

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration - Edit these values
GITHUB_REPO="${GITHUB_REPO:-https://github.com/tomaasz/ocr-dashboard-v3}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/ocr-dashboard-v3}"
NAS_HOST="${NAS_HOST:-192.168.1.100}"
NAS_SHARE="${NAS_SHARE:-scans}"
NAS_MOUNT_POINT="${NAS_MOUNT_POINT:-/mnt/nas-scans}"
NAS_USERNAME="${NAS_USERNAME:-}"
NAS_PASSWORD="${NAS_PASSWORD:-}"
NAS_DOMAIN="${NAS_DOMAIN:-WORKGROUP}"

# PostgreSQL configuration (optional - for local DB)
INSTALL_POSTGRES="${INSTALL_POSTGRES:-no}"
PG_USER="${PG_USER:-ocr_user}"
PG_PASSWORD="${PG_PASSWORD:-ocr_password}"
PG_DATABASE="${PG_DATABASE:-ocr_db}"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

sudo() {
    if [[ -n "${SUDO_PASSWORD:-}" ]]; then
        # Use -S to read password from stdin, -p '' to suppress prompt
        # printf writes to pipe without echoing to terminal
        printf '%s\n' "$SUDO_PASSWORD" | command sudo -S -p '' "$@"
    else
        command sudo "$@"
    fi
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_warning "Running as root - assuming you know what you are doing"
    else
        # Try to refresh sudo credential cache if password provided
        if [[ -n "${SUDO_PASSWORD:-}" ]]; then
            if ! printf '%s\n' "$SUDO_PASSWORD" | command sudo -S -v 2>/dev/null; then
                log_error "Verified sudo password: FAILED. Check your password."
                exit 1
            else
                log_success "Sudo password verified"
            fi
        fi
    fi
}

install_system_packages() {
    log_info "Installing system packages via pacman..."
    
    sudo pacman -Syu --noconfirm
    sudo pacman -S --needed --noconfirm \
        git \
        rsync \
        python \
        python-pip \
        python-virtualenv \
        cifs-utils \
        curl \
        wget \
        base-devel \
        postgresql-libs \
        chromium \
        ttf-liberation \
        nss \
        alsa-lib \
        gtk3 \
        libxrandr \
        libxcomposite \
        libxdamage \
        libxfixes \
        mesa \
        at-spi2-core \
        cups
    
    log_success "System packages installed"
}

install_postgresql() {
    if [[ "$INSTALL_POSTGRES" != "yes" ]]; then
        log_info "Skipping PostgreSQL installation (INSTALL_POSTGRES!=yes)"
        return
    fi
    
    log_info "Installing PostgreSQL..."
    
    sudo pacman -S --needed --noconfirm postgresql
    
    # Initialize PostgreSQL data directory if needed
    if [[ ! -d /var/lib/postgres/data ]]; then
        sudo -u postgres initdb -D /var/lib/postgres/data
    fi
    
    # Start and enable PostgreSQL
    sudo systemctl start postgresql
    sudo systemctl enable postgresql
    
    # Wait for PostgreSQL to start
    sleep 2
    
    # Configure PostgreSQL
    sudo -u postgres psql <<EOF
CREATE USER $PG_USER WITH PASSWORD '$PG_PASSWORD';
CREATE DATABASE $PG_DATABASE OWNER $PG_USER;
GRANT ALL PRIVILEGES ON DATABASE $PG_DATABASE TO $PG_USER;
EOF
    
    log_success "PostgreSQL installed and configured"
    log_info "Connection string: postgresql://$PG_USER:$PG_PASSWORD@localhost:5432/$PG_DATABASE"
}

setup_nas_mount() {
    log_info "Setting up NAS mount..."
    
    local nas_share_path="//${NAS_HOST}/${NAS_SHARE}"
    
    # Check if this NAS share is already configured in fstab (any host, same share name OR same mount point)
    if grep -qE "/${NAS_SHARE}[[:space:]]" /etc/fstab 2>/dev/null || grep -q "${NAS_MOUNT_POINT}" /etc/fstab 2>/dev/null; then
        log_warning "NAS share /${NAS_SHARE} or mount point ${NAS_MOUNT_POINT} already configured in /etc/fstab - skipping"
        
        # Just try to mount if not already mounted
        if ! mountpoint -q "$NAS_MOUNT_POINT" 2>/dev/null; then
            sudo mount -a 2>/dev/null || true
        fi
        
        if mountpoint -q "$NAS_MOUNT_POINT" 2>/dev/null; then
            log_success "NAS already mounted at $NAS_MOUNT_POINT"
        else
            log_info "NAS mount point: $NAS_MOUNT_POINT (may require manual mount)"
        fi
        return
    fi
    
    # Create mount point
    sudo mkdir -p "$NAS_MOUNT_POINT"
    
    # Create credentials file if username/password provided
    if [[ -n "$NAS_USERNAME" && -n "$NAS_PASSWORD" ]]; then
        local creds_file="/etc/nas-credentials"
        local tmp_creds="/tmp/.nas-creds-$$"
        
        # Create temp credentials file (avoid password appearing in logs)
        cat > "$tmp_creds" <<EOF
username=$NAS_USERNAME
password=$NAS_PASSWORD
domain=$NAS_DOMAIN
EOF
        # Move to final location with sudo (silently)
        sudo mv "$tmp_creds" "$creds_file" 2>/dev/null
        sudo chmod 600 "$creds_file" 2>/dev/null
        log_success "NAS credentials saved to $creds_file"
        
        # Add to fstab for persistent mounting
        local fstab_entry="${nas_share_path} ${NAS_MOUNT_POINT} cifs credentials=${creds_file},uid=$(id -u),gid=$(id -g),file_mode=0755,dir_mode=0755 0 0"
        
        if ! grep -q "$NAS_MOUNT_POINT" /etc/fstab; then
            # Use temp file to avoid password leak via pipe to sudo tee
            local tmp_fstab="/tmp/.fstab-entry-$$"
            echo "$fstab_entry" > "$tmp_fstab"
            sudo sh -c "cat '$tmp_fstab' >> /etc/fstab"
            rm -f "$tmp_fstab"
            log_success "Added NAS mount to /etc/fstab"
        else
            log_warning "Mount point $NAS_MOUNT_POINT already in /etc/fstab"
        fi
        
        # Mount now
        sudo mount -a
        
        if mountpoint -q "$NAS_MOUNT_POINT"; then
            log_success "NAS mounted successfully at $NAS_MOUNT_POINT"
        else
            log_error "Failed to mount NAS"
        fi
    else
        log_warning "NAS credentials not provided - skipping automatic mount"
        log_info "Manual mount command:"
        log_info "sudo mount -t cifs ${nas_share_path} ${NAS_MOUNT_POINT} -o username=USER,password=PASS"
    fi
}

clone_repository() {
    log_info "Cloning repository from GitHub..."
    
    if [[ "$GITHUB_REPO" == "SKIP" || "$GITHUB_REPO" == "LOCAL" ]]; then
        log_info "Skipping git clone (mode: $GITHUB_REPO). Expecting files to be copied manually or via rsync."
        mkdir -p "$INSTALL_DIR"
        return
    fi
    
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        log_info "Directory $INSTALL_DIR already exists - updating via git pull..."
        cd "$INSTALL_DIR"
        # Use timeout and disable terminal prompts to prevent hanging
        if GIT_TERMINAL_PROMPT=0 timeout 30 git pull --ff-only 2>/dev/null; then
            log_success "Repository updated successfully"
        else
            log_warning "Git pull failed or timed out - continuing with existing files"
        fi
        return
    elif [[ -d "$INSTALL_DIR" ]]; then
        log_warning "Directory $INSTALL_DIR exists but is not a git repo - initializing..."
        cd "$INSTALL_DIR"
        git init
        if [[ "$GITHUB_REPO" != "SKIP" && "$GITHUB_REPO" != "LOCAL" && -n "$GITHUB_REPO" ]]; then
            git remote add origin "$GITHUB_REPO" 2>/dev/null || git remote set-url origin "$GITHUB_REPO"
            log_success "Git initialized with remote: $GITHUB_REPO"
            log_info "Run 'git fetch origin && git reset --hard origin/main' to sync with remote"
        else
            log_success "Git initialized (no remote configured)"
        fi
        return
    fi
    
    if git clone "$GITHUB_REPO" "$INSTALL_DIR"; then
        log_success "Repository cloned to $INSTALL_DIR"
    else
        log_warning "Git clone failed. Proceeding - expecting files to be synced manually or via rsync."
        mkdir -p "$INSTALL_DIR"
    fi
    cd "$INSTALL_DIR"
}

setup_python_environment() {
    log_info "Setting up Python virtual environment..."
    
    cd "$INSTALL_DIR"
    
    # Create virtual environment
    python -m venv venv
    
    # Activate and upgrade pip
    source venv/bin/activate
    pip install --upgrade pip setuptools wheel
    
    # Install dependencies
    log_info "Installing Python dependencies..."
    if [[ -f "requirements.txt" ]]; then
        pip install -r requirements.txt
    else
        log_warning "requirements.txt not found - skipping dependency installation (will need to run after file sync)"
    fi
    
    # Install Playwright browsers (may fail if playwright not installed yet)
    log_info "Installing Playwright browsers..."
    set +e  # Disable errexit temporarily
    if command -v playwright &>/dev/null || [[ -f "venv/bin/playwright" ]]; then
        venv/bin/playwright install chromium 2>/dev/null || log_warning "Playwright browser install failed"
        venv/bin/playwright install-deps chromium 2>/dev/null || log_warning "Playwright deps install failed"
    else
        log_warning "Playwright not installed yet - will be installed after file sync"
    fi
    set -e  # Re-enable errexit
    
    log_success "Python environment configured"
}

create_env_file() {
    log_info "Creating .env configuration file..."
    
    cd "$INSTALL_DIR"
    
    if [[ -f .env ]]; then
        log_warning ".env file already exists - creating .env.new instead"
        local env_file=".env.new"
    else
        local env_file=".env"
    fi
    
    # Determine PostgreSQL DSN
    local pg_dsn="postgresql://user:password@localhost:5432/ocr_db"
    if [[ "$INSTALL_POSTGRES" == "yes" ]]; then
        pg_dsn="postgresql://$PG_USER:$PG_PASSWORD@localhost:5432/$PG_DATABASE"
    fi
    
    cat > "$env_file" <<EOF
# OCR Dashboard V2 - Environment Configuration
OCR_PG_DSN=$pg_dsn

# Remote Worker Configuration
OCR_REMOTE_HOST=
OCR_REMOTE_USER=$USER
OCR_REMOTE_REPO_DIR=$INSTALL_DIR
OCR_REMOTE_SOURCE_DIR=$NAS_MOUNT_POINT

# Dashboard Configuration  
OCR_DASHBOARD_PORT=9090
OCR_DEFAULT_WORKERS=2
OCR_DEFAULT_SCANS_PER_WORKER=2

# Update Counts
OCR_UPDATE_COUNTS_ON_START=1
OCR_UPDATE_COUNTS_MIN_INTERVAL_SEC=900
EOF
    
    log_success "Environment file created: $env_file"
    
    if [[ "$env_file" == ".env.new" ]]; then
        log_warning "Please review .env.new and merge with existing .env"
    fi
}

setup_systemd_service() {
    log_info "Setting up systemd service..."
    
    # Skip in non-interactive mode (remote deployment)
    # Check NONINTERACTIVE env var or if stdin is not a terminal
    if [[ "${NONINTERACTIVE:-}" == "1" ]] || [[ ! -t 0 ]] || [[ -n "${SSH_CLIENT:-}" ]]; then
        log_info "Non-interactive mode - skipping systemd service installation"
        log_info "Run manually later with: sudo systemctl enable ocr-dashboard"
        return
    fi
    
    read -p "Install systemd service for auto-start? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Skipping systemd service installation"
        return
    fi
    
    local service_file="/etc/systemd/system/ocr-dashboard.service"
    
    sudo tee "$service_file" > /dev/null <<EOF
[Unit]
Description=OCR Dashboard V2
After=network.target postgresql.service

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
Environment="PATH=$INSTALL_DIR/venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$INSTALL_DIR/venv/bin/python run.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    sudo systemctl daemon-reload
    sudo systemctl enable ocr-dashboard.service
    
    log_success "Systemd service installed"
    log_info "Start with: sudo systemctl start ocr-dashboard"
    log_info "Check status: sudo systemctl status ocr-dashboard"
}

print_summary() {
    echo
    echo "=================================================="
    log_success "OCR Dashboard V2 - Arch Linux Setup Complete!"
    echo "=================================================="
    echo
    echo "Installation directory: $INSTALL_DIR"
    echo "NAS mount point: $NAS_MOUNT_POINT"
    echo "Python virtual environment: $INSTALL_DIR/venv"
    echo
    echo "Next steps:"
    echo "  1. Review and edit $INSTALL_DIR/.env"
    echo "  2. Start the dashboard:"
    echo "     cd $INSTALL_DIR"
    echo "     source venv/bin/activate"
    echo "     ./scripts/start_web.sh"
    echo "  3. Access at http://localhost:9090/v2"
    echo
    if [[ "$INSTALL_POSTGRES" == "yes" ]]; then
        echo "PostgreSQL connection:"
        echo "  postgresql://$PG_USER:$PG_PASSWORD@localhost:5432/$PG_DATABASE"
        echo
    fi
    echo "=================================================="
}

main() {
    echo "=================================================="
    echo "  OCR Dashboard V2 - Arch Linux Host Setup"
    echo "=================================================="
    echo
    
    check_root
    install_system_packages
    install_postgresql
    setup_nas_mount
    clone_repository
    setup_python_environment
    create_env_file
    setup_systemd_service
    print_summary
}

main "$@"
