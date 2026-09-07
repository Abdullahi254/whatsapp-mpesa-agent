"""
Environment-backed configuration for the WhatsApp <-> M-Pesa agent.

This is a *starter* pattern: auth_info dicts are built from plain env vars so
you can get the sandbox flow working quickly. For production, replace
`whatsapp_auth_info()` / `mpesa_auth_info()` with a CustomAgent that sets
`requires_auth = True` and reads `self._auth_manager` (Nexus-injected,
encrypted credentials) instead of raw environment variables.
See: https://jarviscore.developers.prescottdata.io/concepts/architecture/#nexus
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    # Defensive: strip a trailing " # comment" if one slipped into a .env value
    # (python-dotenv only strips inline comments in specific formats).
    if " #" in value:
        value = value.split(" #", 1)[0]
    return value.strip()


def _csv_env(name: str) -> list[str]:
    raw = _env(name)
    return [p.strip() for p in raw.split(",") if p.strip()]


PORT = int(_env("PORT", "8000"))
PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL")

ALLOWED_WHATSAPP_SENDERS = _csv_env("ALLOWED_WHATSAPP_SENDERS")


def whatsapp_auth_info() -> dict:
    return {
        "access_token": _env("WHATSAPP_ACCESS_TOKEN"),
        "phone_number_id": _env("WHATSAPP_PHONE_NUMBER_ID"),
        "whatsapp_business_account_id": _env("WHATSAPP_BUSINESS_ACCOUNT_ID"),
    }


def whatsapp_verify_token() -> str:
    return _env("WHATSAPP_VERIFY_TOKEN")


def whatsapp_app_secret() -> str:
    return _env("WHATSAPP_APP_SECRET")


def mpesa_auth_info() -> dict:
    """Shared auth_info for all safaricom_mpesa atoms (STK Push, B2C, B2B)."""
    return {
        "base_url": _env("MPESA_BASE_URL", "https://sandbox.safaricom.co.ke"),
        "username": _env("MPESA_CONSUMER_KEY"),
        "password": _env("MPESA_CONSUMER_SECRET"),
        "business_short_code": _env("MPESA_BUSINESS_SHORT_CODE"),
        "passkey": _env("MPESA_PASSKEY"),
        "initiator_name": _env("MPESA_INITIATOR_NAME"),
        "security_credential": _env("MPESA_SECURITY_CREDENTIAL"),
    }


def mpesa_result_url() -> str:
    return f"{PUBLIC_BASE_URL.rstrip('/')}/webhooks/mpesa/result"


def mpesa_timeout_url() -> str:
    return f"{PUBLIC_BASE_URL.rstrip('/')}/webhooks/mpesa/timeout"


def is_sender_allowed(msisdn: str) -> bool:
    if not ALLOWED_WHATSAPP_SENDERS:
        return True  # explicitly opted out of the allow-list
    return msisdn in ALLOWED_WHATSAPP_SENDERS
