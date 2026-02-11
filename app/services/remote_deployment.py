"""
OCR Dashboard V2 - Remote Deployment Service
Handles remote host deployment through SSH.
"""

import logging
import shlex
import subprocess
from pathlib import Path

from ..config import BASE_DIR
from ..utils.security import validate_hostname, validate_username

logger = logging.getLogger(__name__)


class RemoteDeploymentService:
    """Service for deploying setup scripts to remote hosts via SSH."""

    SCRIPT_DIR = BASE_DIR / "scripts" / "setup"

    @staticmethod
    def get_setup_script(os_type: str) -> Path | None:
        """Get path to setup script for given OS type.

        Args:
            os_type: Operating system type (ubuntu, arch, windows)

        Returns:
            Path to setup script or None if not found
        """
        scripts = {
            "ubuntu": RemoteDeploymentService.SCRIPT_DIR / "setup_ubuntu.sh",
            "arch": RemoteDeploymentService.SCRIPT_DIR / "setup_arch.sh",
            "windows": RemoteDeploymentService.SCRIPT_DIR / "setup_windows.ps1",
        }
        script_path = scripts.get(os_type.lower())
        if script_path and script_path.exists():
            return script_path
        return None

    @staticmethod
    def copy_script_to_remote(
        host: str,
        user: str,
        script_path: Path,
        ssh_opts: str = "",
        ssh_password: str | None = None,
    ) -> tuple[bool, str]:
        """Copy setup script to remote host via SCP.

        Args:
            host: Remote hostname or IP
            user: SSH username
            script_path: Local path to script
            ssh_opts: Additional SSH options
            ssh_password: SSH password (if needed)

        Returns:
            Tuple of (success, message/error)
        """
        try:
            host_addr = validate_hostname(host)
            host_user = validate_username(user)

            # Remote destination path
            remote_path = f"/tmp/{script_path.name}"

            # Build SCP command
            scp_cmd = ["scp"]
            if ssh_opts:
                scp_cmd.extend(shlex.split(ssh_opts))
            scp_cmd.extend([str(script_path), f"{host_user}@{host_addr}:{remote_path}"])

            # Execute SCP
            result = subprocess.run(
                scp_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )

            if result.returncode == 0:
                return True, remote_path
            return False, result.stderr or "SCP failed"

        except Exception as e:
            return False, str(e)

    @staticmethod
    def sync_files_to_remote(
        host: str,
        user: str,
        target_dir: str,
        ssh_opts: str = "",
    ) -> tuple[bool, str]:
        """Sync project files to remote host using rsync."""
        try:
            host_addr = validate_hostname(host)
            host_user = validate_username(user)

            # Exclude list
            excludes = [
                "venv",
                ".git",
                "__pycache__",
                ".env",
                "*.pyc",
                "logs",
                "metrics",
                "test-results",
                "artifacts",
                ".coverage",
                ".pytest_cache",
                ".mypy_cache",
            ]
            exclude_args = []
            for ex in excludes:
                exclude_args.extend(["--exclude", ex])

            # Source dir is current project dir
            source_dir = (
                str(Path(__file__).parent.parent.parent) + "/"
            )  # Trailing slash essential for rsync

            # Construct rsync command
            # rsync -avz --exclude ... <source> <user>@<host>:<target>
            # Use -e to specify ssh options, importantly StrictHostKeyChecking=no to avoid prompts
            ssh_cmd = f"ssh {ssh_opts} -o StrictHostKeyChecking=no"

            cmd = (
                ["rsync", "-avz"]
                + exclude_args
                + ["-e", ssh_cmd]
                + [source_dir, f"{host_user}@{host_addr}:{target_dir}"]
            )

            # Execute
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # Allow 5 mins for sync
                check=False,
            )

            if result.returncode != 0:
                logger.error(f"Rsync stderr: {result.stderr}")
                return False, f"Rsync failed: {result.stderr}"

            return True, result.stdout

        except Exception as e:
            logger.error(f"Error executing rsync: {e}")
            return False, str(e)

    @staticmethod
    def execute_deployment(
        host: str,
        user: str,
        os_type: str,
        config: dict,
        ssh_opts: str = "",
        ssh_password: str | None = None,
    ) -> tuple[bool, str, str]:
        """Execute deployment process."""
        try:
            # Determine if we should use local copy or git clone
            use_local_copy = False
            if not config.get("github_repo") or config.get("github_repo") == "SKIP":
                config["github_repo"] = "LOCAL"
                use_local_copy = True

            host_addr = validate_hostname(host)
            host_user = validate_username(user)

            script_path = RemoteDeploymentService.get_setup_script(os_type)
            if not script_path:
                return False, "", f"Setup script for {os_type} not found"

            # Copy script to remote
            success, remote_script_path_or_error = RemoteDeploymentService.copy_script_to_remote(
                host, user, script_path, ssh_opts, ssh_password
            )
            if not success:
                return False, "", f"Failed to copy script: {remote_script_path_or_error}"

            remote_script_path = remote_script_path_or_error

            # Prepare environment variables for script
            env_vars = []
            if config.get("github_repo"):
                env_vars.append(f"GITHUB_REPO={shlex.quote(config['github_repo'])}")
            if config.get("nas_host"):
                env_vars.append(f"NAS_HOST={shlex.quote(config['nas_host'])}")
            if config.get("nas_share"):
                env_vars.append(f"NAS_SHARE={shlex.quote(config['nas_share'])}")
            if config.get("nas_username"):
                env_vars.append(f"NAS_USERNAME={shlex.quote(config['nas_username'])}")
            if config.get("nas_password"):
                env_vars.append(f"NAS_PASSWORD={shlex.quote(config['nas_password'])}")
            if config.get("sudo_password"):
                env_vars.append(f"SUDO_PASSWORD={shlex.quote(config['sudo_password'])}")
            if config.get("install_postgres"):
                env_vars.append(f"INSTALL_POSTGRES={shlex.quote(str(config['install_postgres']))}")

            # Build execution command
            env_str = " ".join(env_vars)

            if os_type.lower() == "windows":
                # Windows: PowerShell execution
                remote_cmd = f"powershell -ExecutionPolicy Bypass -File {remote_script_path}"
                # TODO: Pass params to PSI script
            else:
                # Linux: Bash execution
                remote_cmd = f"{env_str} bash {remote_script_path}"

            ssh_cmd = ["ssh", "-tt", "-o", "StrictHostKeyChecking=no"]
            if ssh_opts:
                ssh_cmd.extend(shlex.split(ssh_opts))

            # Add host connection
            ssh_cmd.append(f"{host_user}@{host_addr}")

            # Add remote command
            ssh_cmd.append(remote_cmd)

            # Execute setup script
            logger.info(f"Executing deployment on {host}...")
            result = subprocess.run(
                ssh_cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes timeout for installation
                check=False,
            )

            stdout = result.stdout
            stderr = result.stderr
            success = result.returncode == 0

            # If setup was successful, sync files to ensure code is up to date
            # We enforce this because deployment from dashboard implies deploying current version
            if success:
                # Target dir is assumed to be $HOME/ocr-dashboard-v3 as per setup script default
                # But we should probably verify or pass it. Script default is $HOME/ocr-dashboard-v3.
                target_dir = "~/ocr-dashboard-v3/"

                logger.info(f"Syncing files to {host}:{target_dir}...")
                sync_success, sync_msg = RemoteDeploymentService.sync_files_to_remote(
                    host, user, target_dir, ssh_opts
                )

                if sync_success:
                    stdout += f"\n[SUCCESS] Project files synced successfully.\n{sync_msg}"

                    # Run post-sync installation (pip install requirements)
                    # This ensures dependencies are installed even if they were missing during initial setup script
                    logger.info(f"Running post-sync installation on {host}...")

                    # Command to activate venv and install requirements
                    # We assume target_dir is relative to HOME if it starts with ~
                    # But remote shell handles ~ expansion.
                    # We need to be robust about venv existence.
                    post_install_cmd = (
                        f"cd {target_dir} && "
                        "if [ ! -d venv ]; then python3 -m venv venv; fi && "
                        "source venv/bin/activate && "
                        "pip install --upgrade pip && "
                        "pip install -r requirements.txt && "
                        "playwright install chromium"
                    )

                    ssh_post_cmd = ["ssh", "-o", "StrictHostKeyChecking=no"]
                    if ssh_opts:
                        ssh_post_cmd.extend(shlex.split(ssh_opts))
                    ssh_post_cmd.extend([f"{host_user}@{host_addr}", post_install_cmd])

                    result_post = subprocess.run(
                        ssh_post_cmd,
                        capture_output=True,
                        text=True,
                        timeout=900,  # 15 mins for heavy pip/playwright install
                        check=False,
                    )

                    if result_post.returncode == 0:
                        stdout += f"\n[SUCCESS] Dependencies installed successfully.\n{result_post.stdout}"
                    else:
                        success = False
                        stderr += f"\n[ERROR] Dependency installation failed: {result_post.stderr}"
                        stdout += f"\nOutput: {result_post.stdout}"

                else:
                    success = False
                    stderr += f"\n[ERROR] Project file sync failed: {sync_msg}"

            return success, stdout, stderr

        except subprocess.TimeoutExpired:
            return False, "", "Deployment timed out"
        except Exception as e:
            logger.error(f"Deployment error: {e}")
            return False, "", str(e)

    @staticmethod
    def stream_deployment(
        host: str,
        user: str,
        os_type: str,
        config: dict,
        ssh_opts: str = "",
    ) -> subprocess.Popen:
        """Execute deployment and stream output.

        Args:
            host: Remote hostname or IP
            user: SSH username
            os_type: Operating system type
            config: Configuration dictionary
            ssh_opts: Additional SSH options

        Returns:
            Popen process for streaming output
        """
        host_addr = validate_hostname(host)
        host_user = validate_username(user)

        # Get script
        script_path = RemoteDeploymentService.get_setup_script(os_type)
        if not script_path:
            error_msg = f"Setup script for {os_type} not found"
            raise ValueError(error_msg)

        # Copy script to remote
        success, remote_script_path_or_error = RemoteDeploymentService.copy_script_to_remote(
            host, user, script_path, ssh_opts
        )
        if not success:
            error_msg = f"Failed to copy script: {remote_script_path_or_error}"
            raise RuntimeError(error_msg)

        remote_script_path = remote_script_path_or_error

        # Prepare environment variables
        env_vars = []
        if config.get("github_repo"):
            env_vars.append(f"GITHUB_REPO={shlex.quote(config['github_repo'])}")
        if config.get("nas_host"):
            env_vars.append(f"NAS_HOST={shlex.quote(config['nas_host'])}")
        if config.get("nas_share"):
            env_vars.append(f"NAS_SHARE={shlex.quote(config['nas_share'])}")
        if config.get("nas_username"):
            env_vars.append(f"NAS_USERNAME={shlex.quote(config['nas_username'])}")
        if config.get("nas_password"):
            env_vars.append(f"NAS_PASSWORD={shlex.quote(config['nas_password'])}")
        if config.get("sudo_password"):
            env_vars.append(f"SUDO_PASSWORD={shlex.quote(config['sudo_password'])}")
        if config.get("install_postgres"):
            env_vars.append(f"INSTALL_POSTGRES={shlex.quote(config['install_postgres'])}")

        # Build execution command
        if os_type.lower() == "windows":
            env_prefix = " ".join(f"$env:{var}" for var in env_vars)
            exec_cmd = f"powershell -ExecutionPolicy Bypass -File {remote_script_path}"
            if env_prefix:
                exec_cmd = f"{env_prefix}; {exec_cmd}"
        else:
            env_prefix = " ".join(env_vars)
            exec_cmd = f"chmod +x {remote_script_path} && {env_prefix} bash {remote_script_path}"

        # Build SSH command
        # -tt forces pseudo-terminal allocation (needed for sudo with password)
        # -o StrictHostKeyChecking=no avoids host key prompts
        ssh_cmd = ["ssh", "-tt", "-o", "StrictHostKeyChecking=no"]
        if ssh_opts:
            ssh_cmd.extend(shlex.split(ssh_opts))
        ssh_cmd.extend([f"{host_user}@{host_addr}", exec_cmd])

        # Start process for streaming
        return subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
