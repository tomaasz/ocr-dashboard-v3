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


def test_x11_connection(display: str) -> bool:
    """
    Test connectivity to X Server using xset -q.
    Returns True if successful, False otherwise.
    """
    try:
        env = os.environ.copy()
        env["DISPLAY"] = display

        # Run xset q with timeout
        logger.info(f"Testing X11 connection to {display}...")
        result = subprocess.run(
            ["xset", "-q"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=5,  # 5 seconds timeout
            check=False,
        )

        if result.returncode == 0:
            logger.info("✅ X11 connection successful")
            return True
        stderr = result.stderr.decode(errors="replace").strip()
        logger.error(f"❌ X11 connection failed (code {result.returncode}): {stderr}")
        return False

    except subprocess.TimeoutExpired:
        logger.error(
            "❌ X11 connection TIMED OUT after 5s. Firewall issue? (Use 'Allow Public Access' in VcXsrv)"
        )
        return False
    except FileNotFoundError:
        logger.warning("⚠️ 'xset' command not found. Skipping X11 check.")
        return True  # Assume ok if we can't check
    except Exception as e:
        logger.error(f"❌ X11 connection check error: {e}")
        return False


def load_x11_display():
    """Load X11 display from config file."""
    try:
        cache_dir = Path.home() / ".cache" / "ocr-dashboard-v3"
        config_file = cache_dir / "x11_display.json"

        if config_file.exists():
            data = json.loads(config_file.read_text(encoding="utf-8"))
            display = data.get("display", "").strip()
            if display:
                logger.info(f"Loaded X11 Display overrides: {display}")
                return display
    except Exception as e:
        logger.warning(f"Failed to load X11 display config: {e}")
    return None


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

    # Load and apply X11 Display settings
    x11_display = load_x11_display()
    if x11_display:
        logger.info(f"Setting DISPLAY={x11_display}")
        os.environ["DISPLAY"] = x11_display
    elif "DISPLAY" not in os.environ:
        logger.warning(
            "DISPLAY environment variable is not set and no override found. Browser might fail to start!"
        )
        # Try default :0 just in case
        logger.info("Setting default DISPLAY=:0")
        os.environ["DISPLAY"] = ":0"
    else:
        logger.info(f"Using existing DISPLAY={os.environ['DISPLAY']}")

    # Verify X11 connection
    current_display = os.environ.get("DISPLAY", ":0")
    if not test_x11_connection(current_display):
        logger.critical("🛑 ABORTING: Cannot connect to X Server.")
        logger.critical("Please check:")
        logger.critical("1. Is Xming/VcXsrv running?")
        logger.critical("2. Is 'Disable Access Control' checked?")
        logger.critical("3. Is Windows Firewall allowing the connection?")
        logger.critical("4. Is the IP address correct? (Currently trying: " + current_display + ")")
        sys.exit(1)

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
        # This allows user to manually login when session expires
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
                        logger.info("Auto-login successful!")
                    else:
                        logger.warning("Auto-login failed or manual intervention required.")
                except Exception as e:
                    logger.error(f"Auto-login error: {e}")

            logger.info("\n=== LOGIN MODE ACTIVE ===")
            logger.info("Please interact with the browser window to log in.")
            logger.info("Close the browser window to finish this session.")
            logger.info("=========================\n")

        while True:
            time.sleep(1)
            # Check if browser is still connected
            if controller.browser and not controller.browser.is_connected():
                logger.info("Browser closed by user.")
                break

    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        try:
            controller.close()
        except:
            pass


if __name__ == "__main__":
    main()
