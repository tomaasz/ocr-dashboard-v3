"""
Auto-login module for Google accounts with 2FA support.

Handles automatic login when session expires, using stored credentials
and TOTP secret for 2FA.
"""

import json
import logging
from pathlib import Path

try:
    import pyotp
except ImportError:
    pyotp = None

from playwright.sync_api import Page

from ocr_engine.utils.path_security import sanitize_profile_name

from .session_recovery import SessionIssueType

logger = logging.getLogger(__name__)


# SMS verification indicators
SMS_INDICATORS = [
    "Get a verification code",
    "Uzyskaj kod weryfikacyjny",
    "Text me a verification code",
    "Wyślij mi SMS z kodem",
    "Confirm your phone number",
    "Potwierdź numer telefonu",
    "Enter the code we sent",
    "Wpisz kod, który wysłaliśmy",
    "We sent a code to",
    "Wysłaliśmy kod na",
]


class AutoLogin:
    """Handles automatic Google login with 2FA."""

    CREDENTIALS_FILE = Path("config/credentials.json")

    def __init__(self, profile_name: str, db_manager=None):
        self.profile_name = profile_name
        self.credentials = self._load_credentials()
        self.db_manager = db_manager
        self.sms_verification_pending = False

    def _load_credentials(self) -> dict | None:
        """Load credentials for the current profile."""
        if not self.CREDENTIALS_FILE.exists():
            logger.warning(f"[AutoLogin] Credentials file not found: {self.CREDENTIALS_FILE}")
            return None

        try:
            with open(self.CREDENTIALS_FILE) as f:
                data = json.load(f)

            profiles = data.get("profiles", {})

            # Strip gemini-profile- prefix if present (profile_dir.name vs credential key)
            lookup_name = self.profile_name
            if lookup_name.startswith("gemini-profile-"):
                lookup_name = lookup_name[len("gemini-profile-") :]

            creds = profiles.get(lookup_name) or profiles.get(self.profile_name)

            if not creds:
                logger.warning(f"[AutoLogin] No credentials for profile: {lookup_name}")
                return None

            # Validate required fields (email + password required, totp_secret optional)
            required = ["email", "password"]
            missing = [f for f in required if not creds.get(f)]
            if missing:
                logger.warning(f"[AutoLogin] Missing fields for {self.profile_name}: {missing}")
                return None

            if not creds.get("totp_secret"):
                logger.warning(
                    f"[AutoLogin] No totp_secret for {self.profile_name} - "
                    "will attempt login without 2FA"
                )

            logger.info(f"[AutoLogin] Credentials loaded for: {self.profile_name}")
            return creds

        except Exception as e:
            logger.error(f"[AutoLogin] Failed to load credentials: {e}")
            return None

    def can_auto_login(self) -> bool:
        """Check if auto-login is possible."""
        if pyotp is None:
            logger.warning("[AutoLogin] pyotp not installed. Run: pip install pyotp")
            return False
        return self.credentials is not None

    def generate_totp_code(self) -> str | None:
        """Generate current TOTP code."""
        if not self.credentials or pyotp is None:
            return None

        try:
            secret = self.credentials["totp_secret"].replace(" ", "").upper()
            totp = pyotp.TOTP(secret)
            code = totp.now()
            logger.info(f"[AutoLogin] Generated TOTP code: {code[:2]}****")
            return code
        except Exception as e:
            logger.error(f"[AutoLogin] Failed to generate TOTP: {e}")
            return None

    def _check_and_wait_for_captcha(self, page: Page, context: str = "") -> bool:
        """
        Check if CAPTCHA is present and wait for manual resolution.

        Returns True if CAPTCHA was detected and resolved, False if no CAPTCHA.
        Raises exception if CAPTCHA not resolved within timeout.
        """
        try:
            # Common CAPTCHA indicators
            captcha_selectors = [
                "iframe[src*='recaptcha']",
                "iframe[src*='captcha']",
                "[id*='captcha']",
                "[class*='captcha']",
                "div:has-text('verify you')",
                "div:has-text('not a robot')",
                "div:has-text('Verify it')",
            ]

            captcha_found = False
            for selector in captcha_selectors:
                if page.locator(selector).count() > 0:
                    captcha_found = True
                    break

            if not captcha_found:
                return False

            # CAPTCHA detected!
            logger.warning(f"⚠️ [AutoLogin] CAPTCHA DETECTED {context}!")
            logger.info("=" * 70)
            logger.info("🤖 CAPTCHA must be solved manually")
            logger.info("Please solve the CAPTCHA in the browser window")
            logger.info("The script will wait up to 5 minutes...")
            logger.info("=" * 70)

            # Wait for CAPTCHA to disappear (max 5 minutes)
            max_wait = 300  # 5 minutes
            waited = 0
            check_interval = 5  # Check every 5 seconds

            while waited < max_wait:
                # Re-check if CAPTCHA is still present
                still_present = False
                for selector in captcha_selectors:
                    if page.locator(selector).count() > 0:
                        still_present = True
                        break

                if not still_present:
                    logger.info("✅ [AutoLogin] CAPTCHA resolved! Continuing...")
                    page.wait_for_timeout(2000)  # Wait for page to stabilize
                    return True

                page.wait_for_timeout(check_interval * 1000)
                waited += check_interval

                if waited % 30 == 0:  # Log every 30 seconds
                    logger.info(
                        f"[AutoLogin] Still waiting for CAPTCHA... ({waited}s / {max_wait}s)"
                    )

            # Timeout - CAPTCHA still present
            logger.error("❌ [AutoLogin] CAPTCHA not resolved within 5 minutes!")
            raise Exception("CAPTCHA resolution timeout - manual intervention required")

        except Exception as e:
            if "CAPTCHA resolution timeout" in str(e):
                raise
            logger.debug(f"[AutoLogin] CAPTCHA check error: {e}")
            return False

    def perform_login(self, page: Page) -> bool:
        """
        Perform full Google login sequence.

        Returns True if login successful, False otherwise.
        """
        if not self.can_auto_login():
            logger.error("[AutoLogin] Cannot perform auto-login - missing credentials or pyotp")
            return False

        try:
            email = self.credentials["email"]
            password = self.credentials["password"]

            logger.info(f"[AutoLogin] Starting login for: {email}")

            # Step 0: Handle OAuth app verification dialog ("Make sure this app is from Google" / "Sprawdź, czy ta aplikacja została pobrana z Google")
            oauth_dialog_handled = self._handle_oauth_app_verification(page)
            if oauth_dialog_handled:
                page.wait_for_timeout(2000)

            # Step 1: Click Sign in button if present (English and Polish variants)
            sign_in_selectors = [
                "a:has-text('Sign in')",
                "button:has-text('Sign in')",
                "a:has-text('Zaloguj się')",
                "button:has-text('Zaloguj się')",
                "a:has-text('Zaloguj')",
                "button:has-text('Zaloguj')",
            ]
            sign_in_btn = None
            for selector in sign_in_selectors:
                btn = page.locator(selector).first
                if btn.count() > 0:
                    try:
                        if btn.is_visible(timeout=1000):
                            sign_in_btn = btn
                            break
                    except Exception:
                        continue

            if sign_in_btn:
                logger.info("[AutoLogin] Clicking sign-in button")
                sign_in_btn.click()
                page.wait_for_timeout(2000)

            # Step 2: Enter email
            email_input = page.locator("input[type='email']").first
            if not email_input.is_visible(timeout=5000):
                logger.error("[AutoLogin] Email input not found")
                return False

            logger.info("[AutoLogin] Entering email")
            email_input.fill(email)
            page.wait_for_timeout(500)

            # Click Next (after email) - use force click and wait for navigation
            next_selectors = [
                "#identifierNext",  # Google's actual button ID
                "button:has-text('Dalej')",
                "button:has-text('Next')",
                "div[role='button']:has-text('Dalej')",
                "div[role='button']:has-text('Next')",
            ]
            next_clicked = False
            for selector in next_selectors:
                try:
                    btn = page.locator(selector).first
                    if btn.count() > 0 and btn.is_visible(timeout=2000):
                        logger.info(f"[AutoLogin] Clicking Next button: {selector}")
                        btn.click(force=True)
                        next_clicked = True
                        break
                except Exception as e:
                    logger.debug(f"[AutoLogin] Selector {selector} failed: {e}")
                    continue

            if not next_clicked:
                logger.info("[AutoLogin] Next button not found, pressing Enter")
                page.keyboard.press("Enter")

            # Wait for password page to load (Google can be slow, especially with proxy)
            logger.info("[AutoLogin] Waiting for page transition...")
            page.wait_for_timeout(5000)

            # Check for CAPTCHA after email entry
            self._check_and_wait_for_captcha(page, "after email")

            # Step 3: Enter password
            # Google may show a security key (FIDO) challenge instead of password.
            # If so, click "Try another way" and select password option.
            password_input = page.locator("input[type='password']").first
            logger.info("[AutoLogin] Waiting for password input to appear...")

            try:
                password_input.wait_for(state="visible", timeout=5000)
            except Exception:
                # Password not visible — maybe security key challenge is shown
                logger.info(
                    "[AutoLogin] Password input not found, checking for security key challenge..."
                )

                # Detect security key / FIDO challenge
                security_key_indicators = [
                    "text='Use your security key'",
                    "text='Użyj klucza bezpieczeństwa'",
                    "text='używając klucza'",
                    "text='Insert your security key'",
                    "text='Weryfikuję Twoją tożsamość'",
                ]
                is_security_key = False
                for indicator in security_key_indicators:
                    try:
                        if page.locator(indicator).first.count() > 0:
                            is_security_key = True
                            break
                    except Exception:
                        continue

                if is_security_key:
                    logger.info(
                        "[AutoLogin] Security key challenge detected. Clicking 'Try another way'..."
                    )
                    try_another_selectors = [
                        "a:has-text('Wypróbuj inny sposób')",
                        "a:has-text('Try another way')",
                        "button:has-text('Wypróbuj inny sposób')",
                        "button:has-text('Try another way')",
                    ]
                    clicked_another = False
                    for selector in try_another_selectors:
                        try:
                            btn = page.locator(selector).first
                            if btn.count() > 0 and btn.is_visible(timeout=2000):
                                btn.click()
                                clicked_another = True
                                logger.info("[AutoLogin] Clicked 'Try another way'")
                                page.wait_for_timeout(3000)
                                break
                        except Exception:
                            continue

                    if not clicked_another:
                        # Also try Cancel button on WebAuthn dialog first
                        try:
                            cancel_btn = page.locator(
                                "button:has-text('Cancel'), button:has-text('Anuluj')"
                            ).first
                            if cancel_btn.count() > 0 and cancel_btn.is_visible(timeout=2000):
                                cancel_btn.click()
                                page.wait_for_timeout(2000)
                                # Retry "Try another way"
                                for selector in try_another_selectors:
                                    try:
                                        btn = page.locator(selector).first
                                        if btn.count() > 0 and btn.is_visible(timeout=2000):
                                            btn.click()
                                            clicked_another = True
                                            page.wait_for_timeout(3000)
                                            break
                                    except Exception:
                                        continue
                        except Exception:
                            pass

                    if clicked_another:
                        # Now select "Enter your password" option
                        password_option_selectors = [
                            "li:has-text('Wpisz hasło')",
                            "li:has-text('Enter your password')",
                            "div[data-challengetype='12']",  # Google's internal ID for password
                            "[data-challengeindex] :has-text('Wpisz hasło')",
                            "[data-challengeindex] :has-text('Enter your password')",
                            "div[role='link']:has-text('hasło')",
                            "div[role='link']:has-text('password')",
                        ]
                        for selector in password_option_selectors:
                            try:
                                opt = page.locator(selector).first
                                if opt.count() > 0 and opt.is_visible(timeout=2000):
                                    opt.click()
                                    logger.info(f"[AutoLogin] Selected password option: {selector}")
                                    page.wait_for_timeout(3000)
                                    break
                            except Exception:
                                continue

                        # Now wait for password input again
                        try:
                            password_input = page.locator("input[type='password']").first
                            password_input.wait_for(state="visible", timeout=10000)
                        except Exception as e:
                            logger.error(
                                f"[AutoLogin] Password input still not found after selecting 'Try another way': {e}"
                            )
                            return False
                    else:
                        logger.error("[AutoLogin] Could not find 'Try another way' link")
                        return False
                else:
                    logger.error(
                        "[AutoLogin] Password input not found and no security key challenge detected"
                    )
                    return False

            logger.info("[AutoLogin] Entering password")
            password_input.fill(password)
            page.wait_for_timeout(500)

            # Click Next
            next_btn = page.locator("button:has-text('Next'), button:has-text('Dalej')").first
            if next_btn.count() > 0:
                next_btn.click()
                page.wait_for_timeout(3000)
            else:
                page.keyboard.press("Enter")
                page.wait_for_timeout(3000)

            # Check for CAPTCHA after password entry
            self._check_and_wait_for_captcha(page, "after password")

            # Step 4: Check for SMS verification requirement
            if self._detect_sms_verification(page):
                logger.critical(
                    "🚨 [AutoLogin] SMS VERIFICATION REQUIRED - manual intervention needed!"
                )
                self._log_sms_verification_event(page)
                return False

            # Step 5: Handle TOTP 2FA
            totp_input = page.locator(
                "input[type='tel'], input[name='totpPin'], input[id='totpPin']"
            ).first
            if totp_input.is_visible(timeout=5000):
                # Double-check it's not SMS verification
                if self._detect_sms_verification(page):
                    logger.critical(
                        "🚨 [AutoLogin] SMS VERIFICATION REQUIRED - manual intervention needed!"
                    )
                    self._log_sms_verification_event(page)
                    return False

                totp_code = self.generate_totp_code()
                if not totp_code:
                    logger.error(
                        "[AutoLogin] 2FA required but no totp_secret configured! "
                        "Add totp_secret to config/credentials.json for this profile."
                    )
                    return False

                logger.info("[AutoLogin] Entering 2FA code")
                totp_input.fill(totp_code)
                page.wait_for_timeout(500)

                # Click Next/Verify
                next_btn = page.locator(
                    "button:has-text('Next'), button:has-text('Dalej'), button:has-text('Verify')"
                ).first
                if next_btn.count() > 0:
                    next_btn.click()
                else:
                    page.keyboard.press("Enter")

                page.wait_for_timeout(3000)

                # Check for CAPTCHA after 2FA
                self._check_and_wait_for_captcha(page, "after 2FA")

                # Check again after TOTP - Google might still ask for SMS
                if self._detect_sms_verification(page):
                    logger.critical("🚨 [AutoLogin] SMS VERIFICATION REQUIRED after TOTP!")
                    self._log_sms_verification_event(page)
                    return False

            # Step 5: Verify login success
            page.wait_for_timeout(2000)

            # Check if we're on Gemini and logged in
            if "gemini.google.com" in page.url:
                # Look for user avatar or menu indicating logged in
                logged_in = page.locator(
                    "img[aria-label*='Account'], button[aria-label*='Account']"
                ).first
                if logged_in.count() > 0:
                    logger.info("[AutoLogin] ✅ Login successful!")
                    return True

            # Check for error messages
            error = page.locator(
                "div[aria-live='assertive'], span:has-text('Wrong password')"
            ).first
            if error.count() > 0 and error.is_visible(timeout=1000):
                error_text = error.text_content()
                logger.error(f"[AutoLogin] Login error: {error_text}")
                return False

            # Give it more time and check URL
            page.wait_for_timeout(2000)
            if "accounts.google.com" not in page.url:
                logger.info("[AutoLogin] ✅ Login appears successful (left login page)")
                return True

            logger.warning("[AutoLogin] Login status uncertain")
            return False

        except Exception as e:
            logger.error(f"[AutoLogin] Login failed with error: {e}")
            return False

    def handle_session_expired(self, page: Page) -> bool:
        """
        Called when session expired. Attempts auto-login.

        Returns True if login successful and should retry operation.
        """
        logger.info("[AutoLogin] Session expired - attempting auto-login")

        # Navigate to Gemini to trigger login
        try:
            page.goto(
                "https://gemini.google.com/app?hl=pl", wait_until="domcontentloaded", timeout=15000
            )
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning(f"[AutoLogin] Navigation failed: {e}")

        return self.perform_login(page)

    def _handle_oauth_app_verification(self, page: Page) -> bool:
        """
        Handle Google OAuth app verification dialog.

        This dialog appears with text like:
        - "Make sure this app is from Google" (EN)
        - "Sprawdź, czy ta aplikacja została pobrana z Google" (PL)

        And has buttons:
        - "Sign in" / "Zaloguj się" (to proceed)
        - "Cancel" / "Anuluj" (to abort)

        Returns True if dialog was handled, False otherwise.
        """
        try:
            oauth_indicators = [
                "Make sure this app is from Google",
                "Make sure Google made this app",
                "Sprawdź, czy ta aplikacja została pobrana z Google",
                "Sign in with Google",
                "Zaloguj się przez Google",
            ]

            page_content = page.content()
            dialog_detected = any(indicator in page_content for indicator in oauth_indicators)

            if not dialog_detected:
                return False

            logger.info("[AutoLogin] OAuth app verification dialog detected")

            # Try to find and click "Sign in" / "Zaloguj się" button
            sign_in_selectors = [
                "button:has-text('Sign in')",
                "button:has-text('Zaloguj się')",
                "button:has-text('Continue')",
                "button:has-text('Kontynuuj')",
            ]

            for selector in sign_in_selectors:
                btn = page.locator(selector).first
                try:
                    if btn.count() > 0 and btn.is_visible(timeout=1000):
                        logger.info(f"[AutoLogin] Clicking OAuth dialog button: {selector}")
                        btn.click()
                        return True
                except Exception:
                    continue

            logger.warning("[AutoLogin] OAuth dialog detected but couldn't find Sign in button")
            return False

        except Exception as e:
            logger.debug(f"[AutoLogin] OAuth dialog check error: {e}")
            return False

    def _detect_sms_verification(self, page: Page) -> bool:
        """Check if page is showing SMS verification prompt."""
        try:
            page_text = page.content().lower()
            for indicator in SMS_INDICATORS:
                if indicator.lower() in page_text:
                    logger.warning(f"[AutoLogin] SMS indicator found: '{indicator}'")
                    self.sms_verification_pending = True
                    return True
            return False
        except Exception as e:
            logger.debug(f"[AutoLogin] SMS detection error: {e}")
            return False

    def _log_sms_verification_event(self, page: Page):
        """Log SMS verification requirement to database."""
        if not self.db_manager or not hasattr(self.db_manager, "log_critical_event"):
            logger.warning("[AutoLogin] Cannot log SMS event - no db_manager")
            return

        try:
            # Take screenshot for debugging
            screenshot_path = None
            try:
                from pathlib import Path

                screenshot_dir = Path("artifacts/screenshots")
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                safe_profile = sanitize_profile_name(self.profile_name)
                screenshot_path = str(screenshot_dir / f"sms_verification_{safe_profile}.png")
                page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"[AutoLogin] SMS verification screenshot: {screenshot_path}")
            except Exception:
                pass

            self.db_manager.log_critical_event(
                profile_name=self.profile_name,
                event_type=SessionIssueType.SMS_VERIFICATION_REQUIRED,
                message="⚠️ Google wymaga weryfikacji SMS - wymagana ręczna interwencja!",
                requires_action=True,
                meta={
                    "url": page.url,
                    "screenshot": screenshot_path,
                    "email": self.credentials.get("email", "unknown")
                    if self.credentials
                    else "unknown",
                },
            )
            logger.info("[AutoLogin] SMS verification event logged to database")
        except Exception as e:
            logger.error(f"[AutoLogin] Failed to log SMS event: {e}")

    def is_sms_pending(self) -> bool:
        """Check if SMS verification is pending for this profile."""
        return self.sms_verification_pending

    def clear_sms_pending(self):
        """Clear SMS pending flag after manual resolution."""
        self.sms_verification_pending = False
