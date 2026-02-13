#!/usr/bin/env python3
"""Sync Webshare proxy list to config/proxies.json with balanced random assignment.

Requires env:
  WEBSHARE_API_TOKEN
Optional env:
  WEBSHARE_PROXY_MODE (default: direct)
  WEBSHARE_PLAN_ID
  WEBSHARE_PAGE_SIZE (default: 100)
  WEBSHARE_COUNTRY_CODES (comma separated)
  WEBSHARE_MIN_VALID (default: 1)  # only use proxies with valid=true
  OCR_CACHE_DIR (default: ~/.cache/ocr-dashboard-v3)
  OCR_PROXIES_FILE (default: config/proxies.json)
  WEBSHARE_ASSIGN_SEED (optional int for deterministic shuffle)
"""

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _read_url(url: str, headers: dict[str, str] | None = None) -> str:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "replace")


def _api_get(url: str, token: str) -> dict:
    headers = {"Authorization": f"Token {token}"}
    raw = _read_url(url, headers=headers)
    return json.loads(raw) if raw else {}


def _list_profiles(cache_dir: Path) -> list[str]:
    profiles: list[str] = []
    default_dir = cache_dir / "gemini-profile"
    if default_dir.exists():
        profiles.append("default")

    if cache_dir.exists():
        for item in cache_dir.iterdir():
            if item.is_dir() and item.name.startswith("gemini-profile-"):
                suffix = item.name.replace("gemini-profile-", "", 1).strip()
                if suffix:
                    profiles.append(suffix)

    return sorted({p for p in profiles if p})


def _load_existing_profiles(proxies_file: Path) -> list[str]:
    if not proxies_file.exists():
        return []
    try:
        data = json.loads(proxies_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    proxies = data.get("proxies") if isinstance(data, dict) else None
    if isinstance(proxies, dict):
        return sorted({str(k) for k in proxies if k})
    return []


def _get_all_proxies(
    token: str, mode: str, page_size: int, plan_id: str | None, country_codes: str | None
) -> list[dict[str, Any]]:
    proxies: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {"mode": mode, "page": page, "page_size": page_size}
        if plan_id:
            params["plan_id"] = plan_id
        if country_codes:
            params["country_code__in"] = country_codes
        url = f"https://proxy.webshare.io/api/v2/proxy/list/?{urlencode(params)}"
        data = _api_get(url, token)
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            break
        proxies.extend([r for r in results if isinstance(r, dict)])
        if not data.get("next"):
            break
        page += 1
    return proxies


def _build_proxy_entry(item: dict[str, Any]) -> dict[str, str] | None:
    addr = item.get("proxy_address")
    port = item.get("port")
    username = item.get("username")
    password = item.get("password")
    if not addr or not port or not username or not password:
        return None
    return {
        "server": f"http://{addr}:{port}",
        "username": str(username),
        "password": str(password),
    }


def _assign_proxies(
    profiles: list[str], proxy_entries: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    if not profiles or not proxy_entries:
        return {}

    seed_val = os.environ.get("WEBSHARE_ASSIGN_SEED")
    if seed_val:
        try:
            random.seed(int(seed_val))
        except Exception:
            random.seed(seed_val)

    random.shuffle(proxy_entries)

    count_profiles = len(profiles)
    count_proxies = len(proxy_entries)
    max_per_proxy = (count_profiles + count_proxies - 1) // count_proxies

    assignments: dict[str, dict[str, str]] = {}
    usage = [0] * count_proxies

    proxy_index = 0
    for profile in profiles:
        # find next proxy with available capacity
        start = proxy_index
        while usage[proxy_index] >= max_per_proxy:
            proxy_index = (proxy_index + 1) % count_proxies
            if proxy_index == start:
                break
        assignments[profile] = proxy_entries[proxy_index]
        usage[proxy_index] += 1
        proxy_index = (proxy_index + 1) % count_proxies

    return assignments


def main() -> int:
    token = os.environ.get("WEBSHARE_API_TOKEN", "").strip()
    if not token:
        print("Missing WEBSHARE_API_TOKEN env", file=sys.stderr)
        return 2

    mode = os.environ.get("WEBSHARE_PROXY_MODE", "direct").strip() or "direct"
    plan_id = os.environ.get("WEBSHARE_PLAN_ID", "").strip() or None
    country_codes = os.environ.get("WEBSHARE_COUNTRY_CODES", "").strip() or None
    page_size = int(os.environ.get("WEBSHARE_PAGE_SIZE", "100").strip() or "100")
    min_valid = os.environ.get("WEBSHARE_MIN_VALID", "1").strip() != "0"

    cache_dir = Path(
        os.environ.get("OCR_CACHE_DIR", str(Path.home() / ".cache" / "ocr-dashboard-v3"))
    )
    proxies_file_raw = os.environ.get("OCR_PROXIES_FILE", "config/proxies.json").strip()
    # Prevent path traversal
    if ".." in proxies_file_raw or proxies_file_raw.startswith("/"):
        proxies_file_raw = "config/proxies.json"
    # deepcode ignore PT: Safe default provided, input sanitized
    # Security: Path traversal prevented by validation above (lines 160-162)
    # nosemgrep: python.lang.security.audit.path-traversal.path-traversal
    # deepcode ignore PT: Path is validated to not contain ".." or start with "/"
    # skipcq: PYL-W1514
    proxies_file = Path(proxies_file_raw)  # nosec B108

    profiles = _list_profiles(cache_dir)

    if not profiles:
        print("No profiles found", file=sys.stderr)
        return 3

    raw_proxies = _get_all_proxies(token, mode, page_size, plan_id, country_codes)
    if min_valid:
        raw_proxies = [p for p in raw_proxies if p.get("valid") is True]

    proxy_entries = [p for p in (_build_proxy_entry(item) for item in raw_proxies) if p]
    if not proxy_entries:
        print("No proxies available from Webshare", file=sys.stderr)
        return 4

    assignments = _assign_proxies(profiles, proxy_entries)
    if not assignments:
        print("Failed to assign proxies", file=sys.stderr)
        return 5

    proxies_file.parent.mkdir(parents=True, exist_ok=True)
    proxies_file.write_text(json.dumps({"proxies": assignments}, indent=4), encoding="utf-8")

    print(f"Assigned {len(assignments)} profiles to {len(proxy_entries)} proxies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
