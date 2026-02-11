#!/usr/bin/env python3
"""
Verify source directories are accessible on remote hosts before starting profiles
"""

import sys
import subprocess
import shlex
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.services.remote_config import get_effective_remote_config


def check_local_directory(path):
    """Check if local directory exists and is accessible"""
    try:
        p = Path(path)
        if not p.exists():
            return False, f"Directory does not exist: {path}"
        if not p.is_dir():
            return False, f"Path is not a directory: {path}"

        # Try to list contents
        try:
            files = list(p.iterdir())
            return True, f"✅ Accessible ({len(files)} items)"
        except PermissionError:
            return False, f"Permission denied: {path}"
    except Exception as e:
        return False, f"Error: {e}"


def check_remote_directory(host_addr, host_user, ssh_opts, path):
    """Check if directory exists on remote host"""
    try:
        # Build SSH command
        ssh_cmd = ["ssh"]
        if ssh_opts:
            ssh_cmd.extend(shlex.split(ssh_opts))
        ssh_cmd.extend(
            [
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=no",
                f"{host_user}@{host_addr}",
                f"test -d {shlex.quote(path)} && ls -la {shlex.quote(path)} | wc -l",
            ]
        )

        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=15, check=False)

        if result.returncode == 0:
            count = int(result.stdout.strip()) - 2  # Subtract . and ..
            return True, f"✅ Accessible ({count} items)"
        else:
            return False, "❌ Directory not found or not accessible"
    except subprocess.TimeoutExpired:
        return False, "❌ SSH timeout"
    except Exception as e:
        return False, f"❌ Error: {e}"


def main():
    print("=" * 80)
    print("🔍 Source Directory Verification")
    print("=" * 80)

    config = get_effective_remote_config()
    hosts = config.get("OCR_REMOTE_HOSTS_LIST", [])

    if not hosts:
        print("\n⚠️  No remote hosts configured")
        return 1

    all_ok = True

    for host in hosts:
        host_id = host.get("id", "unknown")
        host_name = host.get("name", host_id)
        host_addr = host.get("host", "")
        host_user = host.get("user", "")
        ssh_opts = host.get("ssh", "")
        source_path = host.get("source", "")

        print(f"\n{'─' * 80}")
        print(f"📍 Host: {host_name} ({host_id})")
        print(f"   Address: {host_addr}")
        print(f"   Source: {source_path or '(not configured)'}")
        print(f"{'─' * 80}")

        if not source_path:
            print("   ⚠️  WARNING: No source path configured!")
            all_ok = False
            continue

        # Check if it's a Windows path (contains backslash or drive letter)
        is_windows = "\\" in source_path or ":" in source_path

        if is_windows:
            print("   ℹ️  Windows path detected - skipping remote check")
            print("   💡 Ensure this path is accessible on the Windows host")
            continue

        # Check remote directory
        print("   🔍 Checking remote directory...")
        ok, message = check_remote_directory(host_addr, host_user, ssh_opts, source_path)
        print(f"   {message}")

        if not ok:
            all_ok = False
            print("\n   💡 Troubleshooting:")
            print(
                f"      1. Check if NAS is mounted: ssh {host_user}@{host_addr} 'mount | grep nas'"
            )
            print(f"      2. Check directory: ssh {host_user}@{host_addr} 'ls -la {source_path}'")
            print("      3. Mount NAS if needed")

    print(f"\n{'=' * 80}")
    if all_ok:
        print("✅ All source directories are accessible!")
        print("=" * 80)
        return 0
    else:
        print("❌ Some source directories are NOT accessible!")
        print("   Fix the issues above before starting profiles")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
