"""Public portal, tenant billing and SORA Cloud Control views."""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from urllib.parse import quote

import json

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.models import User
from apps.core.services import save_company_settings
from apps.core.ui import paginate
from apps.saas.catalog import SITE_COPY_DEFAULTS
from apps.saas.domains import (
    DomainError,
    claim_domain,
    disable_domain,
    dns_target,
    remove_domain,
    set_primary_domain,
    verify_domain,
)
from apps.saas.forms import (
    AnnouncementForm,
    CloudPaymentForm,
    CloudTenantCreateForm,
    CloudTenantEditForm,
    DomainClaimForm,
    ExtendTrialForm,
    PlanForm,
    PlatformSettingsForm,
    PublicLeadForm,
    RenewalForm,
    SalesLeadConvertForm,
    SalesLeadForm,
    SalesLeadNoteForm,
    SalesLeadUpdateForm,
    SignupForm,
    TenantProfileForm,
    TenantStatusForm,
)
from apps.saas.gateways import (
    GatewayError,
    apply_mpesa_result,
    callback_receipt,
    complete_payment,
    fail_payment,
    gateway_status,
    mpesa_callback_token_valid,
    mpesa_callback_url,
    query_mpesa_status,
    retrieve_stripe_session,
    start_mpesa_stk,
    start_stripe_checkout,
    stripe_event_payment,
    stripe_payment_matches,
    verify_stripe_signature,
)
from apps.saas.models import (
    Announcement,
    AnnouncementDismissal,
    ImpersonationLog,
    PlatformSetting,
    SALES_LEAD_STATUSES,
    SaasModule,
    SaasPlan,
    SalesLead,
    SalesLeadActivity,
    Tenant,
    TenantAuditLog,
    TenantDomain,
    TenantMembership,
    TenantModule,
    TenantPayment,
)
from apps.saas.services import (
    all_modules,
    available_modules,
    capture_sales_lead,
    enabled_module_map,
    expire_overdue_tenants,
    extend_trial,
    licence_manifest,
    module_map,
    plan_monthly_value,
    platform_settings,
    provision_tenant,
    public_plans,
    record_payment,
    render_placeholders,
    send_platform_mail,
    send_welcome_email,
    set_tenant_status,
    tenant_admins,
    tenant_landing_url,
    tenant_metrics,
    toggle_tenant_module,
    trial_banner,
)

SETTING_GROUPS = [
    ("brand", "Brand & look", "palette"),
    ("site", "Public website", "globe"),
    ("account", "Account screens", "user-shield"),
    ("leads", "Sales & leads", "bullseye"),
    ("billing", "Trial & billing", "credit-card"),
    ("payments", "Payment gateways", "wallet"),
    ("email", "Mail server", "envelope"),
    ("domains", "Custom domains", "globe"),
    ("security", "Security", "shield-halved"),
    ("documents", "Document headers", "file-lines"),
    ("notifications", "Notification rules", "bell"),
]


def cloud_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login?next={quote(request.get_full_path())}")
        if not getattr(request.user, "is_platform_admin", False):
            raise PermissionDenied("SORA Cloud Control is restricted to platform administrators.")
        return view(request, *args, **kwargs)

    return wrapper


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _safe_next(request, fallback="/dashboard"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def _plan_cards(copy=None):
    modules = module_map()
    cta_template = (copy or {}).get("plan_cta") or "Start with {plan}"
    cards = []
    for plan in public_plans():
        names = [
            modules[code].brand_name if code in modules else code
            for code in (plan.included_modules or [])
        ]
        features = list(plan.features or [])
        seat_line = f"Up to {plan.max_users} users" if plan.max_users else "Unlimited users"
        if not any("user" in feature.lower() for feature in features):
            features.insert(0, seat_line)
        cards.append(
            {
                "plan": plan,
                "module_names": names,
                "features": features,
                "cta": render_placeholders(cta_template, {"plan": plan.name}),
            }
        )
    return cards


def _setting_lines(value):
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def _mock_stats(value):
    stats = []
    for line in _setting_lines(value):
        label, _, rest = line.partition("|")
        amount, _, delta = rest.partition("|")
        if label.strip():
            stats.append(
                {"label": label.strip(), "value": amount.strip(), "delta": delta.strip()}
            )
    return stats


def _public_site_context(data):
    trial_days = data.get("trial_days") or 30
    placeholders = {
        "trial_days": trial_days,
        "platform": data.get("platform_name") or "SORA ERP",
        "company": data.get("site_mock_company") or "Apex Networks",
    }
    copy = {
        key: render_placeholders(data.get(f"site_{key}") or default, placeholders)
        for key, default in SITE_COPY_DEFAULTS.items()
    }
    steps = [
        {
            "number": number,
            "title": data.get(f"site_step{number}_title") or "",
            "body": data.get(f"site_step{number}_body") or "",
        }
        for number in (1, 2, 3)
    ]
    return {
        "trial_days": trial_days,
        "trust_points": _setting_lines(data.get("site_trust_points")),
        "steps": steps,
        "copy": copy,
        "mock_stats": _mock_stats(copy["mock_stats"]),
        "mock_chips": _setting_lines(copy["mock_chips"]),
        "signup_points": _setting_lines(copy["signup_points"]),
        "site": {
            "pricing_lead": render_placeholders(
                data.get("site_pricing_lead") or "", {"trial_days": trial_days}
            ),
            "pricing_page_lead": render_placeholders(
                data.get("site_pricing_page_lead") or "", {"trial_days": trial_days}
            ),
            "pricing_calc_lead": render_placeholders(
                data.get("site_pricing_calc_lead") or "", {"trial_days": trial_days}
            ),
        },
    }


# ---------------------------------------------------------------------------
# Public portal
# ---------------------------------------------------------------------------
def landing(request):
    if request.user.is_authenticated:
        return redirect("/dashboard")
    settings_data = platform_settings()
    site_context = _public_site_context(settings_data)
    context = {
        "platform": settings_data,
        "plans": _plan_cards(site_context["copy"]),
        "modules": available_modules(),
        "settings_data": settings_data,
    }
    context.update(site_context)
    return render(request, "saas/landing.html", context)


def pricing(request):
    settings_data = platform_settings()
    site_context = _public_site_context(settings_data)
    context = {
        "platform": settings_data,
        "plans": _plan_cards(site_context["copy"]),
        "modules": available_modules(),
    }
    context.update(site_context)
    return render(request, "saas/pricing.html", context)


@require_http_methods(["GET", "POST"])
def contact(request):
    settings_data = platform_settings()
    site_context = _public_site_context(settings_data)
    sent = request.GET.get("sent") == "1"
    form = PublicLeadForm(
        request.POST or None,
        initial={"modules": request.GET.getlist("module")},
    )
    if request.method == "POST" and form.is_valid():
        cleaned = form.cleaned_data
        now = timezone.now()
        ip_address = _client_ip(request)
        busy_ip = (
            bool(ip_address)
            and SalesLead.objects.filter(
                ip_address=ip_address, created_at__gte=now - timedelta(hours=1)
            ).count()
            >= 8
        )
        if busy_ip:
            form.add_error(None, site_context["copy"]["contact_rate_limited"])
        else:
            duplicate = SalesLead.objects.filter(
                email__iexact=cleaned["email"], created_at__gte=now - timedelta(minutes=10)
            ).exists()
            if not duplicate:
                capture_sales_lead(
                    name=cleaned["name"],
                    email=cleaned["email"],
                    company=cleaned.get("company", ""),
                    phone=cleaned.get("phone", ""),
                    country=cleaned.get("country", ""),
                    modules=cleaned.get("modules") or [],
                    message=cleaned.get("message", ""),
                    source="website",
                    ip_address=ip_address,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    lead_url=request.build_absolute_uri("/cloud/leads/"),
                )
            messages.success(
                request,
                settings_data.get("lead_success_message")
                or "Thank you — your request is in. Our team will be in touch shortly.",
            )
            return redirect("/contact?sent=1")
    return render(
        request,
        "saas/contact.html",
        {
            "platform": settings_data,
            "form": form,
            "sent": sent,
            "modules": available_modules(),
            "lead_points": _setting_lines(settings_data.get("lead_aside_points")),
            **site_context,
        },
    )


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("/dashboard")
    settings_data = platform_settings()
    site_context = _public_site_context(settings_data)
    copy = site_context["copy"]
    selected_modules = []
    if request.method == "POST":
        selected_modules = request.POST.getlist("modules")
        form = SignupForm(request.POST)
        if form.is_valid():
            try:
                result = provision_tenant(
                    company_name=form.cleaned_data["company_name"],
                    admin_email=form.cleaned_data["email"],
                    password=form.cleaned_data["password"],
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    phone=form.cleaned_data["phone"],
                    module_codes=form.cleaned_data["modules"],
                )
            except ValueError as exc:
                form.add_error("email", str(exc))
            else:
                tenant = result["tenant"]
                user = result["user"]
                auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                request.session["sora_tenant_id"] = tenant.id
                email_sent = send_welcome_email(
                    tenant, user, request.build_absolute_uri("/login")
                )
                if settings_data.get("notify_new_tenant") and settings_data.get("sales_email"):
                    send_platform_mail(
                        f"New trial: {tenant.company_name}",
                        f"{tenant.company_name} ({tenant.slug}) started a trial with "
                        f"{len(result['modules'])} module(s). Contact: {user.email}",
                        [settings_data["sales_email"]],
                    )
                messages.success(request, copy["signup_success"])
                if not email_sent:
                    messages.info(
                        request,
                        render_placeholders(copy["signup_email_failed"], {"email": user.email}),
                    )
                return redirect(tenant_landing_url(tenant))
    else:
        initial_modules = [
            code
            for code in (request.GET.get("modules") or "").split(",")
            if code
        ]
        plan_code = request.GET.get("plan")
        if plan_code:
            plan = SaasPlan.objects.filter(code=plan_code, is_active=True).first()
            if plan:
                initial_modules = list(plan.included_modules or [])
        form = SignupForm(initial={"modules": initial_modules} if initial_modules else None)
        selected_modules = list(initial_modules)
    return render(
        request,
        "saas/signup.html",
        {
            "form": form,
            "platform": settings_data,
            "modules": available_modules(),
            "selected_modules": selected_modules,
            **site_context,
        },
    )


# ---------------------------------------------------------------------------
# Tenant billing
# ---------------------------------------------------------------------------
@login_required
def billing(request):
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    settings_data = platform_settings()

    if tenant is None:
        return render(
            request,
            "saas/billing.html",
            {
                "no_workspace": True,
                "platform": settings_data,
                "page_title": "Billing & Plan",
            },
        )

    is_admin = bool(membership and membership.can_manage_billing)
    gateways = gateway_status()
    renewal_form = RenewalForm(
        tenant=tenant,
        stripe_enabled=gateways["stripe"]["ready"],
        mpesa_enabled=gateways["mpesa"]["ready"],
    )
    profile_form = TenantProfileForm(instance=tenant)

    if request.method == "GET" and request.GET.get("checkout") == "cancelled":
        messages.info(request, "Stripe checkout was cancelled. Your workspace has not been charged.")

    if request.method == "POST":
        action = request.POST.get("action")
        if not is_admin:
            raise PermissionDenied("Only workspace owners and administrators can manage billing.")
        if action == "renewal":
            renewal_form = RenewalForm(
                request.POST,
                tenant=tenant,
                stripe_enabled=gateways["stripe"]["ready"],
                mpesa_enabled=gateways["mpesa"]["ready"],
            )
            if renewal_form.is_valid():
                plan = renewal_form.cleaned_data["plan"]
                cycle = renewal_form.cleaned_data["billing_cycle"]
                amount = renewal_form.amount()
                channel = renewal_form.cleaned_data["method"]
                method = "mpesa" if channel == "mpesa_instant" else channel
                payment = TenantPayment.objects.create(
                    tenant=tenant,
                    plan=plan,
                    amount=amount,
                    currency=tenant.currency or "KES",
                    method=method,
                    reference=renewal_form.cleaned_data["reference"],
                    status="pending",
                    billing_cycle=cycle,
                    notes=renewal_form.cleaned_data["notes"],
                    recorded_by=request.user,
                )
                TenantAuditLog.objects.create(
                    tenant=tenant,
                    actor=request.user,
                    action="payment.submitted",
                    details=f"{amount} {tenant.currency} via {payment.get_method_display()} awaiting verification.",
                    ip_address=_client_ip(request),
                )
                if channel in {"stripe", "mpesa_instant"}:
                    return _start_gateway_payment(
                        request,
                        payment,
                        channel,
                        phone=renewal_form.cleaned_data.get("phone_number") or "",
                    )
                if settings_data.get("notify_new_payment") and settings_data.get("billing_email"):
                    send_platform_mail(
                        f"Payment submitted: {tenant.company_name}",
                        f"{tenant.company_name} submitted {amount} {tenant.currency} "
                        f"via {payment.get_method_display()} (ref: {payment.reference or 'n/a'}).",
                        [settings_data["billing_email"]],
                    )
                messages.success(
                    request,
                    "Payment details submitted. Our billing team will verify and activate your "
                    "subscription shortly.",
                )
                return redirect("/billing/")
            messages.error(request, "Please correct the errors in the renewal form.")
        elif action == "profile":
            if tenant.is_locked:
                messages.error(request, "Your workspace is read-only until the subscription is renewed.")
                return redirect("/billing/")
            profile_form = TenantProfileForm(request.POST, instance=tenant)
            if profile_form.is_valid():
                tenant = profile_form.save()
                save_company_settings(
                    {
                        "company_name": tenant.company_name,
                        "company_phone": tenant.phone_number or "",
                    }
                )
                TenantAuditLog.objects.create(
                    tenant=tenant,
                    actor=request.user,
                    action="profile.updated",
                    details="Workspace branding and contact details updated.",
                    ip_address=_client_ip(request),
                )
                messages.success(request, "Workspace profile updated.")
                return redirect("/billing/")
            messages.error(request, "Please correct the errors in the profile form.")

    expire_overdue_tenants()
    tenant.refresh_from_db()
    payments = tenant.payments.select_related("plan").order_by("-created_at")[:25]
    modules = enabled_module_map(tenant)
    plans = public_plans()
    domains = list(tenant.domains.all()) if is_admin else []
    try:
        domain_limit = max(int(settings_data.get("domain_max_per_tenant") or 10), 1)
    except (TypeError, ValueError):
        domain_limit = 10
    return render(
        request,
        "saas/billing.html",
        {
            "tenant": tenant,
            "membership": membership,
            "is_admin": is_admin,
            "renewal_form": renewal_form,
            "profile_form": profile_form,
            "payments": payments,
            "payments_pending": tenant.payments.filter(status="pending").count(),
            "licence_rows": list(modules.values()),
            "trial": trial_banner(tenant),
            "plans": plans,
            "platform": settings_data,
            "gateways": gateways,
            "domains": domains,
            "domain_form": DomainClaimForm(),
            "domain_limit": domain_limit,
            "dns_target": dns_target(request),
            "page_title": "Billing & Plan",
            "locked": tenant.is_locked,
        },
    )


def _domain_tenant(request) -> Tenant:
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    if tenant is None:
        raise PermissionDenied("No workspace is selected.")
    if not membership or not membership.can_manage_billing:
        raise PermissionDenied("Only workspace owners and administrators can manage domains.")
    return tenant


def _domain_write_allowed(request, tenant) -> bool:
    if tenant.is_locked:
        messages.error(request, "Custom domains are read-only until the subscription is renewed.")
        return False
    return True


@login_required
@require_POST
def billing_domain_add(request):
    tenant = _domain_tenant(request)
    if not _domain_write_allowed(request, tenant):
        return redirect("/billing/#domains")
    form = DomainClaimForm(request.POST)
    if form.is_valid():
        try:
            domain = claim_domain(
                tenant,
                form.cleaned_data["hostname"],
                actor=request.user,
                ip_address=_client_ip(request),
            )
        except DomainError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Add the DNS TXT record shown for {domain.hostname}, then check verification.",
            )
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("/billing/#domains")


@login_required
@require_POST
def billing_domain_verify(request, domain_id: int):
    tenant = _domain_tenant(request)
    if not _domain_write_allowed(request, tenant):
        return redirect("/billing/#domains")
    domain = get_object_or_404(TenantDomain, pk=domain_id, tenant=tenant)
    try:
        result = verify_domain(
            domain, actor=request.user, ip_address=_client_ip(request)
        )
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        if result["matched"]:
            messages.success(
                request,
                f"{domain.hostname} is verified and now routes to {tenant.display_name}.",
            )
        else:
            messages.warning(request, f"Not verified yet. {domain.last_error}")
    return redirect("/billing/#domains")


@login_required
@require_POST
def billing_domain_primary(request, domain_id: int):
    tenant = _domain_tenant(request)
    if not _domain_write_allowed(request, tenant):
        return redirect("/billing/#domains")
    domain = get_object_or_404(TenantDomain, pk=domain_id, tenant=tenant)
    try:
        set_primary_domain(domain, actor=request.user, ip_address=_client_ip(request))
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{domain.hostname} is now the primary domain.")
    return redirect("/billing/#domains")


@login_required
@require_POST
def billing_domain_remove(request, domain_id: int):
    tenant = _domain_tenant(request)
    if not _domain_write_allowed(request, tenant):
        return redirect("/billing/#domains")
    domain = get_object_or_404(TenantDomain, pk=domain_id, tenant=tenant)
    host = domain.hostname
    remove_domain(domain, actor=request.user, ip_address=_client_ip(request))
    messages.success(request, f"{host} was removed from this workspace.")
    return redirect("/billing/#domains")


def _start_gateway_payment(request, payment, channel, *, phone=""):
    try:
        if channel == "stripe":
            success_url = (
                request.build_absolute_uri(
                    f"/billing/gateway/stripe/return/?payment={payment.id}"
                )
                + "&session_id={CHECKOUT_SESSION_ID}"
            )
            cancel_url = request.build_absolute_uri("/billing/?checkout=cancelled")
            result = start_stripe_checkout(
                payment, success_url=success_url, cancel_url=cancel_url
            )
            TenantAuditLog.objects.create(
                tenant=payment.tenant,
                actor=request.user,
                action="payment.gateway_started",
                details=f"Stripe checkout {result['id']} opened for {payment.amount} {payment.currency}.",
                ip_address=_client_ip(request),
            )
            return redirect(result["url"])
        callback_url = mpesa_callback_url(request)
        result = start_mpesa_stk(payment, phone=phone, callback_url=callback_url)
        TenantAuditLog.objects.create(
            tenant=payment.tenant,
            actor=request.user,
            action="payment.gateway_started",
            details=(
                f"M-Pesa STK push {result['checkout_request_id']} sent for "
                f"{payment.amount} {payment.currency}."
            ),
            ip_address=_client_ip(request),
        )
        messages.success(request, result["message"])
        return redirect("/billing/")
    except GatewayError as error:
        fail_payment(payment, str(error))
        messages.error(request, f"Online payment could not be started: {error}")
        return redirect("/billing/")


def _billing_admin_payment(request, pk):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404
    membership = getattr(request, "membership", None)
    if not (membership and membership.can_manage_billing):
        raise PermissionDenied("Only workspace owners and administrators can manage billing.")
    return get_object_or_404(
        TenantPayment.objects.select_related("tenant", "plan"), pk=pk, tenant=tenant
    )


@login_required
@require_POST
def billing_stripe_start(request, pk):
    payment = _billing_admin_payment(request, pk)
    if payment.status != "pending" or payment.method != "stripe":
        messages.error(request, "This payment is not awaiting a card checkout.")
        return redirect("/billing/")
    return _start_gateway_payment(request, payment, "stripe")


@login_required
def billing_stripe_return(request):
    payment_id = request.GET.get("payment")
    if not payment_id:
        raise Http404
    payment = _billing_admin_payment(request, payment_id)
    if payment.status == "completed":
        messages.info(request, "This payment has already been confirmed.")
        return redirect("/billing/")
    session_id = request.GET.get("session_id") or payment.gateway_checkout_id
    if not session_id:
        messages.error(request, "The Stripe checkout reference is missing.")
        return redirect("/billing/")
    if payment.gateway_checkout_id and session_id != payment.gateway_checkout_id:
        messages.error(request, "The Stripe checkout reference does not match this payment.")
        return redirect("/billing/")
    try:
        session = retrieve_stripe_session(session_id)
    except GatewayError as error:
        messages.error(request, f"Could not verify the card payment: {error}")
        return redirect("/billing/")
    metadata_payment = (session.get("metadata") or {}).get("payment_id")
    if metadata_payment and str(metadata_payment) != str(payment.id):
        messages.error(request, "The Stripe session belongs to a different payment.")
        return redirect("/billing/")
    if session.get("payment_status") != "paid" and session.get("status") != "complete":
        messages.info(request, "Stripe has not confirmed the payment yet. Refresh this page shortly.")
        return redirect("/billing/")
    if not stripe_payment_matches(payment, session):
        messages.error(request, "The paid amount or currency does not match this invoice.")
        return redirect("/billing/")
    try:
        complete_payment(
            payment,
            reference=session.get("payment_intent") or session_id,
            payload={"checkout_session": session},
            actor=request.user,
        )
    except GatewayError as error:
        messages.error(request, f"The payment was received but activation failed: {error}")
        return redirect("/billing/")
    messages.success(request, "Card payment received. Your subscription is active.")
    return redirect("/billing/")


@login_required
@require_POST
def billing_mpesa_status(request, pk):
    payment = _billing_admin_payment(request, pk)
    if payment.status != "pending" or payment.method != "mpesa" or not payment.gateway_checkout_id:
        messages.error(request, "This payment has no pending M-Pesa prompt.")
        return redirect("/billing/")
    try:
        result = query_mpesa_status(payment)
    except GatewayError as error:
        messages.error(request, f"Could not check the M-Pesa payment: {error}")
        return redirect("/billing/")
    code = result.get("ResultCode")
    if code is None:
        messages.info(
            request,
            result.get("errorMessage")
            or result.get("ResponseDescription")
            or "M-Pesa is still processing the prompt. Try again shortly.",
        )
        return redirect("/billing/")
    apply_mpesa_result(
        payment,
        result_code=code,
        result_desc=result.get("ResultDesc") or "",
        receipt=result.get("MpesaReceiptNumber") or "",
        payload=result,
    )
    payment.refresh_from_db()
    if payment.status == "completed":
        messages.success(request, "M-Pesa payment confirmed. Your subscription is active.")
    else:
        messages.error(request, f"M-Pesa did not complete this payment: {result.get('ResultDesc') or code}")
    return redirect("/billing/")


@csrf_exempt
@require_POST
def stripe_webhook(request):
    settings_data = platform_settings()
    secret = settings_data.get("stripe_webhook_secret") or ""
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    if not secret:
        return JsonResponse({"error": "Stripe webhook secret is not configured."}, status=400)
    if not verify_stripe_signature(request.body, signature, secret):
        return JsonResponse({"error": "Invalid signature."}, status=400)
    try:
        event = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid payload."}, status=400)

    event_type = event.get("type")
    payment = stripe_event_payment(event)
    if payment is not None:
        obj = event.get("data", {}).get("object") or {}
        session_id = obj.get("id")
        if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
            if obj.get("payment_status") == "unpaid":
                return JsonResponse({"received": True, "pending": True})
            if payment.gateway_checkout_id and session_id and session_id != payment.gateway_checkout_id:
                TenantAuditLog.objects.create(
                    tenant=payment.tenant,
                    action="payment.gateway_mismatch",
                    details=f"Stripe session {session_id} did not match payment #{payment.id}.",
                )
                return JsonResponse({"received": True})
            if not stripe_payment_matches(payment, obj):
                TenantAuditLog.objects.create(
                    tenant=payment.tenant,
                    action="payment.gateway_mismatch",
                    details=f"Stripe amount/currency mismatch for payment #{payment.id}.",
                )
                return JsonResponse({"received": True})
            try:
                complete_payment(
                    payment,
                    reference=obj.get("payment_intent") or session_id or "",
                    payload={"webhook_event": event},
                )
            except GatewayError as error:
                return JsonResponse({"error": str(error)}, status=500)
        elif event_type == "checkout.session.expired":
            fail_payment(payment, "Stripe checkout session expired.", payload={"webhook_event": event})
    return JsonResponse({"received": True})


@csrf_exempt
@require_POST
def mpesa_callback(request):
    if not mpesa_callback_token_valid(request):
        return JsonResponse(
            {"ResultCode": 1, "ResultDesc": "Invalid callback token."}, status=403
        )
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid payload."}, status=400)
    callback = (payload.get("Body") or {}).get("stkCallback") or {}
    checkout_id = callback.get("CheckoutRequestID")
    payment = (
        TenantPayment.objects.select_related("tenant", "plan")
        .filter(gateway_checkout_id=checkout_id)
        .first()
        if checkout_id
        else None
    )
    if payment is None:
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    metadata = (callback.get("CallbackMetadata") or {}).get("Item") or []
    paid_amount = next(
        (item.get("Value") for item in metadata if item.get("Name") == "Amount"), None
    )
    if paid_amount is not None and int(paid_amount) != int(payment.amount):
        fail_payment(
            payment,
            f"M-Pesa callback amount {paid_amount} did not match {payment.amount}.",
            payload={"mpesa_callback": payload},
        )
        return JsonResponse({"ResultCode": 1, "ResultDesc": "Amount mismatch."}, status=400)

    apply_mpesa_result(
        payment,
        result_code=callback.get("ResultCode"),
        result_desc=callback.get("ResultDesc") or "",
        receipt=callback_receipt(callback),
        payload=payload,
    )
    return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})


@login_required
def billing_receipt(request, pk):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise Http404
    payment = get_object_or_404(TenantPayment, pk=pk, tenant=tenant)
    return render(
        request,
        "saas/receipt.html",
        {
            "payment": payment,
            "tenant": tenant,
            "platform": platform_settings(),
        },
    )


@login_required
@require_POST
def dismiss_announcement(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk, is_active=True)
    AnnouncementDismissal.objects.get_or_create(announcement=announcement, user=request.user)
    return redirect(_safe_next(request, "/dashboard"))


@require_GET
def licence_manifest_view(request):
    """Licence manifest consumed by the module gateway, agents and integrations."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        response = JsonResponse(
            {"error": "authentication_required", "message": "Sign in to read this manifest."},
            status=401,
        )
    else:
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            response = JsonResponse(
                {"error": "workspace_required", "message": "No workspace is attached to this session."},
                status=404,
            )
        else:
            response = JsonResponse(licence_manifest(tenant))
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# SORA Cloud Control
# ---------------------------------------------------------------------------
@cloud_required
def cloud_dashboard(request):
    expire_overdue_tenants()
    metrics = tenant_metrics()
    now = timezone.now()
    recent_tenants = Tenant.objects.select_related("plan").order_by("-created_at")[:8]
    pending_payments = TenantPayment.objects.filter(status="pending").select_related(
        "tenant", "plan"
    )[:8]
    renewals = (
        Tenant.objects.filter(
            status="active",
            subscription_ends_at__gte=now,
            subscription_ends_at__lte=now + timedelta(days=30),
        )
        .select_related("plan")
        .order_by("subscription_ends_at")[:8]
    )
    max_usage = max([row["count"] for row in metrics["module_usage"]] or [1])
    return render(
        request,
        "saas/cloud/dashboard.html",
        {
            "metrics": metrics,
            "recent_tenants": recent_tenants,
            "pending_payments": pending_payments,
            "renewals": renewals,
            "max_usage": max_usage,
            "active_announcements": Announcement.objects.filter(is_active=True).count(),
            "lead_stats": {
                "new": SalesLead.objects.filter(status="new").count(),
                "open": SalesLead.objects.exclude(status__in=["won", "lost"]).count(),
                "won": SalesLead.objects.filter(status="won").count(),
                "total": SalesLead.objects.count(),
            },
            "recent_leads": SalesLead.objects.select_related("owner").order_by(
                "-created_at", "-id"
            )[:6],
            "page_title": "Cloud Overview",
        },
    )


@cloud_required
def cloud_tenants(request):
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    module_code = (request.GET.get("module") or "").strip()
    tenants = Tenant.objects.select_related("plan").annotate(
        member_count=Count("memberships", filter=Q(memberships__status="active"), distinct=True),
        module_count=Count("modules", filter=Q(modules__is_enabled=True), distinct=True),
    )
    if query:
        tenants = tenants.filter(
            Q(company_name__icontains=query)
            | Q(slug__icontains=query)
            | Q(admin_email__icontains=query)
        )
    if status:
        tenants = tenants.filter(status=status)
    if module_code:
        tenants = tenants.filter(modules__module__code=module_code, modules__is_enabled=True)
    page = paginate(request, tenants.order_by("-created_at"), 15)
    for tenant in page.object_list:
        tenant.licence_list = list(enabled_module_map(tenant).values())
    return render(
        request,
        "saas/cloud/tenants.html",
        {
            "page_obj": page,
            "tenants": page.object_list,
            "query": query,
            "status": status,
            "module_code": module_code,
            "status_choices": Tenant._meta.get_field("status").choices,
            "modules": all_modules(),
            "page_title": "Tenants",
        },
    )


@cloud_required
def cloud_leads(request):
    query = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    owner = (request.GET.get("owner") or "").strip()
    source = (request.GET.get("source") or "").strip()
    leads = SalesLead.objects.select_related("owner", "tenant")
    if query:
        leads = leads.filter(
            Q(name__icontains=query)
            | Q(company__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
        )
    if status:
        leads = leads.filter(status=status)
    if owner.isdigit():
        leads = leads.filter(owner_id=int(owner))
    if source:
        leads = leads.filter(source=source)
    page = paginate(request, leads.order_by("-created_at", "-id"), 20)
    module_labels = {
        module.code: module.brand_name or module.name for module in all_modules()
    }
    today = timezone.localdate()
    for lead in page.object_list:
        lead.module_labels = [module_labels.get(code, code) for code in (lead.modules or [])]
        lead.follow_up_overdue = bool(
            lead.next_follow_up and lead.next_follow_up < today and lead.status not in {"won", "lost"}
        )
    stats = {
        "total": SalesLead.objects.count(),
        "new": SalesLead.objects.filter(status="new").count(),
        "open": SalesLead.objects.exclude(status__in=["won", "lost"]).count(),
        "won": SalesLead.objects.filter(status="won").count(),
    }
    stats["conversion"] = (
        round(stats["won"] * 100 / stats["total"], 1) if stats["total"] else 0
    )
    return render(
        request,
        "saas/cloud/leads.html",
        {
            "page_obj": page,
            "leads": page.object_list,
            "query": query,
            "status": status,
            "owner_filter": owner,
            "source_filter": source,
            "statuses": SALES_LEAD_STATUSES,
            "owners": User.objects.filter(
                is_platform_admin=True, status="active"
            ).order_by("first_name", "last_name"),
            "sources": SalesLead.objects.values_list("source", flat=True)
            .distinct()
            .order_by("source"),
            "stats": stats,
            "page_title": "Sales pipeline",
        },
    )


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_lead_create(request):
    if request.method == "POST":
        form = SalesLeadForm(request.POST)
        if form.is_valid():
            lead = form.save(commit=False)
            lead.source = (lead.source or "manual").strip() or "manual"
            lead.owner = lead.owner or request.user
            lead.save()
            SalesLeadActivity.objects.create(
                lead=lead,
                actor=request.user,
                kind="note",
                note="Lead added from SORA Cloud.",
            )
            messages.success(request, f"Lead for {lead.company or lead.name} created.")
            return redirect(f"/cloud/leads/{lead.id}/")
    else:
        form = SalesLeadForm(
            initial={
                "source": "manual",
                "status": "new",
                "owner": request.user,
            }
        )
    return render(
        request,
        "saas/cloud/lead_form.html",
        {"form": form, "page_title": "New lead"},
    )


@cloud_required
def cloud_lead_detail(request, lead_id):
    lead = get_object_or_404(
        SalesLead.objects.select_related("owner", "tenant"), pk=lead_id
    )
    module_labels = {
        module.code: module.brand_name or module.name for module in all_modules()
    }
    return render(
        request,
        "saas/cloud/lead_detail.html",
        {
            "lead": lead,
            "module_labels": [module_labels.get(code, code) for code in (lead.modules or [])],
            "activities": lead.activities.select_related("actor").order_by("-created_at", "-id"),
            "update_form": SalesLeadUpdateForm(instance=lead),
            "note_form": SalesLeadNoteForm(),
            "page_title": lead.company or lead.name,
        },
    )


@cloud_required
@require_POST
def cloud_lead_update(request, lead_id):
    lead = get_object_or_404(SalesLead, pk=lead_id)
    old_status = lead.status
    old_owner_id = lead.owner_id
    form = SalesLeadUpdateForm(request.POST, instance=lead)
    if form.is_valid():
        lead = form.save()
        changes = []
        if lead.status != old_status:
            changes.append(
                f"Status changed from {dict(SALES_LEAD_STATUSES).get(old_status, old_status)} "
                f"to {lead.status_label}."
            )
        if lead.owner_id != old_owner_id:
            changes.append(
                f"Owner set to {lead.owner.get_full_name() if lead.owner else 'unassigned'}."
            )
        if form.cleaned_data.get("note"):
            changes.append(form.cleaned_data["note"])
        if changes:
            SalesLeadActivity.objects.create(
                lead=lead,
                actor=request.user,
                kind="status" if lead.status != old_status else "note",
                note="\n".join(changes),
            )
        messages.success(request, "Lead updated.")
    else:
        messages.error(request, "Could not update the lead. Check the values and try again.")
    return redirect(f"/cloud/leads/{lead.id}/")


@cloud_required
@require_POST
def cloud_lead_note(request, lead_id):
    lead = get_object_or_404(SalesLead, pk=lead_id)
    form = SalesLeadNoteForm(request.POST)
    if form.is_valid():
        SalesLeadActivity.objects.create(
            lead=lead,
            actor=request.user,
            kind=form.cleaned_data["kind"],
            note=form.cleaned_data["note"],
        )
        messages.success(request, "Note added to the lead timeline.")
    else:
        messages.error(request, "Write a note before saving.")
    return redirect(f"/cloud/leads/{lead.id}/")


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_lead_convert(request, lead_id):
    lead = get_object_or_404(SalesLead.objects.select_related("owner"), pk=lead_id)
    if lead.tenant_id:
        messages.info(request, "This lead is already linked to a workspace.")
        return redirect(f"/cloud/leads/{lead.id}/")
    parts = (lead.name or "").split(" ", 1)
    if request.method == "POST":
        form = SalesLeadConvertForm(request.POST)
        if form.is_valid():
            password = secrets.token_urlsafe(12)
            try:
                result = provision_tenant(
                    company_name=form.cleaned_data["company_name"],
                    admin_email=form.cleaned_data["email"],
                    password=password,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    phone=form.cleaned_data["phone"],
                    module_codes=form.cleaned_data.get("modules") or None,
                    plan=form.cleaned_data.get("plan"),
                    trial_days=form.cleaned_data.get("trial_days"),
                    actor=request.user,
                )
            except ValueError as exc:
                form.add_error("email", str(exc))
            else:
                tenant = result["tenant"]
                lead.status = "won"
                lead.tenant = tenant
                lead.owner = lead.owner or request.user
                lead.next_follow_up = None
                lead.save(
                    update_fields=[
                        "status",
                        "tenant",
                        "owner",
                        "next_follow_up",
                        "updated_at",
                    ]
                )
                SalesLeadActivity.objects.create(
                    lead=lead,
                    actor=request.user,
                    kind="converted",
                    note=f"Converted to workspace {tenant.company_name} ({tenant.slug}).",
                )
                TenantAuditLog.objects.create(
                    tenant=tenant,
                    actor=request.user,
                    action="workspace.created_from_lead",
                    details=f"Provisioned from lead #{lead.id}.",
                    ip_address=_client_ip(request),
                )
                email_sent = send_welcome_email(
                    tenant,
                    result["user"],
                    request.build_absolute_uri("/login"),
                    temp_password=password,
                )
                credential_note = (
                    "Welcome email sent."
                    if email_sent
                    else f"Email was not sent. Temporary password: {password}"
                )
                messages.success(
                    request,
                    f"{tenant.company_name} created from the lead. {credential_note}",
                )
                return redirect(f"/cloud/tenants/{tenant.id}/")
    else:
        form = SalesLeadConvertForm(
            initial={
                "company_name": lead.company or lead.name,
                "first_name": parts[0],
                "last_name": parts[1] if len(parts) > 1 else "",
                "email": lead.email,
                "phone": lead.phone,
                "modules": lead.modules
                or [module.code for module in available_modules()],
                "trial_days": platform_settings().get("trial_days") or 30,
            }
        )
    return render(
        request,
        "saas/cloud/lead_convert.html",
        {"lead": lead, "form": form, "page_title": "Convert lead"},
    )


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_tenant_create(request):
    if request.method == "POST":
        form = CloudTenantCreateForm(request.POST)
        if form.is_valid():
            password = form.cleaned_data.get("password") or secrets.token_urlsafe(9)
            generated_password = not form.cleaned_data.get("password")
            try:
                result = provision_tenant(
                    company_name=form.cleaned_data["company_name"],
                    admin_email=form.cleaned_data["email"],
                    password=password,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                    phone=form.cleaned_data["phone"],
                    module_codes=form.cleaned_data.get("modules") or None,
                    plan=form.cleaned_data.get("plan"),
                    trial_days=form.cleaned_data.get("trial_days"),
                    actor=request.user,
                )
            except ValueError as exc:
                form.add_error("email", str(exc))
            else:
                tenant = result["tenant"]
                if form.cleaned_data.get("notes"):
                    tenant.notes = form.cleaned_data["notes"]
                    tenant.save(update_fields=["notes"])
                TenantAuditLog.objects.create(
                    tenant=tenant,
                    actor=request.user,
                    action="workspace.created_by_cloud",
                    details="Workspace provisioned from SORA Cloud Control.",
                    ip_address=_client_ip(request),
                )
                email_sent = send_welcome_email(
                    tenant,
                    result["user"],
                    request.build_absolute_uri("/login"),
                    temp_password=password if generated_password else None,
                )
                if generated_password:
                    credential_note = f"Temporary password: {password}"
                else:
                    credential_note = "The owner's chosen password is active."
                messages.success(
                    request,
                    f"{tenant.company_name} created. {credential_note} "
                    + ("Welcome email sent." if email_sent else "Email was not sent; share access manually."),
                )
                return redirect(f"/cloud/tenants/{tenant.id}/")
    else:
        form = CloudTenantCreateForm()
    return render(
        request,
        "saas/cloud/tenant_form.html",
        {"form": form, "page_title": "New tenant"},
    )


@cloud_required
def cloud_tenant_detail(request, tenant_id):
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=tenant_id)
    licences = {row.module_id: row for row in tenant.modules.select_related("module")}
    module_rows = [
        {"module": module, "licence": licences.get(module.id)}
        for module in all_modules()
    ]
    memberships = list(tenant.memberships.select_related("user").order_by("role", "id"))
    payments = tenant.payments.select_related("plan").order_by("-created_at")[:15]
    audit_logs = tenant.audit_logs.select_related("actor").order_by("-created_at")[:20]
    impersonations = tenant.impersonation_logs.select_related(
        "platform_admin", "target_user"
    ).order_by("-started_at")[:8]
    now = timezone.now()
    mrr = (
        plan_monthly_value(tenant.plan, tenant.billing_cycle)
        if tenant.effective_status == "active"
        else Decimal("0.00")
    )
    return render(
        request,
        "saas/cloud/tenant_detail.html",
        {
            "tenant": tenant,
            "module_rows": module_rows,
            "memberships": memberships,
            "seat_used": sum(1 for m in memberships if m.status == "active"),
            "module_count": sum(1 for row in module_rows if row["licence"] and row["licence"].is_enabled),
            "payments": payments,
            "pending_payments": tenant.payments.filter(status="pending"),
            "audit_logs": audit_logs,
            "impersonations": impersonations,
            "mrr": mrr,
            "extend_form": ExtendTrialForm(),
            "status_form": TenantStatusForm(initial={"status": tenant.status}),
            "payment_form": CloudPaymentForm(tenant=tenant),
            "edit_form": CloudTenantEditForm(instance=tenant),
            "admins": tenant_admins(tenant),
            "domains": list(tenant.domains.all()),
            "now": now,
            "page_title": tenant.company_name,
        },
    )


@cloud_required
@require_POST
def cloud_tenant_domain_revoke(request, tenant_id, domain_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    domain = get_object_or_404(TenantDomain, pk=domain_id, tenant=tenant)
    reason = (request.POST.get("reason") or "").strip()
    try:
        disable_domain(
            domain,
            actor=request.user,
            reason=reason or "Revoked from SORA Cloud Control.",
            ip_address=_client_ip(request),
        )
    except DomainError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{domain.hostname} no longer routes to this workspace.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_tenant_edit(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = CloudTenantEditForm(request.POST, instance=tenant)
    if form.is_valid():
        form.save()
        TenantAuditLog.objects.create(
            tenant=tenant,
            actor=request.user,
            action="workspace.updated",
            details="Tenant profile updated from SORA Cloud Control.",
            ip_address=_client_ip(request),
        )
        messages.success(request, "Tenant details updated.")
    else:
        messages.error(request, "Tenant details could not be saved. Check the form values.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_toggle_module(request, tenant_id, module_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    module = get_object_or_404(SaasModule, pk=module_id)
    row = TenantModule.objects.filter(tenant=tenant, module=module).first()
    enabled = not (row.is_enabled if row else False)
    toggle_tenant_module(tenant, module, enabled, actor=request.user)
    messages.success(
        request,
        f"{module.brand_name or module.name} {'enabled' if enabled else 'disabled'} for {tenant.company_name}.",
    )
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_extend_trial(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = ExtendTrialForm(request.POST)
    if form.is_valid():
        try:
            extend_trial(tenant, form.cleaned_data["days"], actor=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            reason = form.cleaned_data.get("reason")
            if reason:
                TenantAuditLog.objects.create(
                    tenant=tenant,
                    actor=request.user,
                    action="trial.extension_note",
                    details=reason,
                )
            messages.success(
                request,
                f"Trial extended by {form.cleaned_data['days']} days to "
                f"{timezone.localtime(tenant.trial_ends_at):%d %b %Y}.",
            )
    else:
        messages.error(request, "Enter a valid number of days.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_set_status(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = TenantStatusForm(request.POST)
    if form.is_valid():
        try:
            set_tenant_status(
                tenant,
                form.cleaned_data["status"],
                actor=request.user,
                reason=form.cleaned_data.get("reason", ""),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Status set to {tenant.get_status_display()}.")
    else:
        messages.error(request, "Choose a valid status.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_record_payment(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    form = CloudPaymentForm(request.POST, tenant=tenant)
    if form.is_valid():
        try:
            payment = record_payment(
                tenant,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                reference=form.cleaned_data["reference"],
                plan=form.cleaned_data.get("plan") or tenant.plan,
                billing_cycle=form.cleaned_data["billing_cycle"],
                status="completed",
                actor=request.user,
                notes=form.cleaned_data.get("notes", ""),
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Payment {payment.amount} {payment.currency} recorded and subscription activated.",
            )
    else:
        messages.error(request, "Payment could not be recorded. Check the values.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_confirm_payment(request, payment_id):
    payment = get_object_or_404(TenantPayment.objects.select_related("tenant", "plan"), pk=payment_id)
    if payment.status == "completed":
        messages.info(request, "This payment is already completed.")
        return redirect(f"/cloud/tenants/{payment.tenant_id}/")
    tenant = payment.tenant
    plan = payment.plan or tenant.plan
    if plan is None:
        messages.error(
            request,
            "Choose a plan for this payment before confirming it.",
        )
        return redirect(f"/cloud/tenants/{tenant.id}/")
    payment.status = "completed"
    payment.paid_at = timezone.now()
    payment.recorded_by = request.user
    payment.save(update_fields=["status", "paid_at", "recorded_by"])
    from apps.saas.services import apply_subscription

    apply_subscription(
        tenant,
        plan=plan,
        billing_cycle=payment.billing_cycle,
        actor=request.user,
    )
    payment.period_start = timezone.localdate()
    payment.period_end = tenant.subscription_ends_at.date() if tenant.subscription_ends_at else None
    payment.save(update_fields=["period_start", "period_end"])
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=request.user,
        action="payment.confirmed",
        details=f"Pending payment #{payment.id} confirmed; subscription activated.",
    )
    messages.success(request, f"Payment confirmed and {tenant.company_name} reactivated.")
    return redirect(f"/cloud/tenants/{tenant.id}/")


@cloud_required
@require_POST
def cloud_impersonate(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    membership = (
        tenant.memberships.filter(status="active", role__in=["owner", "admin"], user__status="active")
        .select_related("user")
        .order_by("role", "id")
        .first()
    )
    if membership is None:
        membership = (
            tenant.memberships.filter(status="active", user__status="active")
            .select_related("user")
            .first()
        )
    if membership is None:
        messages.error(request, "This workspace has no active user to impersonate.")
        return redirect(f"/cloud/tenants/{tenant.id}/")

    reason = (request.POST.get("reason") or "").strip()
    platform_admin = request.user
    log = ImpersonationLog.objects.create(
        platform_admin=platform_admin,
        tenant=tenant,
        target_user=membership.user,
        reason=reason,
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    platform_admin_id = platform_admin.id
    auth_login(request, membership.user, backend="django.contrib.auth.backends.ModelBackend")
    request.session["sora_impersonator_id"] = platform_admin_id
    request.session["sora_impersonation_log_id"] = log.id
    request.session["sora_tenant_id"] = tenant.id
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=platform_admin,
        action="support.impersonation_started",
        details=f"SORA support signed in as {membership.user.get_full_name()}. {reason}".strip(),
        ip_address=_client_ip(request),
    )
    messages.info(
        request,
        f"Support mode: you are viewing {tenant.company_name} as {membership.user.get_full_name()}. "
        "Use the return banner to go back to SORA Cloud.",
    )
    return redirect(tenant_landing_url(tenant))


def cloud_return(request):
    if request.method != "POST":
        return redirect("/cloud/")
    platform_admin_id = request.session.get("sora_impersonator_id")
    log_id = request.session.get("sora_impersonation_log_id")
    tenant_id = request.session.get("sora_tenant_id")
    platform_admin = User.objects.filter(
        pk=platform_admin_id, is_platform_admin=True, status="active"
    ).first()
    if platform_admin is None:
        messages.error(request, "The support session could not be restored. Please sign in again.")
        return redirect("/logout")
    if log_id:
        ImpersonationLog.objects.filter(pk=log_id, ended_at__isnull=True).update(
            ended_at=timezone.now()
        )
    auth_login(request, platform_admin, backend="django.contrib.auth.backends.ModelBackend")
    for key in ("sora_impersonator_id", "sora_impersonation_log_id", "sora_tenant_id"):
        request.session.pop(key, None)
    messages.success(request, "You are back in SORA Cloud Control.")
    if tenant_id:
        return redirect(f"/cloud/tenants/{tenant_id}/")
    return redirect("/cloud/")


@cloud_required
def cloud_billing(request):
    expire_overdue_tenants()
    status = (request.GET.get("status") or "").strip()
    payments = TenantPayment.objects.select_related("tenant", "plan").order_by("-created_at")
    if status:
        payments = payments.filter(status=status)
    page = paginate(request, payments, 25)
    completed = TenantPayment.objects.filter(status="completed")
    totals = {
        "all_time": completed.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "this_month": completed.filter(
            paid_at__year=timezone.localdate().year,
            paid_at__month=timezone.localdate().month,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00"),
        "pending": TenantPayment.objects.filter(status="pending").aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00"),
    }
    method_labels = dict(TenantPayment._meta.get_field("method").choices)
    by_method = [
        {
            "method": row["method"],
            "label": method_labels.get(row["method"], row["method"].title()),
            "total": row["total"],
            "count": row["count"],
        }
        for row in completed.values("method")
        .annotate(total=Sum("amount"), count=Count("id"))
        .order_by("-total")
    ]
    metrics = tenant_metrics()
    return render(
        request,
        "saas/cloud/billing.html",
        {
            "page_obj": page,
            "payments": page.object_list,
            "status": status,
            "totals": totals,
            "by_method": by_method,
            "metrics": metrics,
            "status_choices": TenantPayment._meta.get_field("status").choices,
            "page_title": "Billing & MRR",
        },
    )


def _announcement_audience_tenants(audience):
    qs = Tenant.objects.all()
    if audience == "trial":
        qs = qs.filter(status="trial")
    elif audience == "active":
        qs = qs.filter(status="active")
    elif audience == "locked":
        qs = qs.filter(status__in=["expired", "suspended", "cancelled"])
    return qs


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_announcements(request):
    if request.method == "POST":
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.created_by = request.user
            announcement.save()
            email_count = 0
            if announcement.send_email:
                tenants = _announcement_audience_tenants(announcement.audience)
                memberships = TenantMembership.objects.filter(
                    tenant__in=tenants, status="active", role__in=["owner", "admin"]
                ).select_related("user")
                for membership in memberships:
                    if send_platform_mail(
                        announcement.title, announcement.body, [membership.user.email]
                    ):
                        email_count += 1
            messages.success(
                request,
                f"Announcement published to tenant dashboards"
                + (f" and emailed to {email_count} admin(s)." if announcement.send_email else "."),
            )
            return redirect("/cloud/announcements/")
    else:
        form = AnnouncementForm(initial={"starts_at": timezone.now()})
    announcements = Announcement.objects.select_related("created_by").order_by("-created_at")[:50]
    return render(
        request,
        "saas/cloud/announcements.html",
        {
            "form": form,
            "announcements": announcements,
            "levels": Announcement._meta.get_field("level").choices,
            "page_title": "Broadcasts",
        },
    )


@cloud_required
@require_POST
def cloud_announcement_toggle(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    announcement.is_active = not announcement.is_active
    announcement.save(update_fields=["is_active"])
    messages.success(
        request,
        f"Announcement {'activated' if announcement.is_active else 'paused'}.",
    )
    return redirect("/cloud/announcements/")


@cloud_required
def cloud_plans(request):
    plans = SaasPlan.objects.all().order_by("sort_order", "monthly_price")
    module_lookup = module_map()
    rows = []
    for plan in plans:
        rows.append(
            {
                "plan": plan,
                "modules": [
                    module_lookup[code].brand_name
                    for code in (plan.included_modules or [])
                    if code in module_lookup
                ],
                "tenant_count": plan.tenants.count(),
            }
        )
    return render(
        request,
        "saas/cloud/plans.html",
        {"rows": rows, "page_title": "Plans & pricing"},
    )


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_plan_edit(request, plan_id):
    plan = get_object_or_404(SaasPlan, pk=plan_id)
    if request.method == "POST":
        form = PlanForm(request.POST, plan=plan)
        if form.is_valid():
            form.save()
            messages.success(request, f"{plan.name} pricing updated.")
            return redirect("/cloud/plans/")
    else:
        form = PlanForm(plan=plan)
    return render(
        request,
        "saas/cloud/plan_form.html",
        {"form": form, "plan": plan, "page_title": f"Edit {plan.name}"},
    )


@cloud_required
@require_http_methods(["GET", "POST"])
def cloud_settings(request, group="brand"):
    groups = [key for key, _, _ in SETTING_GROUPS]
    if group not in groups:
        raise Http404
    rows = list(PlatformSetting.objects.filter(group=group).order_by("sort_order", "key"))
    if request.method == "POST":
        form = PlatformSettingsForm(request.POST, rows=rows)
        if form.is_valid():
            form.save(actor=request.user)
            messages.success(request, "Platform settings saved.")
            return redirect(f"/cloud/settings/{group}/")
    else:
        form = PlatformSettingsForm(rows=rows)
    return render(
        request,
        "saas/cloud/settings.html",
        {
            "form": form,
            "groups": SETTING_GROUPS,
            "active_group": group,
            "rows": rows,
            "page_title": "Platform settings",
        },
    )
