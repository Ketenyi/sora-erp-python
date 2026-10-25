"""Seed the editable copy for sign-in, recovery and security screens."""

from django.db import migrations

from apps.saas.catalog import PLATFORM_SETTING_SEED


def seed_account_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if group != "account":
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


def unseed_account_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(group="account").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0009_seed_login_copy_settings"),
    ]

    operations = [
        migrations.RunPython(seed_account_copy, unseed_account_copy),
    ]
