"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class Product(TenantScopedModel):
    """Legacy table: products"""
    id = models.AutoField(primary_key=True)
    product_code = models.CharField(max_length=50)
    product_name = models.CharField(max_length=255, null=True, blank=True)
    sku = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    category_id = models.IntegerField(null=True)
    unit = models.CharField(max_length=20, null=True, blank=True, default='pcs')
    type = models.CharField(max_length=7, choices=[('product', 'Product'), ('service', 'Service')], null=True, blank=True, default='product')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    current_stock = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    minimum_stock = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    maximum_stock = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    reorder_level = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    reorder_quantity = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    weight = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    dimensions = models.CharField(max_length=100, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    stock = models.IntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_stock_take = models.DateField(null=True)
    created_by = models.IntegerField(null=True, db_index=True)
    updated_by = models.IntegerField(null=True, db_index=True)

    class Meta:
        unique_together = (
            ("tenant", "product_code"),
            ("tenant", "sku"),
        )
        db_table = "products"

    def __str__(self):
        return f"{self.product_code} · {self.name}"


class ProductCategory(TenantScopedModel):
    """Legacy table: product_categories"""
    id = models.AutoField(primary_key=True)
    category_code = models.CharField(max_length=50)
    category_name = models.CharField(max_length=100)
    parent_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "category_code"),)
        db_table = "product_categories"

    def __str__(self):
        return self.category_name


class Warehouse(TenantScopedModel):
    """Legacy table: warehouses"""
    id = models.AutoField(primary_key=True)
    warehouse_code = models.CharField(max_length=50)
    warehouse_name = models.CharField(max_length=100)
    address = models.TextField(null=True, blank=True)
    manager = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    is_default = models.SmallIntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "warehouse_code"),)
        db_table = "warehouses"

    def __str__(self):
        return self.warehouse_name


class StockAdjustment(TenantScopedModel):
    """Legacy table: stock_adjustments"""
    id = models.AutoField(primary_key=True)
    adjustment_number = models.CharField(max_length=50)
    adjustment_date = models.DateField()
    type = models.CharField(max_length=8, choices=[('increase', 'Increase'), ('decrease', 'Decrease')])
    reason = models.CharField(max_length=16, choices=[('damage', 'Damage'), ('theft', 'Theft'), ('expired', 'Expired'), ('count_correction', 'Count Correction'), ('return', 'Return'), ('other', 'Other')])
    notes = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('draft', 'Draft'), ('approved', 'Approved'), ('posted', 'Posted')], null=True, blank=True, default='draft')
    approved_by = models.IntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "adjustment_number"),)
        db_table = "stock_adjustments"

    def __str__(self):
        return f"StockAdjustment {self.pk}"


class StockAdjustmentItem(TenantScopedModel):
    """Legacy table: stock_adjustment_items"""
    id = models.AutoField(primary_key=True)
    adjustment_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(db_index=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_before = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=15, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "stock_adjustment_items"

    def __str__(self):
        return f"StockAdjustmentItem {self.pk}"


class StockLayer(TenantScopedModel):
    """Legacy table: stock_layers"""
    id = models.AutoField(primary_key=True)
    product_id = models.IntegerField(db_index=True)
    purchase_date = models.DateField(db_index=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    remaining_quantity = models.DecimalField(max_digits=15, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=15, decimal_places=2)
    total_cost = models.DecimalField(max_digits=15, decimal_places=2)
    reference_type = models.CharField(max_length=50, null=True, blank=True)
    reference_id = models.IntegerField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "stock_layers"

    def __str__(self):
        return f"StockLayer {self.pk}"


class StockMovement(TenantScopedModel):
    """Legacy table: stock_movements"""
    id = models.AutoField(primary_key=True)
    product_id = models.IntegerField(db_index=True)
    movement_type = models.CharField(max_length=14, choices=[('purchase', 'Purchase'), ('sale', 'Sale'), ('transfer_in', 'Transfer In'), ('transfer_out', 'Transfer Out'), ('adjustment_in', 'Adjustment In'), ('adjustment_out', 'Adjustment Out'), ('return_in', 'Return In'), ('return_out', 'Return Out')], db_index=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_before = models.DecimalField(max_digits=15, decimal_places=2)
    quantity_after = models.DecimalField(max_digits=15, decimal_places=2)
    reference_type = models.CharField(max_length=50, null=True, blank=True)
    reference_id = models.IntegerField(null=True)
    reference_number = models.CharField(max_length=100, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    notes = models.TextField(null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    created_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "stock_movements"
        indexes = [
            models.Index(fields=["reference_type", "reference_id"]),
            models.Index(fields=["movement_type", "created_at"]),
        ]

    def __str__(self):
        return f"StockMovement {self.pk}"


class StockTransfer(TenantScopedModel):
    """Legacy table: stock_transfers"""
    id = models.AutoField(primary_key=True)
    transfer_number = models.CharField(max_length=50)
    from_location = models.CharField(max_length=100, null=True, blank=True)
    to_location = models.CharField(max_length=100, null=True, blank=True)
    transfer_date = models.DateField()
    status = models.CharField(max_length=10, choices=[('draft', 'Draft'), ('pending', 'Pending'), ('in_transit', 'In Transit'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    notes = models.TextField(null=True, blank=True)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    completed_by = models.IntegerField(null=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "transfer_number"),)
        db_table = "stock_transfers"

    def __str__(self):
        return f"StockTransfer {self.pk}"


class StockTransferItem(TenantScopedModel):
    """Legacy table: stock_transfer_items"""
    id = models.AutoField(primary_key=True)
    transfer_id = models.IntegerField(db_index=True)
    product_id = models.IntegerField(db_index=True)
    quantity = models.DecimalField(max_digits=15, decimal_places=2)
    received_quantity = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    notes = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "stock_transfer_items"

    def __str__(self):
        return f"StockTransferItem {self.pk}"


__all__ = ['Product', 'ProductCategory', 'Warehouse', 'StockAdjustment', 'StockAdjustmentItem', 'StockLayer', 'StockMovement', 'StockTransfer', 'StockTransferItem']
