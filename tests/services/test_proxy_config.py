"""Tests for proxy config loading behavior."""

import json
import os

from src.ocr_engine.ocr.engine.proxy_config import load_proxy_config


def test_load_proxy_config_global_disable_returns_none(tmp_path):
    """Should ignore all proxy sources when OCR_PROXY_DISABLED is enabled."""
    proxies_file = tmp_path / "proxies.json"
    proxies_file.write_text(
        json.dumps({"proxies": {"profile1": {"server": "http://proxy1:8080"}}}),
        encoding="utf-8",
    )

    env_vars = {
        "OCR_PROXY_DISABLED": "1",
        "OCR_PROXY_SERVER": "http://env-proxy:8080",
        "OCR_PROXY_USERNAME": "user123",
        "OCR_PROXY_PASSWORD": "pass123",
    }

    old_env = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env_vars)
        result = load_proxy_config("profile1", proxies_file)
    finally:
        os.environ.clear()
        os.environ.update(old_env)

    assert result is None
