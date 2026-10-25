"""Request guards: licences, security headers, audit trail and MFA."""

from __future__ import annotations

import time
import uuid

from django.conf import settings
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone

from apps.core.access_catalog import resolve_targets
from apps.core.permissions import get_access

SKIP_PREFIXES = ("/static/", "/media/", "/uploads/", "/cloud/")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
APPROVAL_HINTS = ("/approve", "/reject", "/decision", "/decide", "/authorize", "/authorise")

AUDIT_SKIP_PREFIXES = (
    "/static/",
    "/media/",
    "/uploads/",
    "/health",
    "/favicon",
    "/robots.txt",
    "/api/presence/",
)
MFA_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/uploads/",
    "/security",
    "/logout",
    "/login",
    "/password-reset",
    "/health",
    "/favicon",
    "/robots.txt",
)
PRIVATE_PREFIXES = (
    "/dashboard",
    "/login",
    "/logout",
    "/password-reset",
    "/security",
    "/billing",
    "/cloud",
    "/admin",
    "/settings",
    "/sales",
    "/crm",
    "/invoices",
    "/accounts",
    "/inventory",
    "/purchasing",
    "/hr",
    "/projects",
    "/helpdesk",
    "/assets",
    "/pos",
    "/announcements",
    "/api/",
)


def _wants_json(request) -> bool:
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


def _path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in prefixes)


class SecurityHeadersMiddleware:
    """Attach hardened response headers to every response, including errors."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
        )
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        user = getattr(request, "user", None)
        if (
            _path_matches(request.path_info, PRIVATE_PREFIXES)
            or (user is not None and user.is_authenticated)
            or response.status_code >= 400
        ):
            headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if not headers.get("Content-Security-Policy"):
            directives = [
                "default-src 'self'",
                "base-uri 'self'",
                "object-src 'none'",
                "frame-ancestors 'none'",
                "form-action 'self'",
                "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
                "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com",
                "font-src 'self' data: https://cdnjs.cloudflare.com https://fonts.gstatic.com",
                "img-src 'self' data: blob: https:",
                "media-src 'self' data: blob:",
                "connect-src 'self'",
                "manifest-src 'self'",
            ]
            if not settings.DEBUG:
                directives.append("upgrade-insecure-requests")
            headers["Content-Security-Policy"] = "; ".join(directives)
        return response


class RequestAuditMiddleware:
    """Persist authenticated write requests without ever touching request bodies."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = uuid.uuid4().hex
        if request.method in SAFE_METHODS or _path_matches(
            request.path_info, AUDIT_SKIP_PREFIXES
        ):
            response = self.get_response(request)
            response.headers.setdefault("X-Request-ID", request.request_id)
            return response

        started = time.monotonic()
        status_code = 500
        try:
            response = self.get_response(request)
            status_code = response.status_code
            return response
        except PermissionDenied:
            status_code = 403
            raise
        except Http404:
            status_code = 404
            raise
        finally:
            self._record(request, status_code, started)

    def _record(self, request, status_code: int, started: float) -> None:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return
        from apps.core.models import RequestAudit
        from apps.core.security import client_ip

        try:
            RequestAudit.objects.create(
                tenant=getattr(request, "tenant", None),
                actor=user,
                actor_email=user.email[:255],
                method=request.method[:10],
                path=request.path_info[:500],
                status_code=status_code,
                ip_address=client_ip(request),
                user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:500],
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                request_id=getattr(request, "request_id", "")[:64],
            )
        except Exception:
            # Auditing must never break a business transaction.
            pass


class MFAEnforcementMiddleware:
    """Idle-session expiry, forced MFA enrolment and login-freshness checks."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return self.get_response(request)

        from apps.core.security import mfa_required_for, security_policy

        policy = security_policy()
        now = timezone.now().timestamp()
        last_seen = request.session.get("sora_last_activity")
        idle_seconds = policy["session_timeout_minutes"] * 60
        if last_seen and (now - float(last_seen)) > idle_seconds:
            logout(request)
            if _wants_json(request):
                return JsonResponse(
                    {"error": "session_expired", "message": "Your session expired."},
                    status=401,
                )
            return redirect("/login?timeout=1")
        request.session["sora_last_activity"] = now

        mfa = getattr(user, "mfa", None)
        enrolled = bool(mfa and mfa.enabled)
        verified = bool(request.session.get("sora_mfa_verified"))

        if enrolled and not verified:
            logout(request)
            return redirect("/login?mfa=1")

        if mfa_required_for(user) and not enrolled:
            if not _path_matches(request.path_info, MFA_EXEMPT_PREFIXES):
                if _wants_json(request):
                    return JsonResponse(
                        {
                            "error": "mfa_required",
                            "message": "Two-factor authentication must be configured.",
                        },
                        status=403,
                    )
                return redirect("/security/mfa?required=1")

        return self.get_response(request)


def _required_action(request) -> str:
    path = request.path_info.lower()
    if request.method in SAFE_METHODS:
        if "/create" in path or path.endswith("/new"):
            return "create"
        if "/edit" in path:
            return "edit"
        if "/delete" in path:
            return "delete"
        return "view"
    if any(hint in path for hint in APPROVAL_HINTS):
        return "approve"
    return "edit"


class SubmoduleAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path_info
        if (
            user is not None
            and getattr(user, "is_authenticated", False)
            and not path.startswith(SKIP_PREFIXES)
        ):
            targets = resolve_targets(path)
            if targets:
                access = get_access(user)
                action = _required_action(request)
                grant_managed = any(
                    access.has_grant(module) for module, _submodule in targets
                )
                if grant_managed:
                    allowed = any(
                        access.can(module, action, submodule)
                        for module, submodule in targets
                    )
                else:
                    allowed = any(
                        access.can(module, "view", submodule)
                        for module, submodule in targets
                    )
                if not allowed:
                    raise PermissionDenied(
                        "Your role does not include access to this part of the module."
                    )
        return self.get_response(request)
