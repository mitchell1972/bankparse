"""
BankParse — HMRC integration config.

Environment variables (set in Railway):
    HMRC_CLIENT_ID            OAuth client id (from developer.service.hmrc.gov.uk)
    HMRC_CLIENT_SECRET        OAuth client secret
    HMRC_ENV                  'sandbox' | 'production' (drives base URL)
    HMRC_REDIRECT_URI         e.g. https://bankscanai.com/api/hmrc/callback
    HMRC_VENDOR_PRODUCT_NAME  customer-facing product name, e.g. 'BankScan AI'
    HMRC_VENDOR_SOFTWARE_NAME stable software identifier, e.g. 'bankscan-ai'
    HMRC_TOKEN_ENCRYPTION_KEY base64(32 bytes) — AES-GCM key for token-at-rest
                              encryption. Generate once with:
                              python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
"""

from __future__ import annotations

import os


HMRC_CLIENT_ID = os.environ.get("HMRC_CLIENT_ID", "")
HMRC_CLIENT_SECRET = os.environ.get("HMRC_CLIENT_SECRET", "")
HMRC_ENV = os.environ.get("HMRC_ENV", "sandbox").lower()
HMRC_REDIRECT_URI = os.environ.get("HMRC_REDIRECT_URI", "")
HMRC_VENDOR_PRODUCT_NAME = os.environ.get("HMRC_VENDOR_PRODUCT_NAME", "BankScan AI")
HMRC_VENDOR_SOFTWARE_NAME = os.environ.get("HMRC_VENDOR_SOFTWARE_NAME", "bankscan-ai")
HMRC_TOKEN_ENCRYPTION_KEY = os.environ.get("HMRC_TOKEN_ENCRYPTION_KEY", "")

# Base URLs per HMRC API documentation.
SANDBOX_BASE_URL = "https://test-api.service.hmrc.gov.uk"
PRODUCTION_BASE_URL = "https://api.service.hmrc.gov.uk"

HMRC_BASE_URL = PRODUCTION_BASE_URL if HMRC_ENV == "production" else SANDBOX_BASE_URL

# OAuth endpoints (relative to base).
OAUTH_AUTHORIZE_PATH = "/oauth/authorize"
OAUTH_TOKEN_PATH = "/oauth/token"

# Vendor version reported to HMRC fraud headers. Read from app.py's FastAPI
# version at runtime via fraud_headers.py — keeping a fallback here.
DEFAULT_VENDOR_VERSION = "2.3.0"

# Fraud Prevention Headers validator (always available in both envs; we use
# the sandbox path for CI). See:
# https://developer.service.hmrc.gov.uk/api-documentation/docs/api/service/txm-fph-validator-api/
VALIDATOR_VALIDATE_PATH = "/test/fraud-prevention-headers/validate"
VALIDATOR_FEEDBACK_PATH = "/test/fraud-prevention-headers/validation-feedback"

# Connection method we declare in Gov-Client-Connection-Method.
# Per the HMRC spec for browser → our FastAPI → HMRC topology.
CONNECTION_METHOD = "WEB_APP_VIA_SERVER"


def is_configured() -> bool:
    """True if the minimum env required to actually call HMRC is present."""
    return bool(
        HMRC_CLIENT_ID
        and HMRC_CLIENT_SECRET
        and HMRC_REDIRECT_URI
        and HMRC_TOKEN_ENCRYPTION_KEY
    )
