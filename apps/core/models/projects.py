"""Project management models: legacy core plus the SORA PM blueprint.

The original ``projects``, ``project_tasks``, ``project_expenses``,
``project_team`` and ``project_time_entries`` tables are reused and extended
so existing data stays in place. New PM concepts (MOMs, change requests,
magic links, SLA hub, handovers, dashboards and reports) live in ``pm_*``
tables.
"""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel

PROJECT_TYPE_CHOICES = [
    ("external_client", "External Client"),
    ("internal_initiative", "Internal Initiative"),
]

PROJECT_STATUS_CHOICES = [
    ("bidding", "Bidding"),
    ("in_execution", "In Execution"),
    ("on_hold", "On Hold"),
    ("support_mode", "Support Mode"),
    ("completed", "Completed"),
]

TASK_STATUS_CHOICES = [
    ("backlog", "Backlog"),
    ("in_progress", "In Progress"),
    ("blocked", "Blocked"),
    ("in_qa", "In QA"),
    ("complete", "Complete"),
]

MAGIC_ACTION_CHOICES = [
    ("approve_mom", "Approve MOM"),
    ("approve_change_request", "Approve Change Request"),
    ("accept_uat", "Accept UAT"),
]


class Project(TenantScopedModel):
    """Legacy table: projects (extended for the PM blueprint)."""
    id = models.AutoField(primary_key=True)
    project_code = models.CharField(max_length=50)
    project_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    project_type = models.CharField(
        max_length=20,
        choices=PROJECT_TYPE_CHOICES,
        default="external_client",
        db_index=True,
    )
    client_id = models.IntegerField(null=True, db_index=True)
    client_name = models.CharField(max_length=255, null=True, blank=True)
    client_email = models.EmailField(max_length=255, null=True, blank=True)
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    baseline_start_date = models.DateField(null=True)
    baseline_end_date = models.DateField(null=True)
    baseline_locked_at = models.DateTimeField(null=True)
    budget_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    contract_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    allocated_expense_budget = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    actual_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    actual_revenue = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    status = models.CharField(
        max_length=20,
        choices=PROJECT_STATUS_CHOICES,
        null=True,
        blank=True,
        default="bidding",
        db_index=True,
    )
    priority = models.CharField(max_length=8, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")], null=True, blank=True, default="medium")
    manager_id = models.IntegerField(null=True, db_index=True)
    uat_accepted_at = models.DateTimeField(null=True)
    uat_accepted_by = models.EmailField(max_length=255, null=True, blank=True)
    created_by = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "project_code"),)
        db_table = "projects"

    def __str__(self):
        return f"{self.project_code} · {self.project_name}"

    @property
    def is_baseline_locked(self) -> bool:
        return self.baseline_locked_at is not None

    @property
    def effective_start(self):
        return self.baseline_start_date or self.start_date

    @property
    def effective_end(self):
        return self.baseline_end_date or self.end_date


class ProjectBudget(TenantScopedModel):
    """Legacy table: project_budgets"""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    category = models.CharField(max_length=100)
    budgeted_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    actual_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "project_budgets"

    def __str__(self):
        return f"ProjectBudget {self.pk}"


class ProjectComment(TenantScopedModel):
    """Legacy table: project_comments"""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    task_id = models.IntegerField(null=True, db_index=True)
    user_id = models.IntegerField(db_index=True)
    comment = models.TextField()
    attachments = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_comments"

    def __str__(self):
        return f"ProjectComment {self.pk}"


class ProjectExpense(TenantScopedModel):
    """Legacy table: project_expenses (extended for field expenses)."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    journal_entry_id = models.IntegerField(null=True, db_index=True)
    expense_date = models.DateField()
    description = models.TextField(null=True, blank=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    category = models.CharField(max_length=100, null=True, blank=True)
    vendor_id = models.IntegerField(null=True, db_index=True)
    receipt_path = models.CharField(max_length=500, null=True, blank=True)
    submitted_by = models.IntegerField(null=True, db_index=True)
    approved_by = models.IntegerField(null=True, db_index=True)
    status = models.CharField(max_length=8, choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], null=True, blank=True, default="pending")
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_expenses"

    def __str__(self):
        return f"ProjectExpense {self.pk}"


class ProjectInvoice(TenantScopedModel):
    """Legacy table: project_invoices"""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    invoice_id = models.IntegerField(db_index=True)
    allocated_amount = models.DecimalField(max_digits=15, decimal_places=2)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "project_invoices"

    def __str__(self):
        return f"ProjectInvoice {self.pk}"


class ProjectTask(TenantScopedModel):
    """Legacy table: project_tasks (extended with dependencies and scope)."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    parent_task_id = models.IntegerField(null=True, db_index=True)
    task_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    assigned_to = models.IntegerField(null=True, db_index=True)
    started_at = models.DateTimeField(null=True)
    assigned_by = models.IntegerField(null=True)
    start_date = models.DateField(null=True)
    end_date = models.DateField(null=True)
    deadline_date = models.DateField(null=True)
    estimated_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    actual_hours = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal("0.00"))
    estimated_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    actual_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    status = models.CharField(
        max_length=11,
        choices=TASK_STATUS_CHOICES,
        null=True,
        blank=True,
        default="backlog",
        db_index=True,
    )
    priority = models.CharField(max_length=6, choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")], null=True, blank=True, default="medium")
    is_scope_change = models.BooleanField(default=False)
    completion_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal("0.00"))
    completion_timestamp = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    performance_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal("0.00"))
    performance_rating = models.CharField(max_length=9, choices=[("excellent", "Excellent"), ("good", "Good"), ("average", "Average"), ("poor", "Poor"), ("overdue", "Overdue")], null=True, blank=True)
    board_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_tasks"
        ordering = ["board_order", "id"]

    def __str__(self):
        return f"ProjectTask {self.pk}"


class ProjectTeam(TenantScopedModel):
    """Legacy table: project_team"""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField()
    employee_id = models.IntegerField(db_index=True)
    role = models.CharField(max_length=100, null=True, blank=True)
    allocation_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal("100.00"))
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    joined_at = models.DateField(null=True)
    left_at = models.DateField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "project_team"
        unique_together = (("project_id", "employee_id"),)

    def __str__(self):
        return f"ProjectTeam {self.pk}"


class ProjectTimeEntry(TenantScopedModel):
    """Legacy table: project_time_entries"""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    task_id = models.IntegerField(null=True, db_index=True)
    employee_id = models.IntegerField(db_index=True)
    entry_date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.TextField(null=True, blank=True)
    billable = models.SmallIntegerField(null=True, default=1)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    status = models.CharField(max_length=8, choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], null=True, blank=True, default="pending")
    approved_by = models.IntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_time_entries"

    def __str__(self):
        return f"ProjectTimeEntry {self.pk}"


# ---------------------------------------------------------------------------
# PM blueprint tables
# ---------------------------------------------------------------------------
class ProjectMom(TenantScopedModel):
    """Minutes of meeting issued to a client for zero-login approval."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=255)
    meeting_date = models.DateField()
    attendees = models.TextField(null=True, blank=True)
    agenda = models.TextField(null=True, blank=True)
    minutes = models.TextField()
    decisions = models.TextField(null=True, blank=True)
    action_items = models.TextField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("draft", "Draft"),
            ("pending_approval", "Pending Approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        db_index=True,
    )
    approved_by_name = models.CharField(max_length=255, null=True, blank=True)
    approved_at = models.DateTimeField(null=True)
    rejection_reason = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pm_moms"
        ordering = ["-meeting_date", "-id"]

    def __str__(self):
        return f"MOM {self.pk} · {self.title}"


class ProjectChangeRequest(TenantScopedModel):
    """Scope change request that must be funded before work can complete."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    task_id = models.IntegerField(null=True, db_index=True)
    cr_number = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField()
    justification = models.TextField(null=True, blank=True)
    cost_impact = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    schedule_impact_days = models.IntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=[
            ("draft", "Draft"),
            ("pending_approval", "Pending Approval"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        db_index=True,
    )
    requested_by = models.IntegerField(null=True)
    requested_at = models.DateTimeField(null=True, default=timezone.now)
    decided_by_name = models.CharField(max_length=255, null=True, blank=True)
    decided_at = models.DateTimeField(null=True)
    decision_notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "cr_number"),)
        db_table = "pm_change_requests"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.cr_number} · {self.title}"


class ProjectMagicLink(TenantScopedModel):
    """HMAC-hashed, single-use approval token (72 hour maximum TTL)."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    action_type = models.CharField(max_length=50, choices=MAGIC_ACTION_CHOICES)
    target_entity_id = models.IntegerField()
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True)
    recipient_email = models.EmailField(max_length=255, null=True, blank=True)
    sent_at = models.DateTimeField(null=True)
    decision = models.CharField(
        max_length=10,
        choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")],
        default="pending",
    )
    decision_notes = models.TextField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True)
    decided_ip = models.CharField(max_length=64, null=True, blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "pm_magic_links"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Magic link {self.pk} · {self.action_type}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() > self.expires_at

    @property
    def is_active(self) -> bool:
        return self.used_at is None and not self.is_expired


class ProjectAuditLog(TenantScopedModel):
    """Immutable audit trail for project and task modifications."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    task_id = models.IntegerField(null=True, db_index=True)
    user_id = models.IntegerField(null=True, db_index=True)
    action = models.CharField(max_length=40)
    field_name = models.CharField(max_length=100, null=True, blank=True)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "pm_audit_logs"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Audit {self.pk} · {self.action}"


class ProjectHandover(TenantScopedModel):
    """Structured offboarding: reassign tasks, assets and capture context."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(db_index=True)
    departing_employee_id = models.IntegerField(db_index=True)
    incoming_employee_id = models.IntegerField(db_index=True)
    status = models.CharField(
        max_length=20,
        choices=[("draft", "Draft"), ("in_progress", "In Progress"), ("completed", "Completed")],
        default="draft",
        db_index=True,
    )
    state_of_play = models.TextField(help_text="Mandatory 1-page state of play memo.")
    asset_notes = models.TextField(null=True, blank=True)
    initiated_by = models.IntegerField(null=True)
    initiated_at = models.DateTimeField(null=True, default=timezone.now)
    completed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "pm_handovers"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Handover {self.pk}"


class ProjectHandoverItem(TenantScopedModel):
    """Task / asset reassignment recorded inside a handover."""
    id = models.AutoField(primary_key=True)
    handover_id = models.IntegerField(db_index=True)
    item_type = models.CharField(
        max_length=20,
        choices=[("task", "Task"), ("asset", "Asset"), ("note", "Note")],
        default="task",
    )
    reference = models.CharField(max_length=255, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    to_employee_id = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "pm_handover_items"

    def __str__(self):
        return f"HandoverItem {self.pk}"


class SlaContract(TenantScopedModel):
    """Support / AMC contract created at project handover."""
    id = models.AutoField(primary_key=True)
    project_id = models.IntegerField(null=True, blank=True, db_index=True)
    contract_number = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    client_name = models.CharField(max_length=255, null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True)
    support_tier = models.CharField(
        max_length=20,
        choices=[("standard", "Standard"), ("priority", "Priority"), ("24x7", "24x7 Mission Critical")],
        default="standard",
    )
    monthly_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal("0.00"))
    response_target_hours = models.IntegerField(default=8)
    resolution_target_hours = models.IntegerField(default=48)
    status = models.CharField(
        max_length=10,
        choices=[("active", "Active"), ("expired", "Expired"), ("cancelled", "Cancelled")],
        default="active",
        db_index=True,
    )
    auto_created_from_project = models.BooleanField(default=False)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "contract_number"),)
        db_table = "pm_sla_contracts"
        ordering = ["-start_date", "-id"]

    def __str__(self):
        return f"{self.contract_number} · {self.title}"


class SlaTicket(TenantScopedModel):
    """Support ticket, optionally converted from an unresolved project task."""
    id = models.AutoField(primary_key=True)
    contract_id = models.IntegerField(db_index=True)
    project_id = models.IntegerField(null=True, blank=True, db_index=True)
    source_task_id = models.IntegerField(null=True, blank=True, db_index=True)
    ticket_number = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    requester_name = models.CharField(max_length=150, blank=True)
    requester_email = models.EmailField(blank=True)
    requester_phone = models.CharField(max_length=30, blank=True)
    channel = models.CharField(
        max_length=12,
        choices=[
            ("web", "Web / Portal"),
            ("email", "Email"),
            ("phone", "Phone"),
            ("walk_in", "Walk-in"),
            ("internal", "Internal"),
        ],
        default="web",
    )
    category = models.CharField(
        max_length=16,
        choices=[
            ("incident", "Incident"),
            ("service_request", "Service Request"),
            ("problem", "Problem"),
            ("change", "Change"),
        ],
        default="incident",
    )
    priority = models.CharField(
        max_length=8,
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High"), ("critical", "Critical")],
        default="medium",
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ("open", "Open"),
            ("in_progress", "In Progress"),
            ("waiting_client", "Waiting on Client"),
            ("resolved", "Resolved"),
            ("closed", "Closed"),
        ],
        default="open",
        db_index=True,
    )
    assigned_to = models.IntegerField(null=True, db_index=True)
    response_due_at = models.DateTimeField(null=True)
    sla_due_at = models.DateTimeField(null=True)
    first_response_at = models.DateTimeField(null=True)
    response_breached = models.BooleanField(default=False)
    breached = models.BooleanField(default=False)
    survey_sent_at = models.DateTimeField(null=True)
    client_confirmed_at = models.DateTimeField(null=True)
    resolved_at = models.DateTimeField(null=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "ticket_number"),)
        db_table = "pm_sla_tickets"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.ticket_number} · {self.title}"


class SlaHealthCheck(TenantScopedModel):
    """Preventive maintenance visit scheduled against an SLA contract."""
    id = models.AutoField(primary_key=True)
    contract_id = models.IntegerField(db_index=True)
    project_id = models.IntegerField(null=True, blank=True, db_index=True)
    scheduled_date = models.DateField(db_index=True)
    check_type = models.CharField(
        max_length=20,
        choices=[("preventive", "Preventive Maintenance"), ("inspection", "Inspection"), ("health_check", "Health Check")],
        default="preventive",
    )
    status = models.CharField(
        max_length=10,
        choices=[("scheduled", "Scheduled"), ("completed", "Completed"), ("skipped", "Skipped")],
        default="scheduled",
        db_index=True,
    )
    findings = models.TextField(null=True, blank=True)
    performed_by = models.IntegerField(null=True)
    completed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "pm_sla_health_checks"
        ordering = ["scheduled_date", "id"]

    def __str__(self):
        return f"Health check {self.pk}"


class PmDashboard(TenantScopedModel):
    """User-configurable widget grid."""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=255)
    is_default = models.BooleanField(default=False)
    layout_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pm_dashboards"
        ordering = ["-is_default", "title"]

    def __str__(self):
        return self.title


class PmReportDefinition(TenantScopedModel):
    """Saved visual query plus delivery schedule."""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    report_type = models.CharField(max_length=40, default="query")
    filters = models.JSONField(default=dict, blank=True)
    schedule = models.CharField(
        max_length=10,
        choices=[("none", "On Demand"), ("weekly", "Weekly"), ("monthly", "Monthly")],
        default="none",
    )
    recipients = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    last_run_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "pm_report_definitions"
        ordering = ["name"]

    def __str__(self):
        return self.name


class PmFeatureFlag(models.Model):
    """SaaS tier feature gating flags."""
    id = models.AutoField(primary_key=True)
    flag_key = models.CharField(max_length=60, unique=True)
    label = models.CharField(max_length=120)
    tier = models.CharField(
        max_length=20,
        choices=[("core", "Core"), ("standard", "Standard"), ("enterprise", "Enterprise")],
        default="core",
    )
    enabled = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "pm_feature_flags"
        ordering = ["tier", "flag_key"]

    def __str__(self):
        return self.flag_key


__all__ = [
    "Project",
    "ProjectBudget",
    "ProjectComment",
    "ProjectExpense",
    "ProjectInvoice",
    "ProjectTask",
    "ProjectTeam",
    "ProjectTimeEntry",
    "ProjectMom",
    "ProjectChangeRequest",
    "ProjectMagicLink",
    "ProjectAuditLog",
    "ProjectHandover",
    "ProjectHandoverItem",
    "SlaContract",
    "SlaTicket",
    "SlaHealthCheck",
    "PmDashboard",
    "PmReportDefinition",
    "PmFeatureFlag",
]
