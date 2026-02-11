#!/bin/bash
# Install OCR Folder Indexer as systemd service
# Usage: sudo ./install_folder_indexer.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/systemd/ocr-folder-indexer.service"
TARGET="/etc/systemd/system/ocr-folder-indexer.service"

echo "📂 Installing OCR Folder Indexer Service..."

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root (sudo)"
    exit 1
fi

# Check if service file exists
if [ ! -f "$SERVICE_FILE" ]; then
    echo "❌ Service file not found: $SERVICE_FILE"
    exit 1
fi

# Copy service file
echo "📄 Copying service file to $TARGET..."
cp "$SERVICE_FILE" "$TARGET"

# Reload systemd
echo "🔄 Reloading systemd daemon..."
systemctl daemon-reload

# Enable service
echo "✅ Enabling service..."
systemctl enable ocr-folder-indexer.service

# Start service
echo "🚀 Starting service..."
systemctl start ocr-folder-indexer.service

# Show status
echo ""
echo "✅ Installation complete!"
echo ""
systemctl status ocr-folder-indexer.service --no-pager

echo ""
echo "📋 Useful commands:"
echo "  systemctl status ocr-folder-indexer    # Check status"
echo "  systemctl stop ocr-folder-indexer      # Stop"
echo "  systemctl restart ocr-folder-indexer   # Restart"
echo "  journalctl -u ocr-folder-indexer -f    # View logs"
