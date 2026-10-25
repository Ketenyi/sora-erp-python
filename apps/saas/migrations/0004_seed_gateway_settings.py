"""Seed the payment-gateway settings introduced with online billing."""

from django.db import migrations

NEW_SETTINGS = [
    (
        "stripe_enabled",
        "false",
        "bool",
        "payments",
        "Enable Stripe card checkout",
        "Requires the publishable and secret keys below.",
        75,
    ),
    (
        "mpesa_enabled",
        "false",
        "bool",
        "payments",
        "Enable M-Pesa STK push",
        "Requires the consumer key, secret, shortcode and passkey below.",
        105,
    ),
    (
        "mpesa_environment",
        "sandbox",
        "text",
        "payments",
        "M-Pesa environment",
        "Use sandbox or production.",
        145,
    ),
    (
        "mpesa_callback_url",
        "",
        "text",
        "payments",
        "M-Pesa callback URL",
        "Public HTTPS callback; defaults to this deployment.",
        150,
    ),
    (
        "mpesa_callback_token",
        "",
        "secret",
        "payments",
        "M-Pesa callback token",
        "Shared secret required on every callback request.",
        155,
    ),
]


def seed_settings(apps, schema_editor):
    PlatformSetting = apps.get_model("saas", "PlatformSetting")
    for key, value, value_type, group, label, help_text, sort_order in NEW_SETTINGS:
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


class Migration(migrations.Migration):
    dependencies = [
        ("saas", "0003_tenantpayment_gateway_checkout_id_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_settings, migrations.RunPython.noop),
    ]
