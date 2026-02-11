"""
UI Health Checker for Gemini Web OCR.

Detects layout changes by verifying presence of critical UI elements.
Provides diagnostic screenshots and detailed logging when elements are missing.
"""

import logging
import time
from pathlib import Path

from playwright.sync_api import Page

logger = logging.getLogger(__name__)


class UIHealthChecker:
    """Monitors Gemini UI integrity and detects layout changes."""

    # Critical elements required for OCR operation
    CRITICAL_ELEMENTS = {
        "composer": [
            "div[contenteditable='true']",
            "div[role='textbox']",
            "textarea[placeholder*='Gemini']",
            "textarea[placeholder*='Wpisz prompta']",
            "textarea[placeholder*='Type a prompt']",
        ],
        "send_button": [
            "button[aria-label*='Wyślij wiadomość' i]",
            "button[aria-label*='Send']",
            "button[aria-label*='Wyślij']",
            "button[type='submit']",
            "button[data-testid*='send' i]",
            "button:has(svg[aria-label*='Send' i])",
            "button:has(svg[aria-label*='Wyślij' i])",
        ],
        "model_selector": [
            "[data-test-id='bard-mode-menu-button']",
            "[data-test-id='logo-pill-label-container']",
            "button.input-area-switch",
            "div[role='group'][aria-label*='selektor trybu' i]",
            "div[role='group'][aria-label*='mode selector' i]",
            "[data-test-id='model-selector']",
        ],
        "new_chat": [
            "button:has-text('Nowy')",
            "button:has-text('New')",
            "button[aria-label*='New chat']",
            "button[aria-label*='Nowy czat' i]",
            "button[aria-label*='Nowa rozmowa' i]",
            "a[aria-label*='New chat' i]",
            "a[aria-label*='Nowy czat' i]",
        ],
        "upload_button": [
            "button[aria-label*='Upload']",
            "button[aria-label*='Prześlij']",
            "button[aria-label*='Otwórz menu przesyłania pliku' i]",
            "button[aria-label*='Dodaj pliki' i]",
            "button[aria-label*='Dodaj' i]",
            "button[aria-label*='Add' i]",
            "button[aria-label*='Załącz' i]",
            "button[aria-label*='Attach' i]",
            "button:has-text('Narzędzia')",
            "button:has-text('Tools')",
            "input[type='file']",
        ],
    }

    def __init__(self, screenshot_dir: Path | None = None):
        """
        Initialize UI health checker.

        Args:
            screenshot_dir: Directory for diagnostic screenshots
        """
        self.screenshot_dir = screenshot_dir or Path("artifacts/screenshots/ui_health")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def check_ui_integrity(self, page: Page, timeout_ms: int = 2000) -> dict[str, bool]:
        """
        Check if all critical UI elements are present.

        Args:
            page: Playwright page to check
            timeout_ms: Timeout per element check in milliseconds

        Returns:
            Dict mapping element name to presence (True/False)
        """
        results = {}

        for element_name, selectors in self.CRITICAL_ELEMENTS.items():
            found = False
            for selector in selectors:
                try:
                    page.locator(selector).first.wait_for(state="attached", timeout=timeout_ms)
                    found = True
                    break
                except Exception:
                    continue

            results[element_name] = found

        return results

    def get_missing_elements(self, results: dict[str, bool]) -> list[str]:
        """Get list of missing element names."""
        return [name for name, present in results.items() if not present]

    def is_healthy(self, results: dict[str, bool]) -> bool:
        """
        Check if UI is healthy (all critical elements present).

        Allows model_selector to be missing (might be in different state).
        """
        critical_required = ["composer"]
        return all(results.get(name, False) for name in critical_required)

    def report_broken_elements(self, results: dict[str, bool]) -> None:
        """Log which elements are missing for debugging."""
        missing = self.get_missing_elements(results)

        if missing:
            logger.error(f"🚨 UI CHANGE DETECTED! Missing elements: {', '.join(missing)}")
            logger.error("This may indicate Gemini UI layout has changed.")
            logger.error("Check screenshots in artifacts/screenshots/ui_health/")
        else:
            logger.debug("✅ UI health check passed - all elements present")

    def save_diagnostic_screenshot(
        self, page: Page, results: dict[str, bool], context: str = ""
    ) -> Path | None:
        """
        Save diagnostic screenshot with metadata.

        Args:
            page: Page to screenshot
            results: UI integrity check results
            context: Additional context (e.g., "after_reload")

        Returns:
            Path to saved screenshot or None if failed
        """
        try:
            timestamp = int(time.time())
            missing = self.get_missing_elements(results)
            missing_str = "_".join(missing) if missing else "healthy"

            filename = f"ui_health_{timestamp}_{missing_str}"
            if context:
                filename += f"_{context}"
            filename += ".png"

            screenshot_path = self.screenshot_dir / filename
            page.screenshot(path=str(screenshot_path), full_page=True)

            # Save metadata
            metadata_path = screenshot_path.with_suffix(".txt")
            with open(metadata_path, "w", encoding="utf-8") as f:
                f.write(f"Timestamp: {timestamp}\n")
                f.write(f"URL: {page.url}\n")
                f.write(f"Context: {context}\n\n")
                f.write("Element Status:\n")
                for name, present in results.items():
                    status = "✅ PRESENT" if present else "❌ MISSING"
                    f.write(f"  {name}: {status}\n")

            logger.info(f"[UIHealth] Diagnostic screenshot saved: {screenshot_path}")
            return screenshot_path

        except Exception as e:
            logger.warning(f"[UIHealth] Failed to save screenshot: {e}")
            return None

    def check_and_report(self, page: Page, context: str = "", save_screenshot: bool = True) -> bool:
        """
        Convenience method: check UI, report issues, optionally save screenshot.

        Args:
            page: Page to check
            context: Context string for logging
            save_screenshot: Whether to save diagnostic screenshot

        Returns:
            True if UI is healthy, False otherwise
        """
        results = self.check_ui_integrity(page)
        healthy = self.is_healthy(results)

        if not healthy:
            self.report_broken_elements(results)
            if save_screenshot:
                self.save_diagnostic_screenshot(page, results, context)

        return healthy
