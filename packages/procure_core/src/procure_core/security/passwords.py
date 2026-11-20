"""Password hashing and policy validation using Argon2id."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Recommended Argon2id parameters (RFC 9106)
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MiB
    parallelism=4,
    hash_len=32,
    salt_len=16,
)

# Password policy:
# - Minimum length: 10 characters
# - At least 1 uppercase letter
# - At least 1 lowercase letter
# - At least 1 number
# - At least 1 special character (!@#$%^&*()_+-=[]{}|;:,.<>?)
_MIN_LENGTH = 10
_UPPERCASE_RE = re.compile(r"[A-Z]")
_LOWERCASE_RE = re.compile(r"[a-z]")
_DIGIT_RE = re.compile(r"\d")
_SPECIAL_RE = re.compile(r"""[!@#$%^&*()_+\-=\[\]{}|;:,.<>?/~`'"\\/]""")


def validate_password_strength(password: str) -> None:
    """Validate that password meets enterprise security complexity requirements.

    Raises:
        ValueError: If password fails any policy requirement.
    """
    if not password or len(password) < _MIN_LENGTH:
        raise ValueError(f"Password must be at least {_MIN_LENGTH} characters long.")
    if not _UPPERCASE_RE.search(password):
        raise ValueError("Password must contain at least one uppercase letter.")
    if not _LOWERCASE_RE.search(password):
        raise ValueError("Password must contain at least one lowercase letter.")
    if not _DIGIT_RE.search(password):
        raise ValueError("Password must contain at least one number.")
    if not _SPECIAL_RE.search(password):
        raise ValueError("Password must contain at least one special character.")


def hash_password(password: str) -> str:
    """Hash plaintext password using Argon2id with strict policy check."""
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Verify plaintext password against an Argon2id hash."""
    if not password or not hashed:
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
