"""POS checkout engine: totals, credit invoices, refunds and cash-up."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import Invoice, InvoiceItem, Product, StockMovement
from apps.core.services import get_company_settings, next_sequence
from apps.core.tenancy import ALL_TENANTS, current_tenant
from apps.pos.models import (
    DEFAULT_PAYMENT_METHODS,
    PosPayment,
    PosPaymentMethod,
    PosRefund,
    PosRefundItem,
    PosSale,
    PosSaleItem,
    PosSession,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")


class PosError(Exception):
    """Raised when a sale or session action cannot be completed."""


def money(value) -> Decimal:
    return (value or ZERO).quantize(CENT)


def _decimal(value, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value).replace(",", "").strip() or "0")
    except (InvalidOperation, TypeError, ValueError):
        raise PosError(f"{field} is not a valid amount.")
    return parsed


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _int_setting(values: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(values.get(key) or default).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def pos_policy() -> dict:
    """Workspace POS rules, stored as editable company settings."""
    values = get_company_settings()
    try:
        max_discount = Decimal(
            str(values.get("pos_max_discount_percent") or "100").strip() or "100"
        )
    except InvalidOperation:
        max_discount = HUNDRED
    max_discount = max(ZERO, min(max_discount, HUNDRED))
    return {
        "receipt_footer": (values.get("pos_receipt_footer") or "").strip(),
        "max_discount_percent": max_discount,
        "refund_window_days": _int_setting(values, "pos_refund_window_days", 0, 0, 3650),
        "invoice_due_days": _int_setting(values, "invoice_due_days", 30, 0, 365),
    }


# ---------------------------------------------------------------------------
# Tender types
# ---------------------------------------------------------------------------
def sync_payment_methods() -> None:
    """Seed the default tenders once per workspace; admins own them afterwards."""
    tenant = current_tenant()
    if tenant is None or tenant is ALL_TENANTS:
        return
    if PosPaymentMethod.objects.exists():
        return
    for spec in DEFAULT_PAYMENT_METHODS:
        PosPaymentMethod.objects.get_or_create(code=spec["code"], defaults=spec)


def payment_methods(*, enabled_only=True) -> list[PosPaymentMethod]:
    sync_payment_methods()
    queryset = PosPaymentMethod.objects.all()
    if enabled_only:
        queryset = queryset.filter(enabled=True)
    return list(queryset.order_by("sort_order", "id"))


def method_map(*, enabled_only=False) -> dict[str, PosPaymentMethod]:
    return {row.code: row for row in payment_methods(enabled_only=enabled_only)}


def method_labels() -> dict[str, str]:
    return {row.code: row.label for row in payment_methods(enabled_only=False)}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _normalise_payments(payments) -> list[dict]:
    available = method_map(enabled_only=True)
    rows = []
    for raw in payments or []:
        method = (raw.get("method") or "").strip()
        tender = available.get(method)
        if tender is None:
            raise PosError("Choose an enabled payment method.")
        amount = money(_decimal(raw.get("amount"), "Payment amount"))
        if amount < ZERO:
            raise PosError("Payment amounts cannot be negative.")
        if amount == ZERO:
            continue
        reference = (raw.get("reference") or "").strip()[:120]
        if tender.requires_reference and not reference:
            raise PosError(f"Add the {tender.label} transaction reference.")
        rows.append(
            {
                "method": method,
                "amount": amount,
                "reference": reference,
                "tender": tender,
            }
        )
    if not rows:
        raise PosError("Add at least one payment.")
    return rows


def _normalise_items(items) -> dict[int, Decimal]:
    quantities: dict[int, Decimal] = {}
    for raw in items or []:
        try:
            product_id = int(raw.get("product_id"))
        except (TypeError, ValueError):
            raise PosError("One of the cart lines is invalid.")
        quantity = _decimal(raw.get("quantity"), "Quantity")
        if quantity <= ZERO:
            raise PosError("Quantities must be greater than zero.")
        quantities[product_id] = quantities.get(product_id, ZERO) + quantity
    if not quantities:
        raise PosError("The cart is empty.")
    return quantities


def _allocate_discount(lines, discount: Decimal) -> list[Decimal]:
    base = sum((line["line_total"] for line in lines), ZERO)
    if discount <= ZERO or base <= ZERO:
        return [ZERO for _ in lines]
    shares = []
    remaining = discount
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            share = remaining
        else:
            share = money(discount * line["line_total"] / base)
            remaining -= share
        shares.append(max(share, ZERO))
    return shares


def _queue(fn, document, actor_id: int) -> None:
    try:
        fn(document, actor_id=actor_id)
    except Exception:
        logger.exception("Could not queue %s for eTIMS", getattr(document, "pk", "?"))


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------
def create_sale(
    *,
    session: PosSession,
    cashier_id: int,
    items,
    payments,
    discount=ZERO,
    client=None,
    customer_name="",
    customer_phone="",
    notes="",
    sold_at=None,
    client_reference="",
) -> PosSale:
    if session.status != "open":
        raise PosError("This till session is closed. Open a new session to sell.")
    reference = (client_reference or "").strip()[:64]
    if reference:
        existing = PosSale.objects.filter(client_reference=reference).first()
        if existing is not None:
            return existing
    quantities = _normalise_items(items)
    payment_rows = _normalise_payments(payments)
    discount = money(_decimal(discount, "Discount"))
    if discount < ZERO:
        raise PosError("Discount cannot be negative.")
    sold_at = sold_at or timezone.now()
    policy = pos_policy()

    for attempt in range(2):
        try:
            with transaction.atomic():
                return _persist_sale(
                    session=session,
                    cashier_id=cashier_id,
                    quantities=quantities,
                    payment_rows=payment_rows,
                    discount=discount,
                    client=client,
                    customer_name=customer_name,
                    customer_phone=customer_phone,
                    notes=notes,
                    sold_at=sold_at,
                    reference=reference,
                    policy=policy,
                )
        except IntegrityError:
            if reference and attempt == 0:
                existing = PosSale.objects.filter(client_reference=reference).first()
                if existing is not None:
                    return existing
            raise
    raise PosError("The sale could not be completed.")


def _persist_sale(
    *,
    session,
    cashier_id,
    quantities,
    payment_rows,
    discount,
    client,
    customer_name,
    customer_phone,
    notes,
    sold_at,
    reference,
    policy,
) -> PosSale:
    products = {
        product.id: product
        for product in Product.objects.select_for_update().filter(id__in=quantities.keys())
    }
    if set(quantities) - set(products):
        raise PosError("One of the products in the cart is no longer available.")

    lines = []
    subtotal = ZERO
    for product_id, quantity in quantities.items():
        product = products[product_id]
        if product.status != "active":
            raise PosError(f"{product.name} is no longer available for sale.")
        if product.type != "service":
            on_hand = product.current_stock or ZERO
            if on_hand < quantity:
                raise PosError(
                    f"Only {on_hand:.2f} {product.unit or 'units'} of {product.name} remain in stock."
                )
        unit_price = money(product.unit_price or ZERO)
        line_total = money(unit_price * quantity)
        subtotal += line_total
        lines.append(
            {
                "product": product,
                "quantity": quantity,
                "unit_price": unit_price,
                "tax_rate": product.tax_rate or ZERO,
                "line_total": line_total,
            }
        )

    subtotal = money(subtotal)
    if discount > subtotal:
        raise PosError("Discount cannot exceed the value of the goods.")
    max_discount = money(subtotal * policy["max_discount_percent"] / HUNDRED)
    if discount > max_discount:
        raise PosError(
            f"Discounts are limited to {policy['max_discount_percent']:.2f}% of this sale."
        )

    shares = _allocate_discount(lines, discount)
    tax_amount = ZERO
    for line, share in zip(lines, shares):
        line["discount_share"] = share
        line["line_tax"] = money((line["line_total"] - share) * line["tax_rate"] / HUNDRED)
        tax_amount += line["line_tax"]
    tax_amount = money(tax_amount)
    total = money(subtotal - discount + tax_amount)

    paid = money(sum((row["amount"] for row in payment_rows), ZERO))
    if paid + CENT < total:
        raise PosError(f"Payments cover {paid:.2f}, but the sale total is {total:.2f}.")

    cash_paid = money(
        sum((row["amount"] for row in payment_rows if row["tender"].is_cash), ZERO)
    )
    credit_paid = money(
        sum((row["amount"] for row in payment_rows if row["tender"].is_credit), ZERO)
    )
    other_paid = money(paid - cash_paid - credit_paid)
    due_after_other = max(total - other_paid - credit_paid, ZERO)
    change = money(max(cash_paid - due_after_other, ZERO))

    if credit_paid > total:
        raise PosError("The on-account amount cannot exceed the sale total.")
    if credit_paid > ZERO and client is None:
        raise PosError("Select a customer before putting a sale on account.")

    sale = PosSale.objects.create(
        sale_number=next_sequence("POS", "pos_sales", "sale_number"),
        session=session,
        client=client,
        customer_name=(customer_name or "").strip()[:150],
        customer_phone=(customer_phone or "").strip()[:30],
        subtotal=subtotal,
        discount_amount=discount,
        tax_amount=tax_amount,
        total_amount=total,
        amount_paid=paid,
        change_amount=change,
        status="completed",
        cashier=cashier_id,
        client_reference=reference,
        notes=(notes or "").strip(),
        created_at=sold_at,
    )

    for line in lines:
        PosSaleItem.objects.create(
            sale=sale,
            product=line["product"],
            description=line["product"].name,
            quantity=line["quantity"],
            unit_price=line["unit_price"],
            tax_rate=line["tax_rate"],
            line_total=line["line_total"],
        )

    for row in payment_rows:
        PosPayment.objects.create(
            sale=sale,
            method=row["method"],
            amount=row["amount"],
            reference=row["reference"],
            created_at=sold_at,
        )

    for line in lines:
        product = line["product"]
        if product.type == "service":
            continue
        before = product.current_stock or ZERO
        after = before - line["quantity"]
        product.current_stock = after
        product.stock = int(after)
        product.updated_by = cashier_id
        product.save(update_fields=["current_stock", "stock", "updated_by", "updated_at"])
        StockMovement.objects.create(
            product_id=product.id,
            movement_type="sale",
            quantity=line["quantity"],
            quantity_before=before,
            quantity_after=after,
            reference_type="pos_sale",
            reference_id=sale.id,
            reference_number=sale.sale_number,
            unit_price=line["unit_price"],
            total_value=money(line["quantity"] * line["unit_price"]),
            notes=f"POS sale {sale.sale_number}",
            created_by=cashier_id,
            created_at=sold_at,
        )

    if credit_paid > ZERO:
        invoice = _create_credit_invoice(
            sale=sale,
            lines=lines,
            client=client,
            credit_paid=credit_paid,
            paid=paid,
            change=change,
            policy=policy,
            cashier_id=cashier_id,
            sold_at=sold_at,
        )
        sale.invoice = invoice
        sale.save(update_fields=["invoice"])

    from apps.core.etims import enqueue_sale

    _queue(enqueue_sale, sale, cashier_id)
    return sale


def _create_credit_invoice(
    *,
    sale,
    lines,
    client,
    credit_paid,
    paid,
    change,
    policy,
    cashier_id,
    sold_at,
) -> Invoice:
    invoice_paid = money(max(min(paid - change - credit_paid, sale.total_amount), ZERO))
    if invoice_paid + CENT >= sale.total_amount:
        status = "paid"
    elif invoice_paid > ZERO:
        status = "partial"
    else:
        status = "sent"
    local_day = timezone.localtime(sold_at).date()
    due_days = policy["invoice_due_days"]
    invoice = Invoice.objects.create(
        invoice_number=next_sequence("INV", "invoices", "invoice_number"),
        client_id=client.id,
        invoice_date=local_day,
        due_date=local_day + timedelta(days=due_days),
        status=status,
        subtotal=sale.subtotal,
        discount_amount=sale.discount_amount,
        tax_amount=sale.tax_amount,
        total_amount=sale.total_amount,
        paid_amount=invoice_paid,
        notes=f"POS sale {sale.sale_number} on {sale.session.terminal_name}.",
        payment_terms=f"Net {due_days}",
        created_by=cashier_id,
    )
    InvoiceItem.objects.bulk_create(
        [
            InvoiceItem(
                invoice_id=invoice.id,
                product_id=line["product"].id,
                description=line["product"].name,
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                discount=ZERO,
                total=line["line_total"],
            )
            for line in lines
        ]
    )
    return invoice


# ---------------------------------------------------------------------------
# Void and refund
# ---------------------------------------------------------------------------
def submitted_etims_for_sale(sale):
    from apps.core.models import EtimsSubmission

    return (
        EtimsSubmission.objects.filter(pos_sale=sale)
        .exclude(status="cancelled")
        .order_by("-id")
        .first()
    )


def void_sale(sale: PosSale, user_id: int, reason: str = "") -> PosSale:
    if sale.status == "voided":
        raise PosError("This sale has already been voided.")
    if sale.session.status == "closed":
        raise PosError("Sales from a closed shift cannot be voided. Reopen is not supported.")
    if sale.refunds.exists():
        raise PosError("This sale has refunds. Refund the remaining items instead of voiding.")
    if sale.invoice_id:
        raise PosError("This sale was invoiced on account. Cancel or credit the invoice instead.")
    filed = submitted_etims_for_sale(sale)
    if filed is not None and filed.status == "submitted":
        raise PosError(
            "This receipt has been accepted by KRA. Process a refund so a credit note is issued."
        )
    now = timezone.now()
    with transaction.atomic():
        for item in sale.items.select_related("product"):
            product = item.product
            if product is None or product.type == "service":
                continue
            before = product.current_stock or ZERO
            after = before + item.quantity
            product.current_stock = after
            product.stock = int(after)
            product.updated_by = user_id
            product.save(update_fields=["current_stock", "stock", "updated_by", "updated_at"])
            StockMovement.objects.create(
                product_id=product.id,
                movement_type="return_in",
                quantity=item.quantity,
                quantity_before=before,
                quantity_after=after,
                reference_type="pos_void",
                reference_id=sale.id,
                reference_number=sale.sale_number,
                unit_price=item.unit_price,
                total_value=money(item.quantity * item.unit_price),
                notes=f"Voided POS sale {sale.sale_number}",
                created_by=user_id,
                created_at=now,
            )
        sale.status = "voided"
        note = (reason or "").strip()
        if note:
            sale.notes = f"{sale.notes}\nVoid: {note}".strip() if sale.notes else f"Void: {note}"
        sale.save(update_fields=["status", "notes"])
        from apps.core.etims import cancel_pending_for_sale

        cancel_pending_for_sale(sale, reason=f"Sale voided: {note or 'no reason given'}")
    return sale


def refunded_quantities(sale) -> dict[int, Decimal]:
    rows = PosRefundItem.objects.filter(refund__sale=sale).values("sale_item_id").annotate(
        quantity=Sum("quantity")
    )
    return {row["sale_item_id"]: row["quantity"] for row in rows}


def refundable_items(sale) -> list[dict]:
    refunded = refunded_quantities(sale)
    rows = []
    for item in sale.items.select_related("product"):
        already = refunded.get(item.id, ZERO)
        remaining = max(item.quantity - already, ZERO)
        rows.append(
            {
                "item": item,
                "refunded": already,
                "remaining": remaining,
                "refundable": remaining > ZERO,
                "tracked": bool(item.product and item.product.type != "service"),
            }
        )
    return rows


def create_refund(
    *,
    sale: PosSale,
    session: PosSession,
    items,
    method: str,
    reference: str = "",
    reason: str = "",
    user_id: int,
    refunded_at=None,
) -> PosRefund:
    if sale.status != "completed":
        raise PosError("Only completed sales can be refunded.")
    if sale.invoice_id:
        raise PosError(
            "This sale was invoiced on account. Issue a credit note from the invoice instead."
        )
    if session.status != "open":
        raise PosError("Open a till session before processing a refund.")
    policy = pos_policy()
    if policy["refund_window_days"]:
        cutoff = timezone.now() - timedelta(days=policy["refund_window_days"])
        if sale.created_at < cutoff:
            raise PosError(
                f"Refunds are only allowed within {policy['refund_window_days']} days of the sale."
            )
    tender = method_map(enabled_only=True).get((method or "").strip())
    if tender is None:
        raise PosError("Choose an enabled refund method.")
    if tender.is_credit:
        raise PosError("Refunds cannot be paid back to a customer account from the till.")
    reference = (reference or "").strip()[:120]
    if tender.requires_reference and not reference:
        raise PosError(f"Add the {tender.label} refund reference.")
    reason = (reason or "").strip()[:250]
    if not reason:
        raise PosError("Record a reason for the refund.")

    requested = []
    for raw in items or []:
        try:
            sale_item_id = int(raw.get("sale_item_id"))
        except (TypeError, ValueError):
            raise PosError("One of the refund lines is invalid.")
        quantity = _decimal(raw.get("quantity"), "Refund quantity")
        if quantity <= ZERO:
            continue
        requested.append(
            {
                "sale_item_id": sale_item_id,
                "quantity": quantity,
                "restock": bool(raw.get("restock", True)),
            }
        )
    if not requested:
        raise PosError("Enter the quantity to refund on at least one line.")

    refunded_at = refunded_at or timezone.now()
    with transaction.atomic():
        locked = PosSale.objects.select_for_update().get(pk=sale.pk)
        if locked.invoice_id:
            raise PosError(
                "This sale was invoiced on account. Issue a credit note from the invoice instead."
            )
        sale_items = {
            item.id: item for item in locked.items.select_related("product")
        }
        refunded = refunded_quantities(locked)
        subtotal = ZERO
        tax_amount = ZERO
        discount_amount = ZERO
        lines = []
        for row in requested:
            item = sale_items.get(row["sale_item_id"])
            if item is None:
                raise PosError("One of the refund lines is not part of this sale.")
            remaining = max(item.quantity - refunded.get(item.id, ZERO), ZERO)
            if row["quantity"] > remaining:
                raise PosError(
                    f"Only {remaining:.2f} of {item.description} remain refundable."
                )
            share = item.quantity and (row["quantity"] / item.quantity) or ZERO
            line_subtotal = money(item.line_total * share)
            line_discount = ZERO
            if locked.subtotal and locked.discount_amount:
                line_discount = money(locked.discount_amount * line_subtotal / locked.subtotal)
            line_supply = money(line_subtotal - line_discount)
            line_tax = money(line_supply * (item.tax_rate or ZERO) / HUNDRED)
            subtotal += line_subtotal
            discount_amount += line_discount
            tax_amount += line_tax
            lines.append(
                {
                    "item": item,
                    "quantity": row["quantity"],
                    "restock": row["restock"],
                    "unit_price": item.unit_price,
                    "tax_rate": item.tax_rate or ZERO,
                    "line_total": line_subtotal,
                    "line_tax": line_tax,
                    "discount_share": line_discount,
                }
            )
        subtotal = money(subtotal)
        discount_amount = money(discount_amount)
        tax_amount = money(tax_amount)
        total = money(subtotal - discount_amount + tax_amount)
        refundable = max(money(locked.total_amount - locked.refunded_amount), ZERO)
        if total > refundable + CENT:
            total = refundable
        if total <= ZERO:
            raise PosError("There is nothing left to refund on this sale.")

        refund = PosRefund.objects.create(
            refund_number=next_sequence("RFD", "pos_refunds", "refund_number"),
            sale=locked,
            session=session,
            method=tender.code,
            reference=reference,
            reason=reason,
            subtotal=subtotal,
            discount_amount=discount_amount,
            tax_amount=tax_amount,
            total_amount=total,
            refunded_by=user_id,
            created_at=refunded_at,
        )
        PosRefundItem.objects.bulk_create(
            [
                PosRefundItem(
                    refund=refund,
                    sale_item=line["item"],
                    description=line["item"].description,
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    tax_rate=line["tax_rate"],
                    line_total=line["line_total"],
                    restock=line["restock"],
                )
                for line in lines
            ]
        )

        for line in lines:
            product = line["item"].product
            if not line["restock"] or product is None or product.type == "service":
                continue
            product = Product.objects.select_for_update().get(pk=product.pk)
            before = product.current_stock or ZERO
            after = before + line["quantity"]
            product.current_stock = after
            product.stock = int(after)
            product.updated_by = user_id
            product.save(update_fields=["current_stock", "stock", "updated_by", "updated_at"])
            StockMovement.objects.create(
                product_id=product.id,
                movement_type="return_in",
                quantity=line["quantity"],
                quantity_before=before,
                quantity_after=after,
                reference_type="pos_refund",
                reference_id=refund.id,
                reference_number=refund.refund_number,
                unit_price=line["unit_price"],
                total_value=line["line_total"],
                notes=f"Refund {refund.refund_number} of {locked.sale_number}",
                created_by=user_id,
                created_at=refunded_at,
            )

        locked.refunded_amount = money(locked.refunded_amount + total)
        locked.save(update_fields=["refunded_amount"])

    from apps.core.etims import enqueue_refund

    _queue(enqueue_refund, refund, user_id)
    return refund


# ---------------------------------------------------------------------------
# Cash-up
# ---------------------------------------------------------------------------
def session_summary(session: PosSession) -> dict:
    methods = method_map(enabled_only=False)
    used = set(methods)
    by_method = {code: ZERO for code in used}

    for row in PosPayment.objects.filter(
        sale__session=session, sale__status="completed"
    ).values("method", "amount"):
        code = row["method"]
        used.add(code)
        by_method[code] = by_method.get(code, ZERO) + row["amount"]

    refunds = list(
        PosRefund.objects.filter(session=session).values("method", "total_amount")
    )
    for row in refunds:
        code = row["method"]
        used.add(code)
        by_method[code] = by_method.get(code, ZERO) - row["total_amount"]

    sales = list(
        PosSale.objects.filter(session=session, status="completed").values(
            "subtotal",
            "discount_amount",
            "tax_amount",
            "total_amount",
            "amount_paid",
            "change_amount",
        )
    )
    cash_codes = {code for code, tender in methods.items() if tender.is_cash}
    change_total = money(sum((row["change_amount"] for row in sales), ZERO))
    for code in cash_codes:
        by_method[code] = by_method.get(code, ZERO) - change_total
    gross = money(sum((row["total_amount"] for row in sales), ZERO))
    refund_total = money(sum((row["total_amount"] for row in refunds), ZERO))
    cash = money(sum((by_method.get(code, ZERO) for code in cash_codes), ZERO))
    return {
        "count": len(sales),
        "subtotal": money(sum((row["subtotal"] for row in sales), ZERO)),
        "discounts": money(sum((row["discount_amount"] for row in sales), ZERO)),
        "tax": money(sum((row["tax_amount"] for row in sales), ZERO)),
        "gross": gross,
        "paid": money(sum((row["amount_paid"] for row in sales), ZERO)),
        "change": money(sum((row["change_amount"] for row in sales), ZERO)),
        "refund_count": len(refunds),
        "refunds": refund_total,
        "net": money(gross - refund_total),
        "by_method": {code: money(value) for code, value in by_method.items()},
        "cash": cash,
        "expected_cash": money((session.opening_float or ZERO) + cash),
    }
