from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ocr_engine.db.client import DbClient
from ocr_engine.worker.pipeline_exec import run_pipeline_jobdir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerConfig:
    db_dsn: str | None
    poll_interval: float
    run_once: bool


def process_one(db: DbClient) -> bool:
    """
    Claim next job, run pipeline, mark DONE/FAILED.
    Returns True if job was processed, False if no job.
    """
    claimed = db.claim_next_job()
    if not claimed:
        return False

    # Check job_dir
    if not claimed.job_dir:
        db.mark_failed(claimed.job_id, "missing job_dir in job record")
        return True

    job_dir = Path(claimed.job_dir)
    if not job_dir.exists():
        db.mark_failed(claimed.job_id, f"job_dir does not exist: {job_dir}")
        return True

    # Run pipeline
    result = run_pipeline_jobdir(job_dir)

    if result.success:
        db.mark_done(claimed.job_id)
    else:
        # Truncate error to 4000 chars
        error_msg = f"Pipeline failed (exit {result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        error_msg = error_msg[:4000]
        db.mark_failed(claimed.job_id, error_msg)

    return True


def run_worker(config: WorkerConfig) -> int:
    """
    Worker loop: poll for jobs and process them.
    - --once: process one job and exit
    - --poll: poll every N seconds
    Returns 0 on success.
    """
    try:
        db = DbClient(dsn=config.db_dsn)
    except Exception as e:
        logger.critical(f"Failed to initialize DB client: {e}")
        return 1

    while True:
        try:
            processed = process_one(db)

            if config.run_once:
                return 0

            if not processed:
                time.sleep(config.poll_interval)

        except KeyboardInterrupt:
            logger.info("Worker stopped by user")
            return 0
        except Exception as e:
            logger.error(f"Worker loop error: {e}", exc_info=True)
            if config.run_once:
                return 1
            time.sleep(config.poll_interval)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="ocr-worker",
        description="OCR job queue worker",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one job and exit",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=5.0,
        help="Poll interval in seconds (default: 5.0)",
    )

    args = parser.parse_args(argv)

    config = WorkerConfig(
        db_dsn=None,  # Use DATABASE_URL env
        poll_interval=args.poll,
        run_once=args.once,
    )

    logger.info(f"Starting worker (poll={config.poll_interval}s, once={config.run_once})")
    return run_worker(config)


if __name__ == "__main__":
    raise SystemExit(main())
