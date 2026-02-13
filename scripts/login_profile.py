import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root / "src"))

from ocr_engine.ocr.engine.auto_login import AutoLogin
from ocr_engine.ocr.engine.browser_controller import GeminiBrowserController
from ocr_engine.ocr.engine.proxy_config import load_proxy_config
from ocr_engine.utils.path_security import sanitize_profile_name, validate_profiles_dir

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("login_profile")


def _ensure_display() -> str:
    """
    Ensure a DISPLAY is available. If no X11 display exists, start Xvfb.
    Returns the DISPLAY string.
    """
    # Check if DISPLAY is already set and working
    existing_display = os.environ.get("DISPLAY", "")
    if existing_display:
        try:
            result = subprocess.run(
                ["xset", "-q"],
                env={**os.environ, "DISPLAY": existing_display},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                logger.info(f"✅ Using existing X11 display: {existing_display}")
                return existing_display
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Try loading from config
    try:
        cache_dir = Path.home() / ".cache" / "ocr-dashboard-v3"
        config_file = cache_dir / "x11_display.json"
        if config_file.exists():
            data = json.loads(config_file.read_text(encoding="utf-8"))
            display = data.get("display", "").strip()
            if display:
                logger.info(f"Loaded X11 Display from config: {display}")
                os.environ["DISPLAY"] = display
                return display
    except Exception as e:
        logger.debug(f"Failed to load X11 config: {e}")

    # No display available - start Xvfb
    logger.info("No X11 display available. Starting Xvfb virtual display...")
    xvfb_display = ":99"
    try:
        # Kill any existing Xvfb on :99
        subprocess.run(
            ["pkill", "-f", f"Xvfb {xvfb_display}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        time.sleep(0.5)

        # Start Xvfb
        subprocess.Popen(
            ["Xvfb", xvfb_display, "-screen", "0", "1920x1080x24", "-ac"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        os.environ["DISPLAY"] = xvfb_display
        logger.info(f"✅ Xvfb started on {xvfb_display}")
        return xvfb_display
    except Exception as e:
        logger.error(f"❌ Failed to start Xvfb: {e}")
        # Last resort fallback
        os.environ["DISPLAY"] = ":0"
        return ":0"


def main():
    profile_suffix = os.environ.get("OCR_PROFILE_SUFFIX", "")
    if not profile_suffix:
        logger.error("No profile specified (OCR_PROFILE_SUFFIX)")
        sys.exit(1)

    logger.info(f"Starting Login Process for profile: {profile_suffix}")

    # Resolve profile dir
    try:
        profile_name = sanitize_profile_name(profile_suffix)
        dir_name = f"gemini-profile-{profile_name}" if profile_suffix else "gemini-profile"
        profile_dir = validate_profiles_dir() / dir_name
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Failed to setup profile directory: {e}")
        sys.exit(1)

    logger.info(f"Profile Directory: {profile_dir}")

    # Ensure display is available (X11 or Xvfb)
    display = _ensure_display()
    logger.info(f"Using DISPLAY={display}")

    proxy_config = load_proxy_config(profile_name, project_root / "config" / "proxies.json")
    if proxy_config:
        safe_proxy = proxy_config.copy()
        if "password" in safe_proxy:
            safe_proxy["password"] = "***"
        logger.info(f"Using proxy for profile '{profile_name}': {safe_proxy}")

    controller = GeminiBrowserController(
        profile_dir=profile_dir, headed=True, enable_video=False, proxy_config=proxy_config
    )

    try:
        # Start browser WITHOUT clean start check to avoid SessionExpiredError
        controller.start(skip_clean_start=True)
        logger.info("Browser started.")

        # Initialize AutoLogin
        auto_login = AutoLogin(profile_name)

        if controller.context and controller.context.pages:
            page = controller.context.pages[0]

            # Navigate to app to check login state
            logger.info("Navigating to Gemini...")
            try:
                page.goto("https://gemini.google.com/app?hl=pl", timeout=60000)
            except Exception as e:
                logger.warning(
                    f"Navigation error (might be okay if manual interaction needed): {e}"
                )

            # If we have credentials, try auto-login
            if auto_login.can_auto_login():
                logger.info("Credentials found. Attempting auto-login...")
                try:
                    if auto_login.perform_login(page):
                        logger.info("✅ Auto-login successful!")
                        # Keep browser open briefly to save session
                        logger.info("Saving session (waiting 5s)...")
                        time.sleep(5)
                        logger.info("Session saved. Closing browser.")
                        return
                    else:
                        logger.warning("❌ Auto-login failed.")
                except Exception as e:
                    logger.error(f"Auto-login error: {e}")
            else:
                logger.warning(
                    "Cannot auto-login - missing email/password in config/credentials.json"
                )

            logger.info("\n=== LOGIN MODE ACTIVE ===")
            logger.info("Waiting for browser to close or auto-login to complete...")
            logger.info("=========================\n")

        while True:
            time.sleep(1)
            # Check if browser is still connected
            if controller.browser and not controller.browser.is_connected():
                logger.info("Browser closed.")
                break

    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        try:
            controller.close()
        except Exception:
            pass
        # Cleanup Xvfb if we started it
        subprocess.run(
            ["pkill", "-f", "Xvfb :99"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


if __name__ == "__main__":
    main()
