"""Time-based One-Time Password (TOTP) 2FA utilities."""

from __future__ import annotations

import secrets
import string

import pyotp


def generate_totp_secret() -> str:
    """Generate a standard base32 TOTP secret key."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str = "Omnea Procurement") -> str:
    """Generate the standard otpauth:// URI for authenticator QR code generation."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=issuer)


def verify_totp_code(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code with time drift window of +/- 30 seconds."""
    if not secret or not code:
        return False
    clean_code = code.strip().replace(" ", "")
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False
    totp = pyotp.TOTP(secret)
    return bool(totp.verify(clean_code, valid_window=1))


def generate_backup_codes(count: int = 10) -> list[str]:
    """Generate cryptographically secure 10-character alphanumeric backup codes."""
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    for _ in range(count):
        # 10 chars, formatted as XXXXX-XXXXX
        part1 = "".join(secrets.choice(alphabet) for _ in range(5))
        part2 = "".join(secrets.choice(alphabet) for _ in range(5))
        codes.append(f"{part1}-{part2}")
    return codes
