"""Purchasing module: purchase orders, receipts, bills, payments and RFQs."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import (
    BankAccount,
    ChartOfAccount,
    GoodsReceipt,
    GoodsReceiptItem,
    JournalEntry,
    JournalEntryLine,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    RequestForQuotation,
    RfqItem,
    StockMovement,
    SupplierBill,
    SupplierBillItem,
    SupplierPayment,
    Vendor,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_code, to_decimal
from apps.core.ui import csv_response, paginate
from apps.purchasing.forms import (
    GoodsReceiptForm,
    PurchaseOrderForm,
    PurchaseOrderItemFormSet,
    RequestForQuotationForm,
    RfqItemFormSet,
    SupplierBillForm,
    SupplierBillItemFormSet,
    SupplierPaymentForm,
)

ZERO = Decimal("0.00")
PAGE_SIZE = 15
PO_STATUSES = [
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("confirmed", "Confirmed"),
    ("partially_received", "Partially Received"),
    ("fully_received", "Fully Received"),
    ("cancelled", "Cancelled"),
]
BILL_STATUSES = [
    ("received", "Received"),
    ("approved", "Approved"),
    ("partially_paid", "Partially Paid"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
]
PAYMENT_METHODS = [
    ("bank_transfer", "Bank Transfer"),
    ("cash", "Cash"),
    ("cheque", "Cheque"),
    ("mobile_money", "Mobile Money"),
    ("credit_card", "Credit Card"),
]


def _money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


def _vendor_names(ids) -> dict[int, str]:
    return {
        row["id"]: row["company_name"]
        for row in Vendor.objects.filter(id__in=ids).values("id", "company_name")
    }


def _product_names(ids) -> dict[int, Product]:
    return {p.id: p for p in Product.objects.filter(id__in=ids)}


def _po_numbers(ids) -> dict[int, str]:
    return {
        row["id"]: row["po_number"]
        for row in PurchaseOrder.objects.filter(id__in=ids).values("id", "po_number")
    }


def _grn_numbers(ids) -> dict[int, str]:
    return {
        row["id"]: row["grn_number"]
        for row in GoodsReceipt.objects.filter(id__in=ids).values("id", "grn_number")
    }


def _line_values(quantity, unit_price, discount, tax_rate):
    gross = (_money(quantity) * _money(unit_price)) - _money(discount)
    if gross < 0:
        gross = ZERO
    tax = _money(gross * (_money(tax_rate) / Decimal("100")))
    return _money(gross), tax, _money(gross + tax)


def _collect_po_items(formset):
    items = []
    for form in formset:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        product = form.cleaned_data.get("product")
        name = (form.cleaned_data.get("product_name") or "").strip()
        if not product and not name:
            continue
        quantity = form.cleaned_data.get("quantity")
        price = form.cleaned_data.get("unit_price")
        if not quantity or price is None:
            continue
        discount = form.cleaned_data.get("discount") or ZERO
        tax_rate = form.cleaned_data.get("tax_rate") or ZERO
        gross, tax, total = _line_values(quantity, price, discount, tax_rate)
        items.append(
            {
                "product_id": product.id if product else None,
                "product_name": name or (product.name if product else ""),
                "description": (form.cleaned_data.get("description") or "").strip(),
                "quantity": quantity,
                "unit_price": price,
                "discount": discount,
                "tax_rate": tax_rate,
                "tax_amount": tax,
                "total": total,
                "_gross": gross,
            }
        )
    return items


def _collect_bill_items(formset):
    items = []
    for form in formset:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        product = form.cleaned_data.get("product")
        description = (form.cleaned_data.get("description") or "").strip()
        if not product and not description:
            continue
        quantity = form.cleaned_data.get("quantity")
        price = form.cleaned_data.get("unit_price")
        if not quantity or price is None:
            continue
        discount = form.cleaned_data.get("discount") or ZERO
        tax_rate = form.cleaned_data.get("tax_rate") or ZERO
        gross, tax, total = _line_values(quantity, price, discount, tax_rate)
        items.append(
            {
                "product_id": product.id if product else None,
                "description": description or (product.name if product else ""),
                "quantity": quantity,
                "unit_price": price,
                "discount": discount,
                "tax_rate": tax_rate,
                "tax_amount": tax,
                "total": total,
                "_gross": gross,
            }
        )
    return items


def _totals(items):
    subtotal = sum((item["_gross"] for item in items), ZERO)
    tax = sum((item["tax_amount"] for item in items), ZERO)
    return _money(subtotal), _money(tax), _money(subtotal + tax)


def _products_json():
    rows = []
    for product in Product.objects.filter(status="active").order_by("name"):
        rows.append(
            {
                "id": product.id,
                "name": product.name,
                "code": product.product_code,
                "price": float(product.unit_price or 0),
                "cost": float(product.cost_price or 0),
            }
        )
    return json.dumps(rows)


def _post_journal(entry_number, entry_date, description, reference, lines, user_id):
    """Post a balanced journal entry and update account balances.

    ``lines`` is a list of ``(account_code, debit, credit, memo)`` tuples.
    Returns None when the chart of accounts is not configured.
    """
    debit_total = sum((line[1] for line in lines), ZERO)
    credit_total = sum((line[2] for line in lines), ZERO)
    if _money(debit_total) != _money(credit_total) or debit_total <= 0:
        return None
    codes = {line[0] for line in lines}
    accounts = {
        account.account_code: account
        for account in ChartOfAccount.objects.filter(account_code__in=codes)
    }
    if len(accounts) != len(codes):
        return None
    if JournalEntry.objects.filter(entry_number=entry_number).exists():
        return None
    now = timezone.now()
    entry = JournalEntry.objects.create(
        entry_number=entry_number,
        entry_date=entry_date,
        description=description,
        reference=reference,
        status="posted",
        created_by=user_id,
        created_at=now,
        posted_at=now,
        posted_by=user_id,
    )
    for account_code, debit, credit, memo in lines:
        account = accounts[account_code]
        if debit:
            JournalEntryLine.objects.create(
                journal_entry_id=entry.id,
                account_id=account.id,
                entry_type="debit",
                amount=_money(debit),
                description=memo,
                created_at=now,
            )
            account.current_balance = (account.current_balance or ZERO) + _money(debit)
        if credit:
            JournalEntryLine.objects.create(
                journal_entry_id=entry.id,
                account_id=account.id,
                entry_type="credit",
                amount=_money(credit),
                description=memo,
                created_at=now,
            )
            account.current_balance = (account.current_balance or ZERO) - _money(credit)
        account.save(update_fields=["current_balance"])
    return entry


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def dashboard(request):
    today = timezone.localdate()
    bills_open = SupplierBill.objects.filter(
        status__in=["received", "approved", "partially_paid"]
    )
    outstanding = sum(
        ((bill.total_amount or ZERO) - (bill.paid_amount or ZERO) for bill in bills_open),
        ZERO,
    )
    total_bills = SupplierBill.objects.count()
    stats = {
        "purchase_orders": PurchaseOrder.objects.count(),
        "pending_orders": PurchaseOrder.objects.filter(
            status__in=["draft", "sent", "confirmed"]
        ).count(),
        "bills": total_bills,
        "bills_received": SupplierBill.objects.filter(status="received").count(),
        "outstanding": _money(outstanding),
        "spent": _money(
            sum(
                (bill.total_amount or ZERO for bill in SupplierBill.objects.filter(status="paid")),
                ZERO,
            )
        ),
        "vendors": Vendor.objects.filter(status="active").count(),
    }

    recent_orders = list(
        PurchaseOrder.objects.order_by("-created_at", "-id")[:5]
    )
    vendors = _vendor_names({po.vendor_id for po in recent_orders})
    for po in recent_orders:
        po.vendor_name = vendors.get(po.vendor_id, "-")

    buckets = [
        {"label": "Current", "total": ZERO, "count": 0},
        {"label": "1-30 Days", "total": ZERO, "count": 0},
        {"label": "31-60 Days", "total": ZERO, "count": 0},
        {"label": "60+ Days", "total": ZERO, "count": 0},
    ]
    aging_bills = SupplierBill.objects.filter(
        status__in=["approved", "partially_paid"]
    ).order_by("due_date")
    for bill in aging_bills:
        balance = (bill.total_amount or ZERO) - (bill.paid_amount or ZERO)
        if balance <= 0:
            continue
        days = (today - bill.due_date).days if bill.due_date else 0
        if days <= 0:
            index = 0
        elif days <= 30:
            index = 1
        elif days <= 60:
            index = 2
        else:
            index = 3
        buckets[index]["total"] += balance
        buckets[index]["count"] += 1

    return render(
        request,
        "purchasing/dashboard.html",
        {
            "page_title": "Purchasing Dashboard - SORA ERP",
            "stats": stats,
            "recent_orders": recent_orders,
            "aging_buckets": buckets,
        },
    )


# ---------------------------------------------------------------------------
# Purchase orders
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def purchase_orders(request):
    access = get_access(request.user)
    queryset = PurchaseOrder.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        vendor_ids = Vendor.objects.filter(
            company_name__icontains=query
        ).values("id")
        queryset = queryset.filter(
            Q(po_number__icontains=query) | Q(vendor_id__in=vendor_ids)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    vendors = _vendor_names({po.vendor_id for po in page_obj.object_list})
    counts = {
        row["purchase_order_id"]: row["total"]
        for row in PurchaseOrderItem.objects.values("purchase_order_id").annotate(
            total=Count("id")
        )
    }
    for po in page_obj.object_list:
        po.vendor_name = vendors.get(po.vendor_id, "-")
        po.item_count = counts.get(po.id, 0)
    return render(
        request,
        "purchasing/purchase_orders.html",
        {
            "page_title": "Purchase Orders - SORA ERP",
            "page_obj": page_obj,
            "orders": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "status_choices": PO_STATUSES,
            "can_create": access.can("purchasing", "create"),
        },
    )


@login_required
@module_access_required("purchasing")
def purchase_order_create(request):
    access = get_access(request.user)
    if not access.can("purchasing", "create"):
        messages.error(request, "You do not have permission to create purchase orders.")
        return redirect("/purchasing/purchase-orders")
    if request.method == "POST":
        form = PurchaseOrderForm(request.POST)
        formset = PurchaseOrderItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = _collect_po_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the purchase order.")
            else:
                subtotal, tax, total = _totals(items)
                with transaction.atomic():
                    po = PurchaseOrder.objects.create(
                        po_number=next_code(
                            f"PO-{timezone.localdate().year}",
                            "purchase_orders",
                            "po_number",
                            width=5,
                        ),
                        vendor_id=form.cleaned_data["vendor"].id,
                        order_date=form.cleaned_data["order_date"],
                        expected_delivery=form.cleaned_data.get("expected_delivery"),
                        status="draft",
                        subtotal=subtotal,
                        tax_amount=tax,
                        total_amount=total,
                        currency="KES",
                        notes=form.cleaned_data.get("notes") or "",
                        terms=form.cleaned_data.get("terms") or "",
                        created_by=request.user.id,
                        created_at=timezone.now(),
                    )
                    now = timezone.now()
                    PurchaseOrderItem.objects.bulk_create(
                        [
                            PurchaseOrderItem(
                                purchase_order_id=po.id,
                                product_id=item["product_id"],
                                product_name=item["product_name"],
                                description=item["description"],
                                quantity=item["quantity"],
                                unit_price=item["unit_price"],
                                discount=item["discount"],
                                tax_rate=item["tax_rate"],
                                tax_amount=item["tax_amount"],
                                total=item["total"],
                                received_quantity=ZERO,
                                created_at=now,
                            )
                            for item in items
                        ]
                    )
                messages.success(request, f"Purchase order {po.po_number} created.")
                return redirect(f"/purchasing/purchase-orders/view/{po.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = PurchaseOrderForm()
        formset = PurchaseOrderItemFormSet(prefix="items")
    return render(
        request,
        "purchasing/purchase_order_form.html",
        {
            "page_title": "Create Purchase Order - SORA ERP",
            "form": form,
            "formset": formset,
            "is_edit": False,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("purchasing")
def purchase_order_edit(request, pk):
    access = get_access(request.user)
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if not access.can("purchasing", "edit"):
        messages.error(request, "You do not have permission to edit purchase orders.")
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    if po.status != "draft":
        messages.error(request, "Only draft purchase orders can be edited.")
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    if request.method == "POST":
        form = PurchaseOrderForm(request.POST, instance=po)
        formset = PurchaseOrderItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = _collect_po_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the purchase order.")
            else:
                subtotal, tax, total = _totals(items)
                with transaction.atomic():
                    po.vendor_id = form.cleaned_data["vendor"].id
                    po.order_date = form.cleaned_data["order_date"]
                    po.expected_delivery = form.cleaned_data.get("expected_delivery")
                    po.subtotal = subtotal
                    po.tax_amount = tax
                    po.total_amount = total
                    po.notes = form.cleaned_data.get("notes") or ""
                    po.terms = form.cleaned_data.get("terms") or ""
                    po.updated_at = timezone.now()
                    po.save()
                    PurchaseOrderItem.objects.filter(purchase_order_id=po.id).delete()
                    now = timezone.now()
                    PurchaseOrderItem.objects.bulk_create(
                        [
                            PurchaseOrderItem(
                                purchase_order_id=po.id,
                                product_id=item["product_id"],
                                product_name=item["product_name"],
                                description=item["description"],
                                quantity=item["quantity"],
                                unit_price=item["unit_price"],
                                discount=item["discount"],
                                tax_rate=item["tax_rate"],
                                tax_amount=item["tax_amount"],
                                total=item["total"],
                                received_quantity=ZERO,
                                created_at=now,
                            )
                            for item in items
                        ]
                    )
                messages.success(request, "Purchase order updated.")
                return redirect(f"/purchasing/purchase-orders/view/{po.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = PurchaseOrderForm(instance=po)
        form.fields["vendor"].initial = po.vendor_id
        initial = [
            {
                "product": item.product_id,
                "product_name": item.product_name,
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "discount": item.discount,
                "tax_rate": item.tax_rate,
            }
            for item in PurchaseOrderItem.objects.filter(
                purchase_order_id=po.id
            ).order_by("id")
        ]
        formset = PurchaseOrderItemFormSet(initial=initial, prefix="items")
    return render(
        request,
        "purchasing/purchase_order_form.html",
        {
            "page_title": "Edit Purchase Order - SORA ERP",
            "form": form,
            "formset": formset,
            "order": po,
            "is_edit": True,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("purchasing")
def purchase_order_view(request, pk):
    access = get_access(request.user)
    po = get_object_or_404(PurchaseOrder, pk=pk)
    vendor = Vendor.objects.filter(pk=po.vendor_id).first()
    items = list(
        PurchaseOrderItem.objects.filter(purchase_order_id=po.id).order_by("id")
    )
    for item in items:
        item.remaining = max((item.quantity or ZERO) - (item.received_quantity or ZERO), ZERO)
    receipts = list(
        GoodsReceipt.objects.filter(purchase_order_id=po.id).order_by("-created_at")
    )
    bills = list(
        SupplierBill.objects.filter(purchase_order_id=po.id).order_by("-created_at")
    )
    for bill in bills:
        bill.balance = _money((bill.total_amount or ZERO) - (bill.paid_amount or ZERO))
    return render(
        request,
        "purchasing/purchase_order_view.html",
        {
            "page_title": f"Purchase Order {po.po_number}",
            "order": po,
            "vendor": vendor,
            "items": items,
            "receipts": receipts,
            "bills": bills,
            "can_edit": po.status == "draft" and access.can("purchasing", "edit"),
            "can_send": po.status == "draft"
            and access.can("purchasing", "approve"),
            "can_confirm": po.status == "sent"
            and access.can("purchasing", "approve"),
            "can_receive": po.status in {"confirmed", "partially_received"}
            and access.can("purchasing", "create"),
            "can_delete": po.status == "draft"
            and access.can("purchasing", "delete"),
        },
    )


@login_required
@module_access_required("purchasing")
def purchase_order_send(request, pk):
    access = get_access(request.user)
    if not access.can("purchasing", "approve"):
        messages.error(request, "You do not have permission to send purchase orders.")
        return redirect(f"/purchasing/purchase-orders/view/{pk}")
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    if po.status != "draft":
        messages.error(request, "Only draft purchase orders can be sent.")
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    po.status = "sent"
    po.sent_at = timezone.now()
    po.updated_at = timezone.now()
    po.save(update_fields=["status", "sent_at", "updated_at"])
    messages.success(request, f"Purchase order {po.po_number} marked as sent.")
    return redirect(f"/purchasing/purchase-orders/view/{po.id}")


@login_required
@module_access_required("purchasing")
def purchase_order_confirm(request, pk):
    access = get_access(request.user)
    if not access.can("purchasing", "approve"):
        messages.error(request, "You do not have permission to confirm purchase orders.")
        return redirect(f"/purchasing/purchase-orders/view/{pk}")
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    if po.status != "sent":
        messages.error(request, "Only sent purchase orders can be confirmed.")
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    po.status = "confirmed"
    po.confirmed_at = timezone.now()
    po.updated_at = timezone.now()
    po.save(update_fields=["status", "confirmed_at", "updated_at"])
    messages.success(request, f"Purchase order {po.po_number} confirmed.")
    return redirect(f"/purchasing/purchase-orders/view/{po.id}")


@login_required
@module_access_required("purchasing")
def purchase_order_delete(request, pk):
    access = get_access(request.user)
    if not access.can("purchasing", "delete"):
        messages.error(request, "You do not have permission to delete purchase orders.")
        return redirect(f"/purchasing/purchase-orders/view/{pk}")
    po = get_object_or_404(PurchaseOrder, pk=pk)
    if request.method != "POST":
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    if po.status != "draft":
        messages.error(request, "Only draft purchase orders can be deleted.")
        return redirect(f"/purchasing/purchase-orders/view/{po.id}")
    with transaction.atomic():
        PurchaseOrderItem.objects.filter(purchase_order_id=po.id).delete()
        po.delete()
    messages.success(request, "Draft purchase order deleted.")
    return redirect("/purchasing/purchase-orders")


# ---------------------------------------------------------------------------
# Goods receipts
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def goods_receipts(request):
    access = get_access(request.user)
    queryset = GoodsReceipt.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        queryset = queryset.filter(
            Q(grn_number__icontains=query)
            | Q(invoice_number__icontains=query)
            | Q(delivery_note__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    vendors = _vendor_names({grn.vendor_id for grn in page_obj.object_list})
    pos = _po_numbers({grn.purchase_order_id for grn in page_obj.object_list})
    for grn in page_obj.object_list:
        grn.vendor_name = vendors.get(grn.vendor_id, "-")
        grn.po_number = pos.get(grn.purchase_order_id, "-")
    return render(
        request,
        "purchasing/goods_receipts.html",
        {
            "page_title": "Goods Receipts - SORA ERP",
            "page_obj": page_obj,
            "receipts": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "can_create": access.can("purchasing", "create"),
        },
    )


def _eligible_pos():
    return PurchaseOrder.objects.filter(
        status__in=["confirmed", "partially_received"]
    ).order_by("-created_at")


@login_required
@module_access_required("purchasing")
def goods_receipt_create(request):
    access = get_access(request.user)
    if not access.can("purchasing", "create"):
        messages.error(request, "You do not have permission to receive goods.")
        return redirect("/purchasing/goods-receipts")
    eligible = _eligible_pos()
    po_id = request.POST.get("purchase_order_id") or request.GET.get("po_id") or ""
    po = None
    if po_id:
        po = eligible.filter(pk=po_id).first()
    form = GoodsReceiptForm(request.POST or None)
    vendor_map = _vendor_names({row.vendor_id for row in eligible})
    for row in eligible:
        row.vendor_name = vendor_map.get(row.vendor_id, "")
    if po:
        po.vendor_name = vendor_map.get(po.vendor_id, "")
    po_rows = [
        {"id": row.id, "label": f"{row.po_number} · {row.vendor_name or 'Unknown supplier'}"}
        for row in eligible
    ]

    po_items = []
    if po:
        po_items = list(
            PurchaseOrderItem.objects.filter(purchase_order_id=po.id).order_by("id")
        )
        for item in po_items:
            remaining = max(
                (item.quantity or ZERO) - (item.received_quantity or ZERO), ZERO
            )
            item.remaining = remaining

    if request.method == "POST":
        if not po:
            messages.error(request, "Select a confirmed purchase order to receive against.")
        elif form.is_valid():
            ids = request.POST.getlist("po_item_id")
            received_values = request.POST.getlist("quantity_received")
            accepted_values = request.POST.getlist("quantity_accepted")
            rejected_values = request.POST.getlist("quantity_rejected")
            reasons = request.POST.getlist("rejection_reason")
            item_map = {str(item.id): item for item in po_items}
            rows = []
            errors = []
            for index, item_id in enumerate(ids):
                item = item_map.get(str(item_id))
                if not item:
                    continue
                received = to_decimal(
                    received_values[index] if index < len(received_values) else "0"
                )
                if received <= 0:
                    continue
                accepted = to_decimal(
                    accepted_values[index] if index < len(accepted_values) else "0"
                )
                rejected = to_decimal(
                    rejected_values[index] if index < len(rejected_values) else "0"
                )
                remaining = max(
                    (item.quantity or ZERO) - (item.received_quantity or ZERO), ZERO
                )
                label = item.product_name or f"item #{item.id}"
                if accepted < 0 or rejected < 0:
                    errors.append(f"{label}: quantities cannot be negative.")
                if accepted + rejected != received:
                    errors.append(
                        f"{label}: accepted plus rejected must equal received."
                    )
                if received > remaining:
                    errors.append(
                        f"{label}: received {received} exceeds remaining {remaining}."
                    )
                rows.append(
                    {
                        "item": item,
                        "received": received,
                        "accepted": accepted,
                        "rejected": rejected,
                        "reason": reasons[index] if index < len(reasons) else "",
                    }
                )
            if not rows and not errors:
                errors.append("Enter received quantities for at least one item.")
            if errors:
                for error in errors:
                    messages.error(request, error)
            else:
                with transaction.atomic():
                    now = timezone.now()
                    grn = GoodsReceipt.objects.create(
                        grn_number=next_code(
                            f"GRN-{timezone.localdate().year}",
                            "goods_receipts",
                            "grn_number",
                            width=5,
                        ),
                        purchase_order_id=po.id,
                        vendor_id=po.vendor_id,
                        receipt_date=form.cleaned_data["receipt_date"],
                        invoice_number=form.cleaned_data.get("invoice_number") or "",
                        delivery_note=form.cleaned_data.get("delivery_note") or "",
                        status="confirmed",
                        notes=form.cleaned_data.get("notes") or "",
                        received_by=request.user.id,
                        created_at=now,
                        confirmed_at=now,
                    )
                    for row in rows:
                        item = row["item"]
                        GoodsReceiptItem.objects.create(
                            goods_receipt_id=grn.id,
                            purchase_order_item_id=item.id,
                            product_id=item.product_id,
                            quantity_received=row["received"],
                            quantity_accepted=row["accepted"],
                            quantity_rejected=row["rejected"],
                            rejection_reason=row["reason"],
                            created_at=now,
                        )
                        item.received_quantity = (item.received_quantity or ZERO) + row[
                            "accepted"
                        ]
                        item.save(update_fields=["received_quantity"])
                        if item.product_id and row["accepted"] > 0:
                            product = Product.objects.select_for_update().filter(
                                pk=item.product_id
                            ).first()
                            if product:
                                before = product.current_stock or ZERO
                                after = before + row["accepted"]
                                product.current_stock = after
                                product.stock = int(after)
                                product.updated_by = request.user.id
                                product.save()
                                StockMovement.objects.create(
                                    product_id=product.id,
                                    movement_type="purchase",
                                    quantity=row["accepted"],
                                    quantity_before=before,
                                    quantity_after=after,
                                    reference_type="goods_receipt",
                                    reference_id=grn.id,
                                    reference_number=grn.grn_number,
                                    unit_price=item.unit_price,
                                    total_value=_money(row["accepted"] * item.unit_price),
                                    notes=f"Received on {grn.grn_number}",
                                    created_by=request.user.id,
                                    created_at=now,
                                )
                    ordered = sum(
                        (
                            value
                            for value in PurchaseOrderItem.objects.filter(
                                purchase_order_id=po.id
                            ).values_list("quantity", flat=True)
                        ),
                        ZERO,
                    )
                    received = sum(
                        (
                            value
                            for value in PurchaseOrderItem.objects.filter(
                                purchase_order_id=po.id
                            ).values_list("received_quantity", flat=True)
                        ),
                        ZERO,
                    )
                    if received >= ordered:
                        po.status = "fully_received"
                    elif received > 0:
                        po.status = "partially_received"
                    po.updated_at = now
                    po.save(update_fields=["status", "updated_at"])
                messages.success(request, f"Goods receipt {grn.grn_number} recorded.")
                return redirect(f"/purchasing/goods-receipts/view/{grn.id}")
        else:
            messages.error(request, "Please correct the errors below.")

    return render(
        request,
        "purchasing/goods_receipt_form.html",
        {
            "page_title": "Receive Goods - SORA ERP",
            "form": form,
            "pos": po_rows,
            "selected_po": po,
            "po_items": po_items,
            "selected_po_id": str(po.id) if po else "",
        },
    )


@login_required
@module_access_required("purchasing")
def goods_receipt_view(request, pk):
    grn = get_object_or_404(GoodsReceipt, pk=pk)
    vendor = Vendor.objects.filter(pk=grn.vendor_id).first()
    po = PurchaseOrder.objects.filter(pk=grn.purchase_order_id).first()
    items = list(
        GoodsReceiptItem.objects.filter(goods_receipt_id=grn.id).order_by("id")
    )
    po_items = {
        item.id: item
        for item in PurchaseOrderItem.objects.filter(
            id__in=[row.purchase_order_item_id for row in items]
        )
    }
    total_value = ZERO
    for row in items:
        source = po_items.get(row.purchase_order_item_id)
        row.product_name = source.product_name if source else "-"
        row.description = source.description if source else ""
        row.ordered_quantity = source.quantity if source else ZERO
        row.unit_price = source.unit_price if source else ZERO
        row.line_value = _money((row.quantity_accepted or ZERO) * (row.unit_price or ZERO))
        total_value += row.line_value
    return render(
        request,
        "purchasing/goods_receipt_view.html",
        {
            "page_title": f"Goods Receipt {grn.grn_number}",
            "receipt": grn,
            "vendor": vendor,
            "order": po,
            "items": items,
            "total_value": _money(total_value),
        },
    )


# ---------------------------------------------------------------------------
# Supplier bills
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def supplier_bills(request):
    access = get_access(request.user)
    queryset = SupplierBill.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        vendor_ids = Vendor.objects.filter(
            company_name__icontains=query
        ).values("id")
        queryset = queryset.filter(
            Q(bill_number__icontains=query)
            | Q(reference__icontains=query)
            | Q(vendor_id__in=vendor_ids)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    vendors = _vendor_names({bill.vendor_id for bill in page_obj.object_list})
    pos = _po_numbers(
        {bill.purchase_order_id for bill in page_obj.object_list if bill.purchase_order_id}
    )
    grns = _grn_numbers(
        {bill.goods_receipt_id for bill in page_obj.object_list if bill.goods_receipt_id}
    )
    for bill in page_obj.object_list:
        bill.vendor_name = vendors.get(bill.vendor_id, "-")
        bill.po_number = pos.get(bill.purchase_order_id, "")
        bill.grn_number = grns.get(bill.goods_receipt_id, "")
        bill.balance = _money((bill.total_amount or ZERO) - (bill.paid_amount or ZERO))
    return render(
        request,
        "purchasing/supplier_bills.html",
        {
            "page_title": "Supplier Bills - SORA ERP",
            "page_obj": page_obj,
            "bills": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "status_choices": BILL_STATUSES,
            "can_create": access.can("purchasing", "create"),
        },
    )


def _bill_initial_from_po(po):
    initial = []
    for item in PurchaseOrderItem.objects.filter(purchase_order_id=po.id).order_by("id"):
        quantity = item.received_quantity if (item.received_quantity or ZERO) > 0 else item.quantity
        initial.append(
            {
                "product": item.product_id,
                "description": item.product_name,
                "quantity": quantity,
                "unit_price": item.unit_price,
                "discount": item.discount,
                "tax_rate": item.tax_rate,
            }
        )
    return initial


def _bill_initial_from_grn(grn):
    initial = []
    rows = GoodsReceiptItem.objects.filter(goods_receipt_id=grn.id).order_by("id")
    po_items = {
        item.id: item
        for item in PurchaseOrderItem.objects.filter(
            id__in=[row.purchase_order_item_id for row in rows]
        )
    }
    for row in rows:
        source = po_items.get(row.purchase_order_item_id)
        initial.append(
            {
                "product": row.product_id,
                "description": source.product_name if source else "Received goods",
                "quantity": row.quantity_accepted,
                "unit_price": source.unit_price if source else ZERO,
                "discount": ZERO,
                "tax_rate": source.tax_rate if source else ZERO,
            }
        )
    return initial


@login_required
@module_access_required("purchasing")
def supplier_bill_create(request):
    access = get_access(request.user)
    if not access.can("purchasing", "create"):
        messages.error(request, "You do not have permission to create supplier bills.")
        return redirect("/purchasing/supplier-bills")

    po_id = request.POST.get("purchase_order_id") or request.GET.get("po_id") or ""
    grn_id = request.POST.get("goods_receipt_id") or request.GET.get("grn_id") or ""
    po = PurchaseOrder.objects.filter(pk=po_id).first() if po_id else None
    grn = GoodsReceipt.objects.filter(pk=grn_id).first() if grn_id else None
    vendor = None
    initial = []
    if grn:
        po = PurchaseOrder.objects.filter(pk=grn.purchase_order_id).first()
        vendor = Vendor.objects.filter(pk=grn.vendor_id).first()
        initial = _bill_initial_from_grn(grn)
    elif po:
        vendor = Vendor.objects.filter(pk=po.vendor_id).first()
        initial = _bill_initial_from_po(po)
    initial_vendor = vendor.id if vendor else None

    if request.method == "POST":
        form = SupplierBillForm(request.POST)
        formset = SupplierBillItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = _collect_bill_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to the bill.")
            else:
                subtotal, tax, total = _totals(items)
                bill_vendor = form.cleaned_data["vendor"]
                linked_po = po if po and po.vendor_id == bill_vendor.id else None
                linked_grn = (
                    grn
                    if grn and grn.vendor_id == bill_vendor.id and linked_po
                    else None
                )
                bill_number = (form.cleaned_data.get("bill_number") or "").strip()
                if not bill_number:
                    bill_number = next_code(
                        f"SB-{timezone.localdate().year}",
                        "supplier_bills",
                        "bill_number",
                        width=5,
                    )
                with transaction.atomic():
                    now = timezone.now()
                    bill = SupplierBill.objects.create(
                        bill_number=bill_number,
                        vendor_id=bill_vendor.id,
                        purchase_order_id=linked_po.id if linked_po else None,
                        goods_receipt_id=linked_grn.id if linked_grn else None,
                        bill_date=form.cleaned_data["bill_date"],
                        due_date=form.cleaned_data["due_date"],
                        reference=form.cleaned_data.get("reference") or "",
                        subtotal=subtotal,
                        tax_amount=tax,
                        total_amount=total,
                        paid_amount=ZERO,
                        status="received",
                        notes=form.cleaned_data.get("notes") or "",
                        created_by=request.user.id,
                        created_at=now,
                    )
                    SupplierBillItem.objects.bulk_create(
                        [
                            SupplierBillItem(
                                supplier_bill_id=bill.id,
                                purchase_order_item_id=None,
                                product_id=item["product_id"],
                                description=item["description"],
                                quantity=item["quantity"],
                                unit_price=item["unit_price"],
                                discount=item["discount"],
                                tax_rate=item["tax_rate"],
                                tax_amount=item["tax_amount"],
                                total=item["total"],
                                created_at=now,
                            )
                            for item in items
                        ]
                    )
                    journal_lines = [
                        ("5000", subtotal, ZERO, f"Supplier bill {bill_number}")
                    ]
                    if tax > 0:
                        journal_lines.append(
                            ("2100", tax, ZERO, f"VAT on bill {bill_number}")
                        )
                    journal_lines.append(
                        ("2000", ZERO, total, f"Supplier bill {bill_number}")
                    )
                    _post_journal(
                        f"AP-{bill_number}",
                        bill.bill_date,
                        f"Supplier Bill: {bill_number}",
                        bill_number,
                        journal_lines,
                        request.user.id,
                    )
                messages.success(request, f"Supplier bill {bill.bill_number} recorded.")
                return redirect(f"/purchasing/supplier-bills/view/{bill.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = SupplierBillForm(initial={"vendor": initial_vendor} if initial_vendor else None)
        formset = SupplierBillItemFormSet(initial=initial, prefix="items")

    return render(
        request,
        "purchasing/supplier_bill_form.html",
        {
            "page_title": "Create Supplier Bill - SORA ERP",
            "form": form,
            "formset": formset,
            "po": po,
            "grn": grn,
            "po_id": po.id if po else "",
            "grn_id": grn.id if grn else "",
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("purchasing")
def supplier_bill_view(request, pk):
    access = get_access(request.user)
    bill = get_object_or_404(SupplierBill, pk=pk)
    vendor = Vendor.objects.filter(pk=bill.vendor_id).first()
    po = PurchaseOrder.objects.filter(pk=bill.purchase_order_id).first() if bill.purchase_order_id else None
    grn = GoodsReceipt.objects.filter(pk=bill.goods_receipt_id).first() if bill.goods_receipt_id else None
    items = list(SupplierBillItem.objects.filter(supplier_bill_id=bill.id).order_by("id"))
    payments = list(
        SupplierPayment.objects.filter(supplier_bill_id=bill.id).order_by("-payment_date")
    )
    balance = _money((bill.total_amount or ZERO) - (bill.paid_amount or ZERO))
    return render(
        request,
        "purchasing/supplier_bill_view.html",
        {
            "page_title": f"Supplier Bill {bill.bill_number}",
            "bill": bill,
            "vendor": vendor,
            "order": po,
            "receipt": grn,
            "items": items,
            "payments": payments,
            "balance": balance,
            "can_approve": bill.status == "received"
            and access.can("purchasing", "approve"),
            "can_pay": bill.status in {"approved", "partially_paid"}
            and balance > 0
            and access.can("purchasing", "create"),
        },
    )


@login_required
@module_access_required("purchasing")
def supplier_bill_approve(request, pk):
    access = get_access(request.user)
    if not access.can("purchasing", "approve"):
        messages.error(request, "You do not have permission to approve supplier bills.")
        return redirect(f"/purchasing/supplier-bills/view/{pk}")
    bill = get_object_or_404(SupplierBill, pk=pk)
    if request.method != "POST":
        return redirect(f"/purchasing/supplier-bills/view/{bill.id}")
    if bill.status != "received":
        messages.error(request, "Only received bills can be approved.")
        return redirect(f"/purchasing/supplier-bills/view/{bill.id}")
    bill.status = "approved"
    bill.approved_by = request.user.id
    bill.approved_at = timezone.now()
    bill.save(update_fields=["status", "approved_by", "approved_at"])
    messages.success(request, f"Supplier bill {bill.bill_number} approved.")
    return redirect(f"/purchasing/supplier-bills/view/{bill.id}")


# ---------------------------------------------------------------------------
# Supplier payments
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def supplier_payments(request):
    access = get_access(request.user)
    queryset = SupplierPayment.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    if query:
        vendor_ids = Vendor.objects.filter(
            company_name__icontains=query
        ).values("id")
        queryset = queryset.filter(
            Q(payment_number__icontains=query)
            | Q(reference__icontains=query)
            | Q(vendor_id__in=vendor_ids)
        )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    vendors = _vendor_names({payment.vendor_id for payment in page_obj.object_list})
    bill_numbers = {
        row["id"]: row["bill_number"]
        for row in SupplierBill.objects.filter(
            id__in={
                payment.supplier_bill_id
                for payment in page_obj.object_list
                if payment.supplier_bill_id
            }
        ).values("id", "bill_number")
    }
    for payment in page_obj.object_list:
        payment.vendor_name = vendors.get(payment.vendor_id, "-")
        payment.bill_number = bill_numbers.get(payment.supplier_bill_id, "")
    return render(
        request,
        "purchasing/supplier_payments.html",
        {
            "page_title": "Supplier Payments - SORA ERP",
            "page_obj": page_obj,
            "payments": page_obj.object_list,
            "search_query": query,
            "can_create": access.can("purchasing", "create"),
        },
    )


@login_required
@module_access_required("purchasing")
def supplier_payment_create(request):
    access = get_access(request.user)
    if not access.can("purchasing", "create"):
        messages.error(request, "You do not have permission to record payments.")
        return redirect("/purchasing/supplier-payments")
    bill_id = request.GET.get("bill_id") or request.POST.get("bill_initial") or None
    bill = (
        SupplierBill.objects.filter(
            pk=bill_id, status__in=["approved", "partially_paid"]
        ).first()
        if bill_id
        else None
    )
    if request.method == "POST":
        form = SupplierPaymentForm(request.POST)
        formset = None
        if form.is_valid():
            bill = form.cleaned_data.get("supplier_bill")
            vendor = form.cleaned_data["vendor"]
            amount = form.cleaned_data["amount"]
            with transaction.atomic():
                now = timezone.now()
                payment = SupplierPayment.objects.create(
                    payment_number=next_code(
                        f"SP-{timezone.localdate().year}",
                        "supplier_payments",
                        "payment_number",
                        width=5,
                    ),
                    vendor_id=vendor.id,
                    supplier_bill_id=bill.id if bill else None,
                    payment_date=form.cleaned_data["payment_date"],
                    amount=amount,
                    payment_method=form.cleaned_data["payment_method"],
                    bank_account_id=form.cleaned_data["bank_account"].id
                    if form.cleaned_data.get("bank_account")
                    else None,
                    reference=form.cleaned_data.get("reference") or "",
                    notes=form.cleaned_data.get("notes") or "",
                    created_by=request.user.id,
                    created_at=now,
                )
                if bill:
                    paid = (bill.paid_amount or ZERO) + amount
                    bill.paid_amount = paid
                    if paid >= (bill.total_amount or ZERO):
                        bill.status = "paid"
                        bill.paid_at = now
                    else:
                        bill.status = "partially_paid"
                    bill.save(
                        update_fields=["paid_amount", "status", "paid_at"]
                    )
                _post_journal(
                    f"PMT-{payment.payment_number}",
                    payment.payment_date,
                    f"Supplier Payment: {payment.payment_number}",
                    bill.bill_number if bill else "",
                    [
                        ("2000", amount, ZERO, f"Payment {payment.payment_number}"),
                        ("1000", ZERO, amount, f"Payment {payment.payment_number}"),
                    ],
                    request.user.id,
                )
            messages.success(
                request, f"Supplier payment {payment.payment_number} recorded."
            )
            return redirect(f"/purchasing/supplier-payments/view/{payment.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = SupplierPaymentForm(
            initial={"supplier_bill": bill.id, "vendor": bill.vendor_id}
            if bill
            else None,
            bill_id=bill.id if bill else None,
        )
    return render(
        request,
        "purchasing/supplier_payment_form.html",
        {
            "page_title": "Record Supplier Payment - SORA ERP",
            "form": form,
            "bill": bill,
            "balance": _money((bill.total_amount or ZERO) - (bill.paid_amount or ZERO))
            if bill
            else ZERO,
        },
    )


@login_required
@module_access_required("purchasing")
def supplier_payment_view(request, pk):
    payment = get_object_or_404(SupplierPayment, pk=pk)
    vendor = Vendor.objects.filter(pk=payment.vendor_id).first()
    bill = (
        SupplierBill.objects.filter(pk=payment.supplier_bill_id).first()
        if payment.supplier_bill_id
        else None
    )
    bank = (
        BankAccount.objects.filter(pk=payment.bank_account_id).first()
        if payment.bank_account_id
        else None
    )
    return render(
        request,
        "purchasing/supplier_payment_view.html",
        {
            "page_title": f"Supplier Payment {payment.payment_number}",
            "payment": payment,
            "vendor": vendor,
            "bill": bill,
            "bank": bank,
        },
    )


# ---------------------------------------------------------------------------
# Request for quotations
# ---------------------------------------------------------------------------
@login_required
@module_access_required("purchasing")
def rfq_list(request):
    access = get_access(request.user)
    queryset = RequestForQuotation.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    if query:
        queryset = queryset.filter(
            Q(rfq_number__icontains=query) | Q(title__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    counts = {
        row["rfq_id"]: row["total"]
        for row in RfqItem.objects.values("rfq_id").annotate(total=Count("id"))
    }
    for rfq in page_obj.object_list:
        rfq.item_count = counts.get(rfq.id, 0)
    return render(
        request,
        "purchasing/rfq.html",
        {
            "page_title": "Requests for Quotation - SORA ERP",
            "page_obj": page_obj,
            "rfqs": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "can_create": access.can("purchasing", "create"),
        },
    )


@login_required
@module_access_required("purchasing")
def rfq_create(request):
    access = get_access(request.user)
    if not access.can("purchasing", "create"):
        messages.error(request, "You do not have permission to create RFQs.")
        return redirect("/purchasing/rfq")
    if request.method == "POST":
        form = RequestForQuotationForm(request.POST)
        formset = RfqItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            rows = []
            for line in formset:
                if not hasattr(line, "cleaned_data") or not line.cleaned_data:
                    continue
                product = line.cleaned_data.get("product")
                description = (line.cleaned_data.get("description") or "").strip()
                quantity = line.cleaned_data.get("quantity")
                if not product and not description:
                    continue
                if not quantity:
                    continue
                rows.append(
                    {
                        "product_id": product.id if product else None,
                        "description": description or product.name,
                        "quantity": quantity,
                        "unit_of_measure": line.cleaned_data.get("unit_of_measure")
                        or "pcs",
                    }
                )
            if not rows:
                form.add_error(None, "Add at least one item to the RFQ.")
            else:
                with transaction.atomic():
                    rfq = RequestForQuotation.objects.create(
                        rfq_number=next_code(
                            f"RFQ-{timezone.localdate().year}",
                            "request_for_quotations",
                            "rfq_number",
                            width=5,
                        ),
                        title=form.cleaned_data["title"],
                        description=form.cleaned_data.get("description") or "",
                        required_date=form.cleaned_data.get("required_date"),
                        status="draft",
                        created_by=request.user.id,
                        created_at=timezone.now(),
                    )
                    now = timezone.now()
                    RfqItem.objects.bulk_create(
                        [
                            RfqItem(
                                rfq_id=rfq.id,
                                product_id=row["product_id"],
                                description=row["description"],
                                quantity=row["quantity"],
                                unit_of_measure=row["unit_of_measure"],
                                created_at=now,
                            )
                            for row in rows
                        ]
                    )
                messages.success(request, f"RFQ {rfq.rfq_number} created.")
                return redirect(f"/purchasing/rfq/view/{rfq.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = RequestForQuotationForm()
        formset = RfqItemFormSet(prefix="items")
    return render(
        request,
        "purchasing/rfq_form.html",
        {
            "page_title": "Create RFQ - SORA ERP",
            "form": form,
            "formset": formset,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("purchasing")
def rfq_view(request, pk):
    access = get_access(request.user)
    rfq = get_object_or_404(RequestForQuotation, pk=pk)
    items = list(RfqItem.objects.filter(rfq_id=rfq.id).order_by("id"))
    products = _product_names({item.product_id for item in items if item.product_id})
    for item in items:
        product = products.get(item.product_id)
        item.product_label = product.name if product else (item.description or "-")
        item.product_code = product.product_code if product else ""
    return render(
        request,
        "purchasing/rfq_view.html",
        {
            "page_title": f"RFQ {rfq.rfq_number}",
            "rfq": rfq,
            "items": items,
            "can_send": rfq.status == "draft"
            and access.can("purchasing", "approve"),
        },
    )


@login_required
@module_access_required("purchasing")
def rfq_send(request, pk):
    access = get_access(request.user)
    if not access.can("purchasing", "approve"):
        messages.error(request, "You do not have permission to send RFQs.")
        return redirect(f"/purchasing/rfq/view/{pk}")
    rfq = get_object_or_404(RequestForQuotation, pk=pk)
    if request.method != "POST":
        return redirect(f"/purchasing/rfq/view/{rfq.id}")
    if rfq.status != "draft":
        messages.error(request, "Only draft RFQs can be sent.")
        return redirect(f"/purchasing/rfq/view/{rfq.id}")
    rfq.status = "sent"
    rfq.save(update_fields=["status"])
    messages.success(request, f"RFQ {rfq.rfq_number} marked as sent.")
    return redirect(f"/purchasing/rfq/view/{rfq.id}")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _aging_rows():
    today = timezone.localdate()
    bills = SupplierBill.objects.filter(
        status__in=["approved", "partially_paid"]
    ).order_by("due_date")
    vendors = _vendor_names({bill.vendor_id for bill in bills})
    rows = []
    total = ZERO
    for bill in bills:
        outstanding = _money((bill.total_amount or ZERO) - (bill.paid_amount or ZERO))
        if outstanding <= 0:
            continue
        days = (today - bill.due_date).days if bill.due_date else 0
        if days <= 0:
            bucket = "Current"
        elif days <= 30:
            bucket = "1-30 Days"
        elif days <= 60:
            bucket = "31-60 Days"
        else:
            bucket = "60+ Days"
        rows.append(
            {
                "bill": bill,
                "vendor_name": vendors.get(bill.vendor_id, "-"),
                "outstanding": outstanding,
                "days_overdue": max(days, 0),
                "bucket": bucket,
            }
        )
        total += outstanding
    return rows, _money(total)


def _purchase_summary(year):
    rows = []
    for month in range(1, 13):
        orders = PurchaseOrder.objects.filter(
            order_date__year=year, order_date__month=month
        )
        rows.append(
            {
                "month": date(year, month, 1).strftime("%B"),
                "count": orders.count(),
                "amount": _money(
                    sum((po.total_amount or ZERO for po in orders), ZERO)
                ),
            }
        )
    total = _money(sum((row["amount"] for row in rows), ZERO))
    for row in rows:
        row["percent"] = (
            round(float(row["amount"]) / float(total) * 100, 1) if total else 0
        )
    return rows, total


@login_required
@module_access_required("purchasing")
def reports(request):
    access = get_access(request.user)
    _, total_outstanding = _aging_rows()
    today = timezone.localdate()
    current_year = today.year
    _, year_total = _purchase_summary(current_year)
    return render(
        request,
        "purchasing/reports.html",
        {
            "page_title": "Purchasing Reports - SORA ERP",
            "total_outstanding": total_outstanding,
            "year_total": year_total,
            "year": current_year,
            "can_export": access.can("purchasing", "export"),
        },
    )


@login_required
@module_access_required("purchasing")
def report_ap_aging(request):
    access = get_access(request.user)
    rows, total = _aging_rows()
    bucket_map = defaultdict(lambda: {"total": ZERO, "count": 0})
    for row in rows:
        bucket_map[row["bucket"]]["total"] += row["outstanding"]
        bucket_map[row["bucket"]]["count"] += 1
    buckets = [
        {
            "label": label,
            "total": _money(bucket_map[label]["total"]),
            "count": bucket_map[label]["count"],
        }
        for label in ["Current", "1-30 Days", "31-60 Days", "60+ Days"]
    ]
    return render(
        request,
        "purchasing/report_ap_aging.html",
        {
            "page_title": "AP Aging - SORA ERP",
            "rows": rows,
            "total_outstanding": total,
            "buckets": buckets,
            "can_export": access.can("purchasing", "export"),
        },
    )


@login_required
@module_access_required("purchasing")
def report_purchase_summary(request):
    access = get_access(request.user)
    today = timezone.localdate()
    try:
        year = int(request.GET.get("year") or today.year)
    except (TypeError, ValueError):
        year = today.year
    rows, total = _purchase_summary(year)
    years = sorted(
        {
            value.year
            for value in PurchaseOrder.objects.values_list("order_date", flat=True)
            if value
        },
        reverse=True,
    )
    if year not in years:
        years.insert(0, year)
    return render(
        request,
        "purchasing/report_purchase_summary.html",
        {
            "page_title": "Purchase Summary - SORA ERP",
            "rows": rows,
            "total": total,
            "year": year,
            "years": years,
            "can_export": access.can("purchasing", "export"),
        },
    )


@login_required
@module_access_required("purchasing")
def report_export(request):
    access = get_access(request.user)
    if not access.can("purchasing", "export"):
        messages.error(request, "You do not have permission to export reports.")
        return redirect("/purchasing/reports")
    report = request.GET.get("type") or "ap_aging"
    if report == "purchase_summary":
        today = timezone.localdate()
        try:
            year = int(request.GET.get("year") or today.year)
        except (TypeError, ValueError):
            year = today.year
        rows, _ = _purchase_summary(year)
        return csv_response(
            f"purchase_summary_{year}.csv",
            ["Month", "PO Count", "Total Amount"],
            [[row["month"], row["count"], row["amount"]] for row in rows],
        )
    rows, _ = _aging_rows()
    return csv_response(
        "ap_aging.csv",
        [
            "Vendor",
            "Bill #",
            "Bill Date",
            "Due Date",
            "Total Amount",
            "Paid Amount",
            "Outstanding",
            "Aging Bucket",
        ],
        [
            [
                row["vendor_name"],
                row["bill"].bill_number,
                row["bill"].bill_date,
                row["bill"].due_date,
                row["bill"].total_amount,
                row["bill"].paid_amount,
                row["outstanding"],
                row["bucket"],
            ]
            for row in rows
        ],
    )
