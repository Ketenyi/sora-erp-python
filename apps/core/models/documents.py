"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class DocumentAiCategory(TenantScopedModel):
    """Legacy table: document_ai_categories"""
    id = models.AutoField(primary_key=True)
    category_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_ai_categories"

    def __str__(self):
        return f"DocumentAiCategory {self.pk}"


class DocumentApproval(TenantScopedModel):
    """Legacy table: document_approvals"""
    id = models.AutoField(primary_key=True)
    workflow_instance_id = models.IntegerField(db_index=True)
    step_number = models.IntegerField()
    approver_id = models.IntegerField(db_index=True)
    status = models.CharField(max_length=30, null=True, blank=True, default='pending')
    comments = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True)
    rejected_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "document_approvals"

    def __str__(self):
        return f"DocumentApproval {self.pk}"


class DocumentAuditLog(TenantScopedModel):
    """Legacy table: document_audit_log"""
    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(db_index=True)
    action = models.CharField(max_length=50)
    details = models.TextField(null=True, blank=True)
    user_id = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_audit_log"

    def __str__(self):
        return f"DocumentAuditLog {self.pk}"


class DocumentCategory(TenantScopedModel):
    """Legacy table: document_categories"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    parent_id = models.IntegerField(null=True)
    color = models.CharField(max_length=7, null=True, blank=True)

    class Meta:
        db_table = "document_categories"

    def __str__(self):
        return f"DocumentCategory {self.pk}"


class DocumentShare(TenantScopedModel):
    """Legacy table: document_shares"""
    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(db_index=True)
    share_token = models.CharField(max_length=100, unique=True)
    shared_with_type = models.CharField(max_length=50, null=True, blank=True)
    shared_with_id = models.IntegerField(null=True)
    permission = models.CharField(max_length=30, null=True, blank=True, default='view')
    expires_at = models.DateTimeField()
    revoked = models.BooleanField(null=True, default=0)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "document_shares"

    def __str__(self):
        return f"DocumentShare {self.pk}"


class DocumentTag(TenantScopedModel):
    """Legacy table: document_tags"""
    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(db_index=True)
    tag_name = models.CharField(max_length=100)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_tags"

    def __str__(self):
        return f"DocumentTag {self.pk}"


class DocumentTemplate(TenantScopedModel):
    """Legacy table: document_templates"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=150)
    template_type = models.CharField(max_length=50, null=True, blank=True, default='contract')
    content = models.TextField(null=True, blank=True)
    variables = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(null=True, default=1)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_templates"

    def __str__(self):
        return f"DocumentTemplate {self.pk}"


class DocumentVersion(TenantScopedModel):
    """Legacy table: document_versions"""
    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(db_index=True)
    file_name = models.CharField(max_length=255)
    file_path = models.CharField(max_length=500)
    file_size = models.IntegerField(null=True)
    mime_type = models.CharField(max_length=100, null=True, blank=True)
    version_number = models.IntegerField(default=1)
    version_note = models.TextField(null=True, blank=True)
    uploaded_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_versions"

    def __str__(self):
        return f"DocumentVersion {self.pk}"


class DocumentWorkflow(TenantScopedModel):
    """Legacy table: document_workflows"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=150)
    description = models.TextField(null=True, blank=True)
    steps = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_workflows"

    def __str__(self):
        return f"DocumentWorkflow {self.pk}"


class DocumentWorkflowInstance(TenantScopedModel):
    """Legacy table: document_workflow_instances"""
    id = models.AutoField(primary_key=True)
    document_id = models.IntegerField(db_index=True)
    workflow_id = models.IntegerField()
    current_step = models.IntegerField(null=True, default=0)
    status = models.CharField(max_length=30, null=True, blank=True, default='in_progress')
    initiated_by = models.IntegerField(null=True)
    initiated_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "document_workflow_instances"

    def __str__(self):
        return f"DocumentWorkflowInstance {self.pk}"


class GeneratedDocument(TenantScopedModel):
    """Legacy table: generated_documents"""
    id = models.AutoField(primary_key=True)
    template_id = models.IntegerField(db_index=True)
    document_id = models.IntegerField(db_index=True)
    generated_data = models.TextField(null=True, blank=True)
    generated_by = models.IntegerField(null=True)
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "generated_documents"

    def __str__(self):
        return f"GeneratedDocument {self.pk}"


__all__ = ['DocumentAiCategory', 'DocumentApproval', 'DocumentAuditLog', 'DocumentCategory', 'DocumentShare', 'DocumentTag', 'DocumentTemplate', 'DocumentVersion', 'DocumentWorkflow', 'DocumentWorkflowInstance', 'GeneratedDocument']
