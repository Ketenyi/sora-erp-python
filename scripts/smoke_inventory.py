"""Standalone smoke test for the inventory module (transaction rolled back)."""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.core.models import (  # noqa: E402
    Product,
    ProductCategory,
    StockAdjustment,
    StockMovement,
    StockTransfer,
    Warehouse,
)
from apps.core.tenancy import default_tenant, set_current_tenant  # noqa: E402

set_current_tenant(default_tenant())

setup_test_environment()
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ["testserver"]

passed = 0
failed = []


def check(label, condition):
    global passed
    if condition:
        passed += 1
    else:
        failed.append(label)
        print(f"FAIL: {label}")


with transaction.atomic():
    client = Client()
    check("login", client.login(email="admin@sora.com", password="admin123"))

    for path in [
        "/inventory/dashboard",
        "/inventory/products",
        "/inventory/products/create",
        "/inventory/categories",
        "/inventory/categories/create",
        "/inventory/stock-movements",
        "/inventory/stock-transfers",
        "/inventory/stock-transfers/create",
        "/inventory/stock-adjustments",
        "/inventory/stock-adjustments/create",
        "/inventory/reports",
        "/inventory/reports/stock-levels",
        "/inventory/reports/stock-value",
        "/inventory/reports/low-stock",
    ]:
        response = client.get(path)
        check(f"GET {path} -> 200 (got {response.status_code})", response.status_code == 200)

    category = ProductCategory.objects.filter(category_name="Hardware").first()
    warehouse = Warehouse.objects.filter(warehouse_code="WH-0001").first()
    other_warehouse = Warehouse.objects.filter(warehouse_code="WH-0002").first()
    check("seed category exists", category is not None)
    check("seed warehouses exist", warehouse is not None and other_warehouse is not None)

    # Create a product
    response = client.post(
        "/inventory/products/store",
        {
            "product_code": "TST-9001",
            "name": "Test Widget",
            "description": "Smoke test product",
            "category": category.id if category else "",
            "unit": "pcs",
            "type": "product",
            "unit_price": "100.00",
            "cost_price": "60.00",
            "current_stock": "10.00",
            "minimum_stock": "1",
            "maximum_stock": "50",
            "reorder_level": "2",
            "reorder_quantity": "10",
            "tax_rate": "16",
            "status": "active",
        },
    )
    check("create product redirects", response.status_code == 302)
    product = Product.objects.filter(product_code="TST-9001").first()
    check("product created", product is not None)
    check("product_name mirrored", product and product.product_name == "Test Widget")
    check("initial stock saved", product and product.current_stock == 10)

    response = client.get(f"/inventory/products/view/{product.id}")
    check("product view 200", response.status_code == 200)
    response = client.get(f"/inventory/products/edit/{product.id}")
    check("product edit 200", response.status_code == 200)

    # Edit without current_stock: stock must be preserved
    response = client.post(
        f"/inventory/products/update/{product.id}",
        {
            "product_code": "TST-9001",
            "name": "Test Widget Updated",
            "description": "Updated",
            "category": category.id if category else "",
            "unit": "pcs",
            "type": "product",
            "unit_price": "120.00",
            "cost_price": "60.00",
            "minimum_stock": "1",
            "maximum_stock": "50",
            "reorder_level": "2",
            "reorder_quantity": "10",
            "tax_rate": "16",
            "status": "active",
        },
    )
    check("edit product redirects", response.status_code == 302)
    product.refresh_from_db()
    check("edit preserved stock", product.current_stock == 10)
    check("edit updated name", product.name == "Test Widget Updated")

    # Auto-generated category code
    response = client.post(
        "/inventory/categories/store",
        {"category_name": "Smoke Category", "parent": "", "status": "active", "description": "tmp"},
    )
    check("create category redirects", response.status_code == 302)
    created_category = ProductCategory.objects.filter(category_name="Smoke Category").first()
    check("category auto code", created_category and created_category.category_code.startswith("CAT-"))
    response = client.post(f"/inventory/categories/delete/{created_category.id}")
    check("delete empty category", response.status_code == 302 and not ProductCategory.objects.filter(pk=created_category.pk).exists())

    # Transfer
    response = client.post(
        "/inventory/stock-transfers/store",
        {
            "from_location": warehouse.warehouse_name,
            "to_location": other_warehouse.warehouse_name,
            "transfer_date": timezone.localdate().isoformat(),
            "notes": "smoke transfer",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-product": product.id,
            "items-0-quantity": "2",
            "items-0-notes": "two units",
        },
    )
    check("create transfer redirects", response.status_code == 302)
    transfer = StockTransfer.objects.filter(notes="smoke transfer").first()
    check("transfer created", transfer is not None and transfer.status == "pending")
    response = client.get(f"/inventory/stock-transfers/view/{transfer.id}")
    check("transfer view 200", response.status_code == 200)
    before_movements = StockMovement.objects.filter(product_id=product.id).count()
    response = client.post(f"/inventory/stock-transfers/complete/{transfer.id}")
    check("complete transfer redirects", response.status_code == 302)
    transfer.refresh_from_db()
    product.refresh_from_db()
    check("transfer completed", transfer.status == "completed")
    check("transfer did not change stock", product.current_stock == 10)
    check(
        "transfer logged two movements",
        StockMovement.objects.filter(product_id=product.id).count() == before_movements + 2,
    )

    # Adjustment
    response = client.post(
        "/inventory/stock-adjustments/store",
        {
            "adjustment_date": timezone.localdate().isoformat(),
            "type": "decrease",
            "reason": "damage",
            "notes": "smoke adjustment",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-product": product.id,
            "items-0-quantity": "3",
            "items-0-notes": "broken",
        },
    )
    check("create adjustment redirects", response.status_code == 302)
    adjustment = StockAdjustment.objects.filter(notes="smoke adjustment").first()
    check("adjustment created", adjustment is not None and adjustment.status == "draft")
    product.refresh_from_db()
    check("adjustment applied stock immediately", product.current_stock == 7)
    response = client.get(f"/inventory/stock-adjustments/view/{adjustment.id}")
    check("adjustment view 200", response.status_code == 200)
    response = client.post(f"/inventory/stock-adjustments/approve/{adjustment.id}")
    check("approve adjustment redirects", response.status_code == 302)
    adjustment.refresh_from_db()
    check("adjustment approved", adjustment.status == "approved")
    check(
        "adjustment logged movement",
        StockMovement.objects.filter(
            product_id=product.id, reference_number=adjustment.adjustment_number
        ).exists(),
    )

    # Reports and exports
    for report in ["stock_levels", "stock_value", "low_stock"]:
        response = client.get(f"/inventory/reports/export?report={report}")
        check(f"export {report} csv", response.status_code == 200 and response["Content-Type"].startswith("text/csv"))

    response = client.get(f"/inventory/stock-movements/product/{product.id}")
    check("product movement history 200", response.status_code == 200)

    # Deactivate
    response = client.get(f"/inventory/products/delete/{product.id}")
    check("delete confirm 200", response.status_code == 200)
    response = client.post(f"/inventory/products/delete/{product.id}")
    check("deactivate redirects", response.status_code == 302)
    product.refresh_from_db()
    check("product deactivated", product.status == "inactive")

    # Sales user has no inventory access
    sales_client = Client()
    sales_client.login(email="sales@sora.com", password="sales123")
    response = sales_client.get("/inventory/products")
    check("sales rep blocked from inventory (403)", response.status_code == 403)

    # Manager (viewer) can view but cannot create
    manager_client = Client()
    manager_client.login(email="manager@sora.com", password="manager123")
    response = manager_client.get("/inventory/products")
    check("manager can view inventory", response.status_code == 200)
    response = manager_client.post(
        "/inventory/products/store",
        {"product_code": "NOPE-1", "name": "Nope", "unit_price": "1", "status": "active"},
    )
    check("manager blocked from creating product", response.status_code == 302 and not Product.objects.filter(product_code="NOPE-1").exists())

    transaction.set_rollback(True)

print(f"\n{passed} checks passed, {len(failed)} failed.")
if failed:
    print("Failures:")
    for label in failed:
        print(f" - {label}")
raise SystemExit(1 if failed else 0)
