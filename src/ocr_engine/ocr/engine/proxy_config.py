"""Proxy config loader with env override and per-profile fallback."""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_proxy_config(active_profile_name: str, proxies_file: Path | None = None) -> dict | None:
    """Load proxy config for profile; env OCR_PROXY_* overrides file."""
    env_server = os.environ.get("OCR_PROXY_SERVER", "").strip()
    if env_server:
        config: dict[str, str] = {"server": env_server}
        env_user = os.environ.get("OCR_PROXY_USERNAME", "").strip()
        env_password = os.environ.get("OCR_PROXY_PASSWORD", "").strip()
        if env_user:
            config["username"] = env_user
        if env_password:
            config["password"] = env_password
        return config

    if proxies_file is None:
        proxies_file = Path("config/proxies.json").resolve()

    if not proxies_file.exists():
        return None

    try:
        proxies_data = json.loads(proxies_file.read_text(encoding="utf-8"))
        proxies_map = proxies_data.get("proxies", {}) if isinstance(proxies_data, dict) else {}
        if not isinstance(proxies_map, dict):
            return None

        # Check specific profile then default
        proxy_config = proxies_map.get(active_profile_name) or proxies_map.get("default")
        return proxy_config
    except Exception as e:
        logger.error(f"❌ [Proxy] Failed to load proxy config: {e}")
        return None
