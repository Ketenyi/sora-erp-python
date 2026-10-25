"""Expand the editable public-site and enquiry copy in SORA Cloud."""

from django.db import migrations

from apps.saas.catalog import PLATFORM_SETTING_SEED

NEW_KEYS = {
    "site_brand_subtitle",
    "site_hero_title_accent",
    "site_hero_cta",
    "site_hero_cta_secondary",
    "site_pricing_page_kicker",
    "site_pricing_page_title",
    "site_pricing_page_lead",
    "site_pricing_badge",
    "site_pricing_calc_kicker",
    "site_pricing_calc_title",
    "site_pricing_calc_lead",
    "site_pricing_calc_cta",
    "lead_aside_eyebrow",
    "lead_aside_points",
    "lead_success_title",
    "lead_form_heading",
    "lead_form_hint",
    "lead_form_button",
}

OLD_HERO_TITLE = "One platform. Every part of your business."


def expand_settings(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in PLATFORM_SETTING_SEED:
        if key not in NEW_KEYS:
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
    PlatformSetting.objects.filter(key="site_hero_title", value=OLD_HERO_TITLE).update(
        value="One platform."
    )


def collapse_settings(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    PlatformSetting.objects.filter(key__in=NEW_KEYS).delete()
    PlatformSetting.objects.filter(key="site_hero_title", value="One platform.").update(
        value=OLD_HERO_TITLE
    )


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0006_seed_site_and_lead_settings"),
    ]

    operations = [
        migrations.RunPython(expand_settings, collapse_settings),
    ]
