"""Authentication security helpers: throttling, TOTP, backup codes and policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing
from django.utils import timezone

TOTP_DIGITS = 6
TOTP_STEP = 30
TOTP_WINDOW = 1
BACKUP_CODE_COUNT = 10


def client_ip(request) -> str:
    """Return the caller address, honouring proxies only when explicitly enabled."""
    if getattr(settings, "TRUST_PROXY_HEADERS", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45]


def _number(data, key, default, low=1, high=100000):
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def security_policy() -> dict:
    from apps.saas.models import PlatformSetting

    try:
        data = {
            row.key: row.typed_value
            for row in PlatformSetting.objects.filter(key__startswith="security_")
        }
    except Exception:
        data = {}
    return {
        "login_max_attempts": _number(data, "security_login_max_attempts", 5, 1, 100),
        "login_window_minutes": _number(data, "security_login_window_minutes", 15, 1, 1440),
        "login_lockout_minutes": _number(data, "security_login_lockout_minutes", 15, 1, 1440),
        "ip_max_attempts": _number(data, "security_ip_max_attempts", 25, 1, 1000),
        "session_timeout_minutes": _number(data, "security_session_timeout_minutes", 480, 5, 10080),
        "mfa_required_admins": bool(data.get("security_mfa_required_admins")),
        "password_reset_max": _number(data, "security_password_reset_max", 5, 1, 100),
        "password_reset_ttl_minutes": _number(data, "security_reset_ttl_minutes", 60, 5, 1440),
        "audit_retention_days": _number(data, "security_audit_retention_days", 365, 30, 3650),
    }


def record_login_attempt(
    *, email: str, ip_address: str = "", user_agent: str = "", successful: bool, reason: str = ""
):
    from apps.core.models import LoginAttempt

    return LoginAttempt.objects.create(
        email=(email or "")[:255],
        ip_address=(ip_address or "")[:45],
        user_agent=(user_agent or "")[:500],
        successful=successful,
        reason=(reason or "")[:60],
    )


def login_block(email: str, ip_address: str = "") -> dict:
    """Return lockout state for an email and connection."""
    from apps.core.models import LoginAttempt

    policy = security_policy()
    now = timezone.now()
    window_start = now - timedelta(minutes=policy["login_window_minutes"])
    failures = (
        LoginAttempt.objects.filter(created_at__gte=window_start, successful=False)
        .exclude(reason="blocked")
    )
    last_success = (
        LoginAttempt.objects.filter(
            created_at__gte=window_start,
            email__iexact=email,
            successful=True,
            reason__in=["", "password", "mfa"],
        )
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    email_failures = failures.filter(email__iexact=email)
    if last_success:
        email_failures = email_failures.filter(created_at__gt=last_success)
    email_count = email_failures.count()
    ip_count = failures.filter(ip_address=ip_address).count() if ip_address else 0
    blocked_reason = ""
    latest = None
    if email_count >= policy["login_max_attempts"]:
        blocked_reason = "email"
        latest = email_failures.order_by("-created_at").values_list("created_at", flat=True).first()
    if ip_address and ip_count >= policy["ip_max_attempts"]:
        ip_latest = failures.filter(ip_address=ip_address).order_by("-created_at").values_list(
            "created_at", flat=True
        ).first()
        if latest is None or (ip_latest and ip_latest > latest):
            blocked_reason = "ip"
            latest = ip_latest
    if not blocked_reason or latest is None:
        return {"blocked": False, "retry_after": 0, "scope": ""}
    until = latest + timedelta(minutes=policy["login_lockout_minutes"])
    if until <= now:
        return {"blocked": False, "retry_after": 0, "scope": ""}
    return {
        "blocked": True,
        "retry_after": max(1, int((until - now).total_seconds())),
        "scope": blocked_reason,
    }


def reset_request_blocked(email: str, ip_address: str = "") -> bool:
    from apps.core.models import LoginAttempt

    policy = security_policy()
    since = timezone.now() - timedelta(hours=1)
    recent = LoginAttempt.objects.filter(
        created_at__gte=since, reason="password_reset", successful=True
    )
    if recent.filter(email__iexact=email).count() >= policy["password_reset_max"]:
        return True
    return bool(ip_address) and recent.filter(ip_address=ip_address).count() >= policy["password_reset_max"] * 3


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------
def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    padded = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % (10 ** TOTP_DIGITS):0{TOTP_DIGITS}d}"


def verify_totp_counter(
    secret: str, code: str, at=None, window: int = TOTP_WINDOW
) -> int | None:
    """Return the matched time-step so callers can reject replays."""
    code = (code or "").strip().replace(" ", "")
    if not secret or len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    counter = int((at or timezone.now()).timestamp()) // TOTP_STEP
    for offset in range(-window, window + 1):
        candidate = counter + offset
        if hmac.compare_digest(_hotp(secret, candidate), code):
            return candidate
    return None


def verify_totp(secret: str, code: str, at=None, window: int = TOTP_WINDOW) -> bool:
    return verify_totp_counter(secret, code, at=at, window=window) is not None


def otpauth_uri(secret: str, email: str, issuer: str) -> str:
    label = quote(f"{issuer}:{email}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}"
        f"&issuer={quote(issuer, safe='')}&digits={TOTP_DIGITS}&period={TOTP_STEP}"
    )


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> list[str]:
    codes = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(5)).decode("ascii").rstrip("=")[:8]
        codes.append(f"{raw[:4]}-{raw[4:]}")
    return codes


def hash_backup_codes(codes: list[str]) -> list[str]:
    return [make_password(code) for code in codes]


def consume_backup_code(mfa, code: str) -> bool:
    candidate = (code or "").strip().upper().replace(" ", "")
    if not candidate:
        return False
    hashed_codes = list(mfa.backup_codes or [])
    for index, hashed in enumerate(hashed_codes):
        if check_password(candidate, hashed):
            hashed_codes.pop(index)
            mfa.backup_codes = hashed_codes
            mfa.last_used_at = timezone.now()
            mfa.save(update_fields=["backup_codes", "last_used_at", "updated_at"])
            return True
    return False


def mark_totp_used(mfa, counter: int | None = None) -> None:
    mfa.last_used_at = timezone.now()
    fields = ["last_used_at", "updated_at"]
    if counter is not None and (
        mfa.last_totp_counter is None or counter > mfa.last_totp_counter
    ):
        mfa.last_totp_counter = counter
        fields.append("last_totp_counter")
    mfa.save(update_fields=fields)


def mfa_required_for(user) -> bool:
    return bool(
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_platform_admin", False)
        and security_policy()["mfa_required_admins"]
    )


# ---------------------------------------------------------------------------
# Password reset tokens
# ---------------------------------------------------------------------------
RESET_SALT = "sora.password-reset.v1"


def password_reset_token(user) -> str:
    """Signed, time-limited, URL-safe token bound to the current password digest."""
    payload = f"{user.pk}:{(user.password or '')[-16:]}"
    signed = signing.TimestampSigner(salt=RESET_SALT).sign(payload)
    return base64.urlsafe_b64encode(signed.encode("ascii")).decode("ascii").rstrip("=")


def verify_password_reset_token(user, token: str, max_age_minutes: int) -> bool:
    try:
        padded = (token or "") + "=" * (-len(token or "") % 4)
        signed = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        payload = signing.TimestampSigner(salt=RESET_SALT).unsign(
            signed, max_age=max_age_minutes * 60
        )
    except (signing.BadSignature, signing.SignatureExpired, ValueError, UnicodeDecodeError):
        return False
    expected = f"{user.pk}:{(user.password or '')[-16:]}"
    return hmac.compare_digest(payload, expected)


__all__ = [
    "BACKUP_CODE_COUNT",
    "client_ip",
    "consume_backup_code",
    "generate_backup_codes",
    "generate_totp_secret",
    "hash_backup_codes",
    "login_block",
    "mark_totp_used",
    "mfa_required_for",
    "otpauth_uri",
    "password_reset_token",
    "record_login_attempt",
    "reset_request_blocked",
    "security_policy",
    "verify_password_reset_token",
    "verify_totp",
    "verify_totp_counter",
]
