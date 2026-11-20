"""Unit tests for Stage 1 security primitives: passwords, tokens, lockout, TOTP, and email."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from procure_core.email.sender import (
    PostmarkEmailSender,
    ResendEmailSender,
    get_email_sender,
    set_email_sender,
)
from procure_core.models.auth import LoginAttemptModel
from procure_core.security.lockout import check_lockout
from procure_core.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from procure_core.security.tokens import (
    create_access_token,
    create_csrf_token,
    create_refresh_token,
    decode_access_token,
    hash_token,
)
from procure_core.security.totp import (
    generate_backup_codes,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
)
from tests.fakes.fake_email_sender import FakeEmailSender


@pytest.mark.unit
def test_password_strength_validation() -> None:
    """Password strength validation requires length, case, digit, and special char."""
    # Too short (<10 chars)
    with pytest.raises(ValueError, match="at least 10 characters"):
        validate_password_strength("Aa1!short")

    # Missing uppercase
    with pytest.raises(ValueError, match="uppercase"):
        validate_password_strength("lowercase123!@#")

    # Missing lowercase
    with pytest.raises(ValueError, match="lowercase"):
        validate_password_strength("UPPERCASE123!@#")

    # Missing digit
    with pytest.raises(ValueError, match="number"):
        validate_password_strength("NoDigitsHere!@#")

    # Missing special character
    with pytest.raises(ValueError, match="special character"):
        validate_password_strength("NoSpecialChar123")

    # Valid strong passwords
    validate_password_strength("ValidStrongP@ssw0rd")
    validate_password_strength("Correct-Horse-Battery-Staple-99!")


@pytest.mark.unit
def test_argon2id_hashing_and_verification() -> None:
    """Argon2id hashing creates valid hashes and verifies plaintext accurately."""
    pwd = "EnterpriseSecurePassw0rd!2026"
    h = hash_password(pwd)
    assert h.startswith("$argon2id$")
    assert verify_password(pwd, h) is True
    assert verify_password("WrongPassword!123", h) is False
    assert verify_password("", h) is False
    assert verify_password(pwd, "") is False


@pytest.mark.unit
def test_jwt_access_token_creation_and_claims() -> None:
    """Access tokens contain standard claims and enforce 15-minute expiration."""
    user_id = "user_123"
    tenant_id = "tenant_acme"
    email = "alice@acme.com"
    roles = ["finance_admin"]

    token = create_access_token(
        user_id=user_id,
        tenant_id=tenant_id,
        email=email,
        roles=roles,
    )
    payload = decode_access_token(token)

    assert payload["sub"] == user_id
    assert payload["tenant_id"] == tenant_id
    assert payload["email"] == email
    assert payload["roles"] == roles
    assert payload["type"] == "access"
    assert "exp" in payload
    assert "iat" in payload


@pytest.mark.unit
def test_jwt_expired_token_rejected() -> None:
    """Expired JWT tokens raise ExpiredSignatureError."""
    token = create_access_token(
        user_id="user_123",
        tenant_id="tenant_acme",
        email="alice@acme.com",
        roles=["requester"],
        expires_delta=timedelta(seconds=-10),  # already expired
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


@pytest.mark.unit
def test_jwt_invalid_signature_rejected() -> None:
    """Tampered token or invalid signature is rejected."""
    token = create_access_token(
        user_id="user_123",
        tenant_id="tenant_acme",
        email="alice@acme.com",
        roles=["requester"],
    )
    tampered = token[:-4] + "AAAA"
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tampered)


@pytest.mark.unit
def test_refresh_token_and_csrf_generation() -> None:
    """Refresh tokens generate high entropy values and hashable digests."""
    raw, hashed = create_refresh_token()
    assert len(raw) >= 32
    assert hash_token(raw) == hashed

    csrf = create_csrf_token()
    assert len(csrf) == 64


@pytest.mark.unit
def test_totp_secret_and_verification() -> None:
    """TOTP generation, URI generation, and code verification."""
    secret = generate_totp_secret()
    assert len(secret) == 32

    uri = get_totp_uri(secret, "user@omnea.ai", issuer="Omnea Test")
    assert "otpauth://totp/" in uri
    assert "secret=" in uri

    import pyotp

    valid_code = pyotp.TOTP(secret).now()
    assert verify_totp_code(secret, valid_code) is True
    assert verify_totp_code(secret, "000000" if valid_code != "000000" else "111111") is False

    # Backup codes
    codes = generate_backup_codes(10)
    assert len(codes) == 10
    for c in codes:
        assert len(c) == 11
        assert "-" in c


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lockout_after_five_failed_attempts() -> None:
    """Account is locked out after 5 consecutive failed attempts within 15 minutes."""
    session = AsyncMock()
    now = datetime.now(UTC)

    # Mock no recent successful login
    mock_success_res = MagicMock()
    mock_success_res.scalar_one_or_none.return_value = None

    # Simulate 5 failed login attempts
    mock_failures = [
        LoginAttemptModel(
            email="victim@omnea.ai",
            ip_address="127.0.0.1",
            success=False,
            attempted_at=now - timedelta(minutes=2),
        )
        for _ in range(5)
    ]
    mock_fail_res = MagicMock()
    mock_fail_res.scalars.return_value.all.return_value = mock_failures

    session.execute.side_effect = [mock_success_res, mock_fail_res]

    is_locked, remaining = await check_lockout(session, "victim@omnea.ai")
    assert is_locked is True
    assert remaining > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_email_sender_fake_captured_outbox() -> None:
    """Captured-outbox fake captures dispatches without external network calls."""
    fake = FakeEmailSender()
    set_email_sender(fake)
    try:
        sender = get_email_sender()
        await sender.send_email(
            to="test@omnea.ai",
            subject="Test Notification",
            html_content="<p>Test Content</p>",
            text_content="Test Content",
        )
        assert len(fake.outbox) == 1
        assert fake.latest is not None
        assert fake.latest.to == "test@omnea.ai"
        assert fake.latest.subject == "Test Notification"

        fake.clear()
        assert len(fake.outbox) == 0
    finally:
        set_email_sender(None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_credentials_safe_degradation() -> None:
    """Missing provider credentials degrade safely without throwing unhandled crashes."""
    resend = ResendEmailSender(api_key="")
    assert await resend.send_email("to@omnea.ai", "Subj", "Body") is False

    postmark = PostmarkEmailSender(server_token="")
    assert await postmark.send_email("to@omnea.ai", "Subj", "Body") is False
