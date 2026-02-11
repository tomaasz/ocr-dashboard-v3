"""
Extended popup selectors for better popup handling.

Provides comprehensive selectors for various types of popups commonly
encountered on websites: cookie consent, privacy policies, newsletters,
age verification, GDPR compliance, etc.
"""

# Cookie Consent Popups (6 variants)
COOKIE_CONSENT = [
    "button:has-text('Accept All')",
    "button:has-text('Accept all cookies')",
    "button:has-text('Reject All')",
    "button:has-text('I Agree')",
    "button[id*='cookie'][id*='accept' i]",
    "button[class*='cookie'][class*='accept' i]",
]

# Privacy Policy Popups (4 variants)
PRIVACY_POLICY = [
    "button:has-text('Close'):near(text='Privacy Policy')",
    "button:has-text('I Understand'):near(text='Privacy')",
    "div[class*='privacy-modal'] button:has-text('Close')",
    "div[role='dialog']:has-text('Privacy') button[aria-label*='Close' i]",
]

# Terms of Service Popups (3 variants)
TERMS_OF_SERVICE = [
    "button:has-text('Accept Terms')",
    "button:has-text('I Accept'):near(text='Terms')",
    "div[role='dialog']:has-text('Terms') button:has-text('Agree')",
]

# Newsletter/Marketing Popups (3 variants)
NEWSLETTER_MARKETING = [
    "button:has-text('No Thanks'):near(text='Newsletter')",
    "button:has-text('Close'):near(text='Subscribe')",
    "div[class*='newsletter'] button[aria-label*='Close' i]",
]

# Location/Region Selection (2 variants)
LOCATION_REGION = [
    "button:has-text('Continue'):near(text='Region')",
    "button:has-text('Confirm'):near(text='Location')",
]

# Age Verification (2 variants)
AGE_VERIFICATION = [
    "button:has-text('I am 18+')",
    "button:has-text('Confirm'):near(text='Age')",
]

# GDPR Compliance (2 variants)
GDPR_COMPLIANCE = [
    "button:has-text('Accept'):near(text='GDPR')",
    "button:has-text('Manage Preferences'):near(text='GDPR')",
]

# App Download Prompts (2 variants)
APP_DOWNLOAD = [
    "button:has-text('Not Now'):near(text='Download App')",
    "button:has-text('Close'):near(text='Get the App')",
]

# Combined list of all popup selectors (24 total)
POPUP_EXTENSIONS = (
    COOKIE_CONSENT
    + PRIVACY_POLICY
    + TERMS_OF_SERVICE
    + NEWSLETTER_MARKETING
    + LOCATION_REGION
    + AGE_VERIFICATION
    + GDPR_COMPLIANCE
    + APP_DOWNLOAD
)
