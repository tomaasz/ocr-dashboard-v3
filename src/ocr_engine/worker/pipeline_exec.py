from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


def run_pipeline_jobdir(job_dir: Path) -> PipelineResult:
    """
    Run pipeline via subprocess: python -m ocr_engine.pipeline.run <job_dir>
    Returns PipelineResult with returncode, stdout, stderr.
    No prints (stable for tests).
    """
    cmd = [
        sys.executable,
        "-m",
        "ocr_engine.pipeline.run",
        str(job_dir),
    ]

    # S603: trusted input (job_dir is typed Path)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,  # We handle returncode manually
    )

    return PipelineResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )
