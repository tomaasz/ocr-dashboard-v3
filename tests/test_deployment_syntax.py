import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.remote_deployment import RemoteDeploymentService


class TestRemoteDeployment(unittest.TestCase):
    @patch("app.services.remote_deployment.subprocess.run")
    @patch("app.services.remote_deployment.RemoteDeploymentService.get_setup_script")
    @patch("app.services.remote_deployment.RemoteDeploymentService.copy_script_to_remote")
    def test_windows_command_generation(self, mock_copy, mock_get_script, mock_run):
        # Setup mocks
        mock_get_script.return_value = Path("setup_windows.ps1")
        mock_copy.return_value = (True, "/tmp/setup_windows.ps1")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = {"github_repo": "https://github.com/test/repo", "nas_host": "192.168.1.100"}

        # Run execution
        RemoteDeploymentService.execute_deployment(
            host="192.168.1.10", user="testuser", os_type="windows", config=config
        )

        # Get all arguments passed to subprocess.run across all calls
        all_calls = mock_run.call_args_list
        found_execution_command = False

        for call in all_calls:
            call_args = call[0][0]
            full_command = " ".join(call_args)

            # Check if this is the execution command (contains powershell invocation)
            if "powershell -ExecutionPolicy Bypass -File /tmp/setup_windows.ps1" in full_command:
                found_execution_command = True
                self.assertIn("$env:GITHUB_REPO='https://github.com/test/repo'", full_command)
                self.assertIn("$env:NAS_HOST='192.168.1.100'", full_command)

        self.assertTrue(found_execution_command, "Execution command not found in subprocess calls")

    @patch("app.services.remote_deployment.subprocess.run")
    @patch("app.services.remote_deployment.RemoteDeploymentService.get_setup_script")
    @patch("app.services.remote_deployment.RemoteDeploymentService.copy_script_to_remote")
    def test_linux_command_generation(self, mock_copy, mock_get_script, mock_run):
        # Setup mocks
        mock_get_script.return_value = Path("setup_ubuntu.sh")
        mock_copy.return_value = (True, "/tmp/setup_ubuntu.sh")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        config = {"github_repo": "https://github.com/test/repo"}

        # Run execution
        RemoteDeploymentService.execute_deployment(
            host="192.168.1.10", user="testuser", os_type="ubuntu", config=config
        )

        all_calls = mock_run.call_args_list
        found_execution_command = False

        for call in all_calls:
            call_args = call[0][0]
            full_command = " ".join(call_args)

            if "bash /tmp/setup_ubuntu.sh" in full_command:
                found_execution_command = True
                self.assertIn("GITHUB_REPO=https://github.com/test/repo", full_command)

        self.assertTrue(
            found_execution_command, "Linux execution command not found in subprocess calls"
        )


if __name__ == "__main__":
    unittest.main()
