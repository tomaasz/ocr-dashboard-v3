#!/bin/bash
# Mount NAS via Tailscale without modifying fstab
# This creates user-space mounts using mount.cifs

set -e

HOST="4fl1.tail7319.ts.net"
USER="tomaasz"
NAS_IP="100.80.240.52"  # kosciesza via Tailscale
MOUNT_BASE="/home/tomaasz/nas"

echo "=========================================="
echo "Mounting NAS on $HOST via Tailscale"
echo "NAS: kosciesza ($NAS_IP)"
echo "=========================================="

# Create mount points
echo "1. Creating mount points..."
ssh $USER@$HOST "mkdir -p $MOUNT_BASE/Genealogy $MOUNT_BASE/Skany $MOUNT_BASE/Dokumenty"

# Check if cifs-utils is installed
echo "2. Checking cifs-utils..."
ssh $USER@$HOST "which mount.cifs || echo '⚠️  mount.cifs not found - may need sudo apt install cifs-utils'"

# Try to mount Genealogy share (this will likely need credentials)
echo "3. Attempting to mount Genealogy share..."
echo "   Note: This may require CIFS credentials"

# Check if credentials file exists
CRED_EXISTS=$(ssh $USER@$HOST "test -f /etc/samba/credentials && echo 'yes' || echo 'no'")

if [[ "$CRED_EXISTS" == "yes" ]]; then
    echo "   ✅ Credentials file found"
    echo "   Mounting with system credentials..."
    
    # Try mounting with sudo (will fail if no password-less sudo)
    ssh $USER@$HOST "sudo mount -t cifs //$NAS_IP/Genealogy $MOUNT_BASE/Genealogy -o credentials=/etc/samba/credentials,uid=1000,gid=1001" 2>/dev/null && echo "   ✅ Mounted Genealogy" || echo "   ⚠️  Mount failed - may need manual intervention"
else
    echo "   ⚠️  No credentials file found at /etc/samba/credentials"
    echo "   You'll need to mount manually with credentials"
fi

# Check what's mounted
echo ""
echo "4. Checking mounted shares..."
ssh $USER@$HOST "mount | grep '$MOUNT_BASE' || echo 'No shares mounted yet'"

echo ""
echo "=========================================="
echo "Alternative: Create symlink to existing mounts"
echo "=========================================="

# Check if /mnt/kosciesza has any mounts
EXISTING_MOUNTS=$(ssh $USER@$HOST "ls /mnt/kosciesza/ 2>/dev/null | wc -l")

if [[ "$EXISTING_MOUNTS" -gt "0" ]]; then
    echo "Found existing mounts in /mnt/kosciesza/"
    echo "Creating symlink..."
    ssh $USER@$HOST "ln -sf /mnt/kosciesza $MOUNT_BASE/kosciesza 2>/dev/null || true"
    echo "✅ Symlink created: $MOUNT_BASE/kosciesza -> /mnt/kosciesza"
fi

echo ""
echo "=========================================="
echo "Summary"
echo "=========================================="
echo "To mount manually with credentials:"
echo "  ssh $USER@$HOST"
echo "  sudo mount -t cifs //kosciesza/Genealogy ~/nas/Genealogy -o username=USER,password=PASS,uid=1000"
echo ""
echo "To check mounts:"
echo "  ssh $USER@$HOST 'mount | grep nas'"
echo "  ssh $USER@$HOST 'ls -la ~/nas/Genealogy/'"
