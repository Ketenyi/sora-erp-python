"""Core views: authentication, security centre, dashboard and presence API."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.permissions import ADMIN_ROLES, get_access
from apps.core.security import (
    client_ip,
    consume_backup_code,
    generate_backup_codes,
    generate_totp_secret,
    hash_backup_codes,
    login_block,
    mark_totp_used,
    mfa_required_for,
    otpauth_uri,
    password_reset_token,
    record_login_attempt,
    reset_request_blocked,
    security_policy,
    verify_password_reset_token,
    verify_totp,
    verify_totp_counter,
)
from apps.core.services import get_company_settings, get_currency_symbol
from apps.core.uploads import serve_private_media
from apps.saas.catalog import ACCOUNT_COPY_DEFAULTS
from apps.saas.services import (
    default_membership,
    platform_settings,
    render_placeholders,
    send_platform_mail,
    tenant_landing_url,
)


@login_required
def uploaded_file(request, path):
    return serve_private_media(request, path)


def home(request):
    if request.user.is_authenticated:
        return redirect("/dashboard")
    from apps.saas.views import landing

    return landing(request)


def _safe_next(request, candidate: str | None) -> str:
    if not candidate:
        return ""
    try:
        allowed = url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        )
    except Exception:
        return ""
    return candidate if allowed else ""


def _workspace_membership(request, user):
    """Membership the login must land in, honouring a custom tenant domain."""

    from apps.saas.services import default_membership, membership_for

    domain = getattr(request, "custom_domain", None)
    if domain is not None:
        return membership_for(user, domain.tenant_id)
    return default_membership(user)


def _post_login_redirect(request, user):
    next_url = _safe_next(
        request, request.POST.get("next") or request.GET.get("next") or request.session.get("sora_mfa_next")
    )
    request.session.pop("sora_mfa_next", None)
    if next_url:
        return redirect(next_url)
    membership = _workspace_membership(request, user)
    if membership is None:
        if user.is_platform_admin:
            return redirect("/cloud/")
        return redirect("/billing/?no_workspace=1")
    request.session["sora_tenant_id"] = membership.tenant_id
    if membership.tenant.is_locked:
        return redirect("/billing/?locked=1")
    landing = tenant_landing_url(membership.tenant)
    if landing != "/dashboard":
        return redirect(landing)
    if user.role in ADMIN_ROLES:
        return redirect("/admin/dashboard")
    access = get_access(user)
    if access.has_module("sales"):
        return redirect("/sales/dashboard")
    return redirect("/dashboard")


def _complete_login(request, user, *, mfa_verified: bool) -> None:
    login(request, user)
    request.session["sora_last_activity"] = timezone.now().timestamp()
    if mfa_verified:
        request.session["sora_mfa_verified"] = True
    else:
        request.session.pop("sora_mfa_verified", None)


def _lines(value) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _account_copy(data=None, company=None, extra=None) -> dict:
    if data is None:
        data = platform_settings()
    if company is None:
        company = get_company_settings()
    platform_name = data.get("platform_name") or company.get("company_name") or "SORA ERP"
    support_email = data.get("support_email") or "support@soraerp.com"
    placeholders = {"platform": platform_name, "support_email": support_email}
    placeholders.update(extra or {})
    copy = {
        key: render_placeholders(data.get(f"account_{key}") or default, placeholders)
        for key, default in ACCOUNT_COPY_DEFAULTS.items()
    }
    copy["platform_name"] = platform_name
    copy["support_email"] = support_email
    return copy


def _auth_context(request, copy=None, **extra):
    company = get_company_settings()
    data = platform_settings()
    tenant = getattr(request, "tenant", None)
    company_name = company.get("company_name") or "SORA ERP"
    platform_name = data.get("platform_name") or company_name
    support_email = data.get("support_email") or "support@soraerp.com"
    company_logo = company.get("company_logo", "").replace("/assets/uploads/", "/uploads/")
    if tenant is not None:
        company_name = tenant.display_name
        platform_name = tenant.display_name
        if tenant.logo_url:
            company_logo = tenant.logo_url.replace("/assets/uploads/", "/uploads/")
    if copy is None:
        copy = _account_copy(data, company)
    context = {
        "company_name": company_name,
        "company_logo": company_logo,
        "platform_name": platform_name,
        "tenant_branded": tenant is not None,
        "platform_tagline": data.get("platform_tagline")
        or "Run the whole business from one place.",
        "legal_footer": data.get("legal_footer") or f"{platform_name}. All rights reserved.",
        "support_email": support_email,
        "login_kicker": data.get("site_login_kicker") or "Secure workspace access",
        "login_title": data.get("site_login_title") or "Welcome back",
        "login_lead": data.get("site_login_lead")
        or "Sign in to your workspace to pick up where you left off.",
        "login_points": _lines(
            data.get("site_login_points")
            or "Encrypted in transit\nRate-limited sign-in\nOptional two-factor protection"
        ),
        "login_security_note": data.get("site_login_security_note") or "",
        "login_support_note": render_placeholders(
            data.get("site_login_support_note")
            or "Trouble signing in? Contact {support_email}.",
            {"support_email": support_email},
        ),
        "reset_kicker": data.get("site_reset_kicker") or "Account recovery",
        "reset_title": data.get("site_reset_title") or "Reset your password",
        "reset_lead": data.get("site_reset_lead")
        or "Enter the email address on your account and we will send a secure reset link.",
        "reset_success": data.get("site_reset_success")
        or "If an account matches that email, a reset link is on its way.",
        "reset_confirm_title": data.get("site_reset_confirm_title") or "Choose a new password",
        "reset_confirm_lead": data.get("site_reset_confirm_lead")
        or "Use at least eight characters with a mix of letters, numbers and symbols.",
        "reset_invalid": data.get("site_reset_invalid")
        or "This reset link is invalid or has expired.",
        "mfa_kicker": data.get("site_mfa_kicker") or "Second factor",
        "mfa_title": data.get("site_mfa_title") or "Verify it is you",
        "mfa_lead": data.get("site_mfa_lead")
        or "Enter the six-digit code from your authenticator app, or use one of your backup codes.",
        "mfa_help": data.get("site_mfa_help") or "",
        "next_url": _safe_next(request, request.GET.get("next") or request.POST.get("next")),
    }
    context.update(copy)
    context.update(extra)
    if tenant is not None:
        # copy["platform_name"] carries the platform value; keep the custom
        # domain's workspace branding on its own sign-in screen.
        context["company_name"] = tenant.display_name
        context["platform_name"] = tenant.display_name
        if not data.get("legal_footer"):
            context["legal_footer"] = f"{tenant.display_name}. All rights reserved."
    return context


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect("/dashboard")

    copy = _account_copy()
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        ip_address = client_ip(request)
        if not email or not password:
            messages.error(request, copy["login_required"])
        else:
            block = login_block(email, ip_address)
            if block["blocked"]:
                record_login_attempt(
                    email=email,
                    ip_address=ip_address,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    successful=False,
                    reason="blocked",
                )
                minutes = max(1, block["retry_after"] // 60 + 1)
                messages.error(
                    request,
                    render_placeholders(copy["login_locked"], {"minutes": minutes}),
                )
                return render(
                    request,
                    "auth/login.html",
                    _auth_context(request, copy=copy, email=email, blocked=True),
                    status=429,
                )
            user = authenticate(request, username=email, password=password)
            if user is not None:
                custom_domain = getattr(request, "custom_domain", None)
                if custom_domain is not None and _workspace_membership(request, user) is None:
                    record_login_attempt(
                        email=email,
                        ip_address=ip_address,
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        successful=False,
                        reason="wrong_workspace",
                    )
                    messages.error(request, copy["login_wrong_workspace"])
                elif getattr(user, "mfa", None) and user.mfa.enabled:
                    record_login_attempt(
                        email=email,
                        ip_address=ip_address,
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        successful=True,
                        reason="mfa_challenge",
                    )
                    request.session["sora_mfa_user_id"] = user.pk
                    request.session["sora_mfa_started"] = timezone.now().timestamp()
                    request.session["sora_mfa_next"] = _safe_next(
                        request, request.POST.get("next") or request.GET.get("next")
                    )
                    return redirect("/login/verify")
                else:
                    record_login_attempt(
                        email=email,
                        ip_address=ip_address,
                        user_agent=request.META.get("HTTP_USER_AGENT", ""),
                        successful=True,
                        reason="password",
                    )
                    _complete_login(request, user, mfa_verified=False)
                    messages.success(
                        request,
                        render_placeholders(copy["login_success"], {"name": user.get_full_name()}),
                    )
                    return _post_login_redirect(request, user)
            else:
                record_login_attempt(
                    email=email,
                    ip_address=ip_address,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    successful=False,
                    reason="invalid_credentials",
                )
                messages.error(request, copy["login_invalid"])

    if request.GET.get("timeout"):
        messages.info(request, copy["login_timeout"])
    if request.GET.get("mfa"):
        messages.warning(request, copy["login_mfa_note"])

    return render(
        request,
        "auth/login.html",
        _auth_context(request, copy=copy, email=request.POST.get("email", "")),
    )


@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
def login_verify(request):
    from apps.core.models import User

    if request.user.is_authenticated:
        return redirect("/dashboard")

    copy = _account_copy()
    user_id = request.session.get("sora_mfa_user_id")
    started = request.session.get("sora_mfa_started") or 0
    if not user_id or (timezone.now().timestamp() - float(started)) > 600:
        request.session.pop("sora_mfa_user_id", None)
        request.session.pop("sora_mfa_started", None)
        messages.warning(request, copy["login_mfa_expired"])
        return redirect("/login")

    user = User.objects.filter(pk=user_id, status="active").first()
    if user is None or not getattr(user, "mfa", None) or not user.mfa.enabled:
        request.session.pop("sora_mfa_user_id", None)
        return redirect("/login")
    if (
        getattr(request, "custom_domain", None) is not None
        and _workspace_membership(request, user) is None
    ):
        request.session.pop("sora_mfa_user_id", None)
        request.session.pop("sora_mfa_started", None)
        messages.error(request, copy["login_wrong_workspace"])
        return redirect("/login")

    policy = security_policy()
    block = login_block(user.email, client_ip(request))
    if block["blocked"]:
        messages.error(request, copy["login_mfa_locked"])
        return render(
            request,
            "auth/login_verify.html",
            _auth_context(request, copy=copy),
            status=429,
        )

    error = ""
    if request.method == "POST":
        code = (request.POST.get("code") or "").strip()
        counter = verify_totp_counter(user.mfa.secret_plain, code)
        if counter is not None and user.mfa.last_totp_counter is not None:
            if counter <= user.mfa.last_totp_counter:
                counter = None
        valid = counter is not None
        if valid:
            mark_totp_used(user.mfa, counter)
        elif consume_backup_code(user.mfa, code):
            valid = True
        if valid:
            record_login_attempt(
                email=user.email,
                ip_address=client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                successful=True,
                reason="mfa",
            )
            request.session.pop("sora_mfa_user_id", None)
            request.session.pop("sora_mfa_started", None)
            _complete_login(request, user, mfa_verified=True)
            messages.success(
                request,
                render_placeholders(copy["login_success"], {"name": user.get_full_name()}),
            )
            return _post_login_redirect(request, user)
        record_login_attempt(
            email=user.email,
            ip_address=client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            successful=False,
            reason="mfa_failed",
        )
        error = copy["login_mfa_invalid"]

    return render(
        request,
        "auth/login_verify.html",
        _auth_context(
            request,
            copy=copy,
            error=error,
            email=user.email,
            timeout_minutes=policy["login_lockout_minutes"],
        ),
        status=200,
    )


def logout_view(request):
    logout(request)
    messages.info(request, _account_copy()["login_signed_out"])
    return redirect("/login")


def _encode_uid(user) -> str:
    return urlsafe_b64encode(str(user.pk).encode("ascii")).decode("ascii").rstrip("=")


def _decode_uid(value: str):
    padded = (value or "") + "=" * (-len(value or "") % 4)
    try:
        return int(urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except Exception:
        return None


@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
def password_reset_request(request):
    if request.user.is_authenticated:
        return redirect("/security/")

    copy = _account_copy()
    sent = False
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        ip_address = client_ip(request)
        if not email:
            messages.error(request, copy["reset_required_email"])
        else:
            from apps.core.models import User

            policy = security_policy()
            user = User.objects.filter(email__iexact=email, status="active").first()
            if user and not reset_request_blocked(email, ip_address):
                token = password_reset_token(user)
                uid = _encode_uid(user)
                link = request.build_absolute_uri(f"/password-reset/confirm/{uid}/{token}/")
                data = platform_settings()
                context = {
                    "platform": data.get("platform_name") or get_company_settings().get("company_name") or "SORA ERP",
                    "name": user.get_full_name(),
                    "link": link,
                    "minutes": policy["password_reset_ttl_minutes"],
                    "email": user.email,
                }
                subject = render_placeholders(
                    data.get("security_reset_subject") or "Reset your {platform} password",
                    context,
                )
                body = render_placeholders(
                    data.get("security_reset_body")
                    or (
                        "Hi {name},\n\nUse the link below to choose a new password. "
                        "It expires in {minutes} minutes.\n\n{link}\n\n"
                        "If you did not request this, you can ignore this email."
                    ),
                    context,
                )
                try:
                    send_platform_mail(subject, body, [user.email])
                except Exception:
                    pass
                record_login_attempt(
                    email=email,
                    ip_address=ip_address,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    successful=True,
                    reason="password_reset",
                )
            sent = True

    return render(
        request,
        "auth/password_reset.html",
        _auth_context(request, copy=copy, sent=sent),
    )


@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
def password_reset_confirm(request, uidb64, token):
    from apps.core.models import User

    if request.user.is_authenticated:
        return redirect("/security/")

    copy = _account_copy()
    policy = security_policy()
    user = User.objects.filter(pk=_decode_uid(uidb64) or 0, status="active").first()
    valid = bool(user and verify_password_reset_token(user, token, policy["password_reset_ttl_minutes"]))
    if request.method == "POST" and valid:
        password = request.POST.get("password") or ""
        confirm = request.POST.get("confirm_password") or ""
        if password != confirm:
            messages.error(request, copy["reset_mismatch"])
        else:
            try:
                validate_password(password, user)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
            else:
                user.set_password(password)
                user.save(update_fields=["password", "updated_at"])
                record_login_attempt(
                    email=user.email,
                    ip_address=client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    successful=True,
                    reason="password_reset_completed",
                )
                messages.success(request, copy["reset_success_message"])
                return redirect("/login")

    return render(
        request,
        "auth/password_reset_confirm.html",
        _auth_context(request, copy=copy, valid=valid),
        status=200 if valid else 400,
    )


@login_required
def security_center(request):
    from apps.core.models import LoginAttempt, RequestAudit

    mfa = getattr(request.user, "mfa", None)
    backup_codes_left = len(mfa.backup_codes) if mfa else 0
    policy = security_policy()
    copy = _account_copy(
        extra={
            "count": backup_codes_left,
            "minutes": policy["session_timeout_minutes"],
            "attempts": policy["login_max_attempts"],
            "lockout": policy["login_lockout_minutes"],
        }
    )
    attempts = LoginAttempt.objects.filter(email__iexact=request.user.email)[:10]
    audits = RequestAudit.objects.filter(actor=request.user)[:12]
    return render(
        request,
        "core/security.html",
        {
            "page_title": f"Security - {copy['platform_name']}",
            "mfa_enabled": bool(mfa and mfa.enabled),
            "mfa_required": mfa_required_for(request.user),
            "backup_codes_left": backup_codes_left,
            "policy": policy,
            "attempts": attempts,
            "audits": audits,
            **copy,
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def security_mfa(request):
    from apps.core.models import UserMFA

    required = mfa_required_for(request.user)
    mfa = getattr(request.user, "mfa", None)
    enabled = bool(mfa and mfa.enabled)
    backup_codes = request.session.pop("sora_mfa_backup_codes", None)
    error = ""
    copy = _account_copy(
        extra={
            "count": len(mfa.backup_codes) if mfa else 0,
            "date": (
                date_format(mfa.confirmed_at, "j M Y, H:i")
                if mfa and mfa.confirmed_at
                else ""
            ),
        }
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "start" and not enabled:
            request.session["sora_mfa_setup_secret"] = encrypt_secret(generate_totp_secret())
            return redirect("/security/mfa")
        if action == "enable" and not enabled:
            secret = decrypt_secret(request.session.get("sora_mfa_setup_secret", ""))
            code = (request.POST.get("code") or "").strip()
            counter = verify_totp_counter(secret, code) if secret else None
            if not secret:
                error = copy["mfa_error_secret"]
            elif counter is None:
                error = copy["mfa_error_invalid"]
            else:
                codes = generate_backup_codes()
                UserMFA.objects.update_or_create(
                    user=request.user,
                    defaults={
                        "secret": secret,
                        "enabled": True,
                        "backup_codes": hash_backup_codes(codes),
                        "confirmed_at": timezone.now(),
                        "last_totp_counter": counter,
                    },
                )
                request.session.pop("sora_mfa_setup_secret", None)
                request.session["sora_mfa_verified"] = True
                request.session["sora_mfa_backup_codes"] = codes
                messages.success(request, copy["mfa_enabled_success"])
                return redirect("/security/mfa")
        elif action == "disable":
            if required:
                error = copy["mfa_required_disable"]
            elif not request.user.check_password(request.POST.get("password") or ""):
                error = copy["mfa_wrong_password"]
            else:
                code = (request.POST.get("code") or "").strip()
                if not mfa or not (verify_totp(mfa.secret_plain, code) or consume_backup_code(mfa, code)):
                    error = copy["mfa_invalid_code"]
                else:
                    mfa.delete()
                    request.session["sora_mfa_verified"] = True
                    messages.success(request, copy["mfa_disabled_success"])
                    return redirect("/security/")

    setup_secret = ""
    otpauth = ""
    if not enabled:
        stored = request.session.get("sora_mfa_setup_secret")
        if not stored:
            stored = encrypt_secret(generate_totp_secret())
            request.session["sora_mfa_setup_secret"] = stored
        setup_secret = decrypt_secret(stored)
        issuer = platform_settings().get("platform_name") or get_company_settings().get("company_name") or "SORA ERP"
        otpauth = otpauth_uri(setup_secret, request.user.email, issuer)

    return render(
        request,
        "core/security_mfa.html",
        {
            "page_title": "Two-factor authentication - SORA ERP",
            "enabled": enabled,
            "required": required,
            "setup_secret": setup_secret,
            "otpauth": otpauth,
            "backup_codes": backup_codes,
            "error": error,
            "confirmed_at": mfa.confirmed_at if mfa else None,
            "backup_codes_left": len(mfa.backup_codes) if mfa else 0,
            **copy,
        },
    )


@require_http_methods(["GET", "POST"])
@login_required
def security_password(request):
    copy = _account_copy()
    if request.method == "POST":
        current = request.POST.get("current_password") or ""
        password = request.POST.get("password") or ""
        confirm = request.POST.get("confirm_password") or ""
        if not request.user.check_password(current):
            messages.error(request, copy["pass_error_current"])
        elif password != confirm:
            messages.error(request, copy["pass_error_match"])
        else:
            try:
                validate_password(password, request.user)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
            else:
                request.user.set_password(password)
                request.user.save(update_fields=["password", "updated_at"])
                update_session_auth_hash(request, request.user)
                messages.success(request, copy["pass_success"])
                return redirect("/security/")
    return render(
        request,
        "core/security_password.html",
        {"page_title": f"{copy['pass_title']} - {copy['platform_name']}", **copy},
    )


@require_GET
def robots_txt(request):
    return HttpResponse(
        "User-agent: *\nDisallow: /\n",
        content_type="text/plain; charset=utf-8",
    )


@login_required
def dashboard(request):
    from apps.core.models import (
        Attendance,
        BankAccount,
        Client,
        Employee,
        Invoice,
        Quote,
    )

    access = get_access(request.user)
    company = get_company_settings()
    currency = get_currency_symbol(company)
    today = timezone.localdate()
    month_start = today.replace(day=1)

    show_quotes = access.can("sales", "view")
    show_invoices = access.can("accounts", "view") or access.can("sales", "view")
    show_clients = access.can("crm", "view") or access.can("sales", "view")
    show_bank = access.can("accounts", "view")
    show_revenue = access.can("accounts", "view")

    stats = {"quotes": 0, "invoices": 0, "clients": 0, "bank_accounts": 0}
    today_quotes = month_quotes = 0
    pending_invoices = overdue_invoices = 0
    total_revenue = month_revenue = 0

    if show_quotes:
        stats["quotes"] = Quote.objects.count()
        today_quotes = Quote.objects.filter(created_at__date=today).count()
        month_quotes = Quote.objects.filter(
            created_at__year=today.year, created_at__month=today.month
        ).count()
    if show_invoices:
        stats["invoices"] = Invoice.objects.count()
        pending_invoices = Invoice.objects.filter(status__in=["sent", "partial"]).count()
        overdue_invoices = Invoice.objects.filter(
            due_date__lt=today, status__in=["sent", "partial", "overdue"]
        ).count()
        from django.db.models import Sum

        total_revenue = (
            Invoice.objects.filter(status="paid").aggregate(total=Sum("total_amount"))["total"]
            or 0
        )
        month_revenue = (
            Invoice.objects.filter(
                status="paid",
                updated_at__year=today.year,
                updated_at__month=today.month,
            ).aggregate(total=Sum("total_amount"))["total"]
            or 0
        )
    if show_clients:
        stats["clients"] = Client.objects.filter(deleted_at__isnull=True).count()

    bank_accounts = []
    default_bank = None
    if show_bank:
        bank_accounts = list(
            BankAccount.objects.filter(status="active").order_by("-is_default", "bank_name")
        )
        stats["bank_accounts"] = len(bank_accounts)
        default_bank = bank_accounts[0] if bank_accounts else None

    activities = []
    if show_quotes:
        for quote in Quote.objects.order_by("-created_at")[:5]:
            activities.append(
                {
                    "type": "quote",
                    "reference": quote.quote_number,
                    "company_name": _client_name(quote.client_id),
                    "amount": quote.total_amount,
                    "created_at": quote.created_at,
                    "url": f"/sales/quotes/view/{quote.id}",
                }
            )
    if show_invoices:
        for invoice in Invoice.objects.order_by("-created_at")[:5]:
            activities.append(
                {
                    "type": "invoice",
                    "reference": invoice.invoice_number,
                    "company_name": _client_name(invoice.client_id),
                    "amount": invoice.total_amount,
                    "created_at": invoice.created_at,
                    "url": f"/invoices/view/{invoice.id}",
                }
            )
    if show_clients:
        for client in Client.objects.filter(deleted_at__isnull=True).order_by("-created_at")[:5]:
            activities.append(
                {
                    "type": "client",
                    "reference": client.client_code,
                    "company_name": client.company_name,
                    "amount": None,
                    "created_at": client.created_at,
                    "url": f"/crm/clients/view/{client.id}",
                }
            )
    activities.sort(key=lambda item: item["created_at"] or timezone.now(), reverse=True)
    activities = activities[:8]

    employee = _employee_for(request.user)
    today_attendance = None
    if employee:
        today_attendance = Attendance.objects.filter(
            employee_id=employee.id, date=today
        ).first()

    return render(
        request,
        "core/dashboard.html",
        {
            "page_title": "Dashboard - SORA ERP",
            "stats": stats,
            "today_quotes": today_quotes,
            "month_quotes": month_quotes,
            "pending_invoices": pending_invoices,
            "overdue_invoices": overdue_invoices,
            "total_revenue": total_revenue,
            "month_revenue": month_revenue,
            "currency_symbol": currency,
            "show_quotes": show_quotes,
            "show_invoices": show_invoices,
            "show_clients": show_clients,
            "show_bank": show_bank,
            "show_revenue": show_revenue,
            "bank_accounts": bank_accounts,
            "default_bank": default_bank,
            "activities": activities,
            "today_attendance": today_attendance,
        },
    )


def _client_name(client_id):
    from apps.core.models import Client

    if not client_id:
        return ""
    return (
        Client.objects.filter(id=client_id).values_list("company_name", flat=True).first() or ""
    )


def _employee_for(user):
    from apps.core.models import Employee

    if getattr(user, "employee_id", None):
        employee = Employee.objects.filter(id=user.employee_id).first()
        if employee:
            return employee
    return Employee.objects.filter(email=user.email).first()


def _attendance_hours(attendance) -> float:
    if not attendance:
        return 0.0
    if attendance.total_hours:
        return float(attendance.total_hours)
    if attendance.clock_in and attendance.clock_out:
        start = datetime.combine(attendance.date, attendance.clock_in)
        end = datetime.combine(attendance.date, attendance.clock_out)
        if end < start:
            end += timedelta(days=1)
        seconds = (end - start).total_seconds() - (attendance.total_break or 0) * 60
        return round(max(seconds, 0) / 3600, 2)
    return 0.0


@login_required
@require_GET
def presence_status(request):
    from apps.core.models import UserActivityLog

    user = request.user
    employee = _employee_for(user)
    today = timezone.localdate()
    attendance = None
    if employee:
        from apps.core.models import Attendance as AttendanceModel

        attendance = AttendanceModel.objects.filter(
            employee_id=employee.id, date=today
        ).first()

    hours = _attendance_hours(attendance)
    clocked_in = bool(attendance and attendance.clock_in and not attendance.clock_out)

    last = (
        UserActivityLog.objects.filter(user_id=user.id)
        .order_by("-created_at")
        .values("created_at")
        .first()
    )
    last_activity = last["created_at"] if last else None
    now = timezone.now()
    if clocked_in and last_activity and (now - last_activity) < timedelta(minutes=5):
        clock_status = "active"
    elif clocked_in:
        clock_status = "online"
    elif last_activity and (now - last_activity) < timedelta(minutes=15):
        clock_status = "idle"
    else:
        clock_status = "offline"

    return JsonResponse(
        {
            "clock_status": clock_status,
            "status": clock_status,
            "hours_today": hours,
            "clocked_in": clocked_in,
            "clock_in": attendance.clock_in.strftime("%H:%M") if attendance and attendance.clock_in else None,
            "clock_out": attendance.clock_out.strftime("%H:%M") if attendance and attendance.clock_out else None,
            "last_activity": last_activity.isoformat() if last_activity else None,
        }
    )


@login_required
@require_POST
def presence_update(request):
    from apps.core.models import UserActivityLog

    user = request.user
    session_key = request.session.session_key or ""
    last = (
        UserActivityLog.objects.filter(
            user_id=user.id, activity_type="heartbeat", session_id=session_key
        )
        .order_by("-id")
        .first()
    )
    if last:
        last.page_url = request.path[:500]
        last.created_at = timezone.now()
        last.save(update_fields=["page_url", "created_at"])
    else:
        UserActivityLog.objects.create(
            user_id=user.id,
            session_id=session_key,
            activity_type="heartbeat",
            page_url=request.path[:500],
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:1000],
            created_at=timezone.now(),
        )
    return JsonResponse({"success": True})


def _attendance_for_user(user):
    from apps.core.models import Attendance

    employee = _employee_for(user)
    if not employee:
        return None, None
    return employee, Attendance.objects.filter(
        employee_id=employee.id, date=timezone.localdate()
    ).first()


@login_required
@require_POST
def attendance_clock_in(request):
    from apps.core.models import Attendance

    employee, record = _attendance_for_user(request.user)
    if not employee:
        return JsonResponse(
            {"success": False, "message": "Your user is not linked to an employee record."}
        )
    now = timezone.localtime()
    if record and record.clock_in:
        return JsonResponse({"success": False, "message": "You are already clocked in today."})

    if record is None:
        record = Attendance(employee_id=employee.id, date=timezone.localdate())
    record.clock_in = now.time().replace(microsecond=0)
    record.check_in_method = "web"
    record.source = "web"
    record.status = "present"
    if record.clock_in.hour > 9 or (record.clock_in.hour == 9 and record.clock_in.minute > 0):
        record.status = "late"
        start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now.date() == start.date():
            record.late_minutes = int((now - start).total_seconds() // 60)
    record.save()
    return JsonResponse(
        {"success": True, "message": f"Clocked in at {record.clock_in.strftime('%H:%M')}."}
    )


@login_required
@require_POST
def attendance_clock_out(request):
    employee, record = _attendance_for_user(request.user)
    if not employee:
        return JsonResponse(
            {"success": False, "message": "Your user is not linked to an employee record."}
        )
    if not record or not record.clock_in:
        return JsonResponse({"success": False, "message": "You have not clocked in today."})
    if record.clock_out:
        return JsonResponse({"success": False, "message": "You are already clocked out today."})

    now = timezone.localtime()
    record.clock_out = now.time().replace(microsecond=0)
    record.check_out_method = "web"
    start = datetime.combine(record.date, record.clock_in)
    end = datetime.combine(record.date, record.clock_out)
    if end < start:
        end += timedelta(days=1)
    seconds = int((end - start).total_seconds()) - int(record.total_break or 0) * 60
    seconds = max(seconds, 0)
    record.total_seconds = seconds
    record.total_hours = round(seconds / 3600, 2)
    record.save()
    return JsonResponse(
        {"success": True, "message": f"Clocked out at {record.clock_out.strftime('%H:%M')}."}
    )


@require_GET
def health(request):
    return JsonResponse({"status": "ok", "app": "SORA ERP", "time": timezone.now().isoformat()})


def permission_denied(request, exception=None):
    return render(request, "403.html", {"exception": exception}, status=403)


def page_not_found(request, exception=None):
    return render(request, "404.html", status=404)
