import subprocess

import pytest

from app.routes import settings as settings_routes


def test_restart_remote_host_ok():
    host = {"id": "h1", "name": "Host 1", "repo": "/tmp/repo"}

    def run_ssh(_host, _cmd, timeout=0):
        return subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="ok", stderr="")

    result = settings_routes._restart_remote_host(host, timeout=30, run_ssh=run_ssh)
    assert result["status"] == "ok"
    assert result["name"] == "Host 1"


def test_restart_remote_host_failed():
    host = {"id": "h2", "name": "Host 2", "repo": "/tmp/repo"}

    def run_ssh(_host, _cmd, timeout=0):
        return subprocess.CompletedProcess(args=["ssh"], returncode=1, stdout="", stderr="boom")

    result = settings_routes._restart_remote_host(host, timeout=30, run_ssh=run_ssh)
    assert result["status"] == "failed"
    assert "boom" in result["message"]


def test_restart_remote_host_timeout():
    host = {"id": "h3", "name": "Host 3", "repo": "/tmp/repo"}

    def run_ssh(_host, _cmd, timeout=0):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=timeout)

    result = settings_routes._restart_remote_host(host, timeout=30, run_ssh=run_ssh)
    assert result["status"] == "unreachable"


def test_restart_all_hosts_summary(monkeypatch):
    monkeypatch.setattr(
        settings_routes,
        "_resolve_remote_hosts",
        lambda: [
            {"id": "h1", "name": "Host 1", "repo": "/tmp/repo"},
            {"id": "h2", "name": "Host 2", "repo": "/tmp/repo"},
        ],
    )

    monkeypatch.setattr(
        settings_routes,
        "_restart_remote_host",
        lambda host, timeout, run_ssh=None: {
            "id": host.get("id"),
            "name": host.get("name"),
            "status": "ok",
            "message": "ok",
        },
    )

    monkeypatch.setattr(settings_routes, "_schedule_local_full_reset", lambda *_args, **_kw: None)

    result = settings_routes.restart_application_all(settings_routes.BackgroundTasks())
    assert result["summary"]["ok"] == 2
    assert result["summary"]["scheduled"] == 1
