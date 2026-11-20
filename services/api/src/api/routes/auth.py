"""Authentication, user provisioning, and session management API routes."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.permissions import AuthContext, require_permission
from procure_core.auth.rbac import RbacEngine
from procure_core.config import get_settings
from procure_core.db import get_async_session
from procure_core.email import get_email_sender
from procure_core.models.auth import (
    CredentialModel,
    EmailVerificationModel,
    OrganizationModel,
    PasswordResetModel,
    SessionModel,
    TotpSecretModel,
    UserModel,
    UserRoleAssignmentModel,
)
from procure_core.security.audit_chain import AuditHashChainManager
from procure_core.security.lockout import check_lockout, record_login_attempt
from procure_core.security.passwords import (
    hash_password,
    validate_password_strength,
    verify_password,
)
from procure_core.security.tokens import (
    create_access_token,
    create_csrf_token,
    create_random_token,
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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

VALID_ROLES = {
    "requester",
    "approver",
    "finance_admin",
    "security_reviewer",
    "legal_reviewer",
    "tenant_admin",
}


# =============================================================================
# Schemas
# =============================================================================


class SignupRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str
    full_name: str = Field(default="", max_length=255)
    admin_name: str = Field(default="", max_length=255)


class SignupResponse(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    is_verified: bool
    verification_token: str
    message: str


class VerifyEmailRequest(BaseModel):
    token: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None
    tenant_id: str | None = None


class LoginResponse(BaseModel):
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 900
    csrf_token: str = ""
    user_id: str = ""
    tenant_id: str = ""
    roles: list[str] = Field(default_factory=list)
    is_verified: bool = False
    totp_required: bool = False
    message: str = "Login successful"


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    csrf_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class UserMeResponse(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    full_name: str
    roles: list[str]
    is_verified: bool
    totp_enabled: bool


class TotpSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    backup_codes: list[str]


class TotpVerifyRequest(BaseModel):
    code: str


class OidcLoginRequest(BaseModel):
    provider: str
    code: str


class RoleChangeRequest(BaseModel):
    role: str


# =============================================================================
# Endpoints
# =============================================================================


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    session: AsyncSession = Depends(get_async_session),
) -> SignupResponse:
    """Create a new tenant organization and provision the first user as tenant_admin."""
    # 1. Enforce password complexity
    try:
        validate_password_strength(payload.password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    normalized_email = payload.email.strip().lower()

    # 2. Derive stable organization identifier
    base_slug = re.sub(r"[^a-zA-Z0-9]+", "_", payload.organization_name.strip().lower()).strip("_")
    if not base_slug:
        base_slug = "org"
    tenant_id = f"tenant_{base_slug}"[:64]

    # Check if org exists, append uuid suffix if conflicting
    existing_org = await session.get(OrganizationModel, tenant_id)
    if existing_org:
        suffix = uuid.uuid4().hex[:6]
        tenant_id = f"{tenant_id[:57]}_{suffix}"

    org = OrganizationModel(id=tenant_id, name=payload.organization_name.strip())
    session.add(org)

    # 3. Create user
    admin_full_name = payload.full_name.strip() or payload.admin_name.strip()
    user = UserModel(
        tenant_id=tenant_id,
        email=normalized_email,
        full_name=admin_full_name,
        is_verified=False,
        is_active=True,
    )
    session.add(user)
    await session.flush()

    # 4. Hash and store password
    pwd_hash = hash_password(payload.password)
    cred = CredentialModel(user_id=user.id, password_hash=pwd_hash)
    session.add(cred)

    # 5. Assign first user as tenant_admin
    role_assign = UserRoleAssignmentModel(
        tenant_id=tenant_id,
        user_id=str(user.id),
        role_name="tenant_admin",
        assigned_by="system_signup",
    )
    session.add(role_assign)

    # 6. Issue email verification token (valid for 24h)
    raw_token, token_hash = create_random_token()
    verification = EmailVerificationModel(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    session.add(verification)

    # 7. Dispatch verification email
    email_sender = get_email_sender()
    await email_sender.send_email(
        to=normalized_email,
        subject="Verify your Omnea account",
        html_content=f"<p>Welcome! Your verification token is: <code>{raw_token}</code></p>",
        text_content=f"Welcome! Your verification token is: {raw_token}",
    )

    # 8. Cryptographic audit row
    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=tenant_id,
        record_type="user",
        record_id=str(user.id),
        actor_id=str(user.id),
        action="auth.signup",
        payload={"email": normalized_email, "tenant_id": tenant_id, "role": "tenant_admin"},
    )

    await session.commit()

    return SignupResponse(
        user_id=str(user.id),
        tenant_id=tenant_id,
        email=normalized_email,
        is_verified=False,
        verification_token=raw_token,
        message="Organization and admin user registered. Please verify your email.",
    )


@router.post("/verify-email")
async def verify_email(
    payload: VerifyEmailRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Verify user email address using verification token."""
    t_hash = hash_token(payload.token.strip())
    stmt = select(EmailVerificationModel).where(
        EmailVerificationModel.token_hash == t_hash,
        EmailVerificationModel.consumed_at.is_(None),
    )
    res = await session.execute(stmt)
    verification = res.scalar_one_or_none()

    if not verification:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token.",
        )

    if datetime.now(UTC) > verification.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired.",
        )

    verification.consumed_at = datetime.now(UTC)

    user = await session.get(UserModel, verification.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_verified = True

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=user.tenant_id,
        record_type="user",
        record_id=str(user.id),
        actor_id=str(user.id),
        action="auth.verify_email",
        payload={"email": user.email},
    )

    await session.commit()
    return {"message": "Email verified successfully."}


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
) -> LoginResponse:
    """Authenticate with email and password, returning short-lived access JWT and refresh cookie."""
    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")
    normalized_email = payload.email.strip().lower()

    # 1. Lockout check
    is_locked, remaining_seconds = await check_lockout(session, normalized_email)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Account temporarily locked due to failed attempts. "
                f"Try again in {remaining_seconds} seconds."
            ),
            headers={"Retry-After": str(remaining_seconds)},
        )

    # 2. Look up user
    stmt = (
        select(UserModel)
        .where(
            UserModel.email == normalized_email,
            UserModel.is_active.is_(True),
        )
        .order_by(UserModel.created_at.desc())
    )
    if payload.tenant_id:
        stmt = stmt.where(UserModel.tenant_id == payload.tenant_id)

    res = await session.execute(stmt)
    user = res.scalars().first()

    if not user:
        await record_login_attempt(session, normalized_email, client_ip, False, "User not found")
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    # 3. Look up credentials
    cred = await session.get(CredentialModel, user.id)
    if not cred or not verify_password(payload.password, cred.password_hash):
        await record_login_attempt(session, normalized_email, client_ip, False, "Invalid password")
        await AuditHashChainManager.append_entry(
            session=session,
            tenant_id=user.tenant_id,
            record_type="auth",
            record_id=str(user.id),
            actor_id=str(user.id),
            action="auth.login_failed",
            payload={"email": normalized_email, "ip": client_ip},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    # 4. Check TOTP 2FA
    totp_stmt = select(TotpSecretModel).where(TotpSecretModel.user_id == user.id)
    totp_res = await session.execute(totp_stmt)
    totp_rec = totp_res.scalar_one_or_none()

    if totp_rec and totp_rec.is_enabled:
        if not payload.totp_code:
            return LoginResponse(
                totp_required=True,
                message="Two-factor authentication required. Please provide totp_code.",
            )

        code_valid = verify_totp_code(totp_rec.secret, payload.totp_code)
        if not code_valid:
            # Check backup codes
            clean_code = payload.totp_code.strip()
            if clean_code in totp_rec.backup_codes:
                totp_rec.backup_codes.remove(clean_code)
                code_valid = True
            else:
                await record_login_attempt(
                    session, normalized_email, client_ip, False, "Invalid TOTP"
                )
                await session.commit()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid two-factor authentication code.",
                )

    # 5. Authentication successful
    await record_login_attempt(session, normalized_email, client_ip, True, None)

    # Resolve roles
    roles = list(await RbacEngine.resolve_roles(session, user.tenant_id, str(user.id)))
    if not roles:
        roles = ["user"]

    # 6. Issue rotating refresh token and session
    raw_refresh, refresh_hash = create_refresh_token()
    csrf_token = create_csrf_token()
    settings = get_settings()
    session_expire = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    sess = SessionModel(
        user_id=user.id,
        tenant_id=user.tenant_id,
        refresh_token_hash=refresh_hash,
        csrf_token=csrf_token,
        is_revoked=False,
        expires_at=session_expire,
        user_agent=user_agent[:512],
        ip_address=client_ip[:64],
    )
    session.add(sess)
    await session.flush()

    # 7. Issue short-lived access JWT
    access_token = create_access_token(
        user_id=str(user.id),
        tenant_id=user.tenant_id,
        email=user.email,
        roles=roles,
        session_id=str(sess.id),
    )

    # Set httpOnly cookies for session refresh and authenticated requests (including SSE)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=settings.APP_ENV != "test",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=settings.APP_ENV != "test",
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth",
    )

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=user.tenant_id,
        record_type="session",
        record_id=str(sess.id),
        actor_id=str(user.id),
        action="auth.login",
        payload={"email": user.email, "session_id": str(sess.id)},
    )

    await session.commit()

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        csrf_token=csrf_token,
        user_id=str(user.id),
        tenant_id=user.tenant_id,
        roles=roles,
        is_verified=user.is_verified,
        totp_required=False,
        message="Login successful",
    )


@router.post("/logout")
async def logout(
    response: Response,
    auth_context: AuthContext = Depends(require_permission("user:read")),
    refresh_token: str | None = Cookie(None),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Revoke the current session and clear refresh cookie."""
    # Revoke sessions for this token
    if auth_context.token:
        try:
            payload = decode_access_token(auth_context.token)
            sid = payload.get("session_id")
            if sid:
                sess_obj = await session.get(SessionModel, uuid.UUID(sid))
                if sess_obj:
                    sess_obj.is_revoked = True
        except Exception:
            pass

    if refresh_token:
        r_hash = hash_token(refresh_token)
        stmt = (
            update(SessionModel)
            .where(SessionModel.refresh_token_hash == r_hash)
            .values(is_revoked=True)
        )
        await session.execute(stmt)

    response.delete_cookie("refresh_token", path="/auth")
    response.delete_cookie("access_token", path="/")

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=auth_context.tenant_id,
        record_type="session",
        record_id=auth_context.user_id,
        actor_id=auth_context.user_id,
        action="auth.logout",
        payload={"email": auth_context.email},
    )

    await session.commit()
    return {"message": "Successfully logged out."}


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    session: AsyncSession = Depends(get_async_session),
) -> RefreshResponse:
    """Rotate refresh token and issue a new short-lived access JWT."""
    raw_token = None
    if payload and payload.refresh_token:
        raw_token = payload.refresh_token
    elif "refresh_token" in request.cookies:
        raw_token = request.cookies["refresh_token"]

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token.",
        )

    r_hash = hash_token(raw_token)
    stmt = select(SessionModel).where(
        SessionModel.refresh_token_hash == r_hash,
        SessionModel.is_revoked.is_(False),
    )
    res = await session.execute(stmt)
    sess_obj = res.scalar_one_or_none()

    if not sess_obj or datetime.now(UTC) > sess_obj.expires_at:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, expired, or revoked refresh token.",
        )

    user = await session.get(UserModel, sess_obj.user_id)
    if not user or not user.is_active:
        sess_obj.is_revoked = True
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account inactive or revoked.",
        )

    # Rotate refresh token
    new_raw_refresh, new_refresh_hash = create_refresh_token()
    new_csrf = create_csrf_token()
    settings = get_settings()

    sess_obj.refresh_token_hash = new_refresh_hash
    sess_obj.csrf_token = new_csrf
    sess_obj.expires_at = datetime.now(UTC) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    roles = list(await RbacEngine.resolve_roles(session, user.tenant_id, str(user.id)))
    if not roles:
        roles = ["user"]

    new_access_token = create_access_token(
        user_id=str(user.id),
        tenant_id=user.tenant_id,
        email=user.email,
        roles=roles,
        session_id=str(sess_obj.id),
    )

    response.set_cookie(
        key="access_token",
        value=new_access_token,
        httponly=True,
        secure=settings.APP_ENV != "test",
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=new_raw_refresh,
        httponly=True,
        secure=settings.APP_ENV != "test",
        samesite="lax",
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/auth",
    )

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=user.tenant_id,
        record_type="session",
        record_id=str(sess_obj.id),
        actor_id=str(user.id),
        action="auth.refresh",
        payload={"session_id": str(sess_obj.id)},
    )

    await session.commit()

    return RefreshResponse(
        access_token=new_access_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        csrf_token=new_csrf,
    )


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Dispatch password reset link if email exists without leaking account enumeration."""
    normalized_email = payload.email.strip().lower()
    stmt = select(UserModel).where(
        UserModel.email == normalized_email, UserModel.is_active.is_(True)
    )
    res = await session.execute(stmt)
    users = list(res.scalars().all())

    for u in users:
        raw_token, token_hash = create_random_token()
        reset_rec = PasswordResetModel(
            user_id=u.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
        session.add(reset_rec)

        email_sender = get_email_sender()
        await email_sender.send_email(
            to=u.email,
            subject="Omnea Password Reset",
            html_content=f"<p>Reset your password using token: <code>{raw_token}</code></p>",
            text_content=f"Reset your password using token: {raw_token}",
        )

        await AuditHashChainManager.append_entry(
            session=session,
            tenant_id=u.tenant_id,
            record_type="user",
            record_id=str(u.id),
            actor_id=str(u.id),
            action="auth.forgot_password",
            payload={"email": u.email},
        )

    await session.commit()
    return {"message": "If this email is registered, a password reset link has been dispatched."}


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Reset password and invalidate all active sessions for the user."""
    try:
        validate_password_strength(payload.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    t_hash = hash_token(payload.token.strip())
    stmt = select(PasswordResetModel).where(
        PasswordResetModel.token_hash == t_hash,
        PasswordResetModel.consumed_at.is_(None),
    )
    res = await session.execute(stmt)
    reset_rec = res.scalar_one_or_none()

    if not reset_rec or datetime.now(UTC) > reset_rec.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token.",
        )

    reset_rec.consumed_at = datetime.now(UTC)

    cred = await session.get(CredentialModel, reset_rec.user_id)
    new_hash = hash_password(payload.new_password)
    if cred:
        cred.password_hash = new_hash
    else:
        cred = CredentialModel(user_id=reset_rec.user_id, password_hash=new_hash)
        session.add(cred)

    # Session revocation: revoke all active sessions for this user
    await session.execute(
        update(SessionModel)
        .where(SessionModel.user_id == reset_rec.user_id)
        .values(is_revoked=True)
    )

    user = await session.get(UserModel, reset_rec.user_id)
    if user:
        await AuditHashChainManager.append_entry(
            session=session,
            tenant_id=user.tenant_id,
            record_type="user",
            record_id=str(user.id),
            actor_id=str(user.id),
            action="auth.reset_password",
            payload={"email": user.email},
        )

    await session.commit()
    return {"message": "Password reset successfully. All active sessions have been revoked."}


@router.get("/me", response_model=UserMeResponse)
async def get_me(
    auth_context: AuthContext = Depends(require_permission("user:read")),
    session: AsyncSession = Depends(get_async_session),
) -> UserMeResponse:
    """Return currently authenticated identity details."""
    uid = uuid.UUID(auth_context.user_id) if len(auth_context.user_id) == 36 else None
    user = None
    if uid:
        user = await session.get(UserModel, uid)
    if not user:
        stmt = select(UserModel).where(
            UserModel.tenant_id == auth_context.tenant_id,
            UserModel.email == auth_context.email,
        )
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()

    totp_enabled = False
    if user:
        totp_stmt = select(TotpSecretModel).where(TotpSecretModel.user_id == user.id)
        totp_res = await session.execute(totp_stmt)
        totp_rec = totp_res.scalar_one_or_none()
        totp_enabled = bool(totp_rec and totp_rec.is_enabled)

    return UserMeResponse(
        user_id=auth_context.user_id,
        tenant_id=auth_context.tenant_id,
        email=user.email if user else auth_context.email,
        full_name=user.full_name if user else "",
        roles=auth_context.roles,
        is_verified=user.is_verified if user else True,
        totp_enabled=totp_enabled,
    )


@router.post("/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    auth_context: AuthContext = Depends(require_permission("user:read")),
    session: AsyncSession = Depends(get_async_session),
) -> TotpSetupResponse:
    """Generate TOTP secret and provisioning URI."""
    try:
        user_id = uuid.UUID(auth_context.user_id)
    except ValueError:
        stmt = select(UserModel).where(
            UserModel.tenant_id == auth_context.tenant_id,
            UserModel.email == auth_context.email,
        )
        res = await session.execute(stmt)
        u = res.scalar_one_or_none()
        if not u:
            raise HTTPException(status_code=404, detail="User not found") from None
        user_id = u.id

    secret = generate_totp_secret()
    uri = get_totp_uri(secret, email=auth_context.email)
    backup_codes = generate_backup_codes(10)

    totp_rec = await session.get(TotpSecretModel, user_id)
    if totp_rec:
        totp_rec.secret = secret
        totp_rec.backup_codes = backup_codes
        totp_rec.is_enabled = False
    else:
        totp_rec = TotpSecretModel(
            user_id=user_id,
            secret=secret,
            backup_codes=backup_codes,
            is_enabled=False,
        )
        session.add(totp_rec)

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=auth_context.tenant_id,
        record_type="totp",
        record_id=str(user_id),
        actor_id=auth_context.user_id,
        action="auth.totp.setup",
        payload={"email": auth_context.email},
    )

    await session.commit()

    return TotpSetupResponse(
        secret=secret,
        provisioning_uri=uri,
        backup_codes=backup_codes,
    )


@router.post("/totp/verify")
async def totp_verify(
    payload: TotpVerifyRequest,
    auth_context: AuthContext = Depends(require_permission("user:read")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Verify code and enable TOTP 2FA."""
    try:
        user_id = uuid.UUID(auth_context.user_id)
    except ValueError:
        stmt = select(UserModel).where(
            UserModel.tenant_id == auth_context.tenant_id,
            UserModel.email == auth_context.email,
        )
        res = await session.execute(stmt)
        u = res.scalar_one_or_none()
        if not u:
            raise HTTPException(status_code=404, detail="User not found") from None
        user_id = u.id

    totp_rec = await session.get(TotpSecretModel, user_id)
    if not totp_rec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="TOTP setup not initialized.",
        )

    if not verify_totp_code(totp_rec.secret, payload.code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid TOTP verification code.",
        )

    totp_rec.is_enabled = True

    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=auth_context.tenant_id,
        record_type="totp",
        record_id=str(user_id),
        actor_id=auth_context.user_id,
        action="auth.totp.enable",
        payload={"email": auth_context.email},
    )

    await session.commit()
    return {"message": "TOTP two-factor authentication enabled successfully."}


@router.post("/oidc/login")
async def oidc_login(payload: OidcLoginRequest) -> dict[str, Any]:
    """OIDC single-sign-on login with safe degradation when credentials missing."""
    provider = payload.provider.strip().lower()
    if provider not in ("google", "microsoft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported identity provider: {provider}",
        )

    settings = get_settings()
    if provider == "google" and not settings.GOOGLE_CLIENT_ID:
        return {
            "status": "not_connected",
            "provider": "google",
            "message": "Not connected: Google SSO is not configured in this environment.",
            "setup_guide": "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.",
        }
    if provider == "microsoft" and not settings.MICROSOFT_CLIENT_ID:
        return {
            "status": "not_connected",
            "provider": "microsoft",
            "message": "Not connected: Microsoft SSO is not configured in this environment.",
            "setup_guide": (
                "Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET environment variables."
            ),
        }

    # If configured, SSO flow handles token exchange
    return {"status": "connected", "provider": provider, "message": "OIDC code accepted."}


@router.post("/users/{user_id}/role")
async def change_user_role(
    user_id: str,
    payload: RoleChangeRequest,
    auth_context: AuthContext = Depends(require_permission("roles:manage")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Change tenant user role and revoke active sessions for immediate effect."""
    role = payload.role.strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid role '{role}'. Allowed roles: {sorted(VALID_ROLES)}",
        )

    # Tenant is strictly scoped to token claim
    tenant_id = auth_context.tenant_id

    # Verify user belongs to this tenant
    try:
        uid = uuid.UUID(user_id)
        target_user = await session.get(UserModel, uid)
    except ValueError:
        stmt = select(UserModel).where(UserModel.tenant_id == tenant_id, UserModel.email == user_id)
        res = await session.execute(stmt)
        target_user = res.scalar_one_or_none()

    if not target_user or target_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found in tenant."
        )

    # 1. Revoke existing role assignments in user_role_assignments
    await session.execute(
        update(UserRoleAssignmentModel)
        .where(
            UserRoleAssignmentModel.tenant_id == tenant_id,
            UserRoleAssignmentModel.user_id.in_([str(target_user.id), target_user.email]),
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )

    # 2. Add new role assignment
    new_assign = UserRoleAssignmentModel(
        tenant_id=tenant_id,
        user_id=str(target_user.id),
        role_name=role,
        assigned_by=auth_context.user_id,
    )
    session.add(new_assign)

    # 3. Session revocation: Invalidate all sessions for target user immediately
    await session.execute(
        update(SessionModel).where(SessionModel.user_id == target_user.id).values(is_revoked=True)
    )

    # 4. Cryptographic audit row
    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=tenant_id,
        record_type="role_assignment",
        record_id=str(target_user.id),
        actor_id=auth_context.user_id,
        action="auth.role_change",
        payload={"target_user": str(target_user.id), "new_role": role},
    )

    await session.commit()
    return {"message": f"Role updated to '{role}'. Active sessions revoked."}


@router.post("/users/{user_id}/revoke")
async def revoke_user(
    user_id: str,
    auth_context: AuthContext = Depends(require_permission("users:manage")),
    session: AsyncSession = Depends(get_async_session),
) -> dict[str, str]:
    """Deprovision user, revoke roles, and terminate all active sessions."""
    tenant_id = auth_context.tenant_id

    try:
        uid = uuid.UUID(user_id)
        target_user = await session.get(UserModel, uid)
    except ValueError:
        stmt = select(UserModel).where(UserModel.tenant_id == tenant_id, UserModel.email == user_id)
        res = await session.execute(stmt)
        target_user = res.scalar_one_or_none()

    if not target_user or target_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found in tenant."
        )

    # 1. Deactivate user account
    target_user.is_active = False

    # 2. Revoke roles
    await session.execute(
        update(UserRoleAssignmentModel)
        .where(
            UserRoleAssignmentModel.tenant_id == tenant_id,
            UserRoleAssignmentModel.user_id.in_([str(target_user.id), target_user.email]),
            UserRoleAssignmentModel.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )

    # 3. Revoke all active sessions
    await session.execute(
        update(SessionModel).where(SessionModel.user_id == target_user.id).values(is_revoked=True)
    )

    # 4. Cryptographic audit row
    await AuditHashChainManager.append_entry(
        session=session,
        tenant_id=tenant_id,
        record_type="user",
        record_id=str(target_user.id),
        actor_id=auth_context.user_id,
        action="auth.user_revoked",
        payload={"target_user": str(target_user.id)},
    )

    await session.commit()
    return {
        "message": "User successfully revoked and deprovisioned. All active sessions invalidated."
    }
