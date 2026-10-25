"""Seed the SORA Cloud catalogue and backfill the existing workspace."""

from datetime import timedelta

from django.db import migrations
from django.utils import timezone

from apps.saas.catalog import MODULE_SEED, PLAN_SEED, PLATFORM_SETTING_SEED

ADMIN_ROLES = {"admin", "super_admin", "system_admin"}


def seed_control_plane(apps, schema_editor):
    SaasModule = apps.get_model("saas", "SaasModule")
    SaasPlan = apps.get_model("saas", "SaasPlan")
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    Tenant = apps.get_model("saas", "Tenant")
    TenantModule = apps.get_model("saas", "TenantModule")
    TenantMembership = apps.get_model("saas", "TenantMembership")
    TenantAuditLog = apps.get_model("saas", "TenantAuditLog")
    User = apps.get_model("core", "User")
    CompanySetting = apps.get_model("core", "CompanySetting")

    for item in MODULE_SEED:
        SaasModule.objects.get_or_create(
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

    for item in PLAN_SEED:
        SaasPlan.objects.get_or_create(
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

    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        PlatformSetting.objects.get_or_create(
            key=key,
            defaults={
                "value": value,
                "value_type": value_type,
                "group": group,
                "label": label,
                "help_text": help_text,
                "sort_order": sort_order,
            },
        )

    company_name = "SORA ERP Demo"
    company_logo = ""
    for row in CompanySetting.objects.filter(setting_key__in=["company_name", "company_logo"]):
        if row.setting_key == "company_name" and row.setting_value:
            company_name = row.setting_value
        elif row.setting_key == "company_logo" and row.setting_value:
            company_logo = row.setting_value

    users = list(User.objects.order_by("id"))
    primary_admin = next((u for u in users if (u.role or "") in ADMIN_ROLES), None)
    if primary_admin is None:
        primary_admin = next((u for u in users if (u.role or "") in {"hr_manager", "manager"}), None)

    if primary_admin is not None and not User.objects.filter(is_platform_admin=True).exists():
        User.objects.filter(pk=primary_admin.pk).update(is_platform_admin=True)

    admin_email = (
        getattr(primary_admin, "email", None)
        or (users[0].email if users else "admin@sora.demo")
    )
    now = timezone.now()
    tenant, created = Tenant.objects.get_or_create(
        slug="sora-demo",
        defaults={
            "company_name": company_name,
            "admin_email": admin_email,
            "brand_name": company_name,
            "logo_url": company_logo or "",
            "plan": SaasPlan.objects.filter(code="business").first(),
            "status": "active",
            "trial_starts_at": now,
            "trial_ends_at": now + timedelta(days=30),
            "subscription_starts_at": now,
            "subscription_ends_at": now + timedelta(days=3650),
            "max_users": max(len(users), 100),
            "is_demo": True,
            "notes": "Existing workspace backfilled by the SaaS foundation migration.",
        },
    )

    all_modules = list(SaasModule.objects.all())
    existing_module_ids = set(tenant.modules.values_list("module_id", flat=True))
    for module in all_modules:
        if module.id in existing_module_ids:
            continue
        TenantModule.objects.create(
            tenant=tenant,
            module=module,
            is_enabled=True,
            source="manual",
            note="Demo workspace licence",
        )

    for user in users:
        role = "member"
        if primary_admin is not None and user.id == primary_admin.id:
            role = "owner"
        elif (user.role or "") in ADMIN_ROLES:
            role = "admin"
        TenantMembership.objects.get_or_create(
            tenant=tenant,
            user=user,
            defaults={"role": role, "status": "active"},
        )

    if created:
        TenantAuditLog.objects.create(
            tenant=tenant,
            action="workspace.backfilled",
            details="Default demo workspace created while seeding SORA Cloud.",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("saas", "0001_initial"),
        ("core", "0003_user_is_platform_admin"),
    ]

    operations = [
        migrations.RunPython(seed_control_plane, migrations.RunPython.noop),
    ]
