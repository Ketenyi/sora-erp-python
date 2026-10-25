"""Forms for the project management module."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.core.models import (
    Employee,
    Project,
    ProjectChangeRequest,
    ProjectMom,
    ProjectTask,
    ProjectTeam,
    SlaContract,
    SlaTicket,
    Vendor,
)

WIDGET_CHOICES = [
    ("portfolio_summary", "Portfolio summary"),
    ("financial_health", "Financial health"),
    ("expense_burn", "Expense burn alerts"),
    ("project_status", "Projects by status"),
    ("task_status", "Tasks by status"),
    ("upcoming_deadlines", "Upcoming deadlines"),
    ("sla_snapshot", "SLA snapshot"),
    ("team_workload", "Team workload"),
    ("recent_activity", "Recent activity"),
]


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.HiddenInput)):
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def _employee_field(label="Employee", required=False):
    return forms.ModelChoiceField(
        queryset=Employee.objects.filter(status="active").order_by("first_name", "last_name"),
        required=required,
        label=label,
        empty_label="Select employee" if required else "Unassigned",
    )


class ProjectForm(StyledFormMixin, forms.ModelForm):
    manager = _employee_field("Project manager")
    project_code = forms.CharField(
        required=False,
        max_length=50,
        label="Project code",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    contract_value = forms.DecimalField(
        max_digits=15, decimal_places=2, min_value=Decimal("0"), initial=Decimal("0.00"),
        label="Contract value",
    )
    allocated_expense_budget = forms.DecimalField(
        max_digits=15, decimal_places=2, min_value=Decimal("0"), initial=Decimal("0.00"),
        label="Allocated expense budget",
        help_text="Field expenses are monitored against this budget; Finance is alerted at 80%.",
    )

    class Meta:
        model = Project
        fields = [
            "project_code", "project_name", "project_type", "client_name", "client_email",
            "contract_value", "allocated_expense_budget", "start_date", "end_date",
            "status", "priority", "description",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 4}),
            "project_name": forms.TextInput(attrs={"placeholder": "Project or engagement name"}),
            "client_name": forms.TextInput(attrs={"placeholder": "Client organisation"}),
            "client_email": forms.EmailInput(attrs={"placeholder": "client@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["manager"].initial = self.instance.manager_id

    def clean_project_code(self):
        code = (self.cleaned_data.get("project_code") or "").strip().upper()
        if code:
            qs = Project.objects.filter(project_code=code)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("A project with this code already exists.")
        return code

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            raise ValidationError("End date cannot be before the start date.")
        if cleaned.get("project_type") == "external_client" and not cleaned.get("client_name"):
            self.add_error("client_name", "Client name is required for external client projects.")
        return cleaned


class TaskForm(StyledFormMixin, forms.ModelForm):
    parent_task = forms.ModelChoiceField(
        queryset=ProjectTask.objects.none(), required=False, label="Parent task",
        empty_label="No parent (top level)",
    )
    assigned_to = _employee_field("Assignee")
    start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Start date"
    )
    deadline_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}), label="Due date"
    )

    class Meta:
        model = ProjectTask
        fields = [
            "task_name", "description", "start_date", "deadline_date",
            "estimated_hours", "status", "priority", "completion_percent", "is_scope_change",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "task_name": forms.TextInput(attrs={"placeholder": "What needs to be delivered?"}),
            "estimated_hours": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
            "completion_percent": forms.NumberInput(attrs={"step": "1", "min": "0", "max": "100"}),
            "is_scope_change": forms.CheckboxInput(),
        }
        labels = {
            "task_name": "Task title",
            "is_scope_change": "This task is out of contract scope",
        }

    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        queryset = ProjectTask.objects.filter(project_id=project.id).order_by("task_name")
        if self.instance and self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
            self.fields["assigned_to"].initial = self.instance.assigned_to
            self.fields["parent_task"].initial = self.instance.parent_task_id
        self.fields["parent_task"].queryset = queryset

    def clean(self):
        cleaned = super().clean()
        start, due = cleaned.get("start_date"), cleaned.get("deadline_date")
        if start and due and due < start:
            self.add_error("deadline_date", "Due date cannot be before the start date.")
        percent = cleaned.get("completion_percent")
        if cleaned.get("status") == "complete":
            cleaned["completion_percent"] = Decimal("100.00")
        elif percent is not None and percent > 100:
            self.add_error("completion_percent", "Completion cannot exceed 100%.")
        return cleaned


EXPENSE_CATEGORIES = [
    ("transport", "Transport"),
    ("meals", "Meals"),
    ("accommodation", "Accommodation"),
    ("travel", "Travel"),
    ("equipment", "Equipment"),
    ("software", "Software / licenses"),
    ("materials", "Materials"),
    ("other", "Other"),
]


class ExpenseForm(StyledFormMixin, forms.Form):
    expense_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"}), label="Expense date"
    )
    category = forms.ChoiceField(choices=EXPENSE_CATEGORIES, initial="transport")
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Description / purpose",
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"), max_digits=15, decimal_places=2, label="Amount",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.filter(status="active").order_by("company_name"),
        required=False, empty_label="No vendor", label="Vendor",
    )
    receipt = forms.FileField(
        required=False, label="Receipt",
        help_text="JPG, PNG, WEBP or PDF up to 10 MB.",
        widget=forms.ClearableFileInput(attrs={"accept": ".jpg,.jpeg,.png,.webp,.pdf"}),
    )

    def clean_receipt(self):
        upload = self.cleaned_data.get("receipt")
        if not upload:
            return upload
        extension = os.path.splitext(upload.name)[1].lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp", ".pdf"}:
            raise ValidationError("Upload a JPG, PNG, WEBP or PDF receipt.")
        if upload.size > 10 * 1024 * 1024:
            raise ValidationError("Receipts must be 10 MB or smaller.")
        return upload


class MomForm(StyledFormMixin, forms.ModelForm):
    meeting_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"}), label="Meeting date"
    )

    class Meta:
        model = ProjectMom
        fields = ["title", "meeting_date", "attendees", "agenda", "minutes", "decisions", "action_items"]
        widgets = {
            "attendees": forms.Textarea(attrs={"rows": 2, "placeholder": "Names and organisations"}),
            "agenda": forms.Textarea(attrs={"rows": 3}),
            "minutes": forms.Textarea(attrs={"rows": 6}),
            "decisions": forms.Textarea(attrs={"rows": 3}),
            "action_items": forms.Textarea(attrs={"rows": 3, "placeholder": "Owner — action — due date"}),
            "title": forms.TextInput(attrs={"placeholder": "e.g. Weekly delivery review"}),
        }


class ChangeRequestForm(StyledFormMixin, forms.ModelForm):
    task = forms.ModelChoiceField(
        queryset=ProjectTask.objects.none(), required=False, label="Out-of-scope task",
        empty_label="Not linked to a specific task",
    )

    class Meta:
        model = ProjectChangeRequest
        fields = ["task", "title", "description", "justification", "cost_impact", "schedule_impact_days"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "justification": forms.Textarea(attrs={"rows": 3}),
            "cost_impact": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "schedule_impact_days": forms.NumberInput(attrs={"step": "1", "min": "0"}),
            "title": forms.TextInput(attrs={"placeholder": "What is changing and why?"}),
        }
        labels = {
            "cost_impact": "Additional cost",
            "schedule_impact_days": "Schedule impact (days)",
        }

    def __init__(self, *args, project=None, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        queryset = ProjectTask.objects.filter(project_id=project.id).order_by("task_name")
        if self.instance and self.instance.pk:
            self.fields["task"].initial = self.instance.task_id
            queryset = queryset | ProjectTask.objects.filter(pk=self.instance.task_id)
        self.fields["task"].queryset = queryset.distinct()

    def clean_cost_impact(self):
        value = self.cleaned_data.get("cost_impact")
        if value is not None and value < 0:
            raise ValidationError("Additional cost cannot be negative.")
        return value


class ApprovalSendForm(StyledFormMixin, forms.Form):
    email = forms.EmailField(label="Send to")
    message = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        label="Personal message (optional)",
    )


class TeamMemberForm(StyledFormMixin, forms.Form):
    employee = _employee_field("Team member", required=True)
    role = forms.CharField(
        max_length=100, required=False,
        widget=forms.TextInput(attrs={"placeholder": "Engineer, PM, QA..."}),
    )
    allocation_percentage = forms.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("1"), max_value=Decimal("100"),
        initial=Decimal("100"), label="Allocation %",
        widget=forms.NumberInput(attrs={"step": "1", "min": "1", "max": "100"}),
    )
    hourly_rate = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0"),
        label="Hourly cost rate",
    )
    joined_at = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"}), label="Joined"
    )


class TimeEntryForm(StyledFormMixin, forms.Form):
    employee = _employee_field("Team member", required=True)
    task = forms.ModelChoiceField(
        queryset=ProjectTask.objects.none(), required=False, label="Task",
        empty_label="General project time",
    )
    entry_date = forms.DateField(
        initial=date.today, widget=forms.DateInput(attrs={"type": "date"}), label="Work date"
    )
    hours = forms.DecimalField(
        min_value=Decimal("0.1"), max_value=Decimal("24"), max_digits=5, decimal_places=2,
        label="Hours", widget=forms.NumberInput(attrs={"step": "0.25", "min": "0.1", "max": "24"}),
    )
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="What was worked on?"
    )
    billable = forms.BooleanField(required=False, initial=True, label="Billable")
    hourly_rate = forms.DecimalField(
        max_digits=10, decimal_places=2, required=False, min_value=Decimal("0"),
        label="Hourly rate override",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["task"].queryset = ProjectTask.objects.filter(project_id=project.id).order_by("task_name")


class HandoverForm(StyledFormMixin, forms.Form):
    departing_employee = _employee_field("Departing team member", required=True)
    incoming_employee = _employee_field("Incoming team member", required=True)
    task_ids = forms.MultipleChoiceField(
        choices=[], required=False, label="Tasks to reassign",
        widget=forms.CheckboxSelectMultiple(), help_text="Open and blocked tasks must be reassigned.",
    )
    state_of_play = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 8}),
        label="State of play memo",
        help_text="Mandatory context briefing: current status, risks, credentials locations and next actions.",
    )
    asset_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}),
        label="Asset / repository handover notes",
    )

    def __init__(self, *args, project=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.project = project
        tasks = list(
            ProjectTask.objects.filter(project_id=project.id)
            .exclude(status="complete")
            .order_by("status", "task_name")
        )
        self.open_tasks = tasks
        self.required_task_ids = {
            str(task.id) for task in tasks if task.status in {"in_progress", "blocked", "in_qa"}
        }
        self.fields["task_ids"].choices = [
            (str(task.id), f"{task.task_name} · {task.get_status_display()}") for task in tasks
        ]

    def clean_state_of_play(self):
        memo = (self.cleaned_data.get("state_of_play") or "").strip()
        if len(memo) < 50:
            raise ValidationError("Write at least 50 characters so context is not lost.")
        return memo

    def clean(self):
        cleaned = super().clean()
        departing = cleaned.get("departing_employee")
        incoming = cleaned.get("incoming_employee")
        if departing and incoming and departing.id == incoming.id:
            self.add_error("incoming_employee", "Choose a different incoming team member.")
        selected = set(cleaned.get("task_ids") or [])
        missing = self.required_task_ids - selected
        if missing:
            self.add_error(
                "task_ids",
                "Every in-progress, blocked or QA task must be reassigned before the handover can complete.",
            )
        return cleaned


class SlaContractForm(StyledFormMixin, forms.ModelForm):
    contract_number = forms.CharField(
        required=False, max_length=50, label="Contract number",
        widget=forms.TextInput(attrs={"placeholder": "Leave blank to auto-generate"}),
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by("-id"), required=False,
        label="Originating project", empty_label="No linked project",
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
            "contract_number", "project", "title", "client_name", "start_date", "end_date",
            "support_tier", "monthly_value", "response_target_hours", "resolution_target_hours", "status",
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


class SlaTicketForm(StyledFormMixin, forms.ModelForm):
    contract = forms.ModelChoiceField(
        queryset=SlaContract.objects.filter(status="active").order_by("contract_number"),
        label="SLA contract", empty_label="Select contract",
    )
    project = forms.ModelChoiceField(
        queryset=Project.objects.all().order_by("-id"), required=False,
        label="Project", empty_label="No linked project",
    )
    assigned_to = _employee_field("Assigned to")

    class Meta:
        model = SlaTicket
        fields = ["contract", "project", "title", "description", "priority", "status"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "title": forms.TextInput(attrs={"placeholder": "Client-reported issue"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["contract"].queryset = SlaContract.objects.filter(
                pk=self.instance.contract_id
            ) | SlaContract.objects.filter(status="active")
            self.fields["project"].initial = self.instance.project_id
            self.fields["assigned_to"].initial = self.instance.assigned_to


class SlaTicketUpdateForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=SlaTicket._meta.get_field("status").choices)
    assigned_to = _employee_field("Assigned to")
    note = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Update note",
    )


class HealthScheduleForm(StyledFormMixin, forms.Form):
    start_date = forms.DateField(
        initial=date.today() + timedelta(days=7), widget=forms.DateInput(attrs={"type": "date"}),
        label="First visit",
    )
    frequency = forms.ChoiceField(
        choices=[("monthly", "Monthly"), ("quarterly", "Quarterly")], initial="monthly"
    )
    occurrences = forms.IntegerField(
        min_value=1, max_value=12, initial=4, label="Number of visits",
        widget=forms.NumberInput(attrs={"min": "1", "max": "12"}),
    )


class HealthCheckCompleteForm(StyledFormMixin, forms.Form):
    findings = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}), label="Findings and actions"
    )


class DashboardForm(StyledFormMixin, forms.Form):
    title = forms.CharField(max_length=255, label="Dashboard title")
    is_default = forms.BooleanField(required=False, label="Make this my default dashboard")
    widgets = forms.MultipleChoiceField(
        choices=WIDGET_CHOICES, required=True, widget=forms.CheckboxSelectMultiple,
        label="Widgets",
    )
    widget_order = forms.CharField(required=False, widget=forms.HiddenInput())

    def clean(self):
        cleaned = super().clean()
        order = (cleaned.get("widget_order") or "").strip()
        selected = set(cleaned.get("widgets") or [])
        valid_keys = {key for key, _ in WIDGET_CHOICES}
        if order:
            ordered = [key for key in order.split(",") if key in valid_keys and key in selected]
        else:
            ordered = [key for key, _ in WIDGET_CHOICES if key in selected]
        for key in selected:
            if key not in ordered:
                ordered.append(key)
        cleaned["widget_order"] = ordered
        return cleaned


class ReportSaveForm(StyledFormMixin, forms.Form):
    name = forms.CharField(max_length=255, label="Report name")
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Description"
    )
    schedule = forms.ChoiceField(
        choices=[("none", "On demand"), ("weekly", "Weekly email"), ("monthly", "Monthly email")],
        initial="none",
    )
    recipients = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}),
        label="Email recipients",
        help_text="Comma-separated email addresses for scheduled delivery.",
    )
