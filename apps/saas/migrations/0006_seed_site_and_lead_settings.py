"""Seed the public website and sales-lead settings for SORA Cloud."""

from django.db import migrations

from apps.saas.catalog import PLATFORM_SETTING_SEED


def seed_site_and_leads(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if group not in {"site", "leads"}:
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


def unseed_site_and_leads(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(group__in={"site", "leads"}).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0005_saleslead_salesleadactivity"),
    ]

    operations = [
        migrations.RunPython(seed_site_and_leads, unseed_site_and_leads),
    ]
