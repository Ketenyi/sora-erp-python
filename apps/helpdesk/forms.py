"""Forms for the Helpdesk & SLA workspace."""

from __future__ import annotations

from datetime import date

from django import forms
from django.core.exceptions import ValidationError

from apps.core.models import (
    EmailIdentity,
    Employee,
    Project,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
)
from apps.helpdesk.models import (
    PRIORITY_CHOICES,
    SLA_DEFAULTS,
    HelpdeskRoutingRule,
    HelpdeskSetting,
)

POLICY_ORDER = ("critical", "high", "medium", "low")


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def _employee_field(label="Assigned agent", required=False):
    return forms.ModelChoiceField(
        queryset=Employee.objects.filter(status="active").order_by("first_name", "last_name"),
        required=required,
        label=label,
        empty_label="Unassigned",
    )


class TicketForm(StyledFormMixin, forms.ModelForm):
    contract = forms.ModelChoiceField(
        queryset=SlaContract.objects.filter(status="active").order_by("contract_number"),
        label="SLA contract",
        empty_label="Select contract",
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by("-id"),
        required=False,
        label="Project",
        empty_label="No linked project",
    )
    assigned_to = _employee_field()

    class Meta:
        model = SlaTicket
        fields = [
            "contract",
            "project",
            "title",
            "description",
            "requester_name",
            "requester_email",
            "requester_phone",
            "channel",
            "category",
            "priority",
            "assigned_to",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={"rows": 5, "placeholder": "What happened, and what does the requester need?"}
            ),
            "title": forms.TextInput(attrs={"placeholder": "Short summary of the issue"}),
            "requester_name": forms.TextInput(attrs={"placeholder": "Client contact"}),
            "requester_email": forms.EmailInput(attrs={"placeholder": "client@example.com"}),
            "requester_phone": forms.TextInput(attrs={"placeholder": "+254 ..."}),
        }

    def clean_requester_email(self):
        return (self.cleaned_data.get("requester_email") or "").strip().lower()


class TicketUpdateForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=SlaTicket._meta.get_field("status").choices)
    priority = forms.ChoiceField(choices=SlaTicket._meta.get_field("priority").choices)
    assigned_to = _employee_field()
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Optional note for the audit trail"}),
        label="Update note",
    )
    notify_client = forms.BooleanField(
        required=False,
        initial=True,
        label="Email the requester about this update",
    )


class ReplyForm(StyledFormMixin, forms.Form):
    body = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Write a reply or internal note..."}),
        label="Message",
    )
    channel = forms.ChoiceField(
        choices=[("web", "Client portal"), ("email", "Email"), ("phone", "Phone"), ("internal", "Internal note")],
        initial="web",
    )
    is_internal = forms.BooleanField(required=False, label="Internal note (hidden from the client view)")
    notify_client = forms.BooleanField(
        required=False,
        initial=True,
        label="Email this reply to the requester",
        widget=forms.CheckboxInput(attrs={"id": "reply-notify-client"}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("channel") == "internal":
            cleaned["is_internal"] = True
        if cleaned.get("is_internal"):
            cleaned["channel"] = "internal"
            cleaned["notify_client"] = False
        return cleaned


class ContractForm(StyledFormMixin, forms.ModelForm):
    contract_number = forms.CharField(
        required=False,
        max_length=50,
        label="Contract number",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by("-id"),
        required=False,
        label="Originating project",
        empty_label="No linked project",
    )
    start_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"}), label="Start date"
    )
    end_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Renewal / end date"
    )

    class Meta:
        model = SlaContract
        fields = [
            "contract_number",
            "project",
            "title",
            "client_name",
            "start_date",
            "end_date",
            "support_tier",
            "monthly_value",
            "response_target_hours",
            "resolution_target_hours",
            "status",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "Managed support retainer"}),
            "monthly_value": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "response_target_hours": forms.NumberInput(attrs={"min": "1"}),
            "resolution_target_hours": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["project"].initial = self.instance.project_id

    def clean_contract_number(self):
        number = (self.cleaned_data.get("contract_number") or "").strip().upper()
        if number:
            qs = SlaContract.objects.filter(contract_number=number)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("This contract number is already in use.")
        return number

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        if not cleaned.get("client_name") and cleaned.get("project"):
            cleaned["client_name"] = cleaned["project"].client_name or cleaned["project"].project_name
        return cleaned


class HealthCheckForm(StyledFormMixin, forms.ModelForm):
    contract = forms.ModelChoiceField(
        queryset=SlaContract.objects.filter(status="active").order_by("contract_number"),
        label="SLA contract",
        empty_label="Select contract",
    )

    class Meta:
        model = SlaHealthCheck
        fields = ["contract", "scheduled_date", "check_type"]
        widgets = {"scheduled_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["scheduled_date"].initial = date.today()
        if self.instance.pk and self.instance.contract_id:
            self.fields["contract"].initial = self.instance.contract_id


class HealthCheckCompleteForm(StyledFormMixin, forms.Form):
    findings = forms.CharField(
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": "Checks performed, issues found and follow-up actions"}
        ),
        label="Findings",
    )
    performed_by = _employee_field("Performed by")


class HelpdeskMailboxForm(StyledFormMixin, forms.ModelForm):
    mailbox_identity = forms.ModelChoiceField(
        queryset=EmailIdentity.objects.filter(module="helpdesk", is_active=True).order_by(
            "name"
        ),
        required=False,
        label="Shared support mailbox",
        empty_label="No mailbox linked",
    )
    default_contract = forms.ModelChoiceField(
        queryset=SlaContract.objects.filter(status="active").order_by("contract_number"),
        required=False,
        label="Contract for inbound tickets",
        empty_label="Select contract",
    )

    class Meta:
        model = HelpdeskSetting
        fields = [
            "mailbox_identity",
            "mailbox_active",
            "default_contract",
            "status_link_days",
            "auto_reply",
            "auto_assign",
            "notify_client",
        ]
        widgets = {"status_link_days": forms.NumberInput(attrs={"min": "1", "max": "365"})}

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("mailbox_active") and not cleaned.get("mailbox_identity"):
            self.add_error("mailbox_identity", "Link a mailbox before enabling polling.")
        if cleaned.get("mailbox_active") and not cleaned.get("default_contract"):
            self.add_error(
                "default_contract",
                "Choose the contract new email tickets should be logged under.",
            )
        return cleaned


class SlaPolicyForm(StyledFormMixin, forms.Form):
    """One response/resolution pair per priority, stored as editable rows."""

    def __init__(self, *args, policies=None, **kwargs):
        super().__init__(*args, **kwargs)
        policies = policies or {}
        labels = dict(PRIORITY_CHOICES)
        for priority in POLICY_ORDER:
            row = policies.get(priority)
            response = row.response_minutes if row else SLA_DEFAULTS[priority]["response"]
            resolution = row.resolution_minutes if row else SLA_DEFAULTS[priority]["resolution"]
            self.fields[f"{priority}_response"] = forms.IntegerField(
                min_value=5,
                max_value=10080,
                initial=response,
                label=f"{labels[priority]} first response (minutes)",
            )
            self.fields[f"{priority}_resolution"] = forms.IntegerField(
                min_value=15,
                max_value=43200,
                initial=resolution,
                label=f"{labels[priority]} resolution (minutes)",
            )
        self.order_fields(
            [f"{priority}_{kind}" for priority in POLICY_ORDER for kind in ("response", "resolution")]
        )

    def policy_values(self) -> dict[str, tuple[int, int]]:
        return {
            priority: (
                self.cleaned_data[f"{priority}_response"],
                self.cleaned_data[f"{priority}_resolution"],
            )
            for priority in POLICY_ORDER
        }


class HelpdeskSurveyForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = HelpdeskSetting
        fields = [
            "survey_enabled",
            "survey_title",
            "survey_intro",
            "survey_rating_label",
            "survey_comment_label",
            "survey_comment_enabled",
            "survey_thanks",
        ]
        widgets = {
            "survey_intro": forms.Textarea(attrs={"rows": 2}),
            "survey_thanks": forms.Textarea(attrs={"rows": 2}),
            "survey_title": forms.TextInput(attrs={"placeholder": "How did we do?"}),
            "survey_rating_label": forms.TextInput(),
            "survey_comment_label": forms.TextInput(),
        }


class RoutingRuleForm(StyledFormMixin, forms.ModelForm):
    assignee = _employee_field("Assign to agent", required=False)
    match_channel = forms.ChoiceField(
        choices=[("", "Any channel")] + list(SlaTicket._meta.get_field("channel").choices),
        required=False,
        label="Match channel",
    )
    set_priority = forms.ChoiceField(
        choices=[("", "Keep priority")] + list(PRIORITY_CHOICES),
        required=False,
        label="Set priority",
    )
    set_category = forms.ChoiceField(
        choices=[("", "Keep category")] + list(SlaTicket._meta.get_field("category").choices),
        required=False,
        label="Set category",
    )

    class Meta:
        model = HelpdeskRoutingRule
        fields = [
            "name",
            "order",
            "is_active",
            "match_channel",
            "keywords",
            "from_contains",
            "set_priority",
            "set_category",
            "round_robin",
            "notify_client",
            "stop_processing",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Network incidents"}),
            "keywords": forms.Textarea(
                attrs={"rows": 2, "placeholder": "outage, vpn, network"}
            ),
            "from_contains": forms.TextInput(
                attrs={"placeholder": "client.example, vip@acme.com"}
            ),
            "order": forms.NumberInput(attrs={"min": "1"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].empty_label = "No change"
        if self.instance and self.instance.pk and self.instance.assignee_id:
            self.fields["assignee"].initial = self.instance.assignee_id

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("assignee") and cleaned.get("round_robin"):
            self.add_error(
                "round_robin", "Choose a fixed assignee or round robin, not both."
            )
        return cleaned


class SurveyResponseForm(forms.Form):
    rating = forms.TypedChoiceField(
        choices=[(str(value), str(value)) for value in range(1, 6)],
        coerce=int,
        widget=forms.RadioSelect(attrs={"class": "rating-options"}),
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Share a little more detail (optional)"}
        ),
    )
