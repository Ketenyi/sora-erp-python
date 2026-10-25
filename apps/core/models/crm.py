"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class Client(TenantScopedModel):
    """Legacy table: clients"""
    id = models.AutoField(primary_key=True)
    client_code = models.CharField(max_length=50)
    company_name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, null=True, blank=True)
    email = models.CharField(max_length=255, db_index=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    tax_id = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "client_code"),)
        db_table = "clients"

    def __str__(self):
        return f"Client {self.pk}"


class Vendor(TenantScopedModel):
    """Legacy table: vendors"""
    id = models.AutoField(primary_key=True)
    vendor_code = models.CharField(max_length=50)
    company_name = models.CharField(max_length=255, db_index=True)
    contact_person = models.CharField(max_length=255, null=True, blank=True)
    email = models.CharField(max_length=255, null=True, blank=True)
    phone = models.CharField(max_length=50, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True, default='Kenya')
    tax_id = models.CharField(max_length=100, null=True, blank=True)
    payment_terms = models.IntegerField(null=True, default=30)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('deleted', 'Deleted')], null=True, blank=True, default='active', db_index=True)
    created_by = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(null=True)
    deleted_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "vendor_code"),)
        db_table = "vendors"

    def __str__(self):
        return self.company_name


class VendorQuotation(TenantScopedModel):
    """Legacy table: vendor_quotations"""
    id = models.AutoField(primary_key=True)
    rfq_id = models.IntegerField(db_index=True)
    vendor_id = models.IntegerField(db_index=True)
    quotation_number = models.CharField(max_length=50, null=True, blank=True)
    quotation_date = models.DateField()
    valid_until = models.DateField(null=True)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')], null=True, blank=True, default='pending')
    created_at = models.DateTimeField()

    class Meta:
        db_table = "vendor_quotations"

    def __str__(self):
        return f"VendorQuotation {self.pk}"


class VendorQuotationItem(TenantScopedModel):
    """Legacy table: vendor_quotation_items"""
    id = models.AutoField(primary_key=True)
    vendor_quotation_id = models.IntegerField(db_index=True)
    rfq_item_id = models.IntegerField(db_index=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    total = models.DecimalField(max_digits=15, decimal_places=2)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "vendor_quotation_items"

    def __str__(self):
        return f"VendorQuotationItem {self.pk}"


__all__ = ['Client', 'Vendor', 'VendorQuotation', 'VendorQuotationItem']
