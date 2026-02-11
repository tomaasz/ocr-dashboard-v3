# OCR Dashboard V2 - Setup Configuration Example
# ===============================================
# Copy this file and customize for your environment
# Source this file before running setup scripts

# GitHub Repository
export GITHUB_REPO="https://github.com/YOUR_USERNAME/ocr-dashboard-v3.git"

# NAS Configuration
export NAS_HOST="192.168.1.100"           # NAS IP or hostname
export NAS_SHARE="scans"                  # Share name on NAS
export NAS_USERNAME="your_username"       # NAS username
export NAS_PASSWORD="your_password"       # NAS password
export NAS_DOMAIN="WORKGROUP"             # Windows domain (if applicable)

# Installation Paths (Linux)
export INSTALL_DIR="$HOME/ocr-dashboard-v3"
export NAS_MOUNT_POINT="/mnt/nas-scans"

# PostgreSQL Configuration (optional - for local DB)
export INSTALL_POSTGRES="no"              # Set to "yes" to install PostgreSQL
export PG_USER="ocr_user"
export PG_PASSWORD="ocr_password"
export PG_DATABASE="ocr_db"

# Usage:
# 1. Copy this file: cp setup_config.example.sh my_setup_config.sh
# 2. Edit your copy: nano my_setup_config.sh
# 3. Source before running setup: source my_setup_config.sh && ./setup_ubuntu.sh
