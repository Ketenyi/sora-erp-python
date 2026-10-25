"""Seed the editable copy used by sign-in and password recovery screens."""

from django.db import migrations

from apps.saas.catalog import PLATFORM_SETTING_SEED

KEYS = {
    "site_login_kicker",
    "site_login_title",
    "site_login_lead",
    "site_login_points",
    "site_login_security_note",
    "site_login_support_note",
    "site_reset_kicker",
    "site_reset_title",
    "site_reset_lead",
    "site_reset_success",
    "site_reset_confirm_title",
    "site_reset_confirm_lead",
    "site_reset_invalid",
    "site_mfa_kicker",
    "site_mfa_title",
    "site_mfa_lead",
    "site_mfa_help",
}


def seed_login_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if key not in KEYS:
            continue
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


def unseed_login_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(key__in=KEYS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0008_seed_security_settings"),
    ]

    operations = [
        migrations.RunPython(seed_login_copy, unseed_login_copy),
    ]
