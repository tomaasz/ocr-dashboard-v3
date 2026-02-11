from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PwError
from playwright.sync_api import TimeoutError as PwTimeoutError
from playwright.sync_api import sync_playwright

from .base import BaseOcrEngine
from .models import EngineCaps, OcrResult, OcrStage


@dataclass(frozen=True)
class _SelectedImage:
    abs_path: Path
    rel_path: str
    kind: str  # "crop" | "input" | "scan"


class PlaywrightEngine(BaseOcrEngine):
    """
    MVP: real Playwright + real upload do lokalnej strony mock_ocr.html.

    ZASADA:
    - engine wybiera obraz w kolejności:
      1) job_dir/crops/crop_<entry_id>.png
      2) job_dir/input/*.(jpg|jpeg|png|webp)
      3) job_dir/scan.* (pierwszy znaleziony)
    - odpala Playwright, ładuje mock_ocr.html, robi set_input_files, czeka na preview+result
    - zwraca OcrResult(parse_ok=True, raw_response_text=...)
    """

    @property
    def name(self) -> str:
        return "playwright_mock_mvp"

    @property
    def caps(self) -> EngineCaps:
        return EngineCaps(
            supports_stage1=True,
            supports_stage2=True,
            supports_stage3=False,
            supports_chat_rotation=False,
            supports_upload_watchdog=False,
        )

    def _select_image(self, job_dir: Path, entry_id: str) -> _SelectedImage:
        # 1) crop
        crop = job_dir / "crops" / f"crop_{entry_id}.png"
        if crop.exists():
            return _SelectedImage(crop, crop.relative_to(job_dir).as_posix(), "crop")

        # 2) input/*
        input_dir = job_dir / "input"
        if input_dir.exists():
            exts = {".jpg", ".jpeg", ".png", ".webp"}
            files = sorted(
                [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
            )
            if files:
                p = files[0]
                return _SelectedImage(p, p.relative_to(job_dir).as_posix(), "input")

        # 3) scan.*
        scans = sorted([p for p in job_dir.iterdir() if p.is_file() and p.name.startswith("scan.")])
        if scans:
            p = scans[0]
            return _SelectedImage(p, p.relative_to(job_dir).as_posix(), "scan")

        raise FileNotFoundError(
            f"No image found for entry_id={entry_id}. "
            f"Expected one of: crops/crop_{entry_id}.png, input/*.(jpg|jpeg|png|webp), scan.*"
        )

    def _artifact_path(self, job_dir: Path, entry_id: str, stage: OcrStage, suffix: str) -> Path:
        rel = Path("ocr") / "artifacts" / f"{entry_id}_{stage.value}{suffix}"
        return job_dir / rel

    def run_entry(
        self,
        job_dir: Path,
        entry_id: str,
        stage: OcrStage = OcrStage.STAGE1_RAW_AND_CLASSIFY,
    ) -> OcrResult:
        self.ensure_job_layout(job_dir)

        selected = self._select_image(job_dir, entry_id)

        # gdzie jest mock page w repo
        # src/ocr_engine/ocr/engine/mock/mock_ocr.html
        mock_html = Path(__file__).parent / "mock" / "mock_ocr.html"
        if not mock_html.exists():
            raise FileNotFoundError(f"Missing mock OCR page: {mock_html}")

        artifacts: dict[str, str] = {
            "input_image": selected.rel_path,
        }

        # Artefakty diagnostyczne
        screenshot_path = self._artifact_path(job_dir, entry_id, stage, "_mock_screenshot.png")
        raw_path = self._artifact_path(job_dir, entry_id, stage, "_mock_raw.txt")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                page = browser.new_page()

                page.goto(mock_html.resolve().as_uri(), wait_until="load")

                # realny upload
                page.set_input_files("input[type='file']#file", str(selected.abs_path))

                # deterministyczny sygnał sukcesu
                page.locator("#preview").wait_for(state="visible", timeout=10_000)
                # KANON: czekamy na konkretny tekst sukcesu, a nie tylko na widoczność kontenera
                page.locator("text=MOCK_OCR_OK").wait_for(state="visible", timeout=10_000)

                raw = page.locator("#result").inner_text(timeout=5_000)

                # screenshot po sukcesie (przyda się do debug)
                try:
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_path), full_page=True)
                except Exception:
                    pass

                browser.close()

            raw_path.write_text(raw, encoding="utf-8")
            artifacts["mock_screenshot"] = screenshot_path.relative_to(job_dir).as_posix()
            artifacts["raw_response"] = raw_path.relative_to(job_dir).as_posix()

            return OcrResult(
                entry_id=entry_id,
                stage=stage,
                parse_ok=True,
                raw_response_text=raw,
                data={"engine": "mock", "image_kind": selected.kind},
                artifacts=artifacts,
            )

        except (PwTimeoutError, PwError) as e:
            # screenshot na fail (jeśli się uda)
            try:
                # Jeśli page jest dostępna, próbujemy
                pass  # W tym bloku trudno o dostęp do `page` jeśli wyszliśmy z context managera lub on padł.
                # Ale w MVP/mock engine zakładamy, że jeśli poleciał wyjątek z with sync_playwright,
                # to screenshot może być niemożliwy.
            except Exception:
                pass

            msg = f"Playwright error: {e}"
            raw_path.write_text(msg, encoding="utf-8")
            artifacts["raw_response"] = raw_path.relative_to(job_dir).as_posix()

            return OcrResult(
                entry_id=entry_id,
                stage=stage,
                parse_ok=False,
                raw_response_text=msg,
                data={"engine": "mock", "image_kind": selected.kind},
                artifacts=artifacts,
            )
