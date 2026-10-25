"""Seed default settings: email templates, SSO providers and integration keys."""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import CompanySetting, EmailTemplate, SsoSetting

TEMPLATES = [
    {
        "template_key": "quote_sent",
        "name": "Quote sent to client",
        "subject": "Quotation {{ quote_number }} from {{ company_name }}",
        "variables": "quote_number, client_name, company_name, total_amount, valid_until",
        "body": (
            "Dear {{ client_name }},\n\n"
            "Please find attached quotation {{ quote_number }} for {{ total_amount }}.\n"
            "This quotation is valid until {{ valid_until }}.\n\n"
            "Kind regards,\n{{ company_name }}"
        ),
    },
    {
        "template_key": "invoice_sent",
        "name": "Invoice sent to client",
        "subject": "Invoice {{ invoice_number }} from {{ company_name }}",
        "variables": "invoice_number, client_name, company_name, total_amount, due_date",
        "body": (
            "Dear {{ client_name }},\n\n"
            "Please find attached invoice {{ invoice_number }} for {{ total_amount }}, "
            "due on {{ due_date }}.\n\n"
            "Kind regards,\n{{ company_name }}"
        ),
    },
    {
        "template_key": "payment_receipt",
        "name": "Payment receipt",
        "subject": "Payment received - {{ invoice_number }}",
        "variables": "invoice_number, client_name, company_name, amount_paid, payment_date",
        "body": (
            "Dear {{ client_name }},\n\n"
            "We confirm receipt of {{ amount_paid }} on {{ payment_date }} "
            "towards invoice {{ invoice_number }}.\n\n"
            "Thank you,\n{{ company_name }}"
        ),
    },
    {
        "template_key": "leave_decision",
        "name": "Leave request decision",
        "subject": "Your leave request has been {{ status }}",
        "variables": "employee_name, leave_type, start_date, end_date, status, comment",
        "body": (
            "Dear {{ employee_name }},\n\n"
            "Your {{ leave_type }} request from {{ start_date }} to {{ end_date }} "
            "has been {{ status }}.\n"
            "Comment: {{ comment }}\n\n"
            "Human Resources"
        ),
    },
    {
        "template_key": "welcome_user",
        "name": "Welcome new user",
        "subject": "Welcome to {{ company_name }}",
        "variables": "first_name, company_name, login_url, email",
        "body": (
            "Hi {{ first_name }},\n\n"
            "An account has been created for you on {{ company_name }} ERP.\n"
            "Sign in at {{ login_url }} using {{ email }}.\n\n"
            "Kind regards,\n{{ company_name }}"
        ),
    },
]

SETTINGS_DEFAULTS = {
    "app_version": "1.0.0",
    "invoice_due_days": "30",
    "quote_valid_days": "30",
    "etims_enabled": "false",
    "etims_branch_code": "00",
    "etims_auto_submit": "true",
    "etims_timeout": "8",
    "pos_receipt_footer": "Thank you for shopping with us.",
    "pos_max_discount_percent": "100",
    "pos_refund_window_days": "0",
    "openai_enabled": "false",
    "openai_model": "gpt-4o-mini",
    "zoho_presence_enabled": "false",
    "mail_enabled": "false",
    "mail_encryption": "tls",
    "mail_port": "587",
}

SSO_PROVIDERS = ("google", "microsoft", "okta")


class Command(BaseCommand):
    help = "Seed email templates, SSO provider rows and integration defaults."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Overwrite existing email template bodies and subjects.",
        )

    def handle(self, *args, **options):
        now = timezone.now()

        created_settings = 0
        for key, value in SETTINGS_DEFAULTS.items():
            _, created = CompanySetting.objects.get_or_create(
                tenant=None, setting_key=key, defaults={"setting_value": value}
            )
            created_settings += int(created)
        self.stdout.write(f"Company settings: {created_settings} added.")

        created_templates = 0
        updated_templates = 0
        for spec in TEMPLATES:
            template, created = EmailTemplate.objects.get_or_create(
                template_key=spec["template_key"],
                defaults={
                    "name": spec["name"],
                    "subject": spec["subject"],
                    "body": spec["body"],
                    "variables": spec["variables"],
                    "status": "active",
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if created:
                created_templates += 1
            elif options["force"]:
                template.name = spec["name"]
                template.subject = spec["subject"]
                template.body = spec["body"]
                template.variables = spec["variables"]
                template.updated_at = now
                template.save()
                updated_templates += 1
        self.stdout.write(
            f"Email templates: {created_templates} added, {updated_templates} refreshed."
        )

        created_providers = 0
        for provider in SSO_PROVIDERS:
            _, created = SsoSetting.objects.get_or_create(
                provider=provider,
                defaults={"enabled": 0, "redirect_uri": f"/auth/{provider}/callback"},
            )
            created_providers += int(created)
        self.stdout.write(f"SSO providers: {created_providers} added.")

        self.stdout.write(self.style.SUCCESS("Settings seed complete."))
