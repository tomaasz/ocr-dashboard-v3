"""Tests for remote host settings helpers/endpoints."""

import os
import subprocess
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_browse_directory_supports_unsaved_host_payload_linux():
    """Should browse remote directories using direct host/user params."""
    ls_output = (
        "total 4\n"
        "drwxr-xr-x 2 root root 4096 Feb 14 10:00 folder1\n"
        "-rw-r--r-- 1 root root 12 Feb 14 10:01 file1.txt\n"
    )

    with patch("app.routes.settings.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ssh"],
            returncode=0,
            stdout=ls_output,
            stderr="",
        )

        response = client.get(
            "/api/browse-directory",
            params={
                "host": "127.0.0.1",
                "user": "root",
                "ssh_opts": "-p 22",
                "path": "/tmp",
                "os_type": "linux",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["path"] == "/tmp"
    assert any(item["name"] == "folder1" and item["is_dir"] for item in data["items"])
    assert any(item["name"] == "file1.txt" and not item["is_dir"] for item in data["items"])


def test_remote_host_test_endpoint_reports_field_checks():
    """Should run SSH checks and return aggregated test status."""
    with patch("app.routes.settings.subprocess.run") as mock_run:
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="OK\n", stderr=""),
            subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="OK\n", stderr=""),
            subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="OK\n", stderr=""),
            subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="OK\n", stderr=""),
            subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="OK\n", stderr=""),
        ]

        response = client.post(
            "/api/settings/remote-host/test",
            json={
                "host": "127.0.0.1",
                "user": "root",
                "ssh_opts": "-p 22",
                "repo": "/app/ocr-dashboard",
                "python": "/app/venv/bin/python3",
                "profile_root": "/home/root/.cache/profiles",
                "chrome_bin": "/usr/bin/google-chrome",
                "os_type": "linux",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["checks"]["ssh"]["ok"] is True
    assert data["checks"]["repo"]["ok"] is True
    assert data["checks"]["python"]["ok"] is True
    assert data["checks"]["profile_root"]["ok"] is True
    assert data["checks"]["chrome_bin"]["ok"] is True


def test_local_remote_host_defaults_endpoint(tmp_path):
    """Should return localhost-derived defaults for host modal fields."""
    repo_dir = tmp_path / "repo"
    cache_dir = tmp_path / "cache"
    venv_python = repo_dir / "venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    with (
        patch("app.routes.settings.BASE_DIR", repo_dir),
        patch("app.routes.settings.CACHE_DIR", cache_dir),
        patch("app.routes.settings.shutil.which", return_value="/usr/bin/google-chrome"),
        patch.dict(os.environ, {"USER": "tester"}, clear=False),
    ):
        response = client.get("/api/settings/remote-host/local-defaults")

    assert response.status_code == 200
    data = response.json()
    assert data["repo"] == str(repo_dir)
    assert data["python"] == str(venv_python)
    assert data["profile_root"] == str(cache_dir)
    assert data["chrome_bin"] == "/usr/bin/google-chrome"
    assert data["user"] == "tester"
    assert data["ssh_opts"] == "-p 22"
    assert data["os_type"] == "linux"
