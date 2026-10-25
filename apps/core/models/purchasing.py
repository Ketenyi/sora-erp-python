"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class PurchaseOrder(TenantScopedModel):
    """Legacy table: purchase_orders"""
    id = models.AutoField(primary_key=True)
    po_number = models.CharField(max_length=50)
    vendor_id = models.IntegerField(db_index=True)
    order_date = models.DateField(db_index=True)
    expected_delivery = models.DateField(null=True)
    status = models.CharField(max_length=18, choices=[('draft', 'Draft'), ('sent', 'Sent'), ('confirmed', 'Confirmed'), ('partially_received', 'Partially Received'), ('fully_received', 'Fully Received'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, null=True, blank=True, default='KES')
    notes = models.TextField(null=True, blank=True)
    terms = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True)
    confirmed_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "po_number"),)
        db_table = "purchase_orders"

    def __str__(self):
        return f"PurchaseOrder {self.pk}"


class PurchaseOrderItem(TenantScopedModel):
    """Legacy table: purchase_order_items"""
    id = models.AutoField(primary_key=True)
    purchase_order_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    product_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    discount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=15, decimal_places=2)
    received_quantity = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    created_at = models.DateTimeField()

    class Meta:
        db_table = "purchase_order_items"

    def __str__(self):
        return f"PurchaseOrderItem {self.pk}"


class GoodsReceipt(TenantScopedModel):
    """Legacy table: goods_receipts"""
    id = models.AutoField(primary_key=True)
    grn_number = models.CharField(max_length=50)
    purchase_order_id = models.IntegerField(db_index=True)
    vendor_id = models.IntegerField(db_index=True)
    receipt_date = models.DateField()
    invoice_number = models.CharField(max_length=50, null=True, blank=True)
    delivery_note = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft')
    notes = models.TextField(null=True, blank=True)
    received_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "grn_number"),)
        db_table = "goods_receipts"

    def __str__(self):
        return f"GoodsReceipt {self.pk}"


class GoodsReceiptItem(TenantScopedModel):
    """Legacy table: goods_receipt_items"""
    id = models.AutoField(primary_key=True)
    goods_receipt_id = models.IntegerField(db_index=True)
    purchase_order_item_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    quantity_received = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_accepted = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_rejected = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    rejection_reason = models.TextField(null=True, blank=True)
    batch_number = models.CharField(max_length=50, null=True, blank=True)
    expiry_date = models.DateField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "goods_receipt_items"

    def __str__(self):
        return f"GoodsReceiptItem {self.pk}"


class SupplierBill(TenantScopedModel):
    """Legacy table: supplier_bills"""
    id = models.AutoField(primary_key=True)
    bill_number = models.CharField(max_length=50)
    vendor_id = models.IntegerField(db_index=True)
    purchase_order_id = models.IntegerField(null=True, db_index=True)
    goods_receipt_id = models.IntegerField(null=True, db_index=True)
    bill_date = models.DateField()
    due_date = models.DateField(db_index=True)
    reference = models.CharField(max_length=100, null=True, blank=True)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=15, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    status = models.CharField(max_length=14, choices=[('draft', 'Draft'), ('received', 'Received'), ('approved', 'Approved'), ('paid', 'Paid'), ('partially_paid', 'Partially Paid'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField()
    approved_by = models.IntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    paid_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "bill_number"),)
        db_table = "supplier_bills"

    def __str__(self):
        return f"SupplierBill {self.pk}"


class SupplierBillItem(TenantScopedModel):
    """Legacy table: supplier_bill_items"""
    id = models.AutoField(primary_key=True)
    supplier_bill_id = models.IntegerField(db_index=True)
    purchase_order_item_id = models.IntegerField(null=True, db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    discount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total = models.DecimalField(max_digits=15, decimal_places=2)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "supplier_bill_items"

    def __str__(self):
        return f"SupplierBillItem {self.pk}"


class SupplierPayment(TenantScopedModel):
    """Legacy table: supplier_payments"""
    id = models.AutoField(primary_key=True)
    payment_number = models.CharField(max_length=50)
    vendor_id = models.IntegerField(db_index=True)
    supplier_bill_id = models.IntegerField(null=True, db_index=True)
    payment_date = models.DateField()
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    payment_method = models.CharField(max_length=13, choices=[('cash', 'Cash'), ('bank_transfer', 'Bank Transfer'), ('cheque', 'Cheque'), ('mobile_money', 'Mobile Money'), ('credit_card', 'Credit Card')])
    bank_account_id = models.IntegerField(null=True, db_index=True)
    reference = models.CharField(max_length=100, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "payment_number"),)
        db_table = "supplier_payments"

    def __str__(self):
        return f"SupplierPayment {self.pk}"


class RequestForQuotation(TenantScopedModel):
    """Legacy table: request_for_quotations"""
    id = models.AutoField(primary_key=True)
    rfq_number = models.CharField(max_length=50)
    title = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    required_date = models.DateField(null=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('sent', 'Sent'), ('received', 'Received'), ('closed', 'Closed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "rfq_number"),)
        db_table = "request_for_quotations"

    def __str__(self):
        return f"RequestForQuotation {self.pk}"


class Rfq(TenantScopedModel):
    """Legacy table: rfqs"""
    id = models.AutoField(primary_key=True)
    rfq_number = models.CharField(max_length=50)
    client_id = models.IntegerField(db_index=True)
    issue_date = models.DateField()
    required_by = models.DateField()
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('sent', 'Sent'), ('quoted', 'Quoted'), ('closed', 'Closed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    description = models.TextField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("tenant", "rfq_number"),)
        db_table = "rfqs"

    def __str__(self):
        return f"Rfq {self.pk}"


class RfqItem(TenantScopedModel):
    """Legacy table: rfq_items"""
    id = models.AutoField(primary_key=True)
    rfq_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_of_measure = models.CharField(max_length=20, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rfq_items"

    def __str__(self):
        return f"RfqItem {self.pk}"


__all__ = ['PurchaseOrder', 'PurchaseOrderItem', 'GoodsReceipt', 'GoodsReceiptItem', 'SupplierBill', 'SupplierBillItem', 'SupplierPayment', 'RequestForQuotation', 'Rfq', 'RfqItem']
