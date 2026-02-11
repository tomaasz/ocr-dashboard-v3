#!/usr/bin/env python3
"""
Image Preprocessing Worker (prep_worker.py)

Separate process that prepares images for OCR workers.
- Monitors source folder for images
- Prepares only workers_count images ahead
- On restart: clears old preprocessed images
- Communicates via file-based queue

Usage:
    python prep_worker.py --source /path/to/scans --job-dir /path/to/job --workers 3
"""

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from ocr_engine.ocr.engine.image_processor import clear_temp_images, preprocess_image_smart

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PrepWorker] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


class PrepWorker:
    """Preprocesses images for OCR workers."""

    def __init__(
        self,
        source_dir: Path,
        job_dir: Path,
        workers_count: int = 3,
        check_interval: float = 1.0,
    ):
        self.check_interval = check_interval

        # Security: Validate paths to prevent traversal
        # Security: Validate paths to prevent traversal (String-based for robustness)
        cwd = os.path.abspath(os.getcwd())
        source_path_abs = os.path.abspath(str(source_dir))
        job_path_abs = os.path.abspath(str(job_dir))

        if os.path.commonpath([cwd, source_path_abs]) != cwd:
            raise ValueError(
                f"Security violation: Source path '{source_path_abs}' must be within project directory '{cwd}'"
            )

        if os.path.commonpath([cwd, job_path_abs]) != cwd:
            raise ValueError(
                f"Security violation: Job path '{job_path_abs}' must be within project directory '{cwd}'"
            )

        self.source_dir = Path(source_path_abs)
        self.job_dir = Path(job_path_abs)

        self.prep_queue_dir = job_dir / "ocr" / "prep_queue"
        self.temp_dir = job_dir / "ocr" / "temp_images"
        self.status_file = job_dir / "ocr" / "prep_status.json"
        self.progress_file = job_dir / "ocr" / "prep_progress.json"

        self.running = True
        self.processed_files: set[str] = set()  # Already preprocessed
        self.in_flight: set[str] = set()  # Currently being preprocessed

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received shutdown signal. Cleaning up...")
        self.running = False

    def _setup_dirs(self):
        """Create necessary directories."""
        self.prep_queue_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def _clear_old_queue(self):
        """Clear old preprocessed images on startup."""
        if self.prep_queue_dir.exists():
            count = 0
            for f in self.prep_queue_dir.iterdir():
                try:
                    f.unlink()
                    count += 1
                except Exception:
                    pass
            if count > 0:
                logger.info(f"🗑️ Cleared {count} old preprocessed images")

        # Also clear temp directory
        clear_temp_images(self.temp_dir)

    def _load_progress(self):
        """Load progress from previous run."""
        try:
            if self.progress_file.exists():
                data = json.loads(self.progress_file.read_text())
                self.processed_files = set(data.get("processed", []))
                logger.info(f"📝 Loaded progress: {len(self.processed_files)} files already done")
        except Exception as e:
            logger.warning(f"Could not load progress: {e}")

    def _save_progress(self):
        """Save progress to file."""
        try:
            data = {
                "processed": list(self.processed_files),
                "updated_at": datetime.now().isoformat(),
            }
            self.progress_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _get_source_files(self) -> list[Path]:
        """Get all image files from source directory."""
        extensions = (".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".bmp")
        files = []
        for ext in extensions:
            files.extend(self.source_dir.glob(f"*{ext}"))
            files.extend(self.source_dir.glob(f"*{ext.upper()}"))
        return sorted(files, key=lambda x: x.name)

    def _get_queue_count(self) -> int:
        """Count images currently in prep queue."""
        return len(list(self.prep_queue_dir.glob("*")))

    def _get_done_files(self) -> set[str]:
        """Get files that have been processed by OCR (from progress file)."""
        done_file = self.job_dir / "ocr" / "ocr_done.json"
        try:
            if done_file.exists():
                data = json.loads(done_file.read_text())
                return set(data.get("done", []))
        except Exception:
            pass
        return set()

    def _write_status(self, status: str, stage: str = ""):
        """Write current status to file."""
        try:
            data = {
                "status": status,
                "stage": stage,
                "queue_size": self._get_queue_count(),
                "preprocessed": len(self.processed_files),
                "updated_at": datetime.now().isoformat(),
                "pid": os.getpid(),
            }
            self.status_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def _preprocess_file(self, source_file: Path) -> Path | None:
        """Preprocess single file and move to queue."""
        try:
            logger.info(f"🖼️ Preprocessing: {source_file.name}")

            # Preprocess
            output_path = preprocess_image_smart(source_file, self.temp_dir)

            if output_path and output_path.exists():
                # Move to queue
                queue_path = self.prep_queue_dir / output_path.name
                shutil.move(str(output_path), str(queue_path))
                logger.info(f"✅ Ready: {source_file.name} -> {queue_path.name}")
                return queue_path
            logger.warning(f"⚠️ Preprocessing returned no file: {source_file.name}")
            return None

        except Exception as e:
            logger.error(f"❌ Failed to preprocess {source_file.name}: {e}")
            return None

    def run(self):
        """Main loop."""
        logger.info("🚀 Starting PrepWorker")
        logger.info(f"   Source: {self.source_dir}")
        logger.info(f"   Queue:  {self.prep_queue_dir}")
        logger.info(f"   Workers: {self.workers_count}")

        self._setup_dirs()
        self._clear_old_queue()
        self._load_progress()
        self._write_status("RUNNING", "starting")

        last_save_ts = time.time()

        while self.running:
            try:
                # Get current state
                queue_count = self._get_queue_count()
                done_by_ocr = self._get_done_files()

                # Need to keep workers_count images ahead
                needed = self.workers_count - queue_count

                if needed <= 0:
                    # Queue full, wait
                    time.sleep(self.check_interval)
                    continue

                # Get all source files
                all_files = self._get_source_files()

                # Find files to preprocess (not in processed, not done by OCR)
                to_preprocess = []
                for f in all_files:
                    if f.name in self.processed_files:
                        continue
                    if f.name in done_by_ocr:
                        continue
                    if f.name in self.in_flight:
                        continue
                    to_preprocess.append(f)
                    if len(to_preprocess) >= needed:
                        break

                if not to_preprocess:
                    # All done or queue full
                    if len(self.processed_files) >= len(all_files):
                        logger.info("✅ All files preprocessed. Waiting for new files...")
                        time.sleep(5)
                    else:
                        time.sleep(self.check_interval)
                    continue

                # Preprocess needed files
                for source_file in to_preprocess:
                    if not self.running:
                        break

                    self.in_flight.add(source_file.name)
                    result = self._preprocess_file(source_file)
                    self.in_flight.discard(source_file.name)

                    if result:
                        self.processed_files.add(source_file.name)

                    # Save progress periodically
                    if time.time() - last_save_ts > 30:
                        self._save_progress()
                        last_save_ts = time.time()

                self._write_status("RUNNING", "processing")

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(1)

        # Cleanup on exit
        self._save_progress()
        self._write_status("STOPPED", "shutdown")
        logger.info("👋 PrepWorker stopped")


def main():
    parser = argparse.ArgumentParser(description="Image Preprocessing Worker")
    parser.add_argument("--source", required=True, help="Source directory with scans")
    parser.add_argument("--job-dir", required=True, help="Job directory for OCR")
    parser.add_argument("--workers", type=int, default=3, help="Number of OCR workers")
    parser.add_argument("--interval", type=float, default=1.0, help="Check interval in seconds")

    args = parser.parse_args()

    # Security: Validate paths to prevent traversal
    cwd = os.path.abspath(os.getcwd())
    source_path_abs = os.path.abspath(args.source)
    job_path_abs = os.path.abspath(args.job_dir)

    if os.path.commonpath([cwd, source_path_abs]) != cwd:
        logger.error(
            f"❌ Security violation: Source path '{source_path_abs}' must be within project directory '{cwd}'"
        )
        return

    if os.path.commonpath([cwd, job_path_abs]) != cwd:
        logger.error(
            f"❌ Security violation: Job path '{job_path_abs}' must be within project directory '{cwd}'"
        )
        return

    worker = PrepWorker(
        source_dir=Path(source_path),
        job_dir=Path(job_path),
        workers_count=args.workers,
        check_interval=args.interval,
    )
    worker.run()


if __name__ == "__main__":
    main()
