"""End-to-end smoke test for the POS module (rolled back at the end)."""

import json
import os
from decimal import Decimal

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import Client, Invoice, InvoiceItem, Product, StockMovement
from apps.core.tenancy import default_tenant, tenant_context
from apps.pos.models import PosRefund, PosSale, PosSession
from apps.pos.services import session_summary

ZERO = Decimal("0.00")


class Rollback(Exception):
    pass


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")
tenant = default_tenant()
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


def post_json(path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


try:
    with transaction.atomic():
        with tenant_context(tenant):
            PosSession.objects.filter(status="open").update(
                status="closed", closed_at=timezone.now(), closed_by=1
            )

        for url in (
            "/pos/",
            "/pos/dashboard",
            "/pos/sales",
            "/pos/sessions",
            "/pos/sales?status=voided",
            "/settings/pos",
        ):
            response = client.get(url)
            check(f"GET {url}", response.status_code == 200, response.status_code)

        response = client.post(
            "/pos/sessions/open",
            data={
                "terminal_name": "Smoke Till",
                "opening_float": "1500.00",
                "notes": "",
                "next": "/pos/",
            },
        )
        check("POST session open", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            session = PosSession.objects.filter(
                status="open", terminal_name="Smoke Till"
            ).first()
            water = Product.objects.get(product_code="POS-1001")
            service = Product.objects.get(product_code="POS-1007")
            customer = Client.objects.filter(status="active").first()
            water_before = water.current_stock
            session_id = session.id
        check("till session created", session is not None)
        check("active customer available", customer is not None)

        response = client.get(f"/pos/sessions/{session_id}")
        check("GET session detail", response.status_code == 200, response.status_code)

        # --- Split-payment sale with change and idempotency ---------------
        payload = {
            "session_id": session_id,
            "items": [
                {"product_id": water.id, "quantity": 2},
                {"product_id": service.id, "quantity": 1},
            ],
            "payments": [
                {"method": "cash", "amount": "500.00"},
                {"method": "m-pesa", "amount": "300.00", "reference": "SMOKE-001"},
            ],
            "discount": "0.00",
            "customer_name": "Smoke Test",
            "client_reference": "SMOKE-SALE-A",
        }
        response = post_json("/pos/checkout", payload)
        data = response.json()
        check("POST checkout", response.status_code == 200 and data.get("ok"), data)
        check("checkout total", data.get("total") == "719.20", data.get("total"))
        check("checkout change", data.get("change") == "80.80", data.get("change"))
        sale_a_id = data.get("sale_id")

        with tenant_context(tenant):
            sale = PosSale.objects.get(pk=sale_a_id)
            water_after = Product.objects.get(pk=water.id).current_stock
            check("sale persisted", sale.status == "completed" and sale.items.count() == 2)
            check("split payments stored", sale.payments.count() == 2)
            check("stock decremented", water_after == water_before - 2, f"{water_before} -> {water_after}")
            check("tax on supply", sale.tax_amount == Decimal("99.20"), sale.tax_amount)
            check(
                "stock movement written",
                StockMovement.objects.filter(
                    reference_type="pos_sale", reference_id=sale.id, product_id=water.id
                ).exists(),
            )
            check(
                "service not tracked",
                not StockMovement.objects.filter(
                    reference_type="pos_sale", reference_id=sale.id, product_id=service.id
                ).exists(),
            )

        duplicate = post_json("/pos/checkout", payload).json()
        check("idempotent replay skips duplicate", duplicate.get("sale_id") == sale_a_id, duplicate)
        with tenant_context(tenant):
            check(
                "idempotent replay keeps one sale",
                PosSale.objects.filter(client_reference="SMOKE-SALE-A").count() == 1,
            )
            check(
                "idempotent replay keeps stock",
                Product.objects.get(pk=water.id).current_stock == water_before - 2,
            )

        response = client.get(f"/pos/sales/{sale_a_id}")
        check("GET receipt", response.status_code == 200 and data["sale_number"] in response.content.decode())
        response = client.get(f"/pos/sales/{sale_a_id}/refund")
        check("GET refund form", response.status_code == 200, response.status_code)

        # --- Refund one unit, with stock restock -------------------------
        with tenant_context(tenant):
            sale = PosSale.objects.get(pk=sale_a_id)
            water_item = sale.items.get(product_id=water.id)
            water_item_id = water_item.id
        response = client.post(
            f"/pos/sales/{sale_a_id}/refund",
            data={
                "method": "cash",
                f"qty_{water_item_id}": "1",
                f"restock_{water_item_id}": "on",
                "reason": "Damaged bottle",
            },
        )
        check("POST refund", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            sale = PosSale.objects.get(pk=sale_a_id)
            refund = PosRefund.objects.filter(sale=sale).first()
            check("refund recorded", refund is not None)
            check(
                "refund total",
                refund is not None and refund.total_amount == Decimal("69.60"),
                refund and refund.total_amount,
            )
            check(
                "refund restocked",
                Product.objects.get(pk=water.id).current_stock == water_before - 1,
            )
            check("sale refunded amount", sale.refunded_amount == Decimal("69.60"), sale.refunded_amount)
            check(
                "refund movement written",
                StockMovement.objects.filter(
                    reference_type="pos_refund", reference_id=refund.id
                ).exists(),
            )
            refund_id = refund.id
        response = client.get(f"/pos/refunds/{refund_id}")
        check("GET refund receipt", response.status_code == 200, response.status_code)
        response = client.get(f"/pos/sales/{sale_a_id}")
        check("receipt lists refund", response.status_code == 200)

        response = client.post(
            f"/pos/sales/{sale_a_id}/refund",
            data={
                "method": "cash",
                f"qty_{water_item_id}": "2",
                "reason": "Over-refund attempt",
            },
        )
        check("over-refund stays on form", response.status_code == 200, response.status_code)
        with tenant_context(tenant):
            check(
                "no extra refund created",
                PosRefund.objects.filter(sale_id=sale_a_id).count() == 1,
            )

        response = client.post(
            f"/pos/sales/{sale_a_id}/void",
            data={"reason": "Should be blocked"},
            follow=True,
        )
        check("GET void redirect", response.status_code == 200)
        with tenant_context(tenant):
            check(
                "refunded sale not voidable",
                PosSale.objects.get(pk=sale_a_id).status == "completed",
            )

        # --- Discount is applied before tax ------------------------------
        response = post_json(
            "/pos/checkout",
            {
                "session_id": session_id,
                "items": [
                    {"product_id": water.id, "quantity": 2},
                    {"product_id": service.id, "quantity": 1},
                ],
                "payments": [{"method": "cash", "amount": "700.00"}],
                "discount": "62.00",
                "customer_name": "Discount Test",
            },
        )
        data = response.json()
        check("POST discounted checkout", response.status_code == 200 and data.get("ok"), data)
        check("discounted total", data.get("total") == "647.28", data.get("total"))
        check("discounted change", data.get("change") == "52.72", data.get("change"))
        sale_b_id = data.get("sale_id")
        with tenant_context(tenant):
            sale_b = PosSale.objects.get(pk=sale_b_id)
            check("discount stored", sale_b.discount_amount == Decimal("62.00"), sale_b.discount_amount)
            check("tax on discounted supply", sale_b.tax_amount == Decimal("89.28"), sale_b.tax_amount)
            water_b_item_id = sale_b.items.get(product_id=water.id).id

        response = client.post(
            f"/pos/sales/{sale_b_id}/refund",
            data={
                "method": "cash",
                f"qty_{water_b_item_id}": "1",
                f"restock_{water_b_item_id}": "on",
                "reason": "Partial return",
            },
        )
        check("POST discounted refund", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            refund_b = PosRefund.objects.filter(sale_id=sale_b_id).first()
            check(
                "discounted refund prorates discount",
                refund_b is not None and refund_b.total_amount == Decimal("62.64"),
                refund_b and refund_b.total_amount,
            )

        # --- Credit sale creates an invoice ------------------------------
        response = post_json(
            "/pos/checkout",
            {
                "session_id": session_id,
                "items": [{"product_id": water.id, "quantity": 1}],
                "payments": [{"method": "credit", "amount": "69.60"}],
                "client_id": customer.id,
                "customer_name": customer.company_name,
            },
        )
        data = response.json()
        check("POST credit checkout", response.status_code == 200 and data.get("ok"), data)
        check("credit invoice created", bool(data.get("invoice_id")), data)
        with tenant_context(tenant):
            invoice = Invoice.objects.get(pk=data["invoice_id"])
            sale_c = PosSale.objects.get(pk=data["sale_id"])
            check("invoice total", invoice.total_amount == Decimal("69.60"), invoice.total_amount)
            check("invoice on account unpaid", invoice.status == "sent" and invoice.paid_amount == ZERO)
            check("invoice client", invoice.client_id == customer.id)
            check(
                "invoice items",
                InvoiceItem.objects.filter(invoice_id=invoice.id).count() == 1,
            )
            check("sale linked to invoice", sale_c.invoice_id == invoice.id)

        response = client.get(f"/pos/sales/{sale_c.id}/refund")
        check("invoiced sale refund blocked", response.status_code == 302, response.status_code)

        # --- Void a clean sale restores stock ---------------------------
        response = post_json(
            "/pos/checkout",
            {
                "session_id": session_id,
                "items": [{"product_id": water.id, "quantity": 1}],
                "payments": [{"method": "cash", "amount": "69.60"}],
            },
        )
        data = response.json()
        check("POST voidable checkout", response.status_code == 200 and data.get("ok"), data)
        sale_d_id = data.get("sale_id")
        with tenant_context(tenant):
            before_void = Product.objects.get(pk=water.id).current_stock
        response = client.post(
            f"/pos/sales/{sale_d_id}/void",
            data={"reason": "Smoke test void"},
            follow=True,
        )
        check("POST void", response.status_code == 200)
        with tenant_context(tenant):
            sale_d = PosSale.objects.get(pk=sale_d_id)
            restored = Product.objects.get(pk=water.id).current_stock
            check("void restored stock", restored == before_void + 1, f"{before_void} -> {restored}")
            check("sale marked voided", sale_d.status == "voided")
            check(
                "void movement written",
                StockMovement.objects.filter(
                    reference_type="pos_void", reference_id=sale_d.id
                ).exists(),
            )

        # --- Cash-up nets change and refunds ----------------------------
        with tenant_context(tenant):
            summary = session_summary(PosSession.objects.get(pk=session_id))
            check("cash-up nets change/refunds", summary["cash"] == Decimal("934.24"), summary["cash"])
            check("credit tender tracked", summary["by_method"].get("credit") == Decimal("69.60"))
            check("mpesa tender tracked", summary["by_method"].get("m-pesa") == Decimal("300.00"))
            check("net sales", summary["net"] == Decimal("1303.84"), summary["net"])
            check("refund count", summary["refund_count"] == 2, summary["refund_count"])
            check(
                "expected cash",
                summary["expected_cash"] == Decimal("2434.24"),
                summary["expected_cash"],
            )
            expected = summary["expected_cash"]

        response = client.post(
            f"/pos/sessions/{session_id}/close",
            data={"closing_float": str(expected), "notes": "Smoke close"},
        )
        check("POST session close", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            closed = PosSession.objects.get(pk=session_id)
            check("session closed", closed.status == "closed" and closed.cash_variance == ZERO)

        response = post_json(
            "/pos/checkout",
            {
                "session_id": session_id,
                "items": [{"product_id": water.id, "quantity": 1}],
                "payments": [{"method": "cash", "amount": "69.60"}],
            },
        )
        check("closed session blocks checkout", response.status_code == 400, response.status_code)

        raise Rollback
except Rollback:
    pass

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
