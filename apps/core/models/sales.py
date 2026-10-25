"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class Quote(TenantScopedModel):
    """Legacy table: quotes"""
    id = models.AutoField(primary_key=True)
    quote_number = models.CharField(max_length=50)
    client_id = models.IntegerField(db_index=True)
    assigned_to = models.IntegerField(null=True, db_index=True)
    rfq_id = models.IntegerField(null=True, db_index=True)
    issue_date = models.DateField()
    valid_until = models.DateField(db_index=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('sent', 'Sent'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('expired', 'Expired'), ('converted', 'Converted')], null=True, blank=True, default='draft', db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    notes = models.TextField(null=True, blank=True)
    bank_account_id = models.IntegerField(null=True, db_index=True)
    terms_conditions = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True)
    approved_at = models.DateTimeField(null=True)
    approved_by = models.IntegerField(null=True, db_index=True)
    converted_to_invoice = models.IntegerField(null=True)

    class Meta:
        unique_together = (("tenant", "quote_number"),)
        db_table = "quotes"

    def __str__(self):
        return f"Quote {self.pk}"


class QuoteItem(TenantScopedModel):
    """Legacy table: quote_items"""
    id = models.AutoField(primary_key=True)
    quote_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "quote_items"

    def __str__(self):
        return f"QuoteItem {self.pk}"


class QuoteFollowup(TenantScopedModel):
    """Legacy table: quote_followups"""
    id = models.AutoField(primary_key=True)
    quote_id = models.IntegerField(null=True, db_index=True)
    scheduled_date = models.DateTimeField(null=True)
    followup_type = models.CharField(max_length=7, choices=[('email', 'Email'), ('call', 'Call'), ('meeting', 'Meeting')], null=True, blank=True)
    status = models.CharField(max_length=9, choices=[('pending', 'Pending'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "quote_followups"

    def __str__(self):
        return f"QuoteFollowup {self.pk}"


class QuoteMessage(TenantScopedModel):
    """Legacy table: quote_messages"""
    id = models.AutoField(primary_key=True)
    quote_id = models.IntegerField(db_index=True)
    user_id = models.IntegerField(db_index=True)
    message = models.TextField()
    attachment = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "quote_messages"

    def __str__(self):
        return f"QuoteMessage {self.pk}"


class Invoice(TenantScopedModel):
    """Legacy table: invoices"""
    id = models.AutoField(primary_key=True)
    invoice_number = models.CharField(max_length=50)
    client_id = models.IntegerField(db_index=True)
    quote_id = models.IntegerField(null=True, db_index=True)
    invoice_date = models.DateField()
    due_date = models.DateField(db_index=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('sent', 'Sent'), ('partial', 'Partial'), ('paid', 'Paid'), ('overdue', 'Overdue'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    notes = models.TextField(null=True, blank=True)
    bank_account_id = models.IntegerField(null=True)
    payment_terms = models.CharField(max_length=50, null=True, blank=True, default='Net 30')
    lpo_number = models.CharField(max_length=100, null=True, blank=True)
    created_by = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "invoice_number"),)
        db_table = "invoices"
        indexes = [
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self):
        return f"Invoice {self.pk}"


class InvoiceItem(TenantScopedModel):
    """Legacy table: invoice_items"""
    id = models.AutoField(primary_key=True)
    invoice_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField()
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "invoice_items"

    def __str__(self):
        return f"InvoiceItem {self.pk}"


class Payment(TenantScopedModel):
    """Legacy table: payments"""
    id = models.AutoField(primary_key=True)
    payment_number = models.CharField(max_length=50)
    invoice_id = models.IntegerField(db_index=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=13, choices=[('cash', 'Cash'), ('bank_transfer', 'Bank Transfer'), ('credit_card', 'Credit Card'), ('check', 'Check'), ('m-pesa', 'M-Pesa')])
    reference_number = models.CharField(max_length=100, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("tenant", "payment_number"),)
        db_table = "payments"

    def __str__(self):
        return f"Payment {self.pk}"


__all__ = ['Quote', 'QuoteItem', 'QuoteFollowup', 'QuoteMessage', 'Invoice', 'InvoiceItem', 'Payment']
