from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ocr_engine.ocr.engine import OcrStage, PlaywrightEngine
from ocr_engine.pipeline.status import LastError, write_status


def run(job_dir: Path) -> int:
    """
    Minimalny runner pipeline.
    - ładuje job.json
    - uruchamia stub engine
    - zapisuje status techniczny do ocr/status.json
    - wypisuje deterministyczne podsumowanie
    """
    engine = PlaywrightEngine()

    # status: RUNNING
    write_status(job_dir, technical_state="RUNNING", engine_name=engine.name)

    try:
        results = engine.run_job(
            job_dir,
            stages=[OcrStage.STAGE1_RAW_AND_CLASSIFY],
        )
    except Exception as e:
        write_status(
            job_dir,
            technical_state="FAILED",
            engine_name=engine.name,
            last_error=LastError(type=type(e).__name__, message=str(e)),
        )
        print(f"[PIPELINE][ERROR] {e}", file=sys.stderr)
        return 2

    # status: DONE
    write_status(
        job_dir,
        technical_state="DONE",
        engine_name=engine.name,
        results_count=len(results),
    )

    print(f"[PIPELINE] job_dir={job_dir}")
    print(f"[PIPELINE] entries={len(results)}")

    for r in results:
        print(
            "[ENTRY]"
            f" entry_id={r.entry_id}"
            f" stage={r.stage.value}"
            f" parse_ok={r.parse_ok}"
            f" artifacts={list(r.artifacts.keys())}"
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ocr-pipeline-runner",
        description="Run OCR pipeline for given job directory",
    )
    parser.add_argument(
        "job_dir",
        help="Path to jobs/<job_id>/ directory",
    )

    args = parser.parse_args(argv)
    job_dir = Path(args.job_dir).resolve()

    if not job_dir.exists():
        print(f"[PIPELINE][ERROR] job_dir does not exist: {job_dir}", file=sys.stderr)
        return 1

    return run(job_dir)


if __name__ == "__main__":
    raise SystemExit(main())
