"""
Remote hosts configuration storage and retrieval.
Persisted in cache so dashboard UI can edit without env changes.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..config import REMOTE_HOSTS_CONFIG_FILE

REMOTE_CONFIG_KEYS = [
    "OCR_REMOTE_RUN_ENABLED",
    "OCR_REMOTE_HOST",
    "OCR_REMOTE_USER",
    "OCR_REMOTE_REPO_DIR",
    "OCR_REMOTE_SSH_OPTS",
    "OCR_REMOTE_BROWSER_ENABLED",
    "OCR_REMOTE_BROWSER_HOST",
    "OCR_REMOTE_BROWSER_USER",
    "OCR_REMOTE_BROWSER_PROFILE_ROOT",
    "OCR_REMOTE_BROWSER_PYTHON",
    "OCR_REMOTE_BROWSER_PORT_BASE",
    "OCR_REMOTE_BROWSER_PORT_SPAN",
    "OCR_REMOTE_BROWSER_SSH_OPTS",
    "OCR_REMOTE_BROWSER_LOCAL_PORT_BASE",
    "OCR_REMOTE_BROWSER_TUNNEL",
    "OCR_REMOTE_BROWSER_CHROME_BIN",
    "OCR_REMOTE_HOSTS_LIST",
]

_BOOL_KEYS = {
    "OCR_REMOTE_RUN_ENABLED",
    "OCR_REMOTE_BROWSER_ENABLED",
    "OCR_REMOTE_BROWSER_TUNNEL",
}


def _coerce_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, list, dict)):
        return value
    text = str(value).strip()
    if text == "" or text.lower() == "none":
        return None
    return text


def _parse_env_var(key: str, text: str) -> Any:
    """Parse environment variable string based on key type."""
    if key in _BOOL_KEYS:
        return text.lower() in {"1", "true", "yes", "y", "on"}
    if key == "OCR_REMOTE_HOSTS_LIST":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return []
    return None if text.lower() == "none" else text


def load_remote_config() -> dict[str, Any]:
    """Load persisted remote host config from cache."""
    try:
        if REMOTE_HOSTS_CONFIG_FILE.exists():
            data = json.loads(REMOTE_HOSTS_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: _coerce_value(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def save_remote_config(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist remote host config and return cleaned version."""
    clean: dict[str, Any] = {}
    for key in REMOTE_CONFIG_KEYS:
        if key in payload:
            clean[key] = _coerce_value(payload.get(key))
    REMOTE_HOSTS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_HOSTS_CONFIG_FILE.write_text(
        json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return clean


def get_effective_remote_config() -> dict[str, Any]:
    """Return config merged with environment as fallback."""
    stored = load_remote_config()
    effective: dict[str, Any] = {}
    for key in REMOTE_CONFIG_KEYS:
        value = stored.get(key)
        if value is None:
            env_value = os.environ.get(key)
            if env_value is not None and env_value.strip() != "":
                value = _parse_env_var(key, env_value.strip())
        effective[key] = value
    return effective
