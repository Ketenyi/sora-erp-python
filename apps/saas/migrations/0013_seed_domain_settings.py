"""Seed the editable settings that drive custom tenant domains."""

from django.db import migrations

DOMAIN_KEYS = {
    "account_login_wrong_workspace",
    "platform_domain",
    "domain_target",
    "domain_max_per_tenant",
    "domain_verify_cooldown",
    "domain_reverify_hours",
    "domain_failure_threshold",
}


def seed_domain_settings(apps, schema_editor):
    from apps.saas.catalog import PLATFORM_SETTING_SEED

    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if key not in DOMAIN_KEYS:
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


def unseed_domain_settings(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(key__in=DOMAIN_KEYS).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0012_tenant_domains"),
    ]

    operations = [
        migrations.RunPython(seed_domain_settings, unseed_domain_settings),
    ]
