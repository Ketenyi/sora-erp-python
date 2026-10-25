"""KRA eTIMS transmission queue for POS receipts and tax invoices."""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel

STATUSES = [
    ("pending", "Pending"),
    ("processing", "Sending"),
    ("submitted", "Accepted by KRA"),
    ("failed", "Failed"),
    ("rejected", "Rejected by KRA"),
    ("cancelled", "Cancelled"),
]

DOCUMENT_TYPES = [
    ("pos_sale", "POS receipt"),
    ("credit_note", "Credit note"),
    ("invoice", "Tax invoice"),
]


class EtimsSubmission(TenantScopedModel):
    document_type = models.CharField(max_length=16, choices=DOCUMENT_TYPES)
    document_number = models.CharField(max_length=50)
    pos_sale = models.ForeignKey(
        "pos.PosSale",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="etims_submissions",
    )
    pos_refund = models.ForeignKey(
        "pos.PosRefund",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="etims_submissions",
    )
    etims_invoice_no = models.BigIntegerField(null=True, blank=True)
    invoice = models.ForeignKey(
        "core.Invoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="etims_submissions",
    )
    status = models.CharField(max_length=12, choices=STATUSES, default="pending", db_index=True)
    attempts = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    kra_receipt_number = models.CharField(max_length=50, blank=True)
    kra_internal_data = models.TextField(blank=True)
    kra_receipt_signature = models.TextField(blank=True)
    kra_qr_url = models.TextField(blank=True)
    response_code = models.CharField(max_length=20, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    response = models.JSONField(default=dict, blank=True)
    created_by = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "etims_submissions"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "document_type", "document_number"],
                name="uniq_etims_submission_document",
            ),
            models.UniqueConstraint(
                fields=["tenant", "etims_invoice_no"],
                condition=models.Q(etims_invoice_no__isnull=False),
                name="uniq_etims_invoice_number",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"]),
        ]

    def __str__(self):
        return f"{self.document_number} · {self.status}"

    @property
    def is_open(self):
        return self.status in {"pending", "processing", "failed"}


__all__ = ["DOCUMENT_TYPES", "EtimsSubmission", "STATUSES"]
