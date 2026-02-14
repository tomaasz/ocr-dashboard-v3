"""Tests for remote deployment service SCP option handling."""

from pathlib import Path
from unittest.mock import patch

from app.services.remote_deployment import RemoteDeploymentService


class TestCopyScriptToRemote:
    """Test SCP command generation for deployment script upload."""

    @patch("app.services.remote_deployment.subprocess.run")
    def test_converts_ssh_port_flag_for_scp(self, mock_run):
        """Should convert '-p 22' (ssh style) to '-P 22' (scp style)."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        ok, _ = RemoteDeploymentService.copy_script_to_remote(
            host="1.2.3.4",
            user="tester",
            script_path=Path("/tmp/setup.sh"),
            ssh_opts="-p 22 -i ~/.ssh/id_ed25519",
        )

        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[:5] == ["scp", "-P", "22", "-i", "~/.ssh/id_ed25519"]

    @patch("app.services.remote_deployment.subprocess.run")
    def test_converts_compact_port_flag_for_scp(self, mock_run):
        """Should convert compact '-p2222' to '-P2222' for scp."""
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        ok, _ = RemoteDeploymentService.copy_script_to_remote(
            host="1.2.3.4",
            user="tester",
            script_path=Path("/tmp/setup.sh"),
            ssh_opts="-p2222",
        )

        assert ok is True
        cmd = mock_run.call_args[0][0]
        assert cmd[0:2] == ["scp", "-P2222"]
