"""Standalone smoke test for the purchasing module (transaction rolled back)."""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from decimal import Decimal  # noqa: E402

from django.conf import settings  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.core.models import (  # noqa: E402
    ChartOfAccount,
    GoodsReceipt,
    JournalEntry,
    JournalEntryLine,
    Product,
    PurchaseOrder,
    PurchaseOrderItem,
    RfqItem,
    StockMovement,
    SupplierBill,
    SupplierPayment,
    Vendor,
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


def get(path, expected=200):
    response = client.get(path)
    check(f"GET {path} -> {expected} (got {response.status_code})", response.status_code == expected)
    return response


with transaction.atomic():
    client = Client()
    check("login", client.login(email="admin@sora.com", password="admin123"))

    vendor = Vendor.objects.filter(status="active").order_by("id").first()
    if vendor is None:
        vendor = Vendor.objects.create(
            vendor_code="SMOKE-VEN",
            company_name="Smoke Supplier",
            status="active",
            created_at=timezone.now(),
        )
    product = Product.objects.filter(status="active").order_by("id").first()
    check("vendor exists", vendor is not None)
    check("product exists", product is not None)
    check("chart of accounts configured", ChartOfAccount.objects.filter(account_code__in=["1000", "2000", "2100", "5000"]).count() == 4)
    starting_stock = product.current_stock or Decimal("0")

    for path in [
        "/purchasing/dashboard",
        "/purchasing/purchase-orders",
        "/purchasing/purchase-orders/create",
        "/purchasing/goods-receipts",
        "/purchasing/goods-receipts/create",
        "/purchasing/supplier-bills",
        "/purchasing/supplier-bills/create",
        "/purchasing/supplier-payments",
        "/purchasing/supplier-payments/create",
        "/purchasing/rfq",
        "/purchasing/rfq/create",
        "/purchasing/reports",
        "/purchasing/reports/ap-aging",
        "/purchasing/reports/purchase-summary",
    ]:
        get(path)

    # --- Purchase order lifecycle -------------------------------------------------
    formset_base = {
        "items-TOTAL_FORMS": "1",
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0",
        "items-MAX_NUM_FORMS": "1000",
    }
    response = client.post(
        "/purchasing/purchase-orders/store",
        {
            "vendor": vendor.id,
            "order_date": timezone.localdate().isoformat(),
            "expected_delivery": timezone.localdate().isoformat(),
            "terms": "Net 30",
            "notes": "Smoke PO",
            "items-0-product": product.id,
            "items-0-product_name": product.name,
            "items-0-description": "Smoke purchase",
            "items-0-quantity": "10",
            "items-0-unit_price": "100.00",
            "items-0-discount": "50.00",
            "items-0-tax_rate": "16",
            **formset_base,
        },
    )
    check("create PO redirects", response.status_code == 302)
    po = PurchaseOrder.objects.order_by("-id").first()
    check("PO created", po is not None and po.vendor_id == vendor.id)
    check("PO totals computed", po is not None and po.subtotal == Decimal("950.00") and po.tax_amount == Decimal("152.00") and po.total_amount == Decimal("1102.00"))
    check("PO status draft", po is not None and po.status == "draft")
    po_item = PurchaseOrderItem.objects.filter(purchase_order_id=po.id).first()
    check("PO item created", po_item is not None and po_item.product_name == product.name)
    get(f"/purchasing/purchase-orders/view/{po.id}")

    response = client.post(f"/purchasing/purchase-orders/send/{po.id}")
    check("send PO redirects", response.status_code == 302)
    po.refresh_from_db()
    check("PO marked sent", po.status == "sent" and po.sent_at is not None)

    response = client.post(f"/purchasing/purchase-orders/confirm/{po.id}")
    check("confirm PO redirects", response.status_code == 302)
    po.refresh_from_db()
    check("PO confirmed", po.status == "confirmed" and po.confirmed_at is not None)
    get(f"/purchasing/purchase-orders/view/{po.id}")

    # --- Goods receipt: partial, then over-receipt rejection, then complete -------
    get(f"/purchasing/goods-receipts/create?po_id={po.id}")
    response = client.post(
        "/purchasing/goods-receipts/store",
        {
            "purchase_order_id": str(po.id),
            "receipt_date": timezone.localdate().isoformat(),
            "invoice_number": "INV-SMOKE-1",
            "delivery_note": "DN-SMOKE-1",
            "notes": "Partial receipt",
            "po_item_id": [str(po_item.id)],
            "quantity_received": ["4"],
            "quantity_accepted": ["3"],
            "quantity_rejected": ["1"],
            "rejection_reason": ["Damaged"],
        },
    )
    check("create GRN redirects", response.status_code == 302)
    grn = GoodsReceipt.objects.filter(purchase_order_id=po.id).first()
    check("GRN created and confirmed", grn is not None and grn.status == "confirmed")
    po_item.refresh_from_db()
    product.refresh_from_db()
    check("received quantity is accepted qty", po_item.received_quantity == Decimal("3.00"))
    check("stock increased by accepted qty", product.current_stock == starting_stock + Decimal("3.00"))
    check("purchase movement logged", StockMovement.objects.filter(reference_type="goods_receipt", reference_id=grn.id, movement_type="purchase").exists())
    po.refresh_from_db()
    check("PO partially received", po.status == "partially_received")
    get(f"/purchasing/goods-receipts/view/{grn.id}")

    grn_count = GoodsReceipt.objects.filter(purchase_order_id=po.id).count()
    stock_before_over = product.current_stock
    response = client.post(
        "/purchasing/goods-receipts/store",
        {
            "purchase_order_id": str(po.id),
            "receipt_date": timezone.localdate().isoformat(),
            "po_item_id": [str(po_item.id)],
            "quantity_received": ["8"],
            "quantity_accepted": ["8"],
            "quantity_rejected": ["0"],
        },
    )
    check("over-receipt rejected with 200", response.status_code == 200)
    check("over-receipt creates no GRN", GoodsReceipt.objects.filter(purchase_order_id=po.id).count() == grn_count)
    product.refresh_from_db()
    check("over-receipt leaves stock unchanged", product.current_stock == stock_before_over)

    response = client.post(
        "/purchasing/goods-receipts/store",
        {
            "purchase_order_id": str(po.id),
            "receipt_date": timezone.localdate().isoformat(),
            "invoice_number": "INV-SMOKE-2",
            "delivery_note": "",
            "notes": "Final receipt",
            "po_item_id": [str(po_item.id)],
            "quantity_received": ["7"],
            "quantity_accepted": ["7"],
            "quantity_rejected": ["0"],
        },
    )
    check("final receipt redirects", response.status_code == 302)
    po.refresh_from_db()
    po_item.refresh_from_db()
    product.refresh_from_db()
    check("all PO quantity received", po_item.received_quantity == Decimal("10.00") and po.status == "fully_received")
    check("stock increased by all accepted qty", product.current_stock == starting_stock + Decimal("10.00"))

    # --- Supplier bill ------------------------------------------------------------
    get(f"/purchasing/supplier-bills/create?po_id={po.id}")
    bill_data = {
        "purchase_order_id": str(po.id),
        "vendor": vendor.id,
        "bill_number": "SMOKE-BILL-1",
        "bill_date": timezone.localdate().isoformat(),
        "due_date": timezone.localdate().isoformat(),
        "reference": "SUPPLIER-REF",
        "notes": "Smoke bill",
        "items-0-product": product.id,
        "items-0-description": "Smoke bill line",
        "items-0-quantity": "10",
        "items-0-unit_price": "100.00",
        "items-0-discount": "0",
        "items-0-tax_rate": "16",
        **formset_base,
    }
    response = client.post("/purchasing/supplier-bills/store", bill_data)
    check("create bill redirects", response.status_code == 302)
    bill = SupplierBill.objects.filter(bill_number="SMOKE-BILL-1").first()
    check("bill created and received", bill is not None and bill.status == "received")
    check("bill linked to PO", bill is not None and bill.purchase_order_id == po.id)
    check("bill totals computed", bill is not None and bill.subtotal == Decimal("1000.00") and bill.tax_amount == Decimal("160.00") and bill.total_amount == Decimal("1160.00"))
    entry = JournalEntry.objects.filter(entry_number="AP-SMOKE-BILL-1").first()
    check("bill journal posted", entry is not None and entry.status == "posted")
    if entry:
        lines = list(JournalEntryLine.objects.filter(journal_entry_id=entry.id))
        debit = sum((line.amount for line in lines if line.entry_type == "debit"), Decimal("0"))
        credit = sum((line.amount for line in lines if line.entry_type == "credit"), Decimal("0"))
        check("bill journal balanced", debit == Decimal("1160.00") and credit == Decimal("1160.00"))
    get(f"/purchasing/supplier-bills/view/{bill.id}")

    # Duplicate user-provided bill number must be rejected.
    duplicate = client.post("/purchasing/supplier-bills/store", bill_data)
    check("duplicate bill number rejected", duplicate.status_code == 200 and SupplierBill.objects.filter(bill_number="SMOKE-BILL-1").count() == 1)

    response = client.post(f"/purchasing/supplier-bills/approve/{bill.id}")
    check("approve bill redirects", response.status_code == 302)
    bill.refresh_from_db()
    check("bill approved", bill.status == "approved" and bill.approved_at is not None)
    get(f"/purchasing/supplier-bills/view/{bill.id}")

    # --- Supplier payments --------------------------------------------------------
    get(f"/purchasing/supplier-payments/create?bill_id={bill.id}")
    payment_data = {
        "vendor": vendor.id,
        "supplier_bill": bill.id,
        "payment_date": timezone.localdate().isoformat(),
        "amount": "500.00",
        "payment_method": "bank_transfer",
        "bank_account": "",
        "reference": "PAY-SMOKE-1",
        "notes": "First instalment",
    }
    response = client.post("/purchasing/supplier-payments/store", payment_data)
    check("create partial payment redirects", response.status_code == 302)
    payment1 = SupplierPayment.objects.filter(supplier_bill_id=bill.id).order_by("id").first()
    check("partial payment created", payment1 is not None and payment1.amount == Decimal("500.00"))
    bill.refresh_from_db()
    check("bill partially paid", bill.status == "partially_paid" and bill.paid_amount == Decimal("500.00"))
    payment_entry = JournalEntry.objects.filter(entry_number=f"PMT-{payment1.payment_number}").first() if payment1 else None
    check("payment journal posted", payment_entry is not None and payment_entry.status == "posted")
    get(f"/purchasing/supplier-payments/view/{payment1.id}")

    # Reports should show the still-outstanding bill.
    get("/purchasing/reports/ap-aging")
    get("/purchasing/reports/purchase-summary")
    response = client.get("/purchasing/reports/export?type=ap_aging")
    check("AP aging export returns CSV", response.status_code == 200 and response["Content-Type"].startswith("text/csv"))
    response = client.get("/purchasing/reports/export?type=purchase_summary")
    check("purchase summary export returns CSV", response.status_code == 200 and response["Content-Type"].startswith("text/csv"))

    payment_data["amount"] = "660.00"
    payment_data["reference"] = "PAY-SMOKE-2"
    response = client.post("/purchasing/supplier-payments/store", payment_data)
    check("create final payment redirects", response.status_code == 302)
    bill.refresh_from_db()
    check("bill fully paid", bill.status == "paid" and bill.paid_amount == Decimal("1160.00") and bill.paid_at is not None)
    payment2 = SupplierPayment.objects.filter(supplier_bill_id=bill.id).order_by("-id").first()
    check("second payment recorded", payment2 is not None and payment2.amount == Decimal("660.00"))
    get(f"/purchasing/supplier-payments/view/{payment2.id}")

    # Overpayment is blocked while the bill is still open.
    new_bill = SupplierBill.objects.create(
        bill_number="SMOKE-BILL-2",
        vendor_id=vendor.id,
        bill_date=timezone.localdate(),
        due_date=timezone.localdate(),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("16.00"),
        total_amount=Decimal("116.00"),
        paid_amount=Decimal("0.00"),
        status="approved",
        created_by=1,
        created_at=timezone.now(),
    )
    response = client.post(
        "/purchasing/supplier-payments/store",
        {
            "vendor": vendor.id,
            "supplier_bill": new_bill.id,
            "payment_date": timezone.localdate().isoformat(),
            "amount": "500.00",
            "payment_method": "cash",
            "reference": "",
            "notes": "",
        },
    )
    check("overpayment rejected", response.status_code == 200 and not SupplierPayment.objects.filter(supplier_bill_id=new_bill.id).exists())

    # --- RFQ ----------------------------------------------------------------------
    response = client.post(
        "/purchasing/rfq/store",
        {
            "title": "Smoke RFQ",
            "required_date": timezone.localdate().isoformat(),
            "description": "Request for smoke items",
            "items-0-product": product.id,
            "items-0-description": "Smoke item",
            "items-0-quantity": "5",
            "items-0-unit_of_measure": "pcs",
            **formset_base,
        },
    )
    check("create RFQ redirects", response.status_code == 302)
    rfq = RfqItem.objects.order_by("-id").first()
    check("RFQ item created", rfq is not None and rfq.quantity == Decimal("5.00"))
    rfq_parent_id = rfq.rfq_id if rfq else None
    rfq_obj = __import__("apps.core.models", fromlist=["RequestForQuotation"]).RequestForQuotation.objects.filter(pk=rfq_parent_id).first()
    check("RFQ created as draft", rfq_obj is not None and rfq_obj.status == "draft")
    get(f"/purchasing/rfq/view/{rfq_parent_id}")
    response = client.post(f"/purchasing/rfq/send/{rfq_parent_id}")
    check("send RFQ redirects", response.status_code == 302)
    rfq_obj.refresh_from_db()
    check("RFQ sent", rfq_obj.status == "sent")

    # --- Permission boundaries ----------------------------------------------------
    client.logout()
    check("sales login", client.login(email="sales@sora.com", password="sales123"))
    response = client.get("/purchasing/dashboard")
    check("sales blocked from purchasing (403)", response.status_code == 403)
    client.logout()
    check("manager login", client.login(email="manager@sora.com", password="manager123"))
    get("/purchasing/dashboard")
    order_count = PurchaseOrder.objects.count()
    response = client.post(
        "/purchasing/purchase-orders/store",
        {
            "vendor": vendor.id,
            "order_date": timezone.localdate().isoformat(),
            **formset_base,
        },
    )
    check("purchasing viewer cannot create PO", response.status_code == 302 and PurchaseOrder.objects.count() == order_count)

    transaction.set_rollback(True)

    print(f"{passed} checks passed, {len(failed)} failed.")
    if failed:
        raise SystemExit(1)
