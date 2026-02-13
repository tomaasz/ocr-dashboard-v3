#!/usr/bin/env python3
"""Create profile directories for all profiles in proxies.json."""

import json
import sys
from pathlib import Path


def main():
    cache_dir = Path.home() / ".cache" / "ocr-dashboard-v3"
    proxies_file = Path("config/proxies.json")

    if not proxies_file.exists():
        print("proxies.json not found")
        return 1

    data = json.loads(proxies_file.read_text())
    profiles = list(data.get("proxies", {}).keys())

    created = []
    for profile in profiles:
        if profile == "default":
            profile_dir = cache_dir / "gemini-profile"
        else:
            profile_dir = cache_dir / f"gemini-profile-{profile}"

        if not profile_dir.exists():
            profile_dir.mkdir(parents=True, exist_ok=True)
            created.append(profile)
            print(f"Created: {profile}")
        else:
            print(f"Exists: {profile}")

    print(f"\nCreated {len(created)} profile directories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
