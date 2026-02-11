from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import process

client = TestClient(app)


def test_save_and_load_remote_hosts(tmp_path):
    """Test saving and loading the new remote hosts list configuration."""
    with patch(
        "app.services.remote_config.REMOTE_HOSTS_CONFIG_FILE", tmp_path / "remote_hosts.json"
    ):
        payload = {
            "OCR_REMOTE_RUN_ENABLED": True,
            "OCR_REMOTE_HOSTS_LIST": [
                {
                    "id": "test_host_1",
                    "name": "Test Host",
                    "host": "127.0.0.1",
                    "roles": {"worker": True},
                }
            ],
        }

        # Save configuration
        response = client.post("/api/settings/remote-hosts", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["config"]["OCR_REMOTE_RUN_ENABLED"] is True
        assert len(data["config"]["OCR_REMOTE_HOSTS_LIST"]) == 1

        # Load configuration
        response = client.get("/api/settings/remote-hosts")
        assert response.status_code == 200
        data = response.json()
        config = data["config"]
        assert config["OCR_REMOTE_RUN_ENABLED"] is True
        assert len(config["OCR_REMOTE_HOSTS_LIST"]) == 1
        assert config["OCR_REMOTE_HOSTS_LIST"][0]["id"] == "test_host_1"


def test_start_profile_with_remote_host():
    """Test that starting a profile passes the remote_host_id to the service layer."""
    with patch("app.services.process.start_profile_process") as mock_start:
        mock_start.return_value = (True, "Profile started successfully")

        profile_name = "test_profile_abc"
        payload = {"remote_host_id": "host_12345", "execution_mode": "wsl_worker", "headed": False}

        response = client.post(f"/api/profile/{profile_name}/start", json=payload)

        assert response.status_code == 200
        assert response.json()["success"] is True

        mock_start.assert_called_once()
        call_args = mock_start.call_args
        assert call_args[0][0] == profile_name
        kwargs = call_args[1]
        assert kwargs["config"]["remote_host_id"] == "host_12345"
        assert kwargs["config"]["execution_mode"] == "wsl_worker"


def test_apply_remote_hosts_env_logic():
    """Test the logic for applying remote host environment variables."""
    mock_hosts_list = [
        {
            "id": "host_1",
            "host": "192.168.1.100",
            "user": "ubuntu",
            "ssh_opts": "-p 22",
            "repo_dir": "/home/ubuntu/repo",
            "roles": {"worker": True},
        },
        {"id": "host_2", "host": "10.0.0.5", "user": "admin"},
    ]

    with patch("app.services.process.get_effective_remote_config") as mock_config:
        mock_config.return_value = {
            "OCR_REMOTE_HOSTS_LIST": mock_hosts_list,
            "OCR_REMOTE_HOST": "fallback_host",
        }

        # Test Case 1: Apply host_1
        env1 = {}
        process._apply_remote_hosts_env(env1, remote_host_id="host_1")
        # HOST_ENV_MAPPING maps 'host' -> 'OCR_REMOTE_HOST', 'user' -> 'OCR_REMOTE_USER' etc.
        # Assuming mapping is correctly defined in process.py (which we verified)
        assert env1.get("OCR_REMOTE_HOST") == "192.168.1.100"
        assert env1.get("OCR_REMOTE_USER") == "ubuntu"
        assert env1.get("OCR_REMOTE_REPO_DIR") == "/home/ubuntu/repo"
        assert env1.get("OCR_REMOTE_SSH_OPTS") == "-p 22"
        assert env1.get("OCR_REMOTE_RUN_ENABLED") == "1"

        # Test Case 2: Apply host_2 (partial)
        env2 = {}
        process._apply_remote_hosts_env(env2, remote_host_id="host_2")
        assert env2.get("OCR_REMOTE_HOST") == "10.0.0.5"
        assert env2.get("OCR_REMOTE_USER") == "admin"
        assert "OCR_REMOTE_REPO_DIR" not in env2

        # Test Case 3: Invalid ID
        env3 = {}
        process._apply_remote_hosts_env(env3, remote_host_id="unknown_host")
        # Should rely on fallback or remain empty?
        # Logic says: "if remote_host_id is not None: ... if selected: apply ... return"
        # If selected is None (not found), it falls through to "Fallback: Apply global configuration"
        # So it should apply global config.
        assert env3.get("OCR_REMOTE_HOST") == "fallback_host"
