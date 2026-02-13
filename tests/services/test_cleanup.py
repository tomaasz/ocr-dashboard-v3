"""
Tests for app.services.cleanup module.

Tests cleanup functionality for temporary folders.
"""

import shutil
from pathlib import Path

import pytest

from app.services.cleanup import DEFAULT_CLEANUP_TARGETS, cleanup_folders


class TestCleanupFolders:
    """Test cleanup_folders function."""

    def test_cleans_jobs_folder(self, tmp_path):
        """Should clean contents of jobs folder."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "job1").mkdir()
        (jobs_dir / "job2.txt").write_text("test")

        cleaned, errors = cleanup_folders(tmp_path, ["jobs"], force=False)

        assert "jobs" in cleaned
        assert len(errors) == 0
        assert jobs_dir.exists()  # Folder itself should exist
        assert len(list(jobs_dir.iterdir())) == 0  # But be empty

    def test_cleans_logs_folder(self, tmp_path):
        """Should clean contents of logs folder."""
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "log1.txt").write_text("test log")
        (logs_dir / "subdir").mkdir()

        cleaned, errors = cleanup_folders(tmp_path, ["logs"], force=False)

        assert "logs" in cleaned
        assert len(errors) == 0
        assert len(list(logs_dir.iterdir())) == 0

    def test_cleans_ui_health_folder(self, tmp_path):
        """Should clean UI health screenshots folder."""
        ui_health_dir = tmp_path / "artifacts" / "screenshots" / "ui_health"
        ui_health_dir.mkdir(parents=True)
        (ui_health_dir / "screenshot1.png").write_text("fake image")

        cleaned, errors = cleanup_folders(tmp_path, ["ui_health"], force=False)

        assert "ui_health" in cleaned
        assert len(errors) == 0
        assert len(list(ui_health_dir.iterdir())) == 0

    def test_cleans_pycache_recursively(self, tmp_path):
        """Should clean all __pycache__ directories recursively."""
        # Create multiple __pycache__ directories
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "file.pyc").write_text("bytecode")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "__pycache__").mkdir()
        (subdir / "__pycache__" / "another.pyc").write_text("bytecode")

        cleaned, errors = cleanup_folders(tmp_path, ["pycache"], force=False)

        assert "pycache" in cleaned
        assert not (tmp_path / "__pycache__").exists()
        assert not (subdir / "__pycache__").exists()

    def test_handles_nonexistent_folder(self, tmp_path):
        """Should skip nonexistent folders without error."""
        cleaned, errors = cleanup_folders(tmp_path, ["jobs"], force=False)

        # Should not be in cleaned list since it didn't exist
        assert "jobs" not in cleaned
        assert len(errors) == 0

    def test_handles_unknown_target(self, tmp_path):
        """Should return error for unknown target."""
        cleaned, errors = cleanup_folders(tmp_path, ["unknown_folder"], force=False)

        assert "unknown_folder" not in cleaned
        assert len(errors) == 1
        assert "Nieznany folder" in errors[0]

    def test_cleans_multiple_targets(self, tmp_path):
        """Should clean multiple targets in one call."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "job1").mkdir()

        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / "log1.txt").write_text("test")

        cleaned, errors = cleanup_folders(tmp_path, ["jobs", "logs"], force=False)

        assert "jobs" in cleaned
        assert "logs" in cleaned
        assert len(errors) == 0
        assert len(list(jobs_dir.iterdir())) == 0
        assert len(list(logs_dir.iterdir())) == 0

    def test_handles_permission_errors_gracefully(self, tmp_path):
        """Should handle permission errors and continue."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        file1 = jobs_dir / "file1.txt"
        file1.write_text("test")

        # Make file read-only to simulate permission error
        file1.chmod(0o444)

        cleaned, errors = cleanup_folders(tmp_path, ["jobs"], force=False)

        # Should still report as cleaned even if some items failed
        # (depends on implementation - adjust if needed)
        assert "jobs" in cleaned or len(errors) > 0

    def test_force_parameter_accepted(self, tmp_path):
        """Should accept force parameter (even if unused)."""
        jobs_dir = tmp_path / "jobs"
        jobs_dir.mkdir()
        (jobs_dir / "job1").mkdir()

        cleaned, errors = cleanup_folders(tmp_path, ["jobs"], force=True)

        assert "jobs" in cleaned
        assert len(errors) == 0

    def test_default_cleanup_targets_defined(self):
        """Should have default cleanup targets defined."""
        assert isinstance(DEFAULT_CLEANUP_TARGETS, list)
        assert "jobs" in DEFAULT_CLEANUP_TARGETS
        assert "logs" in DEFAULT_CLEANUP_TARGETS
        assert "ui_health" in DEFAULT_CLEANUP_TARGETS

    def test_cleans_artifacts_folder(self, tmp_path):
        """Should clean artifacts folder."""
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        (artifacts_dir / "artifact1.txt").write_text("test")
        (artifacts_dir / "subdir").mkdir()

        cleaned, errors = cleanup_folders(tmp_path, ["artifacts"], force=False)

        assert "artifacts" in cleaned
        assert len(errors) == 0
        assert len(list(artifacts_dir.iterdir())) == 0

    def test_cleans_test_results_folder(self, tmp_path):
        """Should clean test-results folder."""
        test_results_dir = tmp_path / "test-results"
        test_results_dir.mkdir()
        (test_results_dir / "result1.xml").write_text("test")

        cleaned, errors = cleanup_folders(tmp_path, ["test-results"], force=False)

        assert "test-results" in cleaned
        assert len(errors) == 0
        assert len(list(test_results_dir.iterdir())) == 0
