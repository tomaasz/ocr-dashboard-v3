from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_host_load_includes_local_chrome_count():
    with patch("app.routes.dashboard.profile_service.list_profiles", return_value=[]), \
        patch("app.routes.dashboard.process_service.get_profile_pids", return_value=[]), \
        patch("app.routes.dashboard.process_service.pid_is_running", return_value=False), \
        patch("app.routes.dashboard.process_service.iter_runpy_processes", return_value=[]), \
        patch("app.routes.dashboard.execute_query", return_value=[]), \
        patch("app.routes.dashboard.get_effective_remote_config", return_value={"OCR_REMOTE_HOSTS_LIST": []}), \
        patch("app.routes.dashboard._get_local_system_stats", return_value={
            "cpu_percent": 0.0,
            "memory_percent": 0.0,
            "memory_used_gb": 0.0,
            "memory_total_gb": 0.0,
            "available": True,
        }), \
        patch("app.routes.dashboard._get_local_top_processes", return_value=[]), \
        patch("app.routes.dashboard._get_local_chrome_process_count", return_value=7):
        response = client.get("/api/host-load")

    assert response.status_code == 200
    data = response.json()
    assert "hosts" in data
    localhost = data["hosts"][0]
    assert localhost["id"] == "localhost"
    assert localhost["chrome_process_count"] == 7


def test_local_chrome_process_count_includes_playwright_browsers():
    class Dummy:
        def __init__(self, stdout: str, returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    output = "\n".join(
        [
            "chrome",
            "chromium",
            "chrome-headless",
            "chrome-headless-shell",
            "google-chrome",
            "msedge",
            "firefox",
            "WebKitWebProcess",
            "WebKitNetworkProcess",
            "OtherProcess",
        ]
    )

    with patch("app.routes.dashboard.subprocess.run", return_value=Dummy(output)):
        from app.routes.dashboard import _get_local_chrome_process_count

        assert _get_local_chrome_process_count() == 9


def test_kill_host_browsers_local():
    with patch("app.routes.dashboard._kill_local_browser_processes", return_value={
        "before": 4,
        "after": 1,
        "killed_estimate": 3,
    }), patch("app.routes.dashboard.get_effective_remote_config", return_value={"OCR_REMOTE_HOSTS_LIST": []}):
        response = client.post("/api/host-browsers/kill", json={"host_id": "localhost"})

    assert response.status_code == 200
    data = response.json()
    assert data["host_id"] == "localhost"
    assert data["status"] == "ok"
    assert data["killed_estimate"] == 3
