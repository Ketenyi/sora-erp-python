"""Forms for the Settings module."""

from __future__ import annotations

import re

from django import forms
from django.utils import timezone

from apps.core.access_catalog import MODULE_CATALOG, submodule_choices
from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.models import (
    EmailIdentity,
    EmailTemplate,
    ModuleAccessGrant,
    Permission,
    Project,
    Role,
    RolePermission,
    User,
    UserRole,
)
from apps.core.services import save_company_settings
from apps.core.tenancy import ALL_TENANTS, current_tenant
from apps.pos.models import PosPaymentMethod


class SettingsFormMixin:
    _existing: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            if isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.SelectMultiple)):
                field.widget.attrs.setdefault("class", "form-control")
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()

    def _secret_or_stored(self, field_name: str) -> str:
        return self.cleaned_data.get(field_name) or self._existing.get(field_name, "")


class CompanySettingsForm(SettingsFormMixin, forms.Form):
    company_name = forms.CharField(max_length=255, label="Company name")
    company_email = forms.EmailField(required=False, label="Company email")
    company_phone = forms.CharField(max_length=50, required=False, label="Phone")
    company_website = forms.CharField(max_length=255, required=False, label="Website")
    company_address = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Address"
    )
    company_pin = forms.CharField(max_length=50, required=False, label="KRA PIN")
    primary_color = forms.CharField(
        max_length=20,
        required=False,
        label="Brand accent colour",
        initial="#FFD700",
        widget=forms.TextInput(attrs={"type": "color"}),
    )
    currency_symbol = forms.CharField(max_length=10, initial="KSh", label="Currency symbol")
    vat_rate = forms.DecimalField(
        max_digits=5, decimal_places=2, initial=16, label="VAT rate (%)"
    )
    invoice_due_days = forms.IntegerField(min_value=0, initial=30, label="Invoice due days")
    quote_valid_days = forms.IntegerField(min_value=0, initial=30, label="Quote valid days")
    default_inclusions = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Default quote inclusions"
    )
    default_exclusions = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Default quote exclusions"
    )
    default_assumptions = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Default assumptions"
    )
    default_financial_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Default financial notes"
    )

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        initial = {
            "company_name": settings.get("company_name", ""),
            "company_email": settings.get("company_email", ""),
            "company_phone": settings.get("company_phone", ""),
            "company_website": settings.get("company_website", ""),
            "company_address": settings.get("company_address", ""),
            "company_pin": settings.get("company_pin", ""),
            "primary_color": (
                getattr(current_tenant(), "primary_color", "") or "#FFD700"
            ),
            "currency_symbol": settings.get("currency_symbol", "KSh"),
            "vat_rate": settings.get("vat_rate") or settings.get("tax_rate") or "16",
            "invoice_due_days": settings.get("invoice_due_days", "30"),
            "quote_valid_days": settings.get("quote_valid_days", "30"),
            "default_inclusions": settings.get("default_inclusions", ""),
            "default_exclusions": settings.get("default_exclusions", ""),
            "default_assumptions": settings.get("default_assumptions", ""),
            "default_financial_notes": settings.get("default_financial_notes", ""),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def clean_primary_color(self):
        value = (self.cleaned_data.get("primary_color") or "").strip()
        if value and not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise forms.ValidationError("Use a six-digit hex colour such as #FFD700.")
        return value or "#FFD700"

    def save(self):
        values = self.cleaned_data
        mapping = {
            "company_name": values["company_name"],
            "company_email": values.get("company_email") or "",
            "company_phone": values.get("company_phone") or "",
            "company_website": values.get("company_website") or "",
            "company_address": values.get("company_address") or "",
            "company_pin": values.get("company_pin") or "",
            "currency_symbol": values["currency_symbol"],
            "vat_rate": str(values["vat_rate"]),
            "tax_rate": str(values["vat_rate"]),
            "invoice_due_days": str(values["invoice_due_days"]),
            "quote_valid_days": str(values["quote_valid_days"]),
            "default_inclusions": values.get("default_inclusions") or "",
            "default_exclusions": values.get("default_exclusions") or "",
            "default_assumptions": values.get("default_assumptions") or "",
            "default_financial_notes": values.get("default_financial_notes") or "",
        }
        save_company_settings(mapping)

        tenant = current_tenant()
        if tenant is not None and tenant is not ALL_TENANTS:
            previous_name = tenant.company_name
            tenant.company_name = values["company_name"]
            if not tenant.brand_name or tenant.brand_name == previous_name:
                tenant.brand_name = values["company_name"]
            tenant.phone_number = values.get("company_phone") or ""
            tenant.primary_color = values["primary_color"]
            tenant.save(
                update_fields=[
                    "company_name",
                    "brand_name",
                    "phone_number",
                    "primary_color",
                    "updated_at",
                ]
            )


class EmailSettingsForm(SettingsFormMixin, forms.Form):
    mail_enabled = forms.BooleanField(required=False, label="Enable outgoing email")
    mail_host = forms.CharField(max_length=255, required=False, label="SMTP host")
    mail_port = forms.IntegerField(min_value=1, max_value=65535, required=False, initial=587)
    mail_username = forms.CharField(max_length=255, required=False, label="SMTP username")
    mail_password = forms.CharField(
        max_length=255,
        required=False,
        label="SMTP password",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored password.",
    )
    mail_encryption = forms.ChoiceField(
        choices=[("tls", "TLS"), ("ssl", "SSL"), ("none", "None")],
        required=False,
        initial="tls",
    )
    mail_from_address = forms.EmailField(required=False, label="From address")
    mail_from_name = forms.CharField(max_length=255, required=False, label="From name")

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        self._existing = settings
        initial = {
            "mail_enabled": settings.get("mail_enabled") == "true",
            "mail_host": settings.get("mail_host", "smtp.gmail.com"),
            "mail_port": settings.get("mail_port", "587"),
            "mail_username": settings.get("mail_username", ""),
            "mail_password": "",
            "mail_encryption": settings.get("mail_encryption", "tls"),
            "mail_from_address": settings.get("mail_from_address", ""),
            "mail_from_name": settings.get("mail_from_name", "SORA ERP"),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def clean_mail_password(self):
        return self._secret_or_stored("mail_password")

    def save(self):
        values = self.cleaned_data
        mapping = {
            "mail_enabled": "true" if values.get("mail_enabled") else "false",
            "mail_host": values.get("mail_host") or "",
            "mail_port": str(values.get("mail_port") or ""),
            "mail_username": values.get("mail_username") or "",
            "mail_password": encrypt_secret(values.get("mail_password") or ""),
            "mail_encryption": values.get("mail_encryption") or "tls",
            "mail_from_address": values.get("mail_from_address") or "",
            "mail_from_name": values.get("mail_from_name") or "",
        }
        save_company_settings(mapping)

    def smtp_config(self) -> dict:
        values = self.cleaned_data
        return {
            "host": values.get("mail_host") or "",
            "port": values.get("mail_port") or 587,
            "username": values.get("mail_username") or "",
            "password": decrypt_secret(values.get("mail_password") or ""),
            "use_tls": values.get("mail_encryption") == "tls",
            "use_ssl": values.get("mail_encryption") == "ssl",
            "from_address": values.get("mail_from_address") or "noreply@sora.com",
            "from_name": values.get("mail_from_name") or "SORA ERP",
        }


class UserForm(SettingsFormMixin, forms.ModelForm):
    password = forms.CharField(
        required=False,
        label="Password",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the current password when editing.",
    )
    system_roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.order_by("display_name"),
        required=False,
        label="Roles",
        widget=forms.CheckboxSelectMultiple,
        help_text="Roles drive module permissions. The primary role above is the legacy access level.",
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "role", "status"]
        widgets = {"role": forms.Select(), "status": forms.Select()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["system_roles"].initial = list(
                UserRole.objects.filter(user_id=self.instance.pk).values_list(
                    "role_id", flat=True
                )
            )
        else:
            self.fields["password"].required = True

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if password and len(password) < 6:
            raise forms.ValidationError("Use at least 6 characters.")
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        roles = self.cleaned_data.get("system_roles")
        if roles is not None:
            UserRole.objects.filter(user_id=user.id).delete()
            for role in roles:
                UserRole.objects.get_or_create(user_id=user.id, role_id=role.id)
        return user


class RoleForm(SettingsFormMixin, forms.ModelForm):
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.order_by("module", "action"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Permissions",
    )

    class Meta:
        model = Role
        fields = ["name", "display_name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "name": "Lowercase identifier such as sales_manager. It cannot be changed later.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["permissions"].initial = list(
                RolePermission.objects.filter(role_id=self.instance.pk).values_list(
                    "permission_id", flat=True
                )
            )
            self.fields["name"].disabled = True

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip().lower()
        if not re.match(r"^[a-z][a-z0-9_]*$", name):
            raise forms.ValidationError("Use lowercase letters, digits and underscores, starting with a letter.")
        return name

    def save(self, commit=True):
        role = super().save(commit=False)
        now = timezone.now()
        if not role.pk:
            role.created_at = now
        role.updated_at = now
        if commit:
            role.save()
        if self.cleaned_data.get("permissions") is not None:
            RolePermission.objects.filter(role_id=role.id).delete()
            for permission in self.cleaned_data["permissions"]:
                RolePermission.objects.get_or_create(
                    role_id=role.id, permission_id=permission.id
                )
        return role


class EmailTemplateForm(SettingsFormMixin, forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ["subject", "body", "status"]
        widgets = {
            "subject": forms.TextInput(),
            "body": forms.Textarea(attrs={"rows": 14, "class": "form-control mono"}),
        }

    def save(self, commit=True):
        template = super().save(commit=False)
        template.updated_at = timezone.now()
        if commit:
            template.save()
        return template


class EtimsForm(SettingsFormMixin, forms.Form):
    etims_enabled = forms.BooleanField(required=False, label="Enable eTIMS")
    etims_api_url = forms.CharField(
        required=False,
        label="API URL",
        help_text="KRA OSCU base URL, for example https://etims-api-sbx.kra.go.ke.",
    )
    etims_tin = forms.CharField(required=False, label="TIN", help_text="Taxpayer PIN registered with KRA.")
    etims_branch_code = forms.CharField(
        required=False,
        label="Branch code",
        initial="00",
        help_text="Two-digit branch id from your eTIMS onboarding.",
    )
    etims_device_serial = forms.CharField(
        required=False,
        label="Device serial",
        help_text="Device serial number (dvcSrlNo) issued for this terminal.",
    )
    etims_api_key = forms.CharField(required=False, label="API username")
    etims_api_secret = forms.CharField(
        required=False,
        label="API password",
        widget=forms.PasswordInput(render_value=False),
        help_text="Stored encrypted. Leave blank to keep the saved password.",
    )
    etims_auto_submit = forms.BooleanField(
        required=False,
        label="Transmit receipts immediately after checkout",
        help_text="Failed receipts stay queued for the retry worker.",
    )
    etims_timeout = forms.IntegerField(
        required=False,
        min_value=2,
        max_value=30,
        initial=8,
        label="Request timeout (seconds)",
    )

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        self._existing = settings
        initial = {
            "etims_enabled": settings.get("etims_enabled") == "true",
            "etims_api_url": settings.get("etims_api_url", ""),
            "etims_tin": settings.get("etims_tin", ""),
            "etims_branch_code": settings.get("etims_branch_code", "00"),
            "etims_device_serial": settings.get("etims_device_serial", ""),
            "etims_api_key": settings.get("etims_api_key", ""),
            "etims_api_secret": "",
            "etims_auto_submit": settings.get("etims_auto_submit", "true").lower()
            in {"1", "true", "yes", "on"},
            "etims_timeout": int(settings.get("etims_timeout", 8) or 8),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def clean_etims_api_url(self):
        value = (self.cleaned_data.get("etims_api_url") or "").strip().rstrip("/")
        if value and not value.startswith(("https://", "http://")):
            raise forms.ValidationError("Enter a full http(s) URL.")
        return value

    def clean_etims_api_secret(self):
        return self._secret_or_stored("etims_api_secret")

    def save(self):
        values = self.cleaned_data
        mapping = {
            "etims_enabled": "true" if values.get("etims_enabled") else "false",
            "etims_api_url": values.get("etims_api_url") or "",
            "etims_tin": values.get("etims_tin") or "",
            "etims_branch_code": values.get("etims_branch_code") or "00",
            "etims_device_serial": values.get("etims_device_serial") or "",
            "etims_api_key": values.get("etims_api_key") or "",
            "etims_api_secret": encrypt_secret(values.get("etims_api_secret") or ""),
            "etims_auto_submit": "true" if values.get("etims_auto_submit") else "false",
            "etims_timeout": str(values.get("etims_timeout") or 8),
        }
        save_company_settings(mapping)


class PosSettingsForm(SettingsFormMixin, forms.Form):
    pos_receipt_footer = forms.CharField(
        required=False,
        label="Receipt footer",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Printed at the bottom of every receipt and credit note.",
    )
    pos_max_discount_percent = forms.DecimalField(
        required=False,
        min_value=0,
        max_value=100,
        max_digits=5,
        decimal_places=2,
        initial=100,
        label="Maximum discount (%)",
        help_text="Applied to the goods value before tax. Set 0 to block discounts.",
    )
    pos_refund_window_days = forms.IntegerField(
        required=False,
        min_value=0,
        max_value=3650,
        initial=0,
        label="Refund window (days)",
        help_text="0 allows refunds at any time after the sale.",
    )

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        initial = {
            "pos_receipt_footer": settings.get("pos_receipt_footer", ""),
            "pos_max_discount_percent": settings.get("pos_max_discount_percent", "100"),
            "pos_refund_window_days": settings.get("pos_refund_window_days", "0"),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def save(self):
        values = self.cleaned_data
        save_company_settings(
            {
                "pos_receipt_footer": values.get("pos_receipt_footer") or "",
                "pos_max_discount_percent": str(
                    values.get("pos_max_discount_percent")
                    if values.get("pos_max_discount_percent") is not None
                    else 100
                ),
                "pos_refund_window_days": str(
                    values.get("pos_refund_window_days")
                    if values.get("pos_refund_window_days") is not None
                    else 0
                ),
            }
        )


class AgentPolicyForm(SettingsFormMixin, forms.Form):
    agent_heartbeat_seconds = forms.IntegerField(
        min_value=15,
        max_value=3600,
        initial=60,
        label="Heartbeat interval (seconds)",
        help_text="How often enrolled endpoints check in. 15–3600 seconds.",
    )
    agent_inventory_minutes = forms.IntegerField(
        min_value=1,
        max_value=1440,
        initial=30,
        label="Inventory interval (minutes)",
        help_text="Full hardware and software inventory cadence. 1–1440 minutes.",
    )
    agent_token_ttl_hours = forms.IntegerField(
        min_value=1,
        max_value=720,
        initial=24,
        label="Access token lifetime (hours)",
        help_text="Hardware-bound JWT lifetime. Agents refresh before it expires.",
    )
    agent_snapshot_retention = forms.IntegerField(
        min_value=5,
        max_value=365,
        initial=60,
        label="Snapshots kept per endpoint",
        help_text="Older inventory snapshots are pruned automatically.",
    )
    agent_software_limit = forms.IntegerField(
        min_value=0,
        max_value=5000,
        initial=2000,
        label="Software titles per report",
        help_text="0 disables software inventory. Up to 5000 titles are accepted.",
    )

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        initial = {
            "agent_heartbeat_seconds": settings.get("agent_heartbeat_seconds", "60"),
            "agent_inventory_minutes": settings.get("agent_inventory_minutes", "30"),
            "agent_token_ttl_hours": settings.get("agent_token_ttl_hours", "24"),
            "agent_snapshot_retention": settings.get("agent_snapshot_retention", "60"),
            "agent_software_limit": settings.get("agent_software_limit", "2000"),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def save(self):
        values = self.cleaned_data
        save_company_settings(
            {
                "agent_heartbeat_seconds": str(values["agent_heartbeat_seconds"]),
                "agent_inventory_minutes": str(values["agent_inventory_minutes"]),
                "agent_token_ttl_hours": str(values["agent_token_ttl_hours"]),
                "agent_snapshot_retention": str(values["agent_snapshot_retention"]),
                "agent_software_limit": str(values["agent_software_limit"]),
            }
        )


class PosPaymentMethodForm(SettingsFormMixin, forms.ModelForm):
    class Meta:
        model = PosPaymentMethod
        fields = [
            "code",
            "label",
            "etims_code",
            "is_cash",
            "is_credit",
            "requires_reference",
            "enabled",
            "sort_order",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "e.g. airtel-money"}),
            "label": forms.TextInput(attrs={"placeholder": "e.g. Airtel Money"}),
            "etims_code": forms.TextInput(attrs={"placeholder": "e.g. 06", "maxlength": "4"}),
        }

    def __init__(self, *args, **kwargs):
        self.is_new = kwargs.pop("is_new", False)
        super().__init__(*args, **kwargs)
        if not self.is_new and self.instance.pk:
            self.fields["code"].disabled = True

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", code):
            raise forms.ValidationError(
                "Use lowercase letters, numbers, hyphens or underscores (2-32 characters)."
            )
        return code

    def clean_etims_code(self):
        return (self.cleaned_data.get("etims_code") or "").strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_cash") and cleaned.get("is_credit"):
            self.add_error("is_credit", "A tender cannot be both cash and on-account.")
        return cleaned



class OpenAiForm(SettingsFormMixin, forms.Form):
    openai_enabled = forms.BooleanField(required=False, label="Enable AI assistant")
    openai_api_key = forms.CharField(
        required=False,
        label="API key",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored key.",
    )
    openai_model = forms.CharField(required=False, initial="gpt-4o-mini", label="Model")

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        self._existing = settings
        initial = {
            "openai_enabled": settings.get("openai_enabled") == "true",
            "openai_api_key": "",
            "openai_model": settings.get("openai_model", "gpt-4o-mini"),
        }
        super().__init__(*args, initial=initial, **kwargs)

    def clean_openai_api_key(self):
        return self._secret_or_stored("openai_api_key")

    def save(self):
        values = self.cleaned_data
        mapping = {
            "openai_enabled": "true" if values.get("openai_enabled") else "false",
            "openai_api_key": values.get("openai_api_key") or "",
            "openai_model": values.get("openai_model") or "gpt-4o-mini",
        }
        save_company_settings(mapping)


class ZohoForm(SettingsFormMixin, forms.Form):
    zoho_presence_enabled = forms.BooleanField(required=False, label="Enable Zoho presence sync")
    zoho_client_id = forms.CharField(required=False, label="Client ID")
    zoho_client_secret = forms.CharField(
        required=False,
        label="Client secret",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored secret.",
    )
    zoho_refresh_token = forms.CharField(
        required=False,
        label="Refresh token",
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the stored token.",
    )

    def __init__(self, *args, initial_settings=None, **kwargs):
        settings = initial_settings or {}
        self._existing = settings
        initial = {
            "zoho_presence_enabled": settings.get("zoho_presence_enabled") == "true",
            "zoho_client_id": settings.get("zoho_client_id", ""),
            "zoho_client_secret": "",
            "zoho_refresh_token": "",
        }
        super().__init__(*args, initial=initial, **kwargs)

    def clean_zoho_client_secret(self):
        return self._secret_or_stored("zoho_client_secret")

    def clean_zoho_refresh_token(self):
        return self._secret_or_stored("zoho_refresh_token")

    def save(self):
        values = self.cleaned_data
        mapping = {
            "zoho_presence_enabled": "true" if values.get("zoho_presence_enabled") else "false",
            "zoho_client_id": values.get("zoho_client_id") or "",
            "zoho_client_secret": values.get("zoho_client_secret") or "",
            "zoho_refresh_token": values.get("zoho_refresh_token") or "",
        }
        save_company_settings(mapping)


class EmailIdentityForm(SettingsFormMixin, forms.ModelForm):
    class Meta:
        model = EmailIdentity
        fields = [
            "name",
            "module",
            "from_name",
            "from_email",
            "reply_to",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "smtp_encryption",
            "imap_host",
            "imap_port",
            "imap_username",
            "imap_password",
            "imap_encryption",
            "imap_folder",
            "signature",
            "is_default",
            "is_active",
        ]
        widgets = {
            "smtp_password": forms.PasswordInput(render_value=False),
            "imap_password": forms.PasswordInput(render_value=False),
            "signature": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "name": "Identity name",
            "module": "Module",
            "from_name": "Display name",
            "from_email": "From address",
            "reply_to": "Reply-to address",
            "smtp_host": "SMTP host",
            "smtp_port": "SMTP port",
            "smtp_username": "SMTP username",
            "smtp_password": "SMTP password",
            "smtp_encryption": "SMTP security",
            "imap_host": "IMAP host",
            "imap_port": "IMAP port",
            "imap_username": "IMAP username",
            "imap_password": "IMAP password",
            "imap_encryption": "IMAP security",
            "imap_folder": "Mailbox folder",
            "signature": "Signature",
            "is_default": "Primary sender for this module",
            "is_active": "Active",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["smtp_password"].help_text = "Leave blank to keep the stored password."
        self.fields["imap_password"].help_text = "Leave blank to keep the stored password."
        self.fields["reply_to"].required = False
        self.fields["smtp_password"].required = False
        self.fields["imap_password"].required = False
        self.fields["signature"].required = False

    def clean_smtp_password(self):
        value = self.cleaned_data.get("smtp_password") or ""
        if not value and self.instance.pk:
            return self.instance.smtp_password
        return value

    def clean_imap_password(self):
        value = self.cleaned_data.get("imap_password") or ""
        if not value and self.instance.pk:
            return self.instance.imap_password
        return value

    def clean_from_email(self):
        return (self.cleaned_data.get("from_email") or "").strip().lower()

    def clean_reply_to(self):
        return (self.cleaned_data.get("reply_to") or "").strip().lower()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_active") and not (
            cleaned.get("smtp_host") or cleaned.get("imap_host")
        ):
            raise forms.ValidationError(
                "Add an SMTP host to send mail, an IMAP host to receive mail, or both."
            )
        return cleaned

    def save(self, commit=True):
        identity = super().save(commit=False)
        if identity.pk is None and not EmailIdentity.objects.filter(
            module=identity.module, is_default=True
        ).exists():
            identity.is_default = True
        if commit:
            identity.save()
            if identity.is_default:
                EmailIdentity.objects.filter(
                    module=identity.module, is_default=True
                ).exclude(pk=identity.pk).update(is_default=False)
        return identity


class ModuleAccessGrantForm(SettingsFormMixin, forms.ModelForm):
    module = forms.ChoiceField(label="Module")
    submodule = forms.ChoiceField(required=False, label="Submodule")

    class Meta:
        model = ModuleAccessGrant
        fields = ["user", "module", "submodule", "role", "scope", "project", "is_active"]
        labels = {
            "user": "User",
            "role": "Access level",
            "scope": "Scope",
            "project": "Project",
            "is_active": "Active",
        }

    def __init__(self, *args, granted_by=None, **kwargs):
        self.granted_by = granted_by
        super().__init__(*args, **kwargs)
        self.fields["module"].choices = [
            (key, row["label"]) for key, row in MODULE_CATALOG.items()
        ]
        module = (
            self.data.get("module")
            if self.is_bound
            else (self.instance.module or "projects")
        )
        self.fields["submodule"].choices = [("", "Entire module")] + submodule_choices(
            module
        )
        self.fields["user"].queryset = User.objects.filter(status="active").order_by(
            "first_name", "last_name", "email"
        )
        self.fields["project"].queryset = Project.objects.order_by("project_name")
        self.fields["project"].required = False
        self.fields["submodule"].help_text = (
            "Leave blank to give access to every part of the module."
        )
        self.fields["scope"].help_text = (
            "Project and assigned scopes only narrow project lists for the Projects module."
        )
        if not self.instance.pk:
            self.fields["is_active"].initial = True
            if not self.is_bound:
                self.initial.setdefault("module", "projects")

    def clean(self):
        cleaned = super().clean()
        module = cleaned.get("module") or ""
        submodule = cleaned.get("submodule") or ""
        scope = cleaned.get("scope") or "tenant"
        project = cleaned.get("project")

        valid_submodules = {code for code, _label in submodule_choices(module)}
        if submodule and submodule not in valid_submodules:
            self.add_error("submodule", "Choose a submodule that belongs to this module.")

        if scope == "project" and project is None:
            self.add_error("project", "Choose the project this grant applies to.")
        if scope != "project":
            cleaned["project"] = None
            project = None

        if cleaned.get("user") and not self.errors:
            duplicates = ModuleAccessGrant.objects.filter(
                user=cleaned["user"],
                module=module,
                submodule=submodule,
                scope=scope,
                project=project,
                is_active=True,
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise forms.ValidationError(
                    "An identical grant already exists for this user."
                )
        return cleaned

    def save(self, commit=True):
        grant = super().save(commit=False)
        if self.granted_by is not None and not grant.granted_by_id:
            grant.granted_by = self.granted_by
        if commit:
            grant.save()
        return grant
