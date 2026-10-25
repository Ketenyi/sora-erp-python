"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.tenancy import TenantScopedModel


class Setting(models.Model):
    """Legacy table: settings"""
    id = models.AutoField(primary_key=True)
    setting_key = models.CharField(max_length=100, unique=True)
    value = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings"

    def __str__(self):
        return f"Setting {self.pk}"


class CompanySetting(models.Model):
    """Legacy table: company_settings

    Rows with ``tenant`` set override the platform-wide defaults for that
    workspace. Global rows (tenant is null) act as fallbacks.
    """
    id = models.AutoField(primary_key=True)
    tenant = models.ForeignKey(
        "saas.Tenant",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="company_settings",
        db_index=True,
    )
    setting_key = models.CharField(max_length=100)
    setting_value = models.TextField(null=True, blank=True)
    setting_type = models.CharField(max_length=50, null=True, blank=True, default='text')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "company_settings"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "setting_key"],
                name="uniq_company_setting_tenant_key",
            ),
            models.UniqueConstraint(
                fields=["setting_key"],
                condition=models.Q(tenant__isnull=True),
                name="uniq_company_setting_global_key",
            ),
        ]

    def __str__(self):
        return f"CompanySetting {self.pk}"


class Company(models.Model):
    """Legacy table: companies"""
    id = models.AutoField(primary_key=True)
    company_code = models.CharField(max_length=50, unique=True)
    company_name = models.CharField(max_length=255)
    company_email = models.CharField(max_length=255, null=True, blank=True)
    company_phone = models.CharField(max_length=50, null=True, blank=True)
    company_address = models.TextField(null=True, blank=True)
    database_name = models.CharField(max_length=100, unique=True)
    subscription_plan = models.CharField(max_length=10, choices=[('starter', 'Starter'), ('business', 'Business'), ('enterprise', 'Enterprise')], null=True, blank=True, default='starter')
    max_users = models.IntegerField(null=True, default=5)
    max_storage_mb = models.IntegerField(null=True, default=1024)
    status = models.CharField(max_length=9, choices=[('active', 'Active'), ('suspended', 'Suspended'), ('expired', 'Expired')], null=True, blank=True, default='active', db_index=True)
    subscription_expires = models.DateField(null=True)
    database_created = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "companies"

    def __str__(self):
        return f"Company {self.pk}"


class CompanyModule(models.Model):
    """Legacy table: company_modules"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField()
    module_id = models.IntegerField(db_index=True)
    is_active = models.BooleanField(null=True, default=1)
    activated_at = models.DateTimeField(null=True, default=timezone.now)
    expires_at = models.DateField(null=True)

    class Meta:
        db_table = "company_modules"
        unique_together = (('company_id', 'module_id'),)

    def __str__(self):
        return f"CompanyModule {self.pk}"


class Module(models.Model):
    """Legacy table: modules"""
    id = models.AutoField(primary_key=True)
    module_code = models.CharField(max_length=50, unique=True)
    module_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    icon = models.CharField(max_length=50, null=True, blank=True)
    default_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    is_active = models.BooleanField(null=True, default=1)
    sort_order = models.IntegerField(null=True, default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "modules"

    def __str__(self):
        return f"Module {self.pk}"


class ModulePermission(models.Model):
    """Legacy table: module_permissions"""
    id = models.AutoField(primary_key=True)
    module_name = models.CharField(max_length=50)
    role_name = models.CharField(max_length=50)
    can_view = models.BooleanField(null=True, default=1)
    can_create = models.BooleanField(null=True, default=0)
    can_edit = models.BooleanField(null=True, default=0)
    can_delete = models.BooleanField(null=True, default=0)
    can_approve = models.BooleanField(null=True, default=0)

    class Meta:
        db_table = "module_permissions"
        unique_together = (('module_name', 'role_name'),)

    def __str__(self):
        return f"ModulePermission {self.pk}"


class Permission(models.Model):
    """Legacy table: permissions"""
    id = models.AutoField(primary_key=True)
    module = models.CharField(max_length=50)
    action = models.CharField(max_length=50)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "permissions"
        unique_together = (('module', 'action'),)

    def __str__(self):
        return f"Permission {self.pk}"


class Role(models.Model):
    """Legacy table: roles"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=50, unique=True)
    display_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "roles"

    def __str__(self):
        return f"Role {self.pk}"


class RolePermission(models.Model):
    """Legacy table: role_permissions"""
    id = models.AutoField(primary_key=True)
    role_id = models.IntegerField()
    permission_id = models.IntegerField(db_index=True)

    class Meta:
        db_table = "role_permissions"
        unique_together = (('role_id', 'permission_id'),)

    def __str__(self):
        return f"RolePermission {self.pk}"


class UserRole(models.Model):
    """Legacy table: user_roles"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField()
    role_id = models.IntegerField(db_index=True)

    class Meta:
        db_table = "user_roles"
        unique_together = (('user_id', 'role_id'),)

    def __str__(self):
        return f"UserRole {self.pk}"


class UserPermission(models.Model):
    """Legacy table: user_permissions"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField()
    permission_id = models.IntegerField(db_index=True)
    granted = models.SmallIntegerField(null=True, default=1)

    class Meta:
        db_table = "user_permissions"
        unique_together = (('user_id', 'permission_id'),)

    def __str__(self):
        return f"UserPermission {self.pk}"


class UserModuleRole(models.Model):
    """Legacy table: user_module_roles"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField()
    module_name = models.CharField(max_length=50)
    role_name = models.CharField(max_length=50)
    assigned_by = models.IntegerField(null=True)
    assigned_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "user_module_roles"
        unique_together = (('user_id', 'module_name', 'role_name'),)

    def __str__(self):
        return f"UserModuleRole {self.pk}"


class Notification(TenantScopedModel):
    """Legacy table: notifications"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(null=True)
    type = models.CharField(max_length=50, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    is_read = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "notifications"

    def __str__(self):
        return f"Notification {self.pk}"


class EmailTemplate(models.Model):
    """Legacy table: email_templates"""
    id = models.AutoField(primary_key=True)
    template_key = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    variables = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "email_templates"

    def __str__(self):
        return f"EmailTemplate {self.pk}"


class EmailReportLog(models.Model):
    """Legacy table: email_report_logs"""
    id = models.AutoField(primary_key=True)
    report_id = models.IntegerField(db_index=True)
    sent_at = models.DateTimeField(null=True)
    recipients = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=7, choices=[('sent', 'Sent'), ('failed', 'Failed'), ('pending', 'Pending')], null=True, blank=True, default='pending')
    error_message = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "email_report_logs"

    def __str__(self):
        return f"EmailReportLog {self.pk}"


class SsoSetting(models.Model):
    """Legacy table: sso_settings"""
    id = models.AutoField(primary_key=True)
    provider = models.CharField(max_length=50)
    enabled = models.SmallIntegerField(null=True, default=0)
    client_id = models.CharField(max_length=255, null=True, blank=True)
    client_secret = models.CharField(max_length=255, null=True, blank=True)
    redirect_uri = models.CharField(max_length=255, null=True, blank=True)
    additional_config = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sso_settings"

    def __str__(self):
        return f"SsoSetting {self.pk}"


class ApiKey(models.Model):
    """Legacy table: api_keys"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=100)
    api_key = models.CharField(max_length=64, unique=True)
    api_secret = models.CharField(max_length=255, null=True, blank=True)
    permissions = models.TextField(null=True, blank=True)
    last_used = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=7, choices=[('active', 'Active'), ('revoked', 'Revoked'), ('expired', 'Expired')], null=True, blank=True, default='active')
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "api_keys"

    def __str__(self):
        return f"ApiKey {self.pk}"


class Webhook(models.Model):
    """Legacy table: webhooks"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    event_type = models.CharField(max_length=100, db_index=True)
    url = models.CharField(max_length=500)
    secret = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "webhooks"

    def __str__(self):
        return f"Webhook {self.pk}"


class WebhookLog(models.Model):
    """Legacy table: webhook_logs"""
    id = models.AutoField(primary_key=True)
    webhook_id = models.IntegerField(null=True, db_index=True)
    event_type = models.CharField(max_length=100, null=True, blank=True)
    payload = models.TextField(null=True, blank=True)
    response_code = models.IntegerField(null=True)
    response_body = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, db_index=True)

    class Meta:
        db_table = "webhook_logs"

    def __str__(self):
        return f"WebhookLog {self.pk}"


class SavedReport(TenantScopedModel):
    """Legacy table: saved_reports"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=200)
    report_type = models.CharField(max_length=50)
    filters = models.TextField(null=True, blank=True)
    columns = models.TextField(null=True, blank=True)
    schedule_frequency = models.CharField(max_length=7, choices=[('none', 'None'), ('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')], null=True, blank=True, default='none')
    next_send = models.DateTimeField(null=True)
    last_sent = models.DateTimeField(null=True)
    schedule_recipients = models.TextField(null=True, blank=True)
    last_generated = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=6, choices=[('active', 'Active'), ('paused', 'Paused')], null=True, blank=True, default='active')

    class Meta:
        db_table = "saved_reports"

    def __str__(self):
        return f"SavedReport {self.pk}"


class ReportSchedule(TenantScopedModel):
    """Legacy table: report_schedules"""
    id = models.AutoField(primary_key=True)
    report_id = models.IntegerField(db_index=True)
    recipient_emails = models.TextField(null=True, blank=True)
    format = models.CharField(max_length=5, choices=[('pdf', 'Pdf'), ('excel', 'Excel'), ('csv', 'Csv')], null=True, blank=True, default='pdf')
    last_sent = models.DateTimeField(null=True)
    next_send = models.DateTimeField(null=True)

    class Meta:
        db_table = "report_schedules"

    def __str__(self):
        return f"ReportSchedule {self.pk}"


class DocumentSequence(models.Model):
    """Atomic counters for human-readable document numbers.

    Rows are keyed by a tenant scope string rather than a foreign key so
    platform-level numbering keeps working without tenant context.
    """

    id = models.AutoField(primary_key=True)
    scope = models.CharField(max_length=64)
    sequence_key = models.CharField(max_length=128)
    value = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "document_sequences"
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "sequence_key"],
                name="uniq_document_sequence_scope_key",
            ),
        ]

    def __str__(self):
        return f"{self.scope}:{self.sequence_key}={self.value}"


class AuditLog(TenantScopedModel):
    """Legacy table: audit_log"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    action = models.CharField(max_length=50)
    table_name = models.CharField(max_length=50, null=True, blank=True)
    record_id = models.IntegerField(null=True)
    old_data = models.TextField(null=True, blank=True)
    new_data = models.TextField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["table_name", "record_id"]),
        ]

    def __str__(self):
        return f"AuditLog {self.pk}"


class AuditDetail(TenantScopedModel):
    """Legacy table: audit_details"""
    id = models.AutoField(primary_key=True)
    audit_id = models.IntegerField(null=True)
    field_name = models.CharField(max_length=100, null=True, blank=True)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "audit_details"

    def __str__(self):
        return f"AuditDetail {self.pk}"


class UserActivityLog(TenantScopedModel):
    """Legacy table: user_activity_log"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    activity_type = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    page_url = models.CharField(max_length=500, null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now, db_index=True)

    class Meta:
        db_table = "user_activity_log"
        indexes = [
            models.Index(fields=["user_id", "created_at"]),
        ]

    def __str__(self):
        return f"UserActivityLog {self.pk}"


class UserSession(models.Model):
    """Legacy table: user_sessions"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    session_token = models.CharField(max_length=255, unique=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    last_activity = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_sessions"

    def __str__(self):
        return f"UserSession {self.pk}"


class UserCompany(models.Model):
    """Legacy table: user_companies"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    company_id = models.IntegerField(db_index=True)
    role_in_company = models.CharField(max_length=7, choices=[('admin', 'Admin'), ('manager', 'Manager'), ('viewer', 'Viewer')], null=True, blank=True, default='viewer')
    is_default = models.BooleanField(null=True, default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "user_companies"
        unique_together = (('user_id', 'company_id'),)

    def __str__(self):
        return f"UserCompany {self.pk}"


class Attachment(TenantScopedModel):
    """Legacy table: attachments"""
    id = models.AutoField(primary_key=True)
    reference_type = models.CharField(max_length=50)
    reference_id = models.IntegerField()
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_size = models.IntegerField(null=True)
    mime_type = models.CharField(max_length=100, null=True, blank=True)
    ocr_text = models.TextField(null=True, blank=True)
    uploaded_by = models.IntegerField(null=True, db_index=True)
    uploaded_at = models.DateTimeField(null=True, db_index=True)
    expiry_date = models.DateField(null=True)
    reminder_days = models.IntegerField(null=True, default=7)
    version_number = models.IntegerField(null=True, default=1)
    extracted_text = models.TextField(null=True, blank=True)
    ai_summary = models.TextField(null=True, blank=True)
    ai_category_id = models.IntegerField(null=True)
    workflow_id = models.IntegerField(null=True)
    approval_status = models.CharField(max_length=30, null=True, blank=True, default='none')

    class Meta:
        db_table = "attachments"
        indexes = [
            models.Index(fields=["reference_type", "reference_id"]),
        ]

    def __str__(self):
        return f"Attachment {self.pk}"


class Currency(models.Model):
    """Legacy table: currencies"""
    id = models.AutoField(primary_key=True)
    code = models.CharField(max_length=3, null=True, blank=True)
    name = models.CharField(max_length=50, null=True, blank=True)
    symbol = models.CharField(max_length=5, null=True, blank=True)
    decimal_places = models.IntegerField(null=True, default=2)
    is_base = models.SmallIntegerField(null=True, default=0)

    class Meta:
        db_table = "currencies"

    def __str__(self):
        return f"Currency {self.pk}"


class ExchangeRate(models.Model):
    """Legacy table: exchange_rates"""
    id = models.AutoField(primary_key=True)
    from_currency = models.CharField(max_length=3, null=True, blank=True)
    to_currency = models.CharField(max_length=3, null=True, blank=True)
    rate = models.DecimalField(max_digits=15, decimal_places=6, null=True)
    effective_date = models.DateField(null=True)

    class Meta:
        db_table = "exchange_rates"

    def __str__(self):
        return f"ExchangeRate {self.pk}"


class ApprovalWorkflow(TenantScopedModel):
    """Legacy table: approval_workflows"""
    id = models.AutoField(primary_key=True)
    workflow_type = models.CharField(max_length=8, choices=[('leave', 'Leave'), ('overtime', 'Overtime')], db_index=True)
    approver_level = models.IntegerField(default=1)
    approver_role = models.CharField(max_length=50)
    department_id = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "approval_workflows"

    def __str__(self):
        return f"ApprovalWorkflow {self.pk}"


class ApprovalRule(TenantScopedModel):
    """Legacy table: approval_rules"""
    id = models.AutoField(primary_key=True)
    rule_name = models.CharField(max_length=100, null=True, blank=True)
    applies_to = models.CharField(max_length=7, choices=[('invoice', 'Invoice'), ('expense', 'Expense'), ('journal', 'Journal')], null=True, blank=True)
    min_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    approval_levels = models.IntegerField(null=True, default=1)

    class Meta:
        db_table = "approval_rules"

    def __str__(self):
        return f"ApprovalRule {self.pk}"


class ApprovalTracking(TenantScopedModel):
    """Legacy table: approval_tracking"""
    id = models.AutoField(primary_key=True)
    request_type = models.CharField(max_length=8, choices=[('leave', 'Leave'), ('overtime', 'Overtime')])
    request_id = models.IntegerField()
    approver_id = models.IntegerField(db_index=True)
    approval_level = models.IntegerField()
    status = models.CharField(max_length=8, choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], null=True, blank=True, default='pending')
    comments = models.TextField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "approval_tracking"
        indexes = [
            models.Index(fields=["request_type", "request_id"]),
        ]

    def __str__(self):
        return f"ApprovalTracking {self.pk}"


MODULE_ACCESS_ROLES = [
    ("admin", "Module administrator"),
    ("manager", "Manager"),
    ("editor", "Create and edit"),
    ("approver", "Approve"),
    ("viewer", "Read only"),
]

ACCESS_SCOPES = [
    ("tenant", "Whole workspace"),
    ("project", "Specific project"),
    ("assigned", "Assigned projects and records"),
]

EMAIL_IDENTITY_MODULES = [
    ("hr", "Human Resources"),
    ("sales", "Sales & CRM"),
    ("projects", "Projects"),
    ("helpdesk", "Helpdesk & SLA"),
    ("assets", "Asset Management"),
    ("pos", "Point of Sale"),
    ("billing", "Billing & Finance"),
    ("procurement", "Procurement"),
    ("system", "System & security"),
]


class ModuleAccessGrant(TenantScopedModel):
    """Fine-grained access for one user, module and optional submodule.

    ``project`` is required when ``scope`` is ``project``. ``assigned`` uses
    project team membership and gives a project-specific manager (PPM) access
    to the projects they are assigned to only.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="module_access_grants"
    )
    module = models.CharField(max_length=50, db_index=True)
    submodule = models.CharField(max_length=80, blank=True)
    role = models.CharField(max_length=20, choices=MODULE_ACCESS_ROLES, default="viewer")
    scope = models.CharField(max_length=10, choices=ACCESS_SCOPES, default="tenant")
    project = models.ForeignKey(
        "core.Project", null=True, blank=True, on_delete=models.CASCADE, related_name="access_grants"
    )
    is_active = models.BooleanField(default=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="granted_module_access",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "module_access_grants"
        ordering = ["module", "submodule", "id"]
        indexes = [
            models.Index(fields=["tenant", "user", "module"]),
            models.Index(fields=["tenant", "module", "scope"]),
        ]

    def __str__(self):
        label = self.submodule or self.module
        return f"{self.user_id}:{label} ({self.role}/{self.scope})"


class EmailIdentity(TenantScopedModel):
    """Per-module outbound sender and optional inbound mailbox."""

    name = models.CharField(max_length=120)
    module = models.CharField(max_length=30, choices=EMAIL_IDENTITY_MODULES, db_index=True)
    from_name = models.CharField(max_length=150)
    from_email = models.EmailField(max_length=255)
    reply_to = models.EmailField(max_length=255, blank=True)

    smtp_host = models.CharField(max_length=255, blank=True)
    smtp_port = models.IntegerField(default=587)
    smtp_username = models.CharField(max_length=255, blank=True)
    smtp_password = models.CharField(max_length=500, blank=True)
    smtp_encryption = models.CharField(
        max_length=10,
        choices=[("tls", "STARTTLS"), ("ssl", "SSL/TLS"), ("none", "None")],
        default="tls",
    )

    imap_host = models.CharField(max_length=255, blank=True)
    imap_port = models.IntegerField(default=993)
    imap_username = models.CharField(max_length=255, blank=True)
    imap_password = models.CharField(max_length=500, blank=True)
    imap_encryption = models.CharField(
        max_length=10,
        choices=[("ssl", "SSL/TLS"), ("tls", "STARTTLS"), ("none", "None")],
        default="ssl",
    )
    imap_folder = models.CharField(max_length=120, default="INBOX")

    signature = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    last_verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "email_identities"
        ordering = ["module", "-is_default", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "module", "from_email"],
                name="uniq_email_identity_tenant_module_address",
            )
        ]

    def __str__(self):
        return f"{self.name} <{self.from_email}>"

    def save(self, *args, **kwargs):
        self.smtp_password = encrypt_secret(self.smtp_password)
        self.imap_password = encrypt_secret(self.imap_password)
        super().save(*args, **kwargs)

    @property
    def smtp_password_plain(self) -> str:
        return decrypt_secret(self.smtp_password)

    @property
    def imap_password_plain(self) -> str:
        return decrypt_secret(self.imap_password)

    @property
    def can_send(self) -> bool:
        return self.is_active and bool(self.smtp_host and self.from_email)


class UserMFA(models.Model):
    """Optional TOTP second factor with hashed one-time backup codes."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa"
    )
    secret = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=False)
    backup_codes = models.JSONField(default=list, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_totp_counter = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_mfa"

    def save(self, *args, **kwargs):
        self.secret = encrypt_secret(self.secret)
        super().save(*args, **kwargs)

    @property
    def secret_plain(self) -> str:
        return decrypt_secret(self.secret)

    def __str__(self):
        return f"MFA for user {self.user_id}"


class LoginAttempt(models.Model):
    """Login audit and throttling source; survives process restarts."""

    email = models.EmailField(max_length=255, db_index=True)
    ip_address = models.CharField(max_length=45, blank=True, db_index=True)
    user_agent = models.CharField(max_length=500, blank=True)
    successful = models.BooleanField(default=False)
    reason = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "login_attempts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
        ]

    def __str__(self):
        return f"{self.email} {'ok' if self.successful else 'failed'}: {self.reason}"


class RequestAudit(models.Model):
    """Immutable record of every authenticated mutating request.

    Bodies are never stored, so passwords, tokens and personal data posted to
    forms do not leak into the audit trail.
    """

    tenant = models.ForeignKey(
        "saas.Tenant",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="request_audits",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="request_audits",
    )
    actor_email = models.EmailField(max_length=255, blank=True)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    status_code = models.PositiveIntegerField(default=0)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "request_audit"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]

    def __str__(self):
        return f"{self.method} {self.path} -> {self.status_code}"


__all__ = ['Setting', 'CompanySetting', 'Company', 'CompanyModule', 'Module', 'ModulePermission', 'Permission', 'Role', 'RolePermission', 'UserRole', 'UserPermission', 'UserModuleRole', 'Notification', 'EmailTemplate', 'EmailReportLog', 'SsoSetting', 'ApiKey', 'Webhook', 'WebhookLog', 'SavedReport', 'ReportSchedule', 'DocumentSequence', 'AuditLog', 'AuditDetail', 'UserActivityLog', 'UserSession', 'UserCompany', 'Attachment', 'Currency', 'ExchangeRate', 'ApprovalWorkflow', 'ApprovalRule', 'ApprovalTracking', 'MODULE_ACCESS_ROLES', 'ACCESS_SCOPES', 'EMAIL_IDENTITY_MODULES', 'ModuleAccessGrant', 'EmailIdentity', 'UserMFA', 'LoginAttempt', 'RequestAudit']
