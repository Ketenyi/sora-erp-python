"""Point-of-sale terminals, sessions, sales and split payments."""

from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel

ZERO = Decimal("0.00")


def money_value(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


DEFAULT_PAYMENT_METHODS = [
    {
        "code": "cash",
        "label": "Cash",
        "etims_code": "01",
        "is_cash": True,
        "is_credit": False,
        "requires_reference": False,
        "sort_order": 10,
    },
    {
        "code": "m-pesa",
        "label": "M-Pesa",
        "etims_code": "06",
        "is_cash": False,
        "is_credit": False,
        "requires_reference": True,
        "sort_order": 20,
    },
    {
        "code": "card",
        "label": "Card",
        "etims_code": "05",
        "is_cash": False,
        "is_credit": False,
        "requires_reference": True,
        "sort_order": 30,
    },
    {
        "code": "bank_transfer",
        "label": "Bank Transfer",
        "etims_code": "04",
        "is_cash": False,
        "is_credit": False,
        "requires_reference": True,
        "sort_order": 40,
    },
    {
        "code": "credit",
        "label": "On Account",
        "etims_code": "02",
        "is_cash": False,
        "is_credit": True,
        "requires_reference": False,
        "sort_order": 50,
    },
]

PAYMENT_METHODS = [(row["code"], row["label"]) for row in DEFAULT_PAYMENT_METHODS]


class PosPaymentMethod(TenantScopedModel):
    """Workspace-configurable tender types, including the KRA payment code."""

    code = models.CharField(max_length=32)
    label = models.CharField(max_length=64)
    etims_code = models.CharField(max_length=4, blank=True)
    is_cash = models.BooleanField(default=False)
    is_credit = models.BooleanField(default=False)
    requires_reference = models.BooleanField(default=False)
    enabled = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = "pos_payment_methods"
        unique_together = (("tenant", "code"),)
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.label


class PosSession(TenantScopedModel):
    session_number = models.CharField(max_length=50)
    terminal_name = models.CharField(max_length=100, default="Main Terminal")
    opened_by = models.IntegerField()
    opened_at = models.DateTimeField(default=timezone.now)
    opening_float = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    closed_by = models.IntegerField(null=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_float = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    expected_cash = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    cash_variance = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    status = models.CharField(
        max_length=8,
        choices=[("open", "Open"), ("closed", "Closed")],
        default="open",
        db_index=True,
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "pos_sessions"
        unique_together = (("tenant", "session_number"),)
        ordering = ["-opened_at", "-id"]

    def __str__(self):
        return f"{self.session_number} · {self.terminal_name}"

    @property
    def is_open(self):
        return self.status == "open"


class PosSale(TenantScopedModel):
    sale_number = models.CharField(max_length=50)
    session = models.ForeignKey(PosSession, on_delete=models.PROTECT, related_name="sales")
    client = models.ForeignKey(
        "core.Client", null=True, blank=True, on_delete=models.SET_NULL, related_name="pos_sales"
    )
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=30, blank=True)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    change_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    status = models.CharField(
        max_length=9,
        choices=[("completed", "Completed"), ("voided", "Voided")],
        default="completed",
        db_index=True,
    )
    cashier = models.IntegerField()
    invoice = models.ForeignKey(
        "core.Invoice", null=True, blank=True, on_delete=models.SET_NULL, related_name="pos_sales"
    )
    client_reference = models.CharField(max_length=64, blank=True, default="")
    refunded_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "pos_sales"
        unique_together = (("tenant", "sale_number"),)
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["session", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "client_reference"],
                condition=~models.Q(client_reference=""),
                name="uniq_pos_sale_client_reference",
            ),
        ]

    def __str__(self):
        return f"{self.sale_number} · {self.total_amount}"

    @property
    def item_count(self):
        return sum(item.quantity for item in self.items.all())

    @property
    def refundable_amount(self):
        return max(money_value(self.total_amount) - money_value(self.refunded_amount), ZERO)

    @property
    def refund_status(self):
        if money_value(self.refunded_amount) <= ZERO:
            return "none"
        if money_value(self.refunded_amount) + Decimal("0.01") >= money_value(self.total_amount):
            return "full"
        return "partial"


class PosSaleItem(TenantScopedModel):
    sale = models.ForeignKey(PosSale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        "core.Product", null=True, blank=True, on_delete=models.SET_NULL, related_name="pos_sale_items"
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    line_total = models.DecimalField(max_digits=15, decimal_places=2)

    class Meta:
        db_table = "pos_sale_items"
        ordering = ["id"]

    def __str__(self):
        return self.description


class PosPayment(TenantScopedModel):
    sale = models.ForeignKey(PosSale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=32)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    reference = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "pos_payments"
        ordering = ["id"]

    def __str__(self):
        return f"{self.method} {self.amount}"


class PosRefund(TenantScopedModel):
    refund_number = models.CharField(max_length=50)
    sale = models.ForeignKey(PosSale, on_delete=models.PROTECT, related_name="refunds")
    session = models.ForeignKey(PosSession, on_delete=models.PROTECT, related_name="refunds")
    method = models.CharField(max_length=32)
    reference = models.CharField(max_length=120, blank=True)
    reason = models.CharField(max_length=250, blank=True)
    subtotal = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=ZERO)
    refunded_by = models.IntegerField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "pos_refunds"
        unique_together = (("tenant", "refund_number"),)
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.refund_number} · {self.total_amount}"


class PosRefundItem(TenantScopedModel):
    refund = models.ForeignKey(PosRefund, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(PosSaleItem, on_delete=models.PROTECT, related_name="refund_items")
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    line_total = models.DecimalField(max_digits=15, decimal_places=2)
    restock = models.BooleanField(default=True)

    class Meta:
        db_table = "pos_refund_items"
        ordering = ["id"]

    def __str__(self):
        return self.description


__all__ = [
    "DEFAULT_PAYMENT_METHODS",
    "PAYMENT_METHODS",
    "PosPayment",
    "PosPaymentMethod",
    "PosRefund",
    "PosRefundItem",
    "PosSale",
    "PosSaleItem",
    "PosSession",
]
