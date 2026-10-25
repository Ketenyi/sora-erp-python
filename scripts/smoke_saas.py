"""End-to-end checks for the SaaS control plane, licensing and Cloud Control.

Everything runs inside a transaction that is rolled back, so the seeded
workspace and catalogue are never modified. Emails are stubbed out.
"""

import os
import sys
from datetime import timedelta
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import User
from apps.saas import views as saas_views
from apps.saas.models import (
    Announcement,
    AnnouncementDismissal,
    ImpersonationLog,
    PlatformSetting,
    SaasModule,
    SaasPlan,
    SalesLead,
    SalesLeadActivity,
    Tenant,
    TenantAuditLog,
    TenantMembership,
    TenantModule,
    TenantPayment,
)
from apps.saas.services import enabled_module_map, expire_overdue_tenants

saas_views.send_welcome_email = lambda *args, **kwargs: True
saas_views.send_platform_mail = lambda *args, **kwargs: True

results = []


def check(label, response, expected=(200,), contains=None):
    text = response.content.decode("utf-8", "replace")
    ok = response.status_code in expected
    if ok and contains and contains not in text:
        ok = False
        print(f"FAIL {label}: missing text {contains!r}")
    results.append((label, response.status_code, ok))
    if not ok:
        print(f"FAIL {label}: {response.status_code} {text[:500]}")
    return response


def expect(label, condition, detail=""):
    results.append((label, "-", bool(condition)))
    if not condition:
        print(f"FAIL {label}: {detail}")


def location(response):
    return response.headers.get("Location", "")


def settings_payload(group):
    payload = {}
    for row in PlatformSetting.objects.filter(group=group).order_by("sort_order"):
        if row.value_type == "bool":
            payload[f"setting_{row.key}"] = "on" if row.typed_value else ""
        else:
            payload[f"setting_{row.key}"] = str(row.value)
    return payload


stamp = int(timezone.now().timestamp())
public = TestClient(headers={"host": "localhost"})
tenant_client = TestClient(headers={"host": "localhost"})
cloud = TestClient(headers={"host": "localhost"})
platform_admin = User.objects.get(email="admin@sora.com")
cloud.force_login(platform_admin)

with transaction.atomic():
    # ------------------------------------------------------------------
    # Public portal
    # ------------------------------------------------------------------
    check("public_landing", public.get("/"), contains="SORA ERP")
    check("public_pricing", public.get("/pricing"), contains="Pricing")
    check("public_signup", public.get("/signup"), contains="SORA Projects")
    check("public_contact", public.get("/contact"), contains="Talk to sales")
    check("public_health", public.get("/health"))

    # ------------------------------------------------------------------
    # Public enquiry lands in the Cloud sales pipeline
    # ------------------------------------------------------------------
    lead_email = f"smoke.lead.{stamp}@example.com"
    contact_payload = {
        "name": "Amina Smoke",
        "company": "Smoke Enquiry Ltd",
        "email": lead_email,
        "phone": "+254700000010",
        "country": "Kenya",
        "modules": ["SORA_PROJECTS"],
        "message": "We need a delivery workspace for 40 engineers.",
        "consent": "on",
        "website": "",
    }
    response = public.post("/contact", contact_payload)
    expect("lead_capture_redirect", response.status_code == 302, response.status_code)
    expect(
        "lead_capture_target",
        location(response) == "/contact?sent=1",
        location(response),
    )
    public_lead = SalesLead.objects.get(email=lead_email)
    expect(
        "lead_capture_fields",
        public_lead.source == "website"
        and public_lead.modules == ["SORA_PROJECTS"]
        and public_lead.company == "Smoke Enquiry Ltd",
        (public_lead.source, public_lead.modules),
    )
    expect(
        "lead_capture_activity",
        public_lead.activities.filter(kind="note", note__icontains="Enquiry captured").exists(),
    )
    check(
        "lead_capture_success_page",
        public.get("/contact?sent=1"),
        contains="Request received",
    )
    duplicate = public.post("/contact", contact_payload)
    expect("lead_duplicate_redirect", duplicate.status_code == 302, duplicate.status_code)
    expect(
        "lead_duplicate_suppressed",
        SalesLead.objects.filter(email=lead_email).count() == 1,
        SalesLead.objects.filter(email=lead_email).count(),
    )
    honeypot_email = f"smoke.honeypot.{stamp}@example.com"
    honeypot = public.post(
        "/contact", {**contact_payload, "email": honeypot_email, "website": "http://spam.test"}
    )
    expect(
        "lead_honeypot_rejected",
        honeypot.status_code == 200
        and not SalesLead.objects.filter(email=honeypot_email).exists(),
        honeypot.status_code,
    )

    response = tenant_client.get("/cloud/leads/")
    check("cloud_leads_anonymous_redirect", response, expected=(302,))
    expect(
        "cloud_leads_anonymous_login",
        location(response).startswith("/login"),
        location(response),
    )
    check("cloud_leads_page", cloud.get("/cloud/leads/"), contains="Sales pipeline")
    check(
        "cloud_lead_detail",
        cloud.get(f"/cloud/leads/{public_lead.id}/"),
        contains=lead_email,
    )

    manual_email = f"smoke.manual.{stamp}@example.com"
    response = cloud.post(
        "/cloud/leads/new/",
        {
            "name": "Manual Smoke",
            "company": "Manual Smoke Co",
            "email": manual_email,
            "phone": "+254700000011",
            "country": "Kenya",
            "modules": ["SORA_HR"],
            "message": "Interested in HR and payroll.",
            "source": "manual",
            "status": "new",
            "owner": str(platform_admin.id),
            "next_follow_up": "",
            "notes": "",
        },
    )
    expect("cloud_lead_create", response.status_code == 302, response.status_code)
    manual_lead = SalesLead.objects.get(email=manual_email)
    expect(
        "cloud_lead_create_target",
        location(response) == f"/cloud/leads/{manual_lead.id}/",
        location(response),
    )
    check(
        "cloud_lead_update",
        cloud.post(
            f"/cloud/leads/{manual_lead.id}/update/",
            {
                "status": "contacted",
                "owner": str(platform_admin.id),
                "next_follow_up": timezone.localdate().isoformat(),
                "note": "Smoke qualification call.",
            },
        ),
        expected=(302,),
    )
    manual_lead.refresh_from_db()
    expect("cloud_lead_updated", manual_lead.status == "contacted", manual_lead.status)
    expect(
        "cloud_lead_update_activity",
        manual_lead.activities.filter(note__icontains="Smoke qualification").exists(),
    )
    check(
        "cloud_lead_note",
        cloud.post(
            f"/cloud/leads/{manual_lead.id}/note/",
            {"kind": "contact", "note": "Sent the smoke pricing sheet."},
        ),
        expected=(302,),
    )
    expect(
        "cloud_lead_note_saved",
        manual_lead.activities.filter(
            kind="contact", note__icontains="pricing sheet"
        ).exists(),
    )

    convert_email = f"smoke.convert.{stamp}@example.com"
    response = cloud.post(
        f"/cloud/leads/{manual_lead.id}/convert/",
        {
            "company_name": "Smoke Converted Co",
            "first_name": "Smoke",
            "last_name": "Converted",
            "email": convert_email,
            "phone": "",
            "plan": "",
            "modules": ["SORA_PROJECTS"],
            "trial_days": "14",
        },
    )
    expect("cloud_lead_convert", response.status_code == 302, response.status_code)
    converted_tenant = Tenant.objects.get(admin_email=convert_email)
    manual_lead.refresh_from_db()
    expect(
        "cloud_lead_won",
        manual_lead.status == "won" and manual_lead.tenant_id == converted_tenant.id,
        (manual_lead.status, manual_lead.tenant_id),
    )
    expect(
        "cloud_lead_convert_target",
        location(response) == f"/cloud/tenants/{converted_tenant.id}/",
        location(response),
    )
    expect(
        "cloud_lead_convert_audited",
        TenantAuditLog.objects.filter(
            tenant=converted_tenant, action="workspace.created_from_lead"
        ).exists(),
    )
    expect(
        "cloud_lead_converted_activity",
        manual_lead.activities.filter(kind="converted").exists(),
    )

    # ------------------------------------------------------------------
    # Self-service trial signup
    # ------------------------------------------------------------------
    trial_email = f"smoke.trial.{stamp}@example.com"
    response = tenant_client.post(
        "/signup",
        {
            "company_name": "Smoke Trial Workspace",
            "first_name": "Smoke",
            "last_name": "Trial",
            "email": trial_email,
            "phone": "+254700000001",
            "password": "SmokeTrial123!",
            "confirm_password": "SmokeTrial123!",
            "modules": ["SORA_PROJECTS"],
            "accept_terms": "on",
        },
    )
    expect("signup_redirect", response.status_code == 302, response.status_code)
    expect(
        "signup_lands_on_module",
        location(response) == "/projects/dashboard",
        location(response),
    )
    trial_tenant = Tenant.objects.get(admin_email=trial_email)
    expect("signup_trial_status", trial_tenant.status == "trial", trial_tenant.status)
    expect(
        "signup_trial_window",
        trial_tenant.trial_ends_at > timezone.now() + timedelta(days=29),
        trial_tenant.trial_ends_at,
    )
    expect(
        "signup_single_module",
        set(enabled_module_map(trial_tenant)) == {"SORA_PROJECTS"},
        enabled_module_map(trial_tenant).keys(),
    )
    expect(
        "signup_owner_membership",
        TenantMembership.objects.filter(
            tenant=trial_tenant, role="owner", status="active"
        ).exists(),
    )

    # ------------------------------------------------------------------
    # Licensing, dynamic navigation and route gating
    # ------------------------------------------------------------------
    check(
        "trial_dashboard",
        tenant_client.get("/projects/dashboard"),
        contains="SORA Projects",
    )
    dashboard_html = tenant_client.get("/projects/dashboard").content.decode("utf-8")
    expect(
        "nav_hides_unlicensed",
        "Finance &amp; Accounts" not in dashboard_html
        and "Finance & Accounts" not in dashboard_html,
    )
    check(
        "licensed_route_blocked",
        tenant_client.get("/accounts/dashboard"),
        expected=(403,),
        contains="not included in your current SORA subscription",
    )
    check(
        "licensed_route_json",
        tenant_client.get(
            "/accounts/dashboard", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        ),
        expected=(403,),
        contains="module_not_licensed",
    )
    expected_landing = tenant_client.get("/dashboard")
    check("single_module_landing", expected_landing, expected=(302,))
    expect(
        "single_module_landing_target",
        location(expected_landing) == "/projects/dashboard",
        location(expected_landing),
    )
    check("trial_billing", tenant_client.get("/billing/"), contains="Trial workspace")
    check("trial_banner", tenant_client.get("/projects/dashboard"), contains="trial ends in")

    # ------------------------------------------------------------------
    # Day-31 lockout and read-only billing
    # ------------------------------------------------------------------
    trial_tenant.trial_ends_at = timezone.now() - timedelta(days=1)
    trial_tenant.save(update_fields=["trial_ends_at"])
    expire_overdue_tenants()
    trial_tenant.refresh_from_db()
    expect(
        "trial_expired",
        trial_tenant.status == "expired" and trial_tenant.is_locked,
        trial_tenant.status,
    )
    locked = tenant_client.get("/projects/dashboard")
    check("lockout_redirect", locked, expected=(302,))
    expect(
        "lockout_target",
        location(locked).startswith("/billing/"),
        location(locked),
    )
    check(
        "lockout_json",
        tenant_client.get(
            "/projects/dashboard", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        ),
        expected=(402,),
        contains="subscription_locked",
    )
    check("locked_billing", tenant_client.get("/billing/?locked=1"), contains="read-only")
    before_name = trial_tenant.company_name
    check(
        "locked_profile_blocked",
        tenant_client.post(
            "/billing/", {"action": "profile", "company_name": "Should Not Save"}
        ),
        expected=(302,),
    )
    trial_tenant.refresh_from_db()
    expect(
        "locked_profile_unchanged",
        trial_tenant.company_name == before_name,
        trial_tenant.company_name,
    )

    # Confirming a payment without any plan must not 500 or activate.
    guard_payment = TenantPayment.objects.create(
        tenant=trial_tenant,
        amount=Decimal("4500.00"),
        currency=trial_tenant.currency,
        method="manual",
        status="pending",
        billing_cycle="monthly",
    )
    check(
        "confirm_without_plan_guard",
        cloud.post(f"/cloud/payments/{guard_payment.id}/confirm/"),
        expected=(302,),
    )
    guard_payment.refresh_from_db()
    trial_tenant.refresh_from_db()
    expect(
        "confirm_without_plan_kept_pending",
        guard_payment.status == "pending",
        guard_payment.status,
    )
    expect(
        "confirm_without_plan_not_activated",
        trial_tenant.status == "expired",
        trial_tenant.status,
    )

    starter = SaasPlan.objects.get(code="starter")
    check(
        "renewal_submitted",
        tenant_client.post(
            "/billing/",
            {
                "action": "renewal",
                "plan": str(starter.id),
                "billing_cycle": "monthly",
                "method": "manual",
                "reference": "",
                "notes": "Smoke renewal.",
            },
        ),
        expected=(302,),
    )
    renewal_payment = (
        TenantPayment.objects.filter(tenant=trial_tenant, status="pending")
        .exclude(id=guard_payment.id)
        .order_by("-id")
        .first()
    )
    expect("renewal_payment_created", renewal_payment is not None)
    expect(
        "renewal_payment_has_plan",
        renewal_payment is not None and renewal_payment.plan_id == starter.id,
    )
    check(
        "renewal_receipt",
        tenant_client.get(f"/billing/receipts/{renewal_payment.id}/"),
        contains="Payment request",
    )

    # ------------------------------------------------------------------
    # Platform admin confirms the payment and reactivates the tenant
    # ------------------------------------------------------------------
    check(
        "cloud_confirm_payment",
        cloud.post(f"/cloud/payments/{renewal_payment.id}/confirm/"),
        expected=(302,),
    )
    trial_tenant.refresh_from_db()
    renewal_payment.refresh_from_db()
    expect(
        "reactivated_active",
        trial_tenant.status == "active",
        trial_tenant.status,
    )
    expect(
        "reactivated_subscription",
        trial_tenant.subscription_ends_at is not None
        and trial_tenant.subscription_ends_at > timezone.now(),
        trial_tenant.subscription_ends_at,
    )
    expect(
        "reactivated_payment_completed",
        renewal_payment.status == "completed" and renewal_payment.paid_at is not None,
        renewal_payment.status,
    )
    check(
        "reactivated_access",
        tenant_client.get("/projects/dashboard"),
        contains="Project Management",
    )
    check(
        "completed_receipt",
        tenant_client.get(f"/billing/receipts/{renewal_payment.id}/"),
        contains="Payment receipt",
    )
    check(
        "billing_after_renewal",
        tenant_client.get("/billing/"),
        contains="Payment history",
    )

    # ------------------------------------------------------------------
    # Cloud Control overview pages
    # ------------------------------------------------------------------
    check("cloud_denied", tenant_client.get("/cloud/"), expected=(403,))
    check("cloud_dashboard", cloud.get("/cloud/"), contains="Platform overview")
    check("cloud_tenants", cloud.get("/cloud/tenants/"), contains="Tenants")
    check(
        "cloud_tenants_filter",
        cloud.get("/cloud/tenants/?status=trial&module=SORA_HR"),
        contains="Tenants",
    )
    check("cloud_billing_page", cloud.get("/cloud/billing/"), contains="Billing")
    check(
        "cloud_announcements_page",
        cloud.get("/cloud/announcements/"),
        contains="Broadcast",
    )
    check("cloud_plans_page", cloud.get("/cloud/plans/"), contains="Starter")
    check("cloud_settings_page", cloud.get("/cloud/settings/brand/"), contains="Platform")

    # ------------------------------------------------------------------
    # Cloud tenant provisioning and module toggles
    # ------------------------------------------------------------------
    cloud_email = f"smoke.cloud.{stamp}@example.com"
    response = cloud.post(
        "/cloud/tenants/new/",
        {
            "company_name": "Smoke Cloud Client",
            "first_name": "Cloud",
            "last_name": "Owner",
            "email": cloud_email,
            "phone": "+254700000002",
            "password": "SmokeCloud123!",
            "plan": "",
            "modules": ["SORA_HR"],
            "trial_days": "14",
            "notes": "Created by smoke_saas.py",
        },
    )
    expect("cloud_create_redirect", response.status_code == 302, response.status_code)
    cloud_tenant = Tenant.objects.get(admin_email=cloud_email)
    expect(
        "cloud_create_target",
        location(response) == f"/cloud/tenants/{cloud_tenant.id}/",
        location(response),
    )
    expect("cloud_tenant_trial", cloud_tenant.status == "trial", cloud_tenant.status)
    expect(
        "cloud_tenant_licences",
        set(enabled_module_map(cloud_tenant)) == {"SORA_HR"},
        enabled_module_map(cloud_tenant).keys(),
    )
    expect(
        "cloud_tenant_trial_days",
        13 <= (cloud_tenant.trial_ends_at - timezone.now()).days <= 14,
        cloud_tenant.trial_ends_at,
    )
    check(
        "cloud_tenant_detail",
        cloud.get(f"/cloud/tenants/{cloud_tenant.id}/"),
        contains="Smoke Cloud Client",
    )

    cloud_owner = User.objects.get(email=cloud_email)
    owner_client = TestClient(headers={"host": "localhost"})
    owner_client.force_login(cloud_owner)
    check("cloud_owner_hr_access", owner_client.get("/hr/dashboard"))

    hr_module = SaasModule.objects.get(code="SORA_HR")
    check(
        "cloud_toggle_hr_off",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/modules/{hr_module.id}/toggle/"
        ),
        expected=(302,),
    )
    check("cloud_owner_hr_blocked", owner_client.get("/hr/dashboard"), expected=(403,))
    check(
        "cloud_toggle_hr_on",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/modules/{hr_module.id}/toggle/"
        ),
        expected=(302,),
    )
    check("cloud_owner_hr_restored", owner_client.get("/hr/dashboard"))

    finance_module = SaasModule.objects.get(code="SORA_FINANCE")
    check(
        "cloud_toggle_finance",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/modules/{finance_module.id}/toggle/"
        ),
        expected=(302,),
    )
    expect(
        "cloud_toggle_finance_saved",
        TenantModule.objects.get(tenant=cloud_tenant, module=finance_module).is_enabled,
    )

    # ------------------------------------------------------------------
    # Trial extension, status override, profile edit
    # ------------------------------------------------------------------
    before_trial = cloud_tenant.trial_ends_at
    check(
        "cloud_extend_trial",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/trial/extend/",
            {"days": "7", "reason": "Smoke extension."},
        ),
        expected=(302,),
    )
    cloud_tenant.refresh_from_db()
    expect(
        "cloud_trial_extended",
        cloud_tenant.trial_ends_at >= before_trial + timedelta(days=6, hours=23),
        cloud_tenant.trial_ends_at - before_trial,
    )
    check(
        "cloud_suspend",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/status/",
            {"status": "suspended", "reason": "Smoke suspension."},
        ),
        expected=(302,),
    )
    cloud_tenant.refresh_from_db()
    expect("cloud_suspended", cloud_tenant.status == "suspended", cloud_tenant.status)
    check("cloud_owner_suspended_billing", owner_client.get("/billing/"), contains="read-only")
    check(
        "cloud_restore_trial",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/status/",
            {"status": "trial", "reason": "Restored by smoke."},
        ),
        expected=(302,),
    )
    cloud_tenant.refresh_from_db()
    expect("cloud_trial_restored", cloud_tenant.status == "trial", cloud_tenant.status)

    check(
        "cloud_tenant_edit",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/edit/",
            {
                "company_name": "Smoke Cloud Client Ltd",
                "admin_email": cloud_email,
                "phone_number": "+254700000002",
                "brand_name": "Smoke Cloud",
                "logo_url": "",
                "primary_color": "#FFD700",
                "currency": "KES",
                "timezone_name": "Africa/Nairobi",
                "max_users": "12",
                "notes": "Edited by smoke_saas.py",
            },
        ),
        expected=(302,),
    )
    cloud_tenant.refresh_from_db()
    expect(
        "cloud_tenant_edited",
        cloud_tenant.company_name == "Smoke Cloud Client Ltd"
        and cloud_tenant.max_users == 12,
        (cloud_tenant.company_name, cloud_tenant.max_users),
    )

    # ------------------------------------------------------------------
    # Manual payment activates the cloud-created tenant
    # ------------------------------------------------------------------
    check(
        "cloud_record_payment",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/payments/record/",
            {
                "amount": "4500.00",
                "method": "manual",
                "reference": "SMOKE-001",
                "plan": str(starter.id),
                "billing_cycle": "monthly",
                "notes": "Smoke manual payment.",
            },
        ),
        expected=(302,),
    )
    cloud_tenant.refresh_from_db()
    cloud_payment = (
        TenantPayment.objects.filter(tenant=cloud_tenant, status="completed")
        .order_by("-id")
        .first()
    )
    expect(
        "cloud_payment_activated",
        cloud_tenant.status == "active" and cloud_tenant.plan_id == starter.id,
        (cloud_tenant.status, cloud_tenant.plan_id),
    )
    expect(
        "cloud_payment_completed",
        cloud_payment is not None and cloud_payment.paid_at is not None,
    )

    # ------------------------------------------------------------------
    # Cross-tenant receipt isolation
    # ------------------------------------------------------------------
    check(
        "receipt_cross_tenant_blocked",
        owner_client.get(f"/billing/receipts/{renewal_payment.id}/"),
        expected=(404,),
    )

    # ------------------------------------------------------------------
    # Broadcast announcements + dismissal
    # ------------------------------------------------------------------
    check(
        "cloud_announcement_create",
        cloud.post(
            "/cloud/announcements/",
            {
                "title": f"Smoke maintenance {stamp}",
                "body": "The smoke suite says hello to every tenant.",
                "level": "info",
                "audience": "all",
                "is_active": "on",
                "is_dismissible": "on",
                "starts_at": "",
                "ends_at": "",
            },
        ),
        expected=(302,),
    )
    announcement = Announcement.objects.get(title=f"Smoke maintenance {stamp}")
    check(
        "announcement_visible",
        tenant_client.get("/projects/dashboard"),
        contains=announcement.title,
    )
    check(
        "announcement_toggle_pause",
        cloud.post(f"/cloud/announcements/{announcement.id}/toggle/"),
        expected=(302,),
    )
    announcement_html = tenant_client.get("/projects/dashboard").content.decode("utf-8")
    expect("announcement_paused", announcement.title not in announcement_html)
    check(
        "announcement_toggle_resume",
        cloud.post(f"/cloud/announcements/{announcement.id}/toggle/"),
        expected=(302,),
    )
    check(
        "announcement_dismiss",
        tenant_client.post(
            f"/announcements/{announcement.id}/dismiss/",
            {"next": "/projects/dashboard"},
        ),
        expected=(302,),
    )
    expect(
        "announcement_dismissal_saved",
        AnnouncementDismissal.objects.filter(
            announcement=announcement, user__email=trial_email
        ).exists(),
    )
    dismissed_html = tenant_client.get("/projects/dashboard").content.decode("utf-8")
    expect("announcement_dismissed_hidden", announcement.title not in dismissed_html)

    # ------------------------------------------------------------------
    # Plan pricing edit
    # ------------------------------------------------------------------
    check(
        "cloud_plan_edit",
        cloud.post(
            f"/cloud/plans/{starter.id}/edit/",
            {
                "name": starter.name,
                "tagline": starter.tagline,
                "description": starter.description,
                "monthly_price": "4600.00",
                "yearly_price": "44000.00",
                "max_users": str(starter.max_users),
                "included_modules": starter.included_modules,
                "features": "\n".join(starter.features or []),
                "is_public": "on",
                "is_active": "on",
                "sort_order": str(starter.sort_order),
            },
        ),
        expected=(302,),
    )
    starter.refresh_from_db()
    expect(
        "cloud_plan_price_saved",
        starter.monthly_price == Decimal("4600.00"),
        starter.monthly_price,
    )

    # ------------------------------------------------------------------
    # Zero-hardcoding platform settings
    # ------------------------------------------------------------------
    brand_rows = list(PlatformSetting.objects.filter(group="brand").order_by("sort_order"))
    payload = {}
    for row in brand_rows:
        if row.value_type == "bool":
            payload[f"setting_{row.key}"] = "on" if row.typed_value else ""
        else:
            payload[f"setting_{row.key}"] = str(row.value)
    payload["setting_platform_name"] = "SORA Smoke Cloud"
    check(
        "cloud_settings_save",
        cloud.post("/cloud/settings/brand/", payload),
        expected=(302,),
    )
    expect(
        "cloud_settings_persisted",
        PlatformSetting.objects.get(key="platform_name").value == "SORA Smoke Cloud",
        PlatformSetting.objects.get(key="platform_name").value,
    )

    site_payload = settings_payload("site")
    site_payload["setting_site_hero_title"] = "Smoke platform."
    site_payload["setting_site_mock_label"] = "Smoke preview cloud"
    site_payload["setting_site_plan_flag"] = "Smoke favourite"
    site_payload["setting_site_signup_title"] = "Smoke workspace signup."
    check(
        "cloud_settings_site_save",
        cloud.post("/cloud/settings/site/", site_payload),
        expected=(302,),
    )
    expect(
        "cloud_settings_site_persisted",
        PlatformSetting.objects.get(key="site_hero_title").value == "Smoke platform.",
        PlatformSetting.objects.get(key="site_hero_title").value,
    )
    check(
        "public_landing_uses_site_copy",
        public.get("/"),
        contains="Smoke platform.",
    )
    check(
        "public_landing_uses_preview_copy",
        public.get("/"),
        contains="Smoke preview cloud",
    )
    check(
        "public_pricing_uses_plan_copy",
        public.get("/pricing"),
        contains="Smoke favourite",
    )
    check(
        "public_signup_uses_signup_copy",
        public.get("/signup"),
        contains="Smoke workspace signup.",
    )

    lead_payload = settings_payload("leads")
    lead_payload["setting_lead_success_message"] = "Smoke success message from the database."
    check(
        "cloud_settings_leads_save",
        cloud.post("/cloud/settings/leads/", lead_payload),
        expected=(302,),
    )
    check(
        "public_contact_uses_lead_copy",
        public.get("/contact?sent=1"),
        contains="Smoke success message from the database.",
    )

    check("cloud_settings_account_page", cloud.get("/cloud/settings/account/"), expected=(200,))
    account_payload = settings_payload("account")
    account_payload["setting_account_login_trust_note"] = "Smoke sign-in note."
    check(
        "cloud_settings_account_save",
        cloud.post("/cloud/settings/account/", account_payload),
        expected=(302,),
    )
    expect(
        "cloud_settings_account_persisted",
        PlatformSetting.objects.get(key="account_login_trust_note").value
        == "Smoke sign-in note.",
    )
    check(
        "login_uses_account_copy",
        public.get("/login"),
        contains="Smoke sign-in note.",
    )

    # ------------------------------------------------------------------
    # Support impersonation and return
    # ------------------------------------------------------------------
    check(
        "cloud_impersonate",
        cloud.post(
            f"/cloud/tenants/{cloud_tenant.id}/impersonate/",
            {"reason": "Smoke support check."},
        ),
        expected=(302,),
    )
    log = (
        ImpersonationLog.objects.filter(tenant=cloud_tenant, ended_at__isnull=True)
        .order_by("-id")
        .first()
    )
    expect("impersonation_logged", log is not None)
    check(
        "impersonation_banner",
        cloud.get("/projects/dashboard"),
        contains="Support mode",
    )
    check("impersonation_cloud_blocked", cloud.get("/cloud/"), expected=(403,))
    check("cloud_return", cloud.post("/cloud/return/"), expected=(302,))
    if log:
        log.refresh_from_db()
    expect(
        "impersonation_closed",
        log is not None and log.ended_at is not None,
    )
    check("returned_to_cloud", cloud.get("/cloud/"), contains="Platform overview")

    transaction.set_rollback(True)

failures = [row for row in results if not row[2]]
for label, status, ok in results:
    print(f"{'OK ' if ok else 'FAIL'} {str(status):>3}  {label}")
print(
    f"\n{len(results) - len(failures)}/{len(results)} SaaS checks passed; "
    "all changes rolled back."
)
if failures:
    sys.exit(1)
