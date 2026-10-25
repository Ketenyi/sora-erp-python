"""Inventory module: products, categories, movements, transfers and adjustments."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import (
    Product,
    ProductCategory,
    StockAdjustment,
    StockAdjustmentItem,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    Warehouse,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_code
from apps.core.ui import csv_response, paginate
from apps.inventory.forms import (
    ADJUSTMENT_REASONS,
    CategoryForm,
    ProductForm,
    StockAdjustmentForm,
    StockAdjustmentItemFormSet,
    StockTransferForm,
    StockTransferItemFormSet,
)

ZERO = Decimal("0.00")
PAGE_SIZE = 15
MOVEMENT_TYPES = [
    ("purchase", "Purchase"),
    ("sale", "Sale"),
    ("transfer_in", "Transfer In"),
    ("transfer_out", "Transfer Out"),
    ("adjustment_in", "Adjustment In"),
    ("adjustment_out", "Adjustment Out"),
    ("return_in", "Return In"),
    ("return_out", "Return Out"),
]


def _money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


def _category_names() -> dict[int, str]:
    return {
        row["id"]: row["category_name"]
        for row in ProductCategory.objects.values("id", "category_name")
    }


def _product_names(ids) -> dict[int, Product]:
    return {p.id: p for p in Product.objects.filter(id__in=ids)}


def _products_json() -> str:
    rows = []
    for product in Product.objects.filter(status="active").order_by("name"):
        rows.append(
            {
                "id": product.id,
                "code": product.product_code,
                "name": product.name,
                "stock": float(product.current_stock or 0),
                "unit": product.unit or "",
                "cost": float(product.cost_price or 0),
            }
        )
    return json.dumps(rows)


def _decorate_products(products) -> list[Product]:
    categories = _category_names()
    for product in products:
        stock = product.current_stock or ZERO
        cost = product.cost_price or ZERO
        price = product.unit_price or ZERO
        reorder = product.reorder_level or ZERO
        maximum = product.maximum_stock or ZERO
        if stock <= 0:
            status = "out"
        elif reorder and stock <= reorder:
            status = "low"
        elif maximum and stock > maximum:
            status = "over"
        else:
            status = "normal"
        product.category_name = categories.get(product.category_id, "-")
        product.stock_value = _money(stock * cost)
        product.retail_value = _money(stock * price)
        product.stock_status = status
        product.shortage = _money(max(reorder - stock, ZERO))
    return products


def _collect_items(formset, extra_key="notes"):
    items = []
    for form in formset:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        product = form.cleaned_data.get("product")
        quantity = form.cleaned_data.get("quantity")
        if not product or not quantity:
            continue
        items.append(
            {
                "product": product,
                "quantity": quantity,
                extra_key: (form.cleaned_data.get(extra_key) or "").strip(),
            }
        )
    return items


def _log_movement(
    product_id,
    movement_type,
    quantity,
    before,
    after,
    reference_type,
    reference_number,
    user_id,
    unit_price=None,
    location=None,
    notes="",
):
    unit_price = unit_price or ZERO
    StockMovement.objects.create(
        product_id=product_id,
        movement_type=movement_type,
        quantity=quantity,
        quantity_before=before,
        quantity_after=after,
        reference_type=reference_type,
        reference_number=reference_number,
        unit_price=unit_price,
        total_value=_money(quantity * unit_price),
        notes=notes,
        location=location,
        created_by=user_id,
        created_at=timezone.now(),
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def dashboard(request):
    active_products = list(Product.objects.filter(status="active"))
    decorated = _decorate_products(active_products)
    low_stock = [p for p in decorated if p.stock_status == "low"]
    out_of_stock = [p for p in decorated if p.stock_status == "out"]
    total_value = sum((p.stock_value for p in decorated), ZERO)

    recent_movements = list(
        StockMovement.objects.order_by("-created_at", "-id")[:10]
    )
    for movement in recent_movements:
        product = Product.objects.filter(pk=movement.product_id).first()
        movement.product_label = (
            f"{product.product_code} · {product.name}" if product else "-"
        )

    top_products = sorted(
        [p for p in decorated if p.stock_value > 0],
        key=lambda p: p.stock_value,
        reverse=True,
    )[:5]

    category_totals: dict[str, dict] = defaultdict(
        lambda: {"name": "", "products": 0, "quantity": ZERO, "value": ZERO}
    )
    for product in decorated:
        key = product.category_name or "Uncategorised"
        row = category_totals[key]
        row["name"] = key
        row["products"] += 1
        row["quantity"] += product.current_stock or ZERO
        row["value"] += product.stock_value
    category_rows = sorted(
        category_totals.values(), key=lambda r: r["value"], reverse=True
    )[:8]

    return render(
        request,
        "inventory/dashboard.html",
        {
            "page_title": "Inventory Dashboard - SORA ERP",
            "stats": {
                "products": len(active_products),
                "low_stock": len(low_stock),
                "out_of_stock": len(out_of_stock),
                "stock_value": _money(total_value),
            },
            "recent_movements": recent_movements,
            "top_products": top_products,
            "category_rows": category_rows,
            "pending_transfers": StockTransfer.objects.filter(status="pending").count(),
            "draft_adjustments": StockAdjustment.objects.filter(
                status="draft"
            ).count(),
        },
    )


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def products(request):
    access = get_access(request.user)
    queryset = Product.objects.all().order_by("name")
    query = (request.GET.get("q") or "").strip()
    category = request.GET.get("category") or ""
    status = request.GET.get("status") or ""
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(product_name__icontains=query)
            | Q(product_code__icontains=query)
        )
    if category:
        queryset = queryset.filter(category_id=category)
    if status:
        queryset = queryset.filter(status=status)
    page_obj = paginate(request, queryset, PAGE_SIZE)
    _decorate_products(page_obj.object_list)

    return render(
        request,
        "inventory/products.html",
        {
            "page_title": "Products - SORA ERP",
            "page_obj": page_obj,
            "products": page_obj.object_list,
            "search_query": query,
            "category_filter": category,
            "status_filter": status,
            "categories": ProductCategory.objects.order_by("category_name"),
            "can_create": access.can("inventory", "create"),
            "can_edit": access.can("inventory", "edit"),
            "can_delete": access.can("inventory", "delete"),
        },
    )


@login_required
@module_access_required("inventory")
def product_create(request):
    access = get_access(request.user)
    if not access.can("inventory", "create"):
        messages.error(request, "You do not have permission to add products.")
        return redirect("/inventory/products")
    if request.method == "POST":
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.created_by = request.user.id
            product.updated_by = request.user.id
            product.save()
            messages.success(request, f"Product {product.name} created.")
            return redirect(f"/inventory/products/view/{product.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm()
    return render(
        request,
        "inventory/product_form.html",
        {
            "page_title": "Add Product - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("inventory")
def product_edit(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "edit"):
        messages.error(request, "You do not have permission to edit products.")
        return redirect(f"/inventory/products/view/{pk}")
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save(commit=False)
            product.updated_by = request.user.id
            product.save()
            messages.success(request, "Product updated.")
            return redirect(f"/inventory/products/view/{product.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = ProductForm(instance=product)
        form.fields["category"].initial = product.category_id
    return render(
        request,
        "inventory/product_form.html",
        {
            "page_title": "Edit Product - SORA ERP",
            "form": form,
            "product": product,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("inventory")
def product_view(request, pk):
    access = get_access(request.user)
    product = get_object_or_404(Product, pk=pk)
    category = ProductCategory.objects.filter(pk=product.category_id).first()
    movements = list(
        StockMovement.objects.filter(product_id=product.id)
        .order_by("-created_at", "-id")[:25]
    )
    stock = product.current_stock or ZERO
    cost = product.cost_price or ZERO
    price = product.unit_price or ZERO
    return render(
        request,
        "inventory/product_view.html",
        {
            "page_title": f"{product.name} - SORA ERP",
            "product": product,
            "category": category,
            "movements": movements,
            "stock_value": _money(stock * cost),
            "retail_value": _money(stock * price),
            "margin": _money(price - cost),
            "can_edit": access.can("inventory", "edit"),
            "can_delete": access.can("inventory", "delete"),
        },
    )


@login_required
@module_access_required("inventory")
def product_delete(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "delete"):
        messages.error(request, "You do not have permission to delete products.")
        return redirect("/inventory/products")
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        product.status = "inactive"
        product.updated_by = request.user.id
        product.save()
        messages.success(request, f"Product {product.name} deactivated.")
        return redirect("/inventory/products")
    return render(
        request,
        "inventory/confirm_delete.html",
        {
            "page_title": "Delete Product - SORA ERP",
            "object_label": product.name,
            "object_detail": product.product_code,
            "cancel_url": f"/inventory/products/view/{product.id}",
            "confirm_url": f"/inventory/products/delete/{product.id}",
            "warning": "The product will be marked inactive and hidden from new transactions. Stock history is preserved.",
        },
    )


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def categories(request):
    access = get_access(request.user)
    rows = list(ProductCategory.objects.order_by("category_name"))
    counts = {
        row["category_id"]: row["total"]
        for row in Product.objects.values("category_id").annotate(total=Count("id"))
    }
    for category in rows:
        category.product_count = counts.get(category.id, 0)
    return render(
        request,
        "inventory/categories.html",
        {
            "page_title": "Product Categories - SORA ERP",
            "categories": rows,
            "can_create": access.can("inventory", "create"),
            "can_edit": access.can("inventory", "edit"),
            "can_delete": access.can("inventory", "delete"),
        },
    )


@login_required
@module_access_required("inventory")
def category_create(request):
    access = get_access(request.user)
    if not access.can("inventory", "create"):
        messages.error(request, "You do not have permission to add categories.")
        return redirect("/inventory/categories")
    if request.method == "POST":
        form = CategoryForm(request.POST)
        if form.is_valid():
            category = form.save(commit=False)
            category.category_code = next_code(
                "CAT", "product_categories", "category_code"
            )
            category.created_by = request.user.id
            category.created_at = timezone.now()
            category.save()
            messages.success(request, f"Category {category.category_name} created.")
            return redirect("/inventory/categories")
        messages.error(request, "Please correct the errors below.")
    else:
        form = CategoryForm()
    return render(
        request,
        "inventory/category_form.html",
        {
            "page_title": "Add Category - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("inventory")
def category_edit(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "edit"):
        messages.error(request, "You do not have permission to edit categories.")
        return redirect("/inventory/categories")
    category = get_object_or_404(ProductCategory, pk=pk)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save(commit=False)
            category.save()
            messages.success(request, "Category updated.")
            return redirect("/inventory/categories")
        messages.error(request, "Please correct the errors below.")
    else:
        form = CategoryForm(instance=category)
        form.fields["parent"].initial = category.parent_id
    return render(
        request,
        "inventory/category_form.html",
        {
            "page_title": "Edit Category - SORA ERP",
            "form": form,
            "category": category,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("inventory")
def category_delete(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "delete"):
        messages.error(request, "You do not have permission to delete categories.")
        return redirect("/inventory/categories")
    category = get_object_or_404(ProductCategory, pk=pk)
    if Product.objects.filter(category_id=category.id).exists():
        messages.error(
            request,
            "This category still has products and cannot be deleted.",
        )
        return redirect("/inventory/categories")
    category.delete()
    messages.success(request, f"Category {category.category_name} deleted.")
    return redirect("/inventory/categories")


# ---------------------------------------------------------------------------
# Stock movements
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def stock_movements(request):
    access = get_access(request.user)
    queryset = StockMovement.objects.all().order_by("-created_at", "-id")
    query = (request.GET.get("q") or "").strip()
    movement_type = request.GET.get("type") or ""
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    if query:
        product_ids = Product.objects.filter(
            Q(name__icontains=query) | Q(product_code__icontains=query)
        ).values("id")
        queryset = queryset.filter(product_id__in=product_ids)
    if movement_type:
        queryset = queryset.filter(movement_type=movement_type)
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    page_obj = paginate(request, queryset, 25)
    products_map = _product_names({m.product_id for m in page_obj.object_list})
    for movement in page_obj.object_list:
        product = products_map.get(movement.product_id)
        movement.product_label = (
            f"{product.product_code} · {product.name}" if product else "-"
        )
    return render(
        request,
        "inventory/stock_movements.html",
        {
            "page_title": "Stock Movements - SORA ERP",
            "page_obj": page_obj,
            "movements": page_obj.object_list,
            "search_query": query,
            "type_filter": movement_type,
            "movement_types": MOVEMENT_TYPES,
            "date_from": date_from,
            "date_to": date_to,
            "can_export": access.can("inventory", "export"),
        },
    )


@login_required
@module_access_required("inventory")
def stock_movements_by_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    queryset = StockMovement.objects.filter(product_id=product.id).order_by(
        "-created_at", "-id"
    )
    page_obj = paginate(request, queryset, 25)
    total_in = sum(
        (m.quantity for m in queryset if m.movement_type.endswith("_in")), ZERO
    )
    total_out = sum(
        (m.quantity for m in queryset if m.movement_type.endswith("_out")), ZERO
    )
    return render(
        request,
        "inventory/product_movements.html",
        {
            "page_title": f"Stock History - {product.name}",
            "product": product,
            "page_obj": page_obj,
            "movements": page_obj.object_list,
            "total_in": _money(total_in),
            "total_out": _money(total_out),
        },
    )


# ---------------------------------------------------------------------------
# Stock transfers
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def stock_transfers(request):
    access = get_access(request.user)
    queryset = StockTransfer.objects.all().order_by("-created_at", "-id")
    status = request.GET.get("status") or ""
    query = (request.GET.get("q") or "").strip()
    if status:
        queryset = queryset.filter(status=status)
    if query:
        queryset = queryset.filter(
            Q(transfer_number__icontains=query)
            | Q(from_location__icontains=query)
            | Q(to_location__icontains=query)
        )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    counts = {
        row["transfer_id"]: row["total"]
        for row in StockTransferItem.objects.values("transfer_id").annotate(
            total=Count("id")
        )
    }
    for transfer in page_obj.object_list:
        transfer.item_count = counts.get(transfer.id, 0)
    return render(
        request,
        "inventory/stock_transfers.html",
        {
            "page_title": "Stock Transfers - SORA ERP",
            "page_obj": page_obj,
            "transfers": page_obj.object_list,
            "status_filter": status,
            "search_query": query,
            "can_create": access.can("inventory", "create"),
        },
    )


@login_required
@module_access_required("inventory")
def stock_transfer_create(request):
    access = get_access(request.user)
    if not access.can("inventory", "create"):
        messages.error(request, "You do not have permission to create transfers.")
        return redirect("/inventory/stock-transfers")
    if not Warehouse.objects.exists():
        messages.error(
            request,
            "No warehouses are configured. Add a warehouse before creating transfers.",
        )
        return redirect("/inventory/stock-transfers")
    if request.method == "POST":
        form = StockTransferForm(request.POST)
        formset = StockTransferItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to transfer.")
            else:
                with transaction.atomic():
                    transfer = StockTransfer.objects.create(
                        transfer_number=next_code(
                            f"TRF-{timezone.localdate().year}",
                            "stock_transfers",
                            "transfer_number",
                            width=5,
                        ),
                        from_location=form.cleaned_data["from_location"],
                        to_location=form.cleaned_data["to_location"],
                        transfer_date=form.cleaned_data["transfer_date"],
                        status="pending",
                        notes=form.cleaned_data.get("notes") or "",
                        created_by=request.user.id,
                        created_at=timezone.now(),
                    )
                    StockTransferItem.objects.bulk_create(
                        [
                            StockTransferItem(
                                transfer_id=transfer.id,
                                product_id=item["product"].id,
                                quantity=item["quantity"],
                                received_quantity=ZERO,
                                unit_price=item["product"].cost_price or ZERO,
                                notes=item["notes"],
                            )
                            for item in items
                        ]
                    )
                messages.success(
                    request, f"Transfer {transfer.transfer_number} created."
                )
                return redirect(f"/inventory/stock-transfers/view/{transfer.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = StockTransferForm()
        formset = StockTransferItemFormSet(prefix="items")
    return render(
        request,
        "inventory/stock_transfer_form.html",
        {
            "page_title": "Create Stock Transfer - SORA ERP",
            "form": form,
            "formset": formset,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("inventory")
def stock_transfer_view(request, pk):
    access = get_access(request.user)
    transfer = get_object_or_404(StockTransfer, pk=pk)
    items = list(
        StockTransferItem.objects.filter(transfer_id=transfer.id).order_by("id")
    )
    products = _product_names({item.product_id for item in items})
    total_value = ZERO
    for item in items:
        product = products.get(item.product_id)
        item.product_label = (
            f"{product.product_code} · {product.name}" if product else "-"
        )
        item.line_value = _money(item.quantity * (item.unit_price or ZERO))
        total_value += item.line_value
    return render(
        request,
        "inventory/stock_transfer_view.html",
        {
            "page_title": f"Transfer {transfer.transfer_number}",
            "transfer": transfer,
            "items": items,
            "total_value": _money(total_value),
            "can_complete": transfer.status == "pending"
            and access.can("inventory", "approve"),
        },
    )


@login_required
@module_access_required("inventory")
def stock_transfer_complete(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "approve"):
        messages.error(request, "You do not have permission to complete transfers.")
        return redirect(f"/inventory/stock-transfers/view/{pk}")
    transfer = get_object_or_404(StockTransfer, pk=pk)
    if request.method != "POST":
        return redirect(f"/inventory/stock-transfers/view/{transfer.id}")
    if transfer.status != "pending":
        messages.error(request, "Only pending transfers can be completed.")
        return redirect(f"/inventory/stock-transfers/view/{transfer.id}")

    items = list(StockTransferItem.objects.filter(transfer_id=transfer.id))
    shortages = []
    for item in items:
        product = Product.objects.filter(pk=item.product_id).first()
        available = product.current_stock if product else ZERO
        if not product or available < item.quantity:
            label = product.name if product else f"product #{item.product_id}"
            shortages.append(f"{label} (available {available or 0}, needed {item.quantity})")
    if shortages:
        messages.error(
            request, "Insufficient stock: " + "; ".join(shortages) + "."
        )
        return redirect(f"/inventory/stock-transfers/view/{transfer.id}")

    now = timezone.now()
    with transaction.atomic():
        for item in items:
            product = Product.objects.select_for_update().get(pk=item.product_id)
            available = product.current_stock or ZERO
            # A location transfer does not change total company stock, so the
            # movement pair is recorded for the audit trail without a net change.
            _log_movement(
                product.id,
                "transfer_out",
                item.quantity,
                available,
                available,
                "stock_transfer",
                transfer.transfer_number,
                request.user.id,
                unit_price=item.unit_price,
                location=transfer.from_location,
                notes=f"Transfer to {transfer.to_location}",
            )
            _log_movement(
                product.id,
                "transfer_in",
                item.quantity,
                available,
                available,
                "stock_transfer",
                transfer.transfer_number,
                request.user.id,
                unit_price=item.unit_price,
                location=transfer.to_location,
                notes=f"Transfer from {transfer.from_location}",
            )
            item.received_quantity = item.quantity
            item.save(update_fields=["received_quantity"])
        transfer.status = "completed"
        transfer.completed_by = request.user.id
        transfer.completed_at = now
        transfer.save(update_fields=["status", "completed_by", "completed_at"])
    messages.success(request, f"Transfer {transfer.transfer_number} completed.")
    return redirect(f"/inventory/stock-transfers/view/{transfer.id}")


# ---------------------------------------------------------------------------
# Stock adjustments
# ---------------------------------------------------------------------------
@login_required
@module_access_required("inventory")
def stock_adjustments(request):
    access = get_access(request.user)
    queryset = StockAdjustment.objects.all().order_by("-created_at", "-id")
    status = request.GET.get("status") or ""
    adjustment_type = request.GET.get("type") or ""
    query = (request.GET.get("q") or "").strip()
    if status:
        queryset = queryset.filter(status=status)
    if adjustment_type:
        queryset = queryset.filter(type=adjustment_type)
    if query:
        queryset = queryset.filter(
            Q(adjustment_number__icontains=query) | Q(notes__icontains=query)
        )
    page_obj = paginate(request, queryset, PAGE_SIZE)
    return render(
        request,
        "inventory/stock_adjustments.html",
        {
            "page_title": "Stock Adjustments - SORA ERP",
            "page_obj": page_obj,
            "adjustments": page_obj.object_list,
            "status_filter": status,
            "type_filter": adjustment_type,
            "search_query": query,
            "can_create": access.can("inventory", "create"),
        },
    )


@login_required
@module_access_required("inventory")
def stock_adjustment_create(request):
    access = get_access(request.user)
    if not access.can("inventory", "create"):
        messages.error(request, "You do not have permission to create adjustments.")
        return redirect("/inventory/stock-adjustments")
    if request.method == "POST":
        form = StockAdjustmentForm(request.POST)
        formset = StockAdjustmentItemFormSet(request.POST, prefix="items")
        if form.is_valid() and formset.is_valid():
            items = _collect_items(formset)
            if not items:
                form.add_error(None, "Add at least one item to adjust.")
            else:
                with transaction.atomic():
                    adjustment = StockAdjustment.objects.create(
                        adjustment_number=next_code(
                            f"ADJ-{timezone.localdate().year}",
                            "stock_adjustments",
                            "adjustment_number",
                            width=5,
                        ),
                        adjustment_date=form.cleaned_data["adjustment_date"],
                        type=form.cleaned_data["type"],
                        reason=form.cleaned_data["reason"],
                        notes=form.cleaned_data.get("notes") or "",
                        status="draft",
                        created_by=request.user.id,
                        created_at=timezone.now(),
                    )
                    for item in items:
                        product = Product.objects.select_for_update().get(
                            pk=item["product"].id
                        )
                        before = product.current_stock or ZERO
                        if form.cleaned_data["type"] == "increase":
                            after = before + item["quantity"]
                        else:
                            after = max(before - item["quantity"], ZERO)
                        StockAdjustmentItem.objects.create(
                            adjustment_id=adjustment.id,
                            product_id=product.id,
                            quantity=item["quantity"],
                            quantity_before=before,
                            quantity_after=after,
                            unit_cost=product.cost_price or ZERO,
                            notes=item["notes"],
                        )
                        product.current_stock = after
                        product.stock = int(after)
                        product.updated_by = request.user.id
                        product.save()
                        _log_movement(
                            product.id,
                            "adjustment_in"
                            if form.cleaned_data["type"] == "increase"
                            else "adjustment_out",
                            item["quantity"],
                            before,
                            after,
                            "adjustment",
                            adjustment.adjustment_number,
                            request.user.id,
                            unit_price=product.cost_price,
                            notes=item["notes"],
                        )
                messages.success(
                    request,
                    f"Adjustment {adjustment.adjustment_number} created and stock updated.",
                )
                return redirect(f"/inventory/stock-adjustments/view/{adjustment.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = StockAdjustmentForm()
        formset = StockAdjustmentItemFormSet(prefix="items")
    return render(
        request,
        "inventory/stock_adjustment_form.html",
        {
            "page_title": "Create Stock Adjustment - SORA ERP",
            "form": form,
            "formset": formset,
            "reasons": ADJUSTMENT_REASONS,
            "products_json": _products_json(),
        },
    )


@login_required
@module_access_required("inventory")
def stock_adjustment_view(request, pk):
    access = get_access(request.user)
    adjustment = get_object_or_404(StockAdjustment, pk=pk)
    items = list(
        StockAdjustmentItem.objects.filter(adjustment_id=adjustment.id).order_by("id")
    )
    products = _product_names({item.product_id for item in items})
    total_quantity = ZERO
    for item in items:
        product = products.get(item.product_id)
        item.product_label = (
            f"{product.product_code} · {product.name}" if product else "-"
        )
        item.unit = product.unit if product else ""
        item.line_cost = _money(item.quantity * (item.unit_cost or ZERO))
        total_quantity += item.quantity
    return render(
        request,
        "inventory/stock_adjustment_view.html",
        {
            "page_title": f"Adjustment {adjustment.adjustment_number}",
            "adjustment": adjustment,
            "items": items,
            "total_quantity": _money(total_quantity),
            "can_approve": adjustment.status == "draft"
            and access.can("inventory", "approve"),
        },
    )


@login_required
@module_access_required("inventory")
def stock_adjustment_approve(request, pk):
    access = get_access(request.user)
    if not access.can("inventory", "approve"):
        messages.error(request, "You do not have permission to approve adjustments.")
        return redirect(f"/inventory/stock-adjustments/view/{pk}")
    adjustment = get_object_or_404(StockAdjustment, pk=pk)
    if request.method != "POST":
        return redirect(f"/inventory/stock-adjustments/view/{adjustment.id}")
    if adjustment.status != "draft":
        messages.error(request, "Only draft adjustments can be approved.")
        return redirect(f"/inventory/stock-adjustments/view/{adjustment.id}")
    adjustment.status = "approved"
    adjustment.approved_by = request.user.id
    adjustment.approved_at = timezone.now()
    adjustment.save(update_fields=["status", "approved_by", "approved_at"])
    messages.success(
        request, f"Adjustment {adjustment.adjustment_number} approved."
    )
    return redirect(f"/inventory/stock-adjustments/view/{adjustment.id}")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _active_products_for_report(request):
    queryset = Product.objects.filter(status="active").order_by("name")
    query = (request.GET.get("q") or "").strip()
    category = request.GET.get("category") or ""
    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(product_name__icontains=query)
            | Q(product_code__icontains=query)
        )
    if category:
        queryset = queryset.filter(category_id=category)
    return _decorate_products(list(queryset)), query, category


def _low_stock_rows(request):
    products, query, category = _active_products_for_report(request)
    rows = [p for p in products if p.stock_status == "low" or p.stock_status == "out"]
    rows.sort(key=lambda p: p.shortage, reverse=True)
    return rows, query, category


@login_required
@module_access_required("inventory")
def reports(request):
    products = _decorate_products(list(Product.objects.filter(status="active")))
    total_value = sum((p.stock_value for p in products), ZERO)
    total_retail = sum((p.retail_value for p in products), ZERO)
    return render(
        request,
        "inventory/reports.html",
        {
            "page_title": "Inventory Reports - SORA ERP",
            "product_count": len(products),
            "low_count": len([p for p in products if p.stock_status == "low"]),
            "out_count": len([p for p in products if p.stock_status == "out"]),
            "total_value": _money(total_value),
            "total_retail": _money(total_retail),
        },
    )


@login_required
@module_access_required("inventory")
def report_stock_levels(request):
    products, query, category = _active_products_for_report(request)
    page_obj = paginate(request, products, 30)
    return render(
        request,
        "inventory/report_stock_levels.html",
        {
            "page_title": "Stock Levels - SORA ERP",
            "page_obj": page_obj,
            "products": page_obj.object_list,
            "search_query": query,
            "category_filter": category,
            "categories": ProductCategory.objects.order_by("category_name"),
        },
    )


@login_required
@module_access_required("inventory")
def report_stock_value(request):
    products, query, category = _active_products_for_report(request)
    groups: dict[str, dict] = defaultdict(
        lambda: {"name": "", "products": 0, "quantity": ZERO, "cost": ZERO, "retail": ZERO}
    )
    for product in products:
        key = product.category_name or "Uncategorised"
        row = groups[key]
        row["name"] = key
        row["products"] += 1
        row["quantity"] += product.current_stock or ZERO
        row["cost"] += product.stock_value
        row["retail"] += product.retail_value
        row["margin"] = row["retail"] - row["cost"]
    rows = sorted(groups.values(), key=lambda r: r["cost"], reverse=True)
    totals = {
        "products": len(products),
        "quantity": _money(sum((p.current_stock or ZERO for p in products), ZERO)),
        "cost": _money(sum((p.stock_value for p in products), ZERO)),
        "retail": _money(sum((p.retail_value for p in products), ZERO)),
    }
    return render(
        request,
        "inventory/report_stock_value.html",
        {
            "page_title": "Stock Value - SORA ERP",
            "rows": rows,
            "totals": totals,
            "search_query": query,
            "category_filter": category,
            "categories": ProductCategory.objects.order_by("category_name"),
        },
    )


@login_required
@module_access_required("inventory")
def report_low_stock(request):
    rows, query, category = _low_stock_rows(request)
    return render(
        request,
        "inventory/report_low_stock.html",
        {
            "page_title": "Low Stock Alerts - SORA ERP",
            "rows": rows,
            "search_query": query,
            "category_filter": category,
            "categories": ProductCategory.objects.order_by("category_name"),
        },
    )


@login_required
@module_access_required("inventory")
def report_export(request):
    access = get_access(request.user)
    if not access.can("inventory", "export"):
        messages.error(request, "You do not have permission to export reports.")
        return redirect("/inventory/reports")
    report = request.GET.get("report") or "stock_levels"
    if report == "stock_value":
        products, _, _ = _active_products_for_report(request)
        groups: dict[str, dict] = defaultdict(
            lambda: {"quantity": ZERO, "cost": ZERO, "retail": ZERO, "products": 0}
        )
        for product in products:
            row = groups[product.category_name or "Uncategorised"]
            row["quantity"] += product.current_stock or ZERO
            row["cost"] += product.stock_value
            row["retail"] += product.retail_value
            row["products"] += 1
        rows = [
            [name, data["products"], data["quantity"], data["cost"], data["retail"]]
            for name, data in sorted(groups.items())
        ]
        return csv_response(
            "stock_value.csv",
            ["Category", "Products", "Quantity", "Cost Value", "Retail Value"],
            rows,
        )
    if report == "low_stock":
        products, _, _ = _low_stock_rows(request)
        rows = [
            [
                p.product_code,
                p.name,
                p.category_name,
                p.current_stock or 0,
                p.reorder_level or 0,
                p.shortage,
                p.unit or "",
            ]
            for p in products
        ]
        return csv_response(
            "low_stock.csv",
            ["Code", "Product", "Category", "Stock", "Reorder Level", "Shortage", "Unit"],
            rows,
        )
    products, _, _ = _active_products_for_report(request)
    rows = [
        [
            p.product_code,
            p.name,
            p.category_name,
            p.current_stock or 0,
            p.unit or "",
            p.cost_price or 0,
            p.unit_price or 0,
            p.stock_value,
            p.stock_status,
        ]
        for p in products
    ]
    return csv_response(
        "stock_levels.csv",
        [
            "Code",
            "Product",
            "Category",
            "Stock",
            "Unit",
            "Cost Price",
            "Selling Price",
            "Stock Value",
            "Status",
        ],
        rows,
    )
