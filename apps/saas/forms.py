"""Forms for the public portal, billing and SORA Cloud Control."""

from __future__ import annotations

import json
import re

from django import forms
from django.db.models import Q
from django.utils import timezone

from apps.saas.models import (
    ANNOUNCEMENT_AUDIENCES,
    ANNOUNCEMENT_LEVELS,
    PAYMENT_METHODS,
    SALES_LEAD_STATUSES,
    TENANT_STATUS_CHOICES,
    Announcement,
    PlatformSetting,
    SaasModule,
    SaasPlan,
    SalesLead,
    Tenant,
)


class SoraFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
                continue
            if isinstance(field.widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def module_choices(available_only: bool = True):
    queryset = SaasModule.objects.all().order_by("sort_order", "name")
    if available_only:
        queryset = queryset.filter(is_available=True)
    return [(module.code, module.brand_name or module.name) for module in queryset]


class SignupForm(SoraFormMixin, forms.Form):
    company_name = forms.CharField(max_length=255, label="Company name")
    first_name = forms.CharField(max_length=100, label="First name")
    last_name = forms.CharField(max_length=100, label="Last name")
    email = forms.EmailField(max_length=255, label="Work email")
    phone = forms.CharField(max_length=50, required=False, label="Phone number")
    password = forms.CharField(
        min_length=8,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Password",
    )
    confirm_password = forms.CharField(
        min_length=8,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        label="Confirm password",
    )
    modules = forms.MultipleChoiceField(
        choices=[],
        widget=forms.CheckboxSelectMultiple,
        label="Modules to try",
    )
    accept_terms = forms.BooleanField(
        required=True,
        label="I agree to the terms of service and privacy policy",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modules"].choices = module_choices()

    def clean_email(self):
        from apps.core.models import User

        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account with this email already exists. Sign in instead."
            )
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("confirm_password")
        if password and confirm and password != confirm:
            self.add_error("confirm_password", "Passwords do not match.")
        if not cleaned.get("modules"):
            self.add_error("modules", "Select at least one module to trial.")
        return cleaned


class DomainClaimForm(SoraFormMixin, forms.Form):
    hostname = forms.CharField(
        max_length=253,
        label="Domain name",
        widget=forms.TextInput(
            attrs={
                "placeholder": "erp.yourcompany.com",
                "autocomplete": "off",
                "spellcheck": "false",
                "autocapitalize": "none",
            }
        ),
    )

    def clean_hostname(self):
        from apps.saas.domains import DomainError, normalize_hostname

        try:
            return normalize_hostname(self.cleaned_data.get("hostname") or "")
        except DomainError as exc:
            raise forms.ValidationError(str(exc)) from exc


class TenantBrandMixin:
    """Validate tenant branding values before they reach CSS or <img> tags."""

    def clean_primary_color(self):
        value = (self.cleaned_data.get("primary_color") or "").strip()
        if not value:
            return "#FFD700"
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise forms.ValidationError("Use a six-digit hex colour such as #FFD700.")
        return value.upper()

    def clean_logo_url(self):
        value = (self.cleaned_data.get("logo_url") or "").strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "/static/")):
            return value
        tenant = getattr(self, "instance", None)
        if tenant and tenant.pk and value.startswith(f"/uploads/tenants/{tenant.pk}/"):
            return value
        raise forms.ValidationError(
            "Use a hosted https logo URL or upload a logo in Settings → Company."
        )


class TenantProfileForm(TenantBrandMixin, SoraFormMixin, forms.ModelForm):
    class Meta:
        model = Tenant
        fields = [
            "company_name",
            "brand_name",
            "phone_number",
            "logo_url",
            "primary_color",
            "currency",
            "timezone_name",
        ]
        labels = {
            "brand_name": "Display name",
            "logo_url": "Logo URL",
            "timezone_name": "Time zone",
        }
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
        }


class RenewalForm(SoraFormMixin, forms.Form):
    plan = forms.ModelChoiceField(queryset=SaasPlan.objects.none(), label="Plan")
    billing_cycle = forms.ChoiceField(
        choices=[("monthly", "Monthly"), ("yearly", "Yearly (2 months free)")],
        label="Billing cycle",
    )
    method = forms.ChoiceField(
        choices=[
            ("mpesa", "M-Pesa"),
            ("bank", "Bank transfer"),
            ("manual", "Cash / arranged with billing"),
        ],
        label="Payment method",
    )
    phone_number = forms.CharField(
        max_length=20,
        required=False,
        label="M-Pesa phone number",
        widget=forms.TextInput(attrs={"placeholder": "0712 345 678"}),
        help_text="Required for instant STK push payments.",
    )
    reference = forms.CharField(max_length=120, required=False, label="Payment reference")
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Anything we should know?",
    )

    def __init__(self, *args, tenant=None, stripe_enabled=False, mpesa_enabled=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.tenant = tenant
        self.stripe_enabled = stripe_enabled
        self.mpesa_enabled = mpesa_enabled
        choices = []
        if stripe_enabled:
            choices.append(("stripe", "Card via Stripe (pay now)"))
        if mpesa_enabled:
            choices.append(("mpesa_instant", "M-Pesa STK push (pay now)"))
        choices.extend(
            [
                ("mpesa", "M-Pesa (submit reference for verification)"),
                ("bank", "Bank transfer"),
                ("manual", "Cash / arranged with billing"),
            ]
        )
        self.fields["method"].choices = choices
        self.fields["plan"].queryset = SaasPlan.objects.filter(is_active=True).order_by(
            "sort_order", "monthly_price"
        )
        if tenant and tenant.plan_id:
            self.fields["plan"].initial = tenant.plan_id
        elif not self.is_bound:
            self.fields["plan"].initial = SaasPlan.objects.filter(is_active=True).first()

    def amount(self):
        plan = self.cleaned_data.get("plan")
        cycle = self.cleaned_data.get("billing_cycle", "monthly")
        if plan is None:
            return 0
        return plan.yearly_price if cycle == "yearly" else plan.monthly_price

    def clean(self):
        cleaned = super().clean()
        method = cleaned.get("method")
        reference = (cleaned.get("reference") or "").strip()
        phone = (cleaned.get("phone_number") or "").strip()
        if method == "stripe" and not self.stripe_enabled:
            self.add_error("method", "Card payments are not enabled for this platform.")
        if method == "mpesa_instant":
            if not self.mpesa_enabled:
                self.add_error("method", "M-Pesa STK push is not enabled for this platform.")
            else:
                from apps.saas.gateways import GatewayError, normalize_phone

                try:
                    cleaned["phone_number"] = normalize_phone(phone)
                except GatewayError as error:
                    self.add_error("phone_number", str(error))
        elif method in {"mpesa", "bank"} and not reference:
            self.add_error("reference", "Enter the transaction or transfer reference.")
        return cleaned


class CloudTenantCreateForm(SoraFormMixin, forms.Form):
    company_name = forms.CharField(max_length=255)
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    email = forms.EmailField(max_length=255)
    phone = forms.CharField(max_length=50, required=False)
    password = forms.CharField(
        required=False,
        min_length=8,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to generate a temporary password.",
    )
    plan = forms.ModelChoiceField(
        queryset=SaasPlan.objects.filter(is_active=True),
        required=False,
        empty_label="Trial (no plan yet)",
    )
    modules = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False
    )
    trial_days = forms.IntegerField(min_value=1, max_value=365, required=False, initial=30)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modules"].choices = module_choices()
        self.fields["modules"].initial = [code for code, _ in module_choices()]

    def clean_email(self):
        from apps.core.models import User

        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email already belongs to a login.")
        return email


class CloudTenantEditForm(TenantBrandMixin, SoraFormMixin, forms.ModelForm):
    class Meta:
        model = Tenant
        fields = [
            "company_name",
            "admin_email",
            "phone_number",
            "brand_name",
            "logo_url",
            "primary_color",
            "currency",
            "timezone_name",
            "max_users",
            "notes",
        ]
        labels = {
            "admin_email": "Billing contact email",
            "brand_name": "Display name",
            "logo_url": "Logo URL",
            "timezone_name": "Time zone",
            "max_users": "Seat limit (0 = unlimited)",
        }
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class ExtendTrialForm(SoraFormMixin, forms.Form):
    days = forms.IntegerField(min_value=1, max_value=365, initial=14, label="Extend by (days)")
    reason = forms.CharField(max_length=255, required=False, label="Reason")


class TenantStatusForm(SoraFormMixin, forms.Form):
    status = forms.ChoiceField(choices=TENANT_STATUS_CHOICES, label="New status")
    reason = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Reason / note",
    )


class CloudPaymentForm(SoraFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=12, decimal_places=2)
    method = forms.ChoiceField(choices=PAYMENT_METHODS)
    reference = forms.CharField(max_length=120, required=False)
    plan = forms.ModelChoiceField(
        queryset=SaasPlan.objects.filter(is_active=True), required=False
    )
    billing_cycle = forms.ChoiceField(
        choices=[("monthly", "Monthly"), ("yearly", "Yearly")], initial="monthly"
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant and not self.is_bound:
            self.fields["plan"].initial = tenant.plan_id
            self.fields["billing_cycle"].initial = tenant.billing_cycle
            if tenant.plan:
                self.fields["amount"].initial = (
                    tenant.plan.yearly_price
                    if tenant.billing_cycle == "yearly"
                    else tenant.plan.monthly_price
                )


class AnnouncementForm(SoraFormMixin, forms.ModelForm):
    class Meta:
        model = Announcement
        fields = [
            "title",
            "body",
            "level",
            "audience",
            "is_active",
            "is_dismissible",
            "send_email",
            "starts_at",
            "ends_at",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4}),
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["starts_at"].required = False
        self.fields["ends_at"].required = False
        self.fields["starts_at"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["ends_at"].input_formats = ["%Y-%m-%dT%H:%M"]

    def clean_starts_at(self):
        return self.cleaned_data.get("starts_at") or timezone.now()


class PlanForm(SoraFormMixin, forms.Form):
    name = forms.CharField(max_length=100)
    tagline = forms.CharField(max_length=200, required=False)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    monthly_price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    yearly_price = forms.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    max_users = forms.IntegerField(min_value=0, help_text="0 = unlimited")
    included_modules = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False
    )
    features = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="One feature per line.",
    )
    is_public = forms.BooleanField(required=False, initial=True)
    is_active = forms.BooleanField(required=False, initial=True)
    sort_order = forms.IntegerField(min_value=0, initial=0)

    def __init__(self, *args, plan=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = plan
        self.fields["included_modules"].choices = [
            (module.code, module.brand_name or module.name)
            for module in SaasModule.objects.all().order_by("sort_order", "name")
        ]
        if plan is not None and not self.is_bound:
            self.initial.update(
                {
                    "name": plan.name,
                    "tagline": plan.tagline,
                    "description": plan.description,
                    "monthly_price": plan.monthly_price,
                    "yearly_price": plan.yearly_price,
                    "max_users": plan.max_users,
                    "included_modules": plan.included_modules,
                    "features": "\n".join(plan.features or []),
                    "is_public": plan.is_public,
                    "is_active": plan.is_active,
                    "sort_order": plan.sort_order,
                }
            )

    def save(self):
        plan = self.plan or SaasPlan()
        plan.name = self.cleaned_data["name"]
        plan.tagline = self.cleaned_data.get("tagline", "")
        plan.description = self.cleaned_data.get("description", "")
        plan.monthly_price = self.cleaned_data["monthly_price"]
        plan.yearly_price = self.cleaned_data["yearly_price"]
        plan.max_users = self.cleaned_data["max_users"]
        plan.included_modules = self.cleaned_data.get("included_modules", [])
        plan.features = [
            line.strip()
            for line in (self.cleaned_data.get("features") or "").splitlines()
            if line.strip()
        ]
        plan.is_public = self.cleaned_data.get("is_public", False)
        plan.is_active = self.cleaned_data.get("is_active", False)
        plan.sort_order = self.cleaned_data.get("sort_order", 0)
        plan.save()
        return plan


class PlatformSettingsForm(SoraFormMixin, forms.Form):
    """Builds one field per PlatformSetting row for a given group."""

    def __init__(self, *args, rows=None, **kwargs):
        self.rows = list(rows or [])
        super().__init__(*args, **kwargs)
        for row in self.rows:
            initial = row.typed_value
            if row.value_type == "bool":
                field = forms.BooleanField(required=False, initial=bool(initial), label=row.label)
            elif row.value_type == "number":
                field = forms.IntegerField(required=False, initial=initial or 0, label=row.label)
            elif row.value_type == "email":
                field = forms.EmailField(required=False, initial=initial, label=row.label)
            elif row.value_type == "secret":
                field = forms.CharField(
                    required=False,
                    widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                    label=row.label,
                )
            elif row.value_type == "textarea":
                field = forms.CharField(
                    required=False,
                    widget=forms.Textarea(attrs={"rows": 3}),
                    initial=initial,
                    label=row.label,
                )
            elif row.value_type == "color":
                field = forms.CharField(
                    required=False,
                    widget=forms.TextInput(attrs={"type": "color"}),
                    initial=initial or "#FFD700",
                    label=row.label,
                )
            else:
                field = forms.CharField(required=False, initial=initial, label=row.label)
            field.help_text = row.help_text
            field.row = row
            self.fields[f"setting_{row.key}"] = field

    def save(self, actor=None):
        for row in self.rows:
            value = self.cleaned_data.get(f"setting_{row.key}")
            if row.value_type == "bool":
                row.value = "true" if value else "false"
            elif row.value_type == "secret":
                if not value:
                    continue
                row.value = value
            else:
                row.value = "" if value is None else str(value)
            row.updated_by = actor
            row.save()
        return self.rows


class PublicLeadForm(SoraFormMixin, forms.Form):
    name = forms.CharField(max_length=150, label="Your name")
    company = forms.CharField(max_length=255, required=False, label="Company")
    email = forms.EmailField(max_length=255, label="Work email")
    phone = forms.CharField(max_length=50, required=False, label="Phone")
    country = forms.CharField(max_length=100, required=False, label="Country")
    modules = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False, label="Modules you need"
    )
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "What are you trying to run?"}),
        label="What would you like help with?",
    )
    consent = forms.BooleanField(label="Consent")
    website = forms.CharField(required=False, widget=forms.HiddenInput, label="")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modules"].choices = module_choices()
        self.fields["website"].widget.attrs.update(
            {"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}
        )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("This request could not be accepted.")
        return ""


class SalesLeadForm(SoraFormMixin, forms.ModelForm):
    modules = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False, label="Interested modules"
    )

    class Meta:
        model = SalesLead
        fields = [
            "name",
            "company",
            "email",
            "phone",
            "country",
            "modules",
            "message",
            "source",
            "status",
            "owner",
            "next_follow_up",
            "notes",
        ]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "next_follow_up": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.models import User

        self.fields["modules"].choices = module_choices()
        self.fields["owner"].queryset = User.objects.filter(
            is_platform_admin=True, status="active"
        ).order_by("first_name", "last_name")
        self.fields["owner"].required = False

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class SalesLeadUpdateForm(SoraFormMixin, forms.ModelForm):
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        label="Add a note",
        help_text="Optional. Saved to the lead timeline with the change.",
    )

    class Meta:
        model = SalesLead
        fields = ["status", "owner", "next_follow_up"]
        widgets = {"next_follow_up": forms.DateInput(attrs={"type": "date"})}
        labels = {"next_follow_up": "Next follow-up"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.core.models import User

        self.fields["status"].choices = SALES_LEAD_STATUSES
        self.fields["owner"].queryset = User.objects.filter(
            is_platform_admin=True, status="active"
        ).order_by("first_name", "last_name")
        self.fields["owner"].required = False


class SalesLeadNoteForm(SoraFormMixin, forms.Form):
    kind = forms.ChoiceField(
        choices=[("note", "Note"), ("contact", "Contacted")], label="Type"
    )
    note = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), label="Note")


class SalesLeadConvertForm(SoraFormMixin, forms.Form):
    company_name = forms.CharField(max_length=255, label="Workspace / company name")
    first_name = forms.CharField(max_length=100, label="First name")
    last_name = forms.CharField(max_length=100, label="Last name")
    email = forms.EmailField(max_length=255, label="Owner login email")
    phone = forms.CharField(max_length=50, required=False, label="Phone")
    plan = forms.ModelChoiceField(
        queryset=SaasPlan.objects.filter(is_active=True),
        required=False,
        empty_label="Trial (no plan yet)",
        label="Plan",
    )
    modules = forms.MultipleChoiceField(
        choices=[], widget=forms.CheckboxSelectMultiple, required=False, label="Licensed modules"
    )
    trial_days = forms.IntegerField(
        min_value=1, max_value=365, required=False, initial=30, label="Trial length (days)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modules"].choices = module_choices()

    def clean_email(self):
        from apps.core.models import User

        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email already belongs to a login.")
        return email
