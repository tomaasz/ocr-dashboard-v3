#!/bin/bash
# Update fstab to use Tailscale IP for NAS mounts on host 4fl1

set -e

HOST="4fl1.tail7319.ts.net"
USER="tomaasz"
OLD_IP="192.168.1.200"
NEW_IP="100.80.240.52"

echo "=========================================="
echo "Updating fstab on $HOST"
echo "Old IP: $OLD_IP (local network)"
echo "New IP: $NEW_IP (Tailscale)"
echo "=========================================="

# Backup current fstab
echo "1. Creating backup of /etc/fstab..."
ssh $USER@$HOST "sudo cp /etc/fstab /etc/fstab.backup.$(date +%Y%m%d_%H%M%S)"

# Replace IP in fstab
echo "2. Updating IP addresses in /etc/fstab..."
ssh $USER@$HOST "sudo sed -i 's|//$OLD_IP/|//$NEW_IP/|g' /etc/fstab"

# Show changes
echo "3. Verifying changes..."
ssh $USER@$HOST "grep '$NEW_IP' /etc/fstab | head -5"

# Reload systemd
echo "4. Reloading systemd daemon..."
ssh $USER@$HOST "sudo systemctl daemon-reload"

# Try to mount
echo "5. Attempting to mount all from fstab..."
ssh $USER@$HOST "sudo mount -a" || echo "⚠️  Some mounts may have failed - check manually"

# Check mount status
echo "6. Checking mount status..."
ssh $USER@$HOST "df -h | grep kosciesza || echo 'No mounts found yet'"

echo ""
echo "=========================================="
echo "✅ Update complete!"
echo "=========================================="
echo ""
echo "To verify manually:"
echo "  ssh $USER@$HOST 'mount | grep kosciesza'"
echo "  ssh $USER@$HOST 'ls -la /mnt/kosciesza/Genealogy/'"
