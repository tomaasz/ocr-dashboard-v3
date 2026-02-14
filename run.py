import os
import sys
import time
from pathlib import Path

# Only Gemini engine is supported.
from ocr_engine.ocr.engine.gemini_engine import GeminiEngine

# Constant for restart signal
EXIT_CODE_RESTART = 100

# Automatyczne dodanie src do PYTHONPATH, żebyś nie musiał pamiętać o 'export'
sys.path.append(str(Path.cwd() / "src"))


def main():
    # Pobieranie konfiguracji ze zmiennych (z domyślnymi wartościami)
    batch_id = os.environ.get("OCR_BATCH_ID") or f"batch_{time.strftime('%Y%m%d_%H%M%S')}"
    is_headed = os.environ.get("OCR_HEADED", "0") == "1"
    profile_suffix = os.environ.get("OCR_PROFILE_SUFFIX", "(domyślny)")
    engine_type = "gemini"

    print("=" * 60)
    print(" OCR RUNNER V2")
    print("=" * 60)
    print(f" Engine:        {engine_type}")
    print(f" Batch ID:      {batch_id}")
    print(f" Tryb okienkowy:{is_headed}")
    print(f" Profil:        {profile_suffix}")
    print("-" * 60)

    # Run loop for auto-restart support
    while True:
        engine = GeminiEngine(
            job_dir=f"jobs/{batch_id}",
            prompt_id="raw_ocr",
            headed=is_headed,
        )
        exit_code = engine.run()

        if exit_code == EXIT_CODE_RESTART:
            print("🔄 [System] Restart signal received (browser crash). Restarting engine in 5s...")
            time.sleep(5)
            continue

        sys.exit(exit_code)


if __name__ == "__main__":
    main()
