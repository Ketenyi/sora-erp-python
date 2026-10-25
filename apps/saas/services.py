"""Tenant lifecycle, licensing, billing and platform settings services."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db import transaction
from django.db.models import Case, Count, Q, Sum, When
from django.utils import timezone
from django.utils.text import slugify

from apps.saas.models import (
    Announcement,
    AnnouncementDismissal,
    LOCKED_STATUSES,
    PlatformSetting,
    SaasModule,
    SaasPlan,
    Tenant,
    TenantAuditLog,
    TenantMembership,
    TenantModule,
    TenantPayment,
)
from apps.saas.catalog import MODULE_SEED, PLAN_SEED

CORE_PATH_PREFIXES = (
    "/dashboard",
    "/settings",
    "/admin",
    "/billing",
    "/announcements",
    "/api",
    "/uploads",
    "/logout",
    "/login",
    "/health",
    "/static",
)

AUDIENCE_STATUS = {
    "trial": "trial",
    "active": "active",
    "locked": "locked",
}


def platform_settings() -> dict:
    """Return every global setting with its typed value."""
    try:
        rows = list(PlatformSetting.objects.all())
    except Exception:
        return {}
    return {row.key: row.typed_value for row in rows}


def platform_setting(key: str, default=None):
    value = platform_settings().get(key)
    return default if value in (None, "") else value


def get_setting_row(key: str) -> PlatformSetting | None:
    return PlatformSetting.objects.filter(key=key).first()


def plan_queryset():
    return SaasPlan.objects.filter(is_active=True).order_by("sort_order", "monthly_price")


def public_plans():
    return plan_queryset().filter(is_public=True)


def available_modules():
    return SaasModule.objects.filter(is_available=True).order_by("sort_order", "name")


def all_modules():
    return SaasModule.objects.all().order_by("sort_order", "name")


def module_map():
    return {module.code: module for module in all_modules()}


def module_by_code(code: str) -> SaasModule | None:
    if not code:
        return None
    return SaasModule.objects.filter(code=code).first()


def enabled_module_map(tenant: Tenant | None) -> dict[str, TenantModule]:
    if tenant is None:
        return {}
    cached = getattr(tenant, "_enabled_modules", None)
    if cached is not None:
        return cached
    now = timezone.now()
    rows = (
        TenantModule.objects.filter(tenant=tenant, is_enabled=True)
        .select_related("module")
        .order_by("module__sort_order")
    )
    mapping = {
        row.module.code: row
        for row in rows
        if row.valid_until is None or row.valid_until > now
    }
    tenant._enabled_modules = mapping
    return mapping


def enabled_module_codes(tenant: Tenant | None) -> set[str]:
    return set(enabled_module_map(tenant).keys())


def tenant_has_code(tenant: Tenant | None, code: str) -> bool:
    return code in enabled_module_map(tenant)


def nav_module_codes(tenant: Tenant | None) -> set[str]:
    return enabled_module_codes(tenant)


def licence_manifest(tenant: Tenant | None) -> dict:
    """Machine-readable licence manifest evaluated by the module gateway.

    ``plan_type`` is the commercial plan code when the workspace subscribes to
    a bundle, ``STANDALONE_<MODULE>`` when exactly one module is licensed (for
    example ``STANDALONE_SIGNATURES``), and ``MODULAR`` for a custom mix.
    """
    if tenant is None:
        return {
            "tenant_id": None,
            "plan_type": "NONE",
            "enabled_modules": [],
            "is_trial": False,
            "trial_expires_at": None,
        }
    codes = list(enabled_module_map(tenant))
    plan = tenant.plan
    if not codes:
        plan_type = "NONE"
    elif plan is not None and plan.code:
        plan_type = plan.code.upper()
    elif len(codes) == 1:
        plan_type = f"STANDALONE_{codes[0].removeprefix('SORA_')}"
    else:
        plan_type = "MODULAR"
    trial_expires_at = None
    if tenant.is_trial and tenant.trial_ends_at:
        trial_expires_at = tenant.trial_ends_at.isoformat()
    return {
        "tenant_id": tenant.pk,
        "plan_type": plan_type,
        "enabled_modules": codes,
        "is_trial": bool(tenant.is_trial),
        "trial_expires_at": trial_expires_at,
    }


def tenant_landing_url(tenant: Tenant | None) -> str:
    if tenant is None:
        return "/dashboard"
    codes = enabled_module_codes(tenant)
    modules = [row.module for row in enabled_module_map(tenant).values()]
    if len(codes) == 1 and modules:
        route = modules[0].landing_route
        if route:
            return route
    return "/dashboard"


def route_module(path: str) -> SaasModule | None:
    path = path or "/"
    for module in all_modules():
        for prefix in module.route_prefixes or []:
            if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                return module
    return None


def tenant_can_access_path(tenant: Tenant | None, path: str) -> bool:
    if tenant is None:
        return True
    if path.startswith("/helpdesk/status/"):
        # Signed client status links are intentionally reachable regardless of
        # the signed-in viewer's own module licences.
        return True
    module = route_module(path)
    if module is None:
        return True
    return tenant_has_code(tenant, module.code)


def default_membership(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        TenantMembership.objects.filter(user=user, status="active")
        .select_related("tenant", "tenant__plan")
        .annotate(
            role_rank=Case(
                When(role="owner", then=0),
                When(role="admin", then=1),
                When(role="manager", then=2),
                default=3,
            )
        )
        .order_by("role_rank", "id")
        .first()
    )


def membership_for(user, tenant_id):
    if user is None or not getattr(user, "is_authenticated", False) or not tenant_id:
        return None
    return (
        TenantMembership.objects.filter(user=user, tenant_id=tenant_id, status="active")
        .select_related("tenant", "tenant__plan")
        .first()
    )


def tenant_admins(tenant: Tenant):
    return (
        TenantMembership.objects.filter(tenant=tenant, status="active", role__in=["owner", "admin"])
        .select_related("user")
        .order_by("role", "id")
    )


def expire_overdue_tenants() -> dict:
    """Flip trials/subscriptions that passed their end date to expired."""
    now = timezone.now()
    expired_trials = Tenant.objects.filter(status="trial", trial_ends_at__lt=now).update(
        status="expired", updated_at=now
    )
    expired_subs = Tenant.objects.filter(
        status="active", subscription_ends_at__isnull=False, subscription_ends_at__lt=now
    ).update(status="expired", updated_at=now)
    expired_grace = Tenant.objects.filter(
        status="past_due", grace_ends_at__isnull=False, grace_ends_at__lt=now
    ).update(status="expired", updated_at=now)
    return {
        "trials": expired_trials,
        "subscriptions": expired_subs,
        "grace": expired_grace,
        "total": expired_trials + expired_subs + expired_grace,
    }


def _unique_slug(company_name: str) -> str:
    base = slugify(company_name)[:80] or "workspace"
    candidate = base
    counter = 2
    while Tenant.objects.filter(slug=candidate).exists():
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _unique_username(email: str) -> str:
    from apps.core.models import User

    base = (email.split("@")[0] or "user")[:80]
    candidate = base
    counter = 2
    while User.objects.filter(username=candidate).exists():
        candidate = f"{base}{counter}"
        counter += 1
    return candidate


@transaction.atomic
def provision_tenant(
    *,
    company_name: str,
    admin_email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    module_codes: list[str] | None = None,
    trial_days: int | None = None,
    actor=None,
    is_demo: bool = False,
    plan: SaasPlan | None = None,
) -> dict:
    """Create a workspace, its first admin login and licence manifest."""
    from apps.core.models import User

    email = (admin_email or "").strip().lower()
    if User.objects.filter(email__iexact=email).exists():
        raise ValueError("An account with this email already exists. Please sign in instead.")

    settings_data = platform_settings()
    if trial_days is None:
        try:
            trial_days = int(settings_data.get("trial_days") or 30)
        except (TypeError, ValueError):
            trial_days = 30

    now = timezone.now()
    tenant = Tenant.objects.create(
        company_name=company_name.strip(),
        slug=_unique_slug(company_name),
        admin_email=email,
        phone_number=phone.strip(),
        brand_name=company_name.strip(),
        currency=str(settings_data.get("default_currency") or "KES"),
        status="active" if plan else "trial",
        trial_starts_at=now,
        trial_ends_at=now + timedelta(days=trial_days),
        subscription_starts_at=now if plan else None,
        subscription_ends_at=(now + timedelta(days=365)) if plan else None,
        max_users=plan.max_users if plan and plan.max_users else 10,
        is_demo=is_demo,
    )

    user = User(
        username=_unique_username(email),
        email=email,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        role="admin",
        status="active",
    )
    user.set_password(password)
    user.save(using="default")

    TenantMembership.objects.create(tenant=tenant, user=user, role="owner", status="active")

    from apps.core.services import provision_company_settings

    provision_company_settings(tenant)

    catalogue = {module.code: module for module in available_modules()}
    if module_codes is None:
        selected = list(catalogue.values())
    else:
        selected = [catalogue[code] for code in module_codes if code in catalogue]
    if not selected:
        selected = list(catalogue.values())
    if plan and plan.included_modules:
        selected_codes = set(plan.included_modules)
        selected = [module for module in all_modules() if module.code in selected_codes]

    for module in selected:
        TenantModule.objects.create(
            tenant=tenant,
            module=module,
            is_enabled=True,
            source="plan" if plan else "trial",
            note="Workspace provisioning",
            granted_by=actor,
        )

    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor or user,
        action="workspace.provisioned",
        details=f"{tenant.company_name} provisioned with {len(selected)} module(s).",
    )
    tenant._enabled_modules = None
    return {"tenant": tenant, "user": user, "modules": selected}


def send_platform_mail(subject: str, body: str, recipients: list[str], html: str | None = None) -> bool:
    recipients = [email for email in recipients if email]
    if not recipients:
        return False
    data = platform_settings()
    host = str(data.get("saas_smtp_host") or "").strip()
    if not host and settings.EMAIL_BACKEND.endswith("smtp.EmailBackend") and not settings.EMAIL_HOST_USER:
        # No mailbox is configured; let the caller surface a manual fallback instead of timing out.
        return False
    try:
        if host:
            connection = get_connection(
                backend=settings.EMAIL_BACKEND,
                host=host,
                port=int(data.get("saas_smtp_port") or 587),
                username=str(data.get("saas_smtp_user") or ""),
                password=str(data.get("saas_smtp_password") or ""),
                use_tls=bool(data.get("saas_smtp_tls")),
            )
        else:
            connection = get_connection()
        from_email = str(data.get("saas_from_email") or settings.DEFAULT_FROM_EMAIL)
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=from_email,
            to=recipients,
            connection=connection,
        )
        if html:
            message.attach_alternative(html, "text/html")
        message.send(fail_silently=False)
        return True
    except Exception:
        return False


def send_welcome_email(tenant: Tenant, user, login_url: str, temp_password: str | None = None) -> bool:
    data = platform_settings()
    platform = data.get("platform_name") or "SORA ERP"
    modules = [row.module.brand_name or row.module.name for row in enabled_module_map(tenant).values()]
    trial_line = (
        f"Your trial ends on {timezone.localtime(tenant.trial_ends_at):%d %B %Y}."
        if tenant.trial_ends_at
        else ""
    )
    credential_line = (
        f"Temporary password: {temp_password} (change it after signing in.)"
        if temp_password
        else "Use the password chosen when the workspace was created."
    )
    subject = f"Welcome to {platform} — {tenant.company_name} is ready"
    body = "\n".join(
        [
            f"Hello {user.get_full_name()},",
            "",
            f"Your workspace for {tenant.company_name} has been created.",
            f"Sign in: {login_url}",
            f"Email: {user.email}",
            credential_line,
            "",
            trial_line,
            f"Enabled modules: {', '.join(modules) if modules else 'All modules'}",
            "",
            f"Need help? Contact {data.get('support_email') or 'support@soraerp.com'}.",
            "",
            f"— The {platform} team",
        ]
    )
    html = (
        f"<p>Hello {user.get_full_name()},</p>"
        f"<p>Your workspace for <strong>{tenant.company_name}</strong> has been created.</p>"
        f"<p><a href=\"{login_url}\">Sign in to {platform}</a><br>Email: {user.email}<br>"
        f"{credential_line}</p>"
        f"<p>{trial_line}<br>Enabled modules: {', '.join(modules) if modules else 'All modules'}</p>"
        f"<p>Need help? Contact {data.get('support_email') or 'support@soraerp.com'}.</p>"
    )
    return send_platform_mail(subject, body, [user.email], html=html)


def sync_tenant_modules(tenant: Tenant, plan: SaasPlan, actor=None) -> None:
    """Apply a plan's included modules, leaving manual add-ons untouched."""
    included = set(plan.included_modules or [])
    if not included:
        return
    modules = {module.code: module for module in all_modules()}
    for code in included:
        module = modules.get(code)
        if module is None:
            continue
        row, created = TenantModule.objects.get_or_create(
            tenant=tenant, module=module, defaults={"is_enabled": True, "source": "plan"}
        )
        if not created and not row.is_enabled:
            row.is_enabled = True
            row.source = "plan"
            row.enabled_at = timezone.now()
            row.disabled_at = None
            row.save(update_fields=["is_enabled", "source", "enabled_at", "disabled_at"])
    TenantModule.objects.filter(tenant=tenant, source__in=["plan", "trial"]).exclude(
        module__code__in=included
    ).update(is_enabled=False, disabled_at=timezone.now())
    tenant._enabled_modules = None


def apply_subscription(
    tenant: Tenant,
    *,
    plan: SaasPlan | None = None,
    billing_cycle: str = "monthly",
    months: int | None = None,
    actor=None,
) -> Tenant:
    now = timezone.now()
    plan = plan or tenant.plan
    if plan is None:
        raise ValueError("A plan is required to activate a subscription.")
    base = tenant.subscription_ends_at if tenant.status == "active" and tenant.subscription_ends_at and tenant.subscription_ends_at > now else now
    if months:
        days = 30 * months
    else:
        days = 365 if billing_cycle == "yearly" else 30
    tenant.plan = plan
    tenant.billing_cycle = billing_cycle
    tenant.status = "active"
    tenant.subscription_starts_at = tenant.subscription_starts_at or now
    tenant.subscription_ends_at = base + timedelta(days=days)
    tenant.grace_ends_at = None
    tenant.activated_at = tenant.activated_at or now
    tenant.suspended_reason = ""
    tenant.cancelled_at = None
    if plan.max_users:
        tenant.max_users = plan.max_users
    tenant.save()
    sync_tenant_modules(tenant, plan, actor=actor)
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="subscription.activated",
        details=f"{plan.name} activated ({billing_cycle}); valid until {tenant.subscription_ends_at:%Y-%m-%d}.",
    )
    return tenant


@transaction.atomic
def record_payment(
    tenant: Tenant,
    *,
    amount,
    method: str = "manual",
    reference: str = "",
    plan: SaasPlan | None = None,
    billing_cycle: str = "monthly",
    status: str = "completed",
    actor=None,
    notes: str = "",
    period_start=None,
    period_end=None,
) -> TenantPayment:
    plan = plan or tenant.plan
    payment = TenantPayment.objects.create(
        tenant=tenant,
        plan=plan,
        amount=Decimal(str(amount)),
        currency=tenant.currency or "KES",
        method=method,
        reference=reference,
        status=status,
        billing_cycle=billing_cycle,
        notes=notes,
        recorded_by=actor,
        paid_at=timezone.now() if status == "completed" else None,
        period_start=period_start,
        period_end=period_end,
    )
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="payment.recorded",
        details=f"{payment.amount} {payment.currency} via {payment.get_method_display()} ({status}).",
    )
    if status == "completed":
        apply_subscription(tenant, plan=plan, billing_cycle=billing_cycle, actor=actor)
        payment.period_start = tenant.subscription_starts_at.date()
        payment.period_end = tenant.subscription_ends_at.date()
        payment.save(update_fields=["period_start", "period_end"])
    return payment


def extend_trial(tenant: Tenant, days: int, actor=None) -> Tenant:
    days = int(days)
    if days <= 0:
        raise ValueError("Extension days must be positive.")
    now = timezone.now()
    base = tenant.trial_ends_at if tenant.trial_ends_at and tenant.trial_ends_at > now else now
    tenant.trial_ends_at = base + timedelta(days=days)
    if tenant.status in {"trial", "expired"}:
        tenant.status = "trial"
        tenant.suspended_reason = ""
    tenant.save()
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="trial.extended",
        details=f"Trial extended by {days} days to {tenant.trial_ends_at:%Y-%m-%d}.",
    )
    return tenant


def set_tenant_status(tenant: Tenant, status: str, actor=None, reason: str = "") -> Tenant:
    valid = {choice for choice, _ in tenant._meta.get_field("status").choices}
    if status not in valid:
        raise ValueError("Unknown tenant status.")
    now = timezone.now()
    tenant.status = status
    if status == "active":
        tenant.activated_at = tenant.activated_at or now
        tenant.suspended_reason = ""
        tenant.cancelled_at = None
        if not tenant.subscription_ends_at:
            tenant.subscription_starts_at = tenant.subscription_starts_at or now
            tenant.subscription_ends_at = now + timedelta(days=365)
    elif status == "suspended":
        tenant.suspended_at = now
        tenant.suspended_reason = reason or "Suspended by SORA support."
    elif status == "cancelled":
        tenant.cancelled_at = now
    elif status == "past_due":
        days = int(platform_settings().get("grace_days") or 3)
        tenant.grace_ends_at = now + timedelta(days=days)
    tenant.save()
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="status.changed",
        details=f"Status set to {status}. {reason}".strip(),
    )
    return tenant


def toggle_tenant_module(tenant: Tenant, module: SaasModule, enabled: bool, actor=None) -> TenantModule:
    row, created = TenantModule.objects.get_or_create(
        tenant=tenant, module=module, defaults={"is_enabled": enabled, "source": "manual"}
    )
    if not created:
        row.is_enabled = enabled
        row.source = "manual"
        row.enabled_at = timezone.now() if enabled else row.enabled_at
        row.disabled_at = None if enabled else timezone.now()
        row.granted_by = actor
        row.save()
    tenant._enabled_modules = None
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="module.enabled" if enabled else "module.disabled",
        details=f"{module.brand_name or module.name} {'enabled' if enabled else 'disabled'}.",
    )
    return row


def announcements_for(tenant: Tenant | None, user):
    if tenant is None or user is None or not getattr(user, "is_authenticated", False):
        return []
    now = timezone.now()
    status = "locked" if tenant.is_locked else tenant.effective_status
    audiences = ["all"]
    if status == "trial":
        audiences.append("trial")
    elif status == "active":
        audiences.append("active")
    else:
        audiences.append("locked")
    dismissed = AnnouncementDismissal.objects.filter(user=user).values_list("announcement_id", flat=True)
    return list(
        Announcement.objects.filter(
            is_active=True,
            starts_at__lte=now,
            audience__in=audiences,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        .exclude(id__in=dismissed)
        .order_by("-created_at")[:3]
    )


def trial_banner(tenant: Tenant | None) -> dict | None:
    if tenant is None or tenant.status != "trial":
        return None
    days = tenant.trial_days_left
    if tenant.is_locked:
        return None
    tone = "critical" if days <= 3 else "warning" if days <= 7 else "info"
    return {"days_left": days, "tone": tone, "ends_at": tenant.trial_ends_at}


def tenant_metrics() -> dict:
    now = timezone.now()
    status_counts = {
        row["status"]: row["count"]
        for row in Tenant.objects.values("status").annotate(count=Count("id"))
    }
    tenants = list(
        Tenant.objects.select_related("plan").all()
    )
    active_paid = [t for t in tenants if t.effective_status == "active"]
    trials = [t for t in tenants if t.effective_status == "trial"]
    expired = [t for t in tenants if t.effective_status == "expired"]
    suspended = [t for t in tenants if t.effective_status == "suspended"]
    cancelled = [t for t in tenants if t.effective_status == "cancelled"]
    mrr = Decimal("0.00")
    for tenant in active_paid:
        if tenant.plan:
            price = tenant.plan.monthly_price
            if tenant.billing_cycle == "yearly":
                price = (tenant.plan.yearly_price or tenant.plan.monthly_price * 12) / Decimal("12")
            mrr += price or Decimal("0.00")
    modules = (
        TenantModule.objects.filter(is_enabled=True, tenant__status__in=["active", "trial"])
        .values("module__code", "module__name", "module__brand_name")
        .annotate(count=Count("tenant", distinct=True))
        .order_by("-count")
    )
    payments_this_month = TenantPayment.objects.filter(
        status="completed", paid_at__year=now.year, paid_at__month=now.month
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    new_this_month = Tenant.objects.filter(created_at__gte=month_start).count()
    churn_base = len(active_paid) + len(cancelled)
    churn_rate = (len(cancelled) / churn_base * 100) if churn_base else 0
    return {
        "total": len(tenants),
        "active": len(active_paid),
        "trials": len(trials),
        "expired": len(expired),
        "suspended": len(suspended),
        "cancelled": len(cancelled),
        "past_due": status_counts.get("past_due", 0),
        "mrr": mrr,
        "arr": mrr * 12,
        "payments_this_month": payments_this_month,
        "new_this_month": new_this_month,
        "churn_rate": churn_rate,
        "module_usage": list(modules),
    }


def plan_monthly_value(plan: SaasPlan | None, cycle: str = "monthly") -> Decimal:
    if plan is None:
        return Decimal("0.00")
    if cycle == "yearly":
        return (plan.yearly_price or plan.monthly_price * 12) / Decimal("12")
    return plan.monthly_price or Decimal("0.00")


def seed_reference_data() -> dict:
    """Idempotently ensure the catalogue and plans exist (used after deploy)."""
    created_modules = 0
    for item in MODULE_SEED:
        _, created = SaasModule.objects.get_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "brand_name": item.get("brand_name", ""),
                "description": item.get("description", ""),
                "icon": item.get("icon", "cube"),
                "nav_key": item.get("nav_key", ""),
                "landing_route": item.get("landing_route", ""),
                "route_prefixes": item.get("route_prefixes", []),
                "monthly_price": item.get("monthly_price", "0.00"),
                "is_available": item.get("is_available", True),
                "sort_order": item.get("sort_order", 0),
            },
        )
        created_modules += int(created)
    created_plans = 0
    for item in PLAN_SEED:
        _, created = SaasPlan.objects.get_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "tagline": item.get("tagline", ""),
                "description": item.get("description", ""),
                "monthly_price": item.get("monthly_price", "0.00"),
                "yearly_price": item.get("yearly_price", "0.00"),
                "max_users": item.get("max_users", 10),
                "included_modules": item.get("included_modules", []),
                "features": item.get("features", []),
                "sort_order": item.get("sort_order", 0),
            },
        )
        created_plans += int(created)
    return {"modules": created_modules, "plans": created_plans}


def render_placeholders(text: str, context: dict) -> str:
    result = text or ""
    for key, value in context.items():
        result = result.replace("{" + key + "}", str(value or ""))
    return result


def capture_sales_lead(
    *,
    name: str,
    email: str,
    company: str = "",
    phone: str = "",
    country: str = "",
    modules=None,
    message: str = "",
    source: str = "website",
    ip_address: str = "",
    user_agent: str = "",
    lead_url: str = "",
    actor=None,
):
    """Store a website or manual enquiry and run the Cloud notification flow."""
    from apps.saas.models import SalesLead, SalesLeadActivity

    settings_data = platform_settings()
    available = {code for code, _label in available_module_values()}
    chosen = [code for code in (modules or []) if code in available]
    try:
        follow_up_days = int(settings_data.get("lead_followup_days") or 0)
    except (TypeError, ValueError):
        follow_up_days = 0
    lead = SalesLead.objects.create(
        name=name.strip(),
        company=(company or "").strip(),
        email=email.strip().lower(),
        phone=(phone or "").strip(),
        country=(country or "").strip(),
        modules=chosen,
        message=(message or "").strip(),
        source=source,
        owner=actor if actor is not None else None,
        next_follow_up=(
            timezone.localdate() + timedelta(days=follow_up_days)
            if follow_up_days
            else None
        ),
        ip_address=ip_address or "",
        user_agent=(user_agent or "")[:500],
    )
    labels = {
        module.code: module.brand_name or module.name
        for module in SaasModule.objects.filter(code__in=chosen)
    }
    module_names = ", ".join(labels.get(code, code) for code in chosen) or "Not specified"
    platform_name = settings_data.get("platform_name") or "SORA ERP"
    sales_email = settings_data.get("sales_email") or ""
    context = {
        "name": lead.name,
        "company": lead.company or lead.name,
        "email": lead.email,
        "phone": lead.phone or "Not provided",
        "country": lead.country or "Not provided",
        "modules": module_names,
        "message": lead.message or "No message",
        "platform": platform_name,
        "sales_email": sales_email or "our sales team",
        "link": lead_url or "",
    }
    SalesLeadActivity.objects.create(
        lead=lead,
        actor=actor,
        kind="note",
        note=f"Enquiry captured from {source.replace('_', ' ')}.",
    )

    ack_sent = False
    if settings_data.get("lead_ack_enabled", True):
        subject = render_placeholders(
            str(settings_data.get("lead_ack_subject") or "We received your request — {platform}"),
            context,
        )
        body = render_placeholders(
            str(settings_data.get("lead_ack_body") or ""), context
        )
        ack_sent = send_platform_mail(_one_line(subject), body, [lead.email])
        SalesLeadActivity.objects.create(
            lead=lead,
            actor=actor,
            kind="contact" if ack_sent else "note",
            note=(
                f"Acknowledgement email sent to {lead.email}."
                if ack_sent
                else "Acknowledgement email could not be sent; confirm mail settings."
            ),
        )

    notify_recipient = (
        settings_data.get("lead_notify_email")
        or sales_email
        or settings_data.get("support_email")
        or ""
    )
    notify_sent = False
    if notify_recipient:
        subject = render_placeholders(
            str(settings_data.get("lead_notify_subject") or "New website enquiry — {company}"),
            context,
        )
        body = render_placeholders(
            str(settings_data.get("lead_notify_body") or ""), context
        )
        notify_sent = send_platform_mail(_one_line(subject), body, [notify_recipient])
    return {"lead": lead, "ack_sent": ack_sent, "notify_sent": notify_sent}


def available_module_values() -> list[tuple[str, str]]:
    return [
        (module.code, module.brand_name or module.name)
        for module in SaasModule.objects.filter(is_available=True).order_by("sort_order", "name")
    ]


def _one_line(value: str) -> str:
    return " ".join(str(value or "").split())
