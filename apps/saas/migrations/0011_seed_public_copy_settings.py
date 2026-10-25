"""Seed the editable copy used by the public sales website."""

from django.db import migrations

from apps.saas.catalog import PLATFORM_SETTING_SEED


def seed_public_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if group != "site" or sort_order < 320:
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


def unseed_public_copy(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(group="site", sort_order__gte=320).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0010_seed_account_copy_settings"),
    ]

    operations = [
        migrations.RunPython(seed_public_copy, unseed_public_copy),
    ]
