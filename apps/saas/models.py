"""Control-plane models for SORA Cloud: tenants, licences, billing and settings."""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

TENANT_STATUS_CHOICES = [
    ("trial", "Trial"),
    ("active", "Active"),
    ("past_due", "Past Due"),
    ("expired", "Expired"),
    ("suspended", "Suspended"),
    ("cancelled", "Cancelled"),
]
LOCKED_STATUSES = {"expired", "suspended", "cancelled"}

MEMBERSHIP_ROLES = [
    ("owner", "Owner"),
    ("admin", "Administrator"),
    ("manager", "Manager"),
    ("member", "Member"),
]
TENANT_ADMIN_ROLES = {"owner", "admin"}

PAYMENT_METHODS = [
    ("manual", "Manual / Cash"),
    ("bank", "Bank Transfer"),
    ("mpesa", "M-Pesa"),
    ("stripe", "Stripe"),
    ("paypal", "PayPal"),
    ("card", "Card"),
]
PAYMENT_STATUSES = [
    ("pending", "Pending"),
    ("completed", "Completed"),
    ("failed", "Failed"),
    ("refunded", "Refunded"),
]

ANNOUNCEMENT_LEVELS = [
    ("info", "Information"),
    ("success", "Success"),
    ("warning", "Warning"),
    ("critical", "Critical"),
]
ANNOUNCEMENT_AUDIENCES = [
    ("all", "All tenants"),
    ("trial", "Trials only"),
    ("active", "Paying tenants"),
    ("locked", "Expired / suspended"),
]


class SaasModule(models.Model):
    """A product module that can be licensed to a tenant."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    brand_name = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, default="cube")
    nav_key = models.CharField(max_length=30, blank=True)
    landing_route = models.CharField(max_length=200, blank=True)
    route_prefixes = models.JSONField(default=list, blank=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    is_core = models.BooleanField(default=False)
    is_available = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saas_modules"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.brand_name or self.name


class SaasPlan(models.Model):
    """Commercial bundle shown on the public pricing page."""

    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    tagline = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    yearly_price = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    max_users = models.IntegerField(default=10)
    included_modules = models.JSONField(default=list, blank=True)
    features = models.JSONField(default=list, blank=True)
    is_public = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saas_plans"
        ordering = ["sort_order", "monthly_price"]

    def __str__(self):
        return self.name

    @property
    def yearly_saving(self):
        if self.monthly_price and self.yearly_price:
            return max(self.monthly_price * 12 - self.yearly_price, Decimal("0.00"))
        return Decimal("0.00")


class Tenant(models.Model):
    """A customer workspace. Business rows are scoped by ``tenant_id``."""

    company_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True)
    admin_email = models.EmailField(max_length=255)
    phone_number = models.CharField(max_length=50, blank=True)
    brand_name = models.CharField(max_length=255, blank=True)
    logo_url = models.CharField(max_length=500, blank=True)
    primary_color = models.CharField(max_length=20, blank=True, default="#FFD700")
    currency = models.CharField(max_length=10, default="KES")
    timezone_name = models.CharField(max_length=64, blank=True, default="Africa/Nairobi")

    plan = models.ForeignKey(SaasPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="tenants")
    billing_cycle = models.CharField(
        max_length=10,
        choices=[("monthly", "Monthly"), ("yearly", "Yearly")],
        default="monthly",
    )
    status = models.CharField(max_length=20, choices=TENANT_STATUS_CHOICES, default="trial", db_index=True)
    trial_starts_at = models.DateTimeField(default=timezone.now)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    subscription_starts_at = models.DateTimeField(null=True, blank=True)
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    grace_ends_at = models.DateTimeField(null=True, blank=True)
    max_users = models.IntegerField(default=10)

    is_demo = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    suspended_reason = models.CharField(max_length=255, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saas_tenants"
        ordering = ["-created_at"]

    def __str__(self):
        return self.company_name

    @property
    def display_name(self):
        return self.brand_name or self.company_name

    @property
    def effective_status(self):
        if self.status == "trial" and self.trial_ends_at and self.trial_ends_at < timezone.now():
            return "expired"
        if self.status == "active" and self.subscription_ends_at and self.subscription_ends_at < timezone.now():
            return "expired"
        return self.status

    @property
    def effective_status_label(self):
        return dict(TENANT_STATUS_CHOICES).get(
            self.effective_status, self.effective_status.replace("_", " ").title()
        )

    @property
    def is_trial(self):
        return self.status == "trial" and self.effective_status == "trial"

    @property
    def trial_days_left(self):
        if not self.trial_ends_at:
            return 0
        return max((self.trial_ends_at.date() - timezone.localdate()).days, 0)

    @property
    def is_locked(self):
        status = self.effective_status
        if status == "past_due":
            return not self.grace_ends_at or self.grace_ends_at < timezone.now()
        return status in LOCKED_STATUSES

    @property
    def lock_reason(self):
        status = self.effective_status
        if status == "expired":
            return "Your trial or subscription has expired."
        if status == "suspended":
            return self.suspended_reason or "This workspace is suspended."
        if status == "cancelled":
            return "This workspace has been cancelled."
        if status == "past_due":
            return "Your payment is overdue."
        return ""

    @property
    def days_overdue(self):
        reference = self.subscription_ends_at if self.status == "active" else self.trial_ends_at
        if not reference:
            return 0
        return max((timezone.localdate() - reference.date()).days, 0)

    def has_module(self, code: str) -> bool:
        return self.modules.filter(module__code=code, is_enabled=True).exists()

    def enabled_module_codes(self) -> list[str]:
        return list(
            self.modules.filter(is_enabled=True).values_list("module__code", flat=True)
        )


class TenantDomain(models.Model):
    """A tenant-owned hostname proven with a DNS TXT record.

    Only ``verified`` domains are routed to the workspace. Rows remain behind
    after a domain is revoked so support can see its history; the hostname stays
    unique so a pending claim can never be hijacked by another tenant.
    """

    STATUS_CHOICES = [
        ("pending", "Pending verification"),
        ("verified", "Verified"),
        ("disabled", "Disabled"),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="domains")
    hostname = models.CharField(max_length=253, unique=True)
    is_primary = models.BooleanField(default=False)
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="pending", db_index=True
    )
    verification_token = models.CharField(max_length=64)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    check_count = models.PositiveIntegerField(default=0)
    consecutive_failures = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_tenant_domains",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saas_tenant_domains"
        ordering = ["-is_primary", "hostname"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_primary=True),
                name="saas_tenant_one_primary_domain",
            )
        ]

    def __str__(self):
        return f"{self.hostname} ({self.tenant_id})"

    @property
    def verification_record_name(self):
        return f"_sora-verification.{self.hostname}"

    @property
    def verification_record_value(self):
        return f"sora-verify={self.verification_token}"

    @property
    def is_verified(self):
        return self.status == "verified"

    @property
    def is_pending(self):
        return self.status == "pending"

    @property
    def status_class(self):
        return {"verified": "success", "pending": "warning"}.get(self.status, "secondary")

    @property
    def url(self):
        return f"https://{self.hostname}"


class TenantMembership(models.Model):
    """Links a global login to a workspace."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tenant_memberships")
    role = models.CharField(max_length=20, choices=MEMBERSHIP_ROLES, default="member")
    status = models.CharField(
        max_length=10,
        choices=[("active", "Active"), ("invited", "Invited"), ("disabled", "Disabled")],
        default="active",
        db_index=True,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_tenant_invitations",
    )
    joined_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "saas_tenant_memberships"
        unique_together = (("tenant", "user"),)
        ordering = ["role", "user__first_name"]

    def __str__(self):
        return f"{self.user} @ {self.tenant}"

    @property
    def is_tenant_admin(self):
        return self.role in TENANT_ADMIN_ROLES

    @property
    def can_manage_billing(self):
        return self.role in TENANT_ADMIN_ROLES


class TenantModule(models.Model):
    """The tenant's licence manifest entry for one module."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="modules")
    module = models.ForeignKey(SaasModule, on_delete=models.CASCADE, related_name="tenant_licences")
    is_enabled = models.BooleanField(default=True, db_index=True)
    source = models.CharField(
        max_length=10,
        choices=[
            ("trial", "Trial"),
            ("plan", "Plan"),
            ("addon", "Add-on"),
            ("manual", "Manual override"),
        ],
        default="manual",
    )
    enabled_at = models.DateTimeField(default=timezone.now)
    disabled_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "saas_tenant_modules"
        unique_together = (("tenant", "module"),)
        ordering = ["module__sort_order"]

    def __str__(self):
        state = "enabled" if self.is_enabled else "disabled"
        return f"{self.tenant} / {self.module.code} ({state})"

    @property
    def is_valid(self):
        return self.is_enabled and (self.valid_until is None or self.valid_until > timezone.now())


class TenantPayment(models.Model):
    """Manual or gateway payment recorded against a tenant."""

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(SaasPlan, null=True, blank=True, on_delete=models.SET_NULL)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")
    method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="manual")
    reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=PAYMENT_STATUSES, default="pending", db_index=True)
    billing_cycle = models.CharField(
        max_length=10, choices=[("monthly", "Monthly"), ("yearly", "Yearly")], default="monthly"
    )
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    gateway_checkout_id = models.CharField(max_length=191, blank=True, db_index=True)
    gateway_payload = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="recorded_tenant_payments"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_tenant_payments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant} {self.amount} {self.currency} ({self.status})"


class Announcement(models.Model):
    """Broadcast message shown on tenant dashboards."""

    title = models.CharField(max_length=200)
    body = models.TextField()
    level = models.CharField(max_length=10, choices=ANNOUNCEMENT_LEVELS, default="info")
    audience = models.CharField(max_length=10, choices=ANNOUNCEMENT_AUDIENCES, default="all")
    is_active = models.BooleanField(default=True, db_index=True)
    is_dismissible = models.BooleanField(default=True)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)
    send_email = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_announcements"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_live(self):
        now = timezone.now()
        return self.is_active and self.starts_at <= now and (self.ends_at is None or self.ends_at >= now)


class AnnouncementDismissal(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="dismissals")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saas_announcement_dismissals"
        unique_together = (("announcement", "user"),)


class PlatformSetting(models.Model):
    """Zero-hardcoding global settings store for SORA Cloud."""

    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True)
    value_type = models.CharField(
        max_length=10,
        choices=[
            ("text", "Text"),
            ("email", "Email"),
            ("number", "Number"),
            ("bool", "Boolean"),
            ("secret", "Secret"),
            ("color", "Colour"),
            ("textarea", "Long text"),
        ],
        default="text",
    )
    group = models.CharField(max_length=40, default="general", db_index=True)
    label = models.CharField(max_length=150)
    help_text = models.CharField(max_length=255, blank=True)
    sort_order = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        db_table = "saas_platform_settings"
        ordering = ["group", "sort_order", "key"]

    def __str__(self):
        return self.key

    @property
    def typed_value(self):
        raw = self.value
        if self.value_type == "bool":
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}
        if self.value_type == "number":
            try:
                return int(raw)
            except (TypeError, ValueError):
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return 0
        return raw


class ImpersonationLog(models.Model):
    """Audit trail for support impersonation sessions."""

    platform_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="impersonation_sessions"
    )
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="impersonation_logs")
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="impersonated_sessions"
    )
    reason = models.CharField(max_length=255, blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.TextField(blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "saas_impersonation_logs"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.platform_admin} as {self.target_user} @ {self.tenant}"


class TenantAuditLog(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    action = models.CharField(max_length=80)
    details = models.TextField(blank=True)
    ip_address = models.CharField(max_length=45, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "saas_tenant_audit_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant} {self.action}"


SALES_LEAD_STATUSES = [
    ("new", "New enquiry"),
    ("contacted", "Contacted"),
    ("qualified", "Qualified"),
    ("proposal", "Proposal sent"),
    ("won", "Won"),
    ("lost", "Lost"),
]


class SalesLead(models.Model):
    """Public website enquiry feeding the SORA Cloud sales pipeline.

    Leads intentionally live on the control plane, not inside a tenant, because
    they exist before a workspace is provisioned.
    """

    name = models.CharField(max_length=150)
    company = models.CharField(max_length=255, blank=True)
    email = models.EmailField(max_length=255, db_index=True)
    phone = models.CharField(max_length=50, blank=True)
    country = models.CharField(max_length=100, blank=True)
    modules = models.JSONField(default=list, blank=True)
    message = models.TextField(blank=True)
    source = models.CharField(max_length=60, default="website")
    status = models.CharField(
        max_length=20, choices=SALES_LEAD_STATUSES, default="new", db_index=True
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_sales_leads",
    )
    notes = models.TextField(blank=True)
    next_follow_up = models.DateField(null=True, blank=True)
    tenant = models.ForeignKey(
        Tenant, null=True, blank=True, on_delete=models.SET_NULL, related_name="source_leads"
    )
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saas_sales_leads"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.name} · {self.company or self.email}"

    @property
    def status_label(self):
        return dict(SALES_LEAD_STATUSES).get(self.status, self.status.title())


class SalesLeadActivity(models.Model):
    lead = models.ForeignKey(SalesLead, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    kind = models.CharField(
        max_length=20,
        choices=[("note", "Note"), ("status", "Status change"), ("contact", "Contacted"), ("converted", "Converted")],
        default="note",
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "saas_sales_lead_activities"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.lead_id}:{self.kind}"
