"""Tenant resolution, trial lockout and licence route gating."""

from __future__ import annotations

import time
from contextlib import contextmanager
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import JsonResponse
from django.http.request import split_domain_port
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers

from apps.core.tenancy import tenant_context
from apps.saas.domains import clean_host, domain_for_host, is_platform_host, platform_origin
from apps.saas.services import (
    default_membership,
    enabled_module_codes,
    expire_overdue_tenants,
    membership_for,
    tenant_can_access_path,
    tenant_landing_url,
)

PUBLIC_PREFIXES = (
    "/static/",
    "/uploads/",
    "/media/",
    "/login",
    "/logout",
    "/signup",
    "/pricing",
    "/health",
    "/favicon",
    "/robots.txt",
    "/password-reset",
    "/security/",
    "/contact",
    "/cloud/",
    "/onboard-device",
    "/api/assets/agent/",
)

LOCK_EXEMPT_PREFIXES = (
    "/billing",
    "/api/licence/",
    "/logout",
    "/login",
    "/health",
    "/static/",
    "/uploads/",
    "/media/",
    "/announcements/",
    "/security/",
    "/password-reset",
    "/helpdesk/status/",
)

_expiry_checked_at = 0.0


def _maybe_expire(force: bool = False) -> None:
    global _expiry_checked_at
    now = time.monotonic()
    if not force and now - _expiry_checked_at < 300:
        return
    _expiry_checked_at = now
    try:
        expire_overdue_tenants()
    except Exception:
        pass


def _is_public(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _is_lock_exempt(path: str) -> bool:
    return any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in LOCK_EXEMPT_PREFIXES)


def _wants_json(request) -> bool:
    return (
        request.path.startswith("/api/")
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in (request.headers.get("Accept") or "")
    )


@contextmanager
def _tenant_timezone(tenant):
    name = (tenant.timezone_name or "").strip()
    try:
        ZoneInfo(name)
    except Exception:
        name = ""
    if not name:
        yield
        return
    with timezone.override(name):
        yield


class HostValidationMiddleware:
    """Reject unknown hosts and attach the custom domain row to the request.

    Django's ALLOWED_HOSTS also contains ``*`` so customer hostnames reach this
    middleware. Only platform hosts, verified tenant domains and domains still
    awaiting (or suspended from) verification are allowed through.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.custom_domain = None
        request.host_name = ""
        if not getattr(settings, "SORA_CUSTOM_DOMAINS", False):
            return self.get_response(request)

        raw_host = request.get_host()
        domain, _port = split_domain_port(raw_host)
        host = clean_host(domain or raw_host)
        if not host:
            raise DisallowedHost(f"Invalid HTTP_HOST header: {raw_host!r}.")
        request.host_name = host

        if is_platform_host(host):
            return self.get_response(request)

        row = domain_for_host(host)
        if row is None:
            raise DisallowedHost(f"Invalid HTTP_HOST header: {raw_host!r}.")
        request.custom_domain = row
        return self.get_response(request)


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        request.enabled_module_codes = set()
        request.impersonator = None
        request.impersonating = False

        path = request.path
        custom = getattr(request, "custom_domain", None)
        if custom is not None and custom.status != "verified":
            return self._domain_unavailable(request, custom)

        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            if custom is not None:
                tenant = custom.tenant
                request.tenant = tenant
                with tenant_context(tenant), _tenant_timezone(tenant):
                    if path == "/" or path.rstrip("/") in {"/pricing", "/signup", "/contact"}:
                        return redirect("/login")
                    return self.get_response(request)
            return self.get_response(request)

        _maybe_expire()

        impersonator_id = request.session.get("sora_impersonator_id")
        if impersonator_id:
            from apps.core.models import User

            request.impersonator = User.objects.filter(pk=impersonator_id).first()
            request.impersonating = request.impersonator is not None

        membership = None
        if custom is not None:
            membership = membership_for(user, custom.tenant_id)
            if membership is None:
                public_paths = {
                    "/health",
                    "/favicon.ico",
                    "/robots.txt",
                }
                if path == "/logout" or path.startswith("/cloud/"):
                    request.tenant = custom.tenant
                    with tenant_context(custom.tenant), _tenant_timezone(custom.tenant):
                        return self.get_response(request)
                if path in public_paths:
                    return self.get_response(request)
                return self._no_access(request, custom)
            request.session["sora_tenant_id"] = custom.tenant_id
        else:
            session_tenant_id = request.session.get("sora_tenant_id")
            if session_tenant_id:
                membership = membership_for(user, session_tenant_id)
                if membership is None:
                    request.session.pop("sora_tenant_id", None)
            if membership is None:
                membership = default_membership(user)
        if membership is not None:
            request.session["sora_tenant_id"] = membership.tenant_id
            request.tenant = membership.tenant
            request.membership = membership
            request.enabled_module_codes = enabled_module_codes(membership.tenant)

        if membership is None:
            if getattr(user, "is_platform_admin", False):
                if not path.startswith("/cloud") and not _is_public(path):
                    return redirect("/cloud/")
            elif not _is_lock_exempt(path):
                return redirect("/billing/?no_workspace=1")
            return self.get_response(request)

        tenant = request.tenant
        bypass_lock = bool(getattr(user, "is_platform_admin", False) or request.impersonating)

        with tenant_context(tenant), _tenant_timezone(tenant):
            if tenant.is_locked and not bypass_lock and not _is_lock_exempt(path):
                if _wants_json(request):
                    response = JsonResponse(
                        {
                            "error": "subscription_locked",
                            "message": tenant.lock_reason,
                            "billing_url": "/billing/",
                        },
                        status=402,
                    )
                    add_never_cache_headers(response)
                    return response
                return redirect("/billing/?locked=1")

            if not tenant_can_access_path(tenant, path):
                if _wants_json(request):
                    return JsonResponse(
                        {
                            "error": "module_not_licensed",
                            "message": "This module is not included in your SORA subscription.",
                        },
                        status=403,
                    )
                from django.core.exceptions import PermissionDenied

                raise PermissionDenied(
                    "This module is not included in your current SORA subscription. "
                    "Your administrator can add it from Billing & Plan."
                )

            if path == "/dashboard" and request.method == "GET":
                landing = tenant_landing_url(tenant)
                if landing != "/dashboard":
                    return redirect(landing)

            return self.get_response(request)

    def _domain_unavailable(self, request, domain):
        if _wants_json(request):
            response = JsonResponse(
                {
                    "error": "domain_not_verified",
                    "message": "This custom domain is not ready for this workspace.",
                },
                status=404,
            )
            add_never_cache_headers(response)
            return response
        tenant = domain.tenant
        status = 403 if domain.status == "disabled" else 404
        with tenant_context(tenant), _tenant_timezone(tenant):
            request.tenant = tenant
            return render(
                request,
                "saas/domain_unavailable.html",
                {
                    "domain": domain,
                    "tenant": tenant,
                    "platform_url": platform_origin(),
                    "page_title": "Domain not ready",
                },
                status=status,
            )

    def _no_access(self, request, domain):
        if _wants_json(request):
            response = JsonResponse(
                {
                    "error": "workspace_access_denied",
                    "message": "Your account does not have access to this workspace.",
                },
                status=403,
            )
            add_never_cache_headers(response)
            return response
        tenant = domain.tenant
        with tenant_context(tenant), _tenant_timezone(tenant):
            request.tenant = tenant
            return render(
                request,
                "saas/domain_no_access.html",
                {
                    "domain": domain,
                    "tenant": tenant,
                    "platform_url": platform_origin(),
                    "page_title": "Workspace access",
                },
                status=403,
            )
