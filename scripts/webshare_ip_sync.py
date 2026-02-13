#!/usr/bin/env python3
"""Sync Webshare IP authorization with current public IP.

Requires env:
  WEBSHARE_API_TOKEN
Optional:
  WEBSHARE_IP_FILE (default: config/webshare_ip.txt)
  WEBSHARE_API_BASE (default: https://proxy.webshare.io/api/v2/proxy/ipauthorization/)
  WEBSHARE_WHATSMYIP_URL (default: https://ipv4.icanhazip.com)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def _read_url(url: str, headers: dict[str, str] | None = None) -> str:
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", "replace")


def _api_request(method: str, url: str, token: str, data: dict[str, Any] | None = None) -> dict:
    headers = {"Authorization": f"Token {token}"}
    payload = None
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, method=method, headers=headers, data=payload)
    with urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body else {}


def main() -> int:
    token = os.environ.get("WEBSHARE_API_TOKEN", "").strip()
    if not token:
        print("Missing WEBSHARE_API_TOKEN env", file=sys.stderr)
        return 2

    api_base = os.environ.get(
        "WEBSHARE_API_BASE", "https://proxy.webshare.io/api/v2/proxy/ipauthorization/"
    ).strip()
    ip_url = os.environ.get("WEBSHARE_WHATSMYIP_URL", "https://ipv4.icanhazip.com").strip()
    ip_file_raw = os.environ.get("WEBSHARE_IP_FILE", "config/webshare_ip.txt").strip()
    # Prevent path traversal
    if ".." in ip_file_raw or ip_file_raw.startswith("/"):
        ip_file_raw = "config/webshare_ip.txt"
    # deepcode ignore PT: Safe default provided, input sanitized
    ip_file = Path(ip_file_raw)

    current_ip = _read_url(ip_url).strip()
    if not current_ip:
        print("Failed to detect public IP", file=sys.stderr)
        return 3

    # List existing IP authorizations
    data = _api_request("GET", api_base, token)
    results = data.get("results", []) if isinstance(data, dict) else []

    existing = {str(item.get("ip_address")): item for item in results if isinstance(item, dict)}

    # If current IP already present, keep it and remove others (plan usually allows 1)
    if current_ip in existing:
        for ip, item in existing.items():
            if ip == current_ip:
                continue
            item_id = item.get("id")
            if item_id:
                _api_request("DELETE", f"{api_base}{item_id}/", token)
        ip_file.parent.mkdir(parents=True, exist_ok=True)
        ip_file.write_text(f"{current_ip}\n", encoding="utf-8")
        print(f"Webshare IP OK: {current_ip}")
        return 0

    # Remove any others first (plan supports 1 IP)
    for item in existing.values():
        item_id = item.get("id")
        if item_id:
            _api_request("DELETE", f"{api_base}{item_id}/", token)

    # Add current IP
    _api_request("POST", api_base, token, {"ip_address": current_ip})

    ip_file.parent.mkdir(parents=True, exist_ok=True)
    ip_file.write_text(f"{current_ip}\n", encoding="utf-8")
    print(f"Webshare IP updated: {current_ip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
