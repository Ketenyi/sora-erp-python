"""Seed retail POS products, historical shifts and a live demo till."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import Client, Product, ProductCategory, User
from apps.core.services import next_sequence
from apps.core.tenancy import default_tenant, tenant_context
from apps.pos.models import PosSession
from apps.pos.services import create_sale, money, session_summary
from apps.saas.models import SaasModule, TenantModule

ZERO = Decimal("0.00")

PRODUCTS = [
    {
        "code": "POS-1001",
        "name": "Erin 500ml Mineral Water",
        "unit": "pcs",
        "price": "60.00",
        "cost": "35.00",
        "stock": "120",
        "reorder": "24",
        "tax": "16.00",
    },
    {
        "code": "POS-1002",
        "name": "Bic Cristal Ballpoint Pen",
        "unit": "pcs",
        "price": "20.00",
        "cost": "10.00",
        "stock": "300",
        "reorder": "50",
        "tax": "16.00",
    },
    {
        "code": "POS-1003",
        "name": "A4 Copy Paper (Ream)",
        "unit": "ream",
        "price": "650.00",
        "cost": "480.00",
        "stock": "44",
        "reorder": "10",
        "tax": "16.00",
    },
    {
        "code": "POS-1004",
        "name": "USB-C Charger 65W",
        "unit": "pcs",
        "price": "2200.00",
        "cost": "1450.00",
        "stock": "18",
        "reorder": "6",
        "tax": "16.00",
    },
    {
        "code": "POS-1005",
        "name": "Wireless Mouse",
        "unit": "pcs",
        "price": "2800.00",
        "cost": "1750.00",
        "stock": "3",
        "reorder": "5",
        "tax": "16.00",
    },
    {
        "code": "POS-1006",
        "name": "Thermal Receipt Roll",
        "unit": "roll",
        "price": "180.00",
        "cost": "110.00",
        "stock": "0",
        "reorder": "12",
        "tax": "16.00",
    },
    {
        "code": "POS-1007",
        "name": "Same-day Delivery",
        "unit": "trip",
        "price": "500.00",
        "cost": "0.00",
        "stock": "0",
        "reorder": "0",
        "tax": "16.00",
        "type": "service",
    },
]

SHIFTS = [
    {
        "days_ago": 2,
        "terminal": "Front Counter",
        "float": "5000.00",
        "open_time": time(8, 10),
        "close_time": time(18, 15),
        "variance": "-40.00",
        "sales": [
            {
                "time": time(9, 10),
                "lines": [("POS-1001", "3"), ("POS-1002", "2")],
                "payments": [("cash", "rest", "")],
            },
            {
                "time": time(10, 45),
                "lines": [("POS-1003", "1"), ("POS-1004", "1")],
                "payments": [("m-pesa", "2000.00", "QK73JD82L1"), ("cash", "rest", "")],
            },
            {
                "time": time(12, 5),
                "lines": [("POS-1007", "1")],
                "payments": [("card", "rest", "VISA-4412")],
            },
            {
                "time": time(15, 40),
                "lines": [("POS-1005", "1"), ("POS-1003", "2")],
                "payments": [("cash", "rest", "")],
            },
        ],
    },
    {
        "days_ago": 1,
        "terminal": "Front Counter",
        "float": "4000.00",
        "open_time": time(8, 5),
        "close_time": time(18, 2),
        "variance": "0.00",
        "sales": [
            {
                "time": time(9, 20),
                "lines": [("POS-1001", "6"), ("POS-1002", "4")],
                "payments": [("cash", "rest", "")],
            },
            {
                "time": time(13, 15),
                "lines": [("POS-1004", "2"), ("POS-1007", "1")],
                "discount": "84.00",
                "payments": [("m-pesa", "3000.00", "QK81LM20T7"), ("card", "rest", "VISA-4412")],
                "note": "Corporate stationery order.",
            },
        ],
    },
]


class Command(BaseCommand):
    help = "Create POS retail products, closed demo shifts and an open till for admin."

    def handle(self, *args, **options):
        tenant = default_tenant()
        if tenant is None:
            raise CommandError("No workspace exists. Apply migrations before seeding.")
        admin = User.objects.filter(email="admin@sora.com").first()
        if admin is None:
            raise CommandError("Demo admin (admin@sora.com) was not found. Seed users first.")

        module = SaasModule.objects.filter(code="SORA_POS").first()
        if module is not None and not module.is_available:
            module.is_available = True
            module.save(update_fields=["is_available"])
        if module is not None:
            TenantModule.objects.get_or_create(
                tenant=tenant,
                module=module,
                defaults={"is_enabled": True, "source": "manual", "note": "Demo showcase"},
            )

        with tenant_context(tenant):
            with transaction.atomic():
                self._seed(admin)

    def _seed(self, admin):
        now = timezone.localtime()
        tz = timezone.get_current_timezone()
        category, _ = ProductCategory.objects.get_or_create(
            category_code="CAT-POS",
            defaults={
                "category_name": "Retail Counter",
                "description": "Over-the-counter retail products for the POS terminal.",
                "status": "active",
                "created_by": admin.id,
                "created_at": now,
            },
        )

        products = {}
        created_products = 0
        for spec in PRODUCTS:
            product, created = Product.objects.get_or_create(
                product_code=spec["code"],
                defaults={
                    "sku": spec["code"],
                    "name": spec["name"],
                    "product_name": spec["name"],
                    "description": "POS demo product.",
                    "category_id": category.id,
                    "unit": spec["unit"],
                    "type": spec.get("type", "product"),
                    "unit_price": Decimal(spec["price"]),
                    "cost_price": Decimal(spec["cost"]),
                    "current_stock": Decimal(spec["stock"]),
                    "stock": int(Decimal(spec["stock"])),
                    "minimum_stock": Decimal(spec["reorder"]),
                    "maximum_stock": Decimal(spec["stock"]) * 3,
                    "reorder_level": Decimal(spec["reorder"]),
                    "reorder_quantity": Decimal(spec["reorder"]) * 2,
                    "tax_rate": Decimal(spec["tax"]),
                    "status": "active",
                    "created_by": admin.id,
                },
            )
            products[spec["code"]] = product
            created_products += int(created)

        if PosSession.objects.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    f"POS demo ready: {created_products} new product(s); till sessions already present."
                )
            )
            return

        client = Client.objects.filter(status="active").order_by("id").first()
        created_sales = 0
        for shift in SHIFTS:
            day = now.date() - timedelta(days=shift["days_ago"])
            opened_at = timezone.make_aware(
                datetime.combine(day, shift["open_time"]), tz
            )
            session = PosSession.objects.create(
                session_number=next_sequence("SES", "pos_sessions", "session_number"),
                terminal_name=shift["terminal"],
                opened_by=admin.id,
                opened_at=opened_at,
                opening_float=Decimal(shift["float"]),
                status="open",
                notes="Demo shift seeded for the POS showcase.",
            )
            for index, sale_spec in enumerate(shift["sales"]):
                sold_at = timezone.make_aware(
                    datetime.combine(day, sale_spec["time"]), tz
                )
                self._create_sale(
                    session,
                    sale_spec,
                    products,
                    admin.id,
                    sold_at,
                    client if index == 1 else None,
                )
                created_sales += 1
            summary = session_summary(session)
            variance = Decimal(shift["variance"])
            closing = money(summary["expected_cash"] + variance)
            closed_at = timezone.make_aware(
                datetime.combine(day, shift["close_time"]), tz
            )
            session.expected_cash = summary["expected_cash"]
            session.closing_float = closing
            session.cash_variance = money(closing - summary["expected_cash"])
            session.closed_by = admin.id
            session.closed_at = closed_at
            session.status = "closed"
            session.save()

        opened_at = now - timedelta(hours=2)
        session = PosSession.objects.create(
            session_number=next_sequence("SES", "pos_sessions", "session_number"),
            terminal_name="Front Counter",
            opened_by=admin.id,
            opened_at=opened_at,
            opening_float=Decimal("3000.00"),
            status="open",
            notes="Live demo till. Cash up from the Sessions screen.",
        )
        live_sales = [
            {
                "lines": [("POS-1003", "3")],
                "payments": [("m-pesa", "rest", "QK90BN41X2")],
                "sold_at": now - timedelta(minutes=78),
            },
            {
                "lines": [("POS-1001", "2"), ("POS-1002", "5")],
                "payments": [("cash", "rest", "")],
                "sold_at": now - timedelta(minutes=32),
            },
        ]
        for sale_spec in live_sales:
            self._create_sale(
                session, sale_spec, products, admin.id, sale_spec["sold_at"], None
            )
            created_sales += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"POS demo ready: {created_products} new product(s), "
                f"{len(SHIFTS)} closed shift(s), 1 open till, {created_sales} sale(s)."
            )
        )

    def _create_sale(self, session, spec, products, admin_id, sold_at, client):
        lines = []
        subtotal = ZERO
        tax = ZERO
        for code, quantity in spec["lines"]:
            product = products[code]
            quantity = Decimal(quantity)
            line_total = money(product.unit_price * quantity)
            subtotal += line_total
            tax += money(line_total * (product.tax_rate or ZERO) / Decimal("100"))
            lines.append({"product_id": product.id, "quantity": str(quantity)})
        discount = Decimal(spec.get("discount", "0.00"))
        total = money(subtotal + tax - discount)

        remaining = total
        payments = []
        for method, amount, reference in spec["payments"]:
            value = remaining if amount == "rest" else Decimal(amount)
            remaining -= value
            payments.append(
                {"method": method, "amount": str(value), "reference": reference}
            )
        if remaining != ZERO:
            payments.append(
                {"method": "cash", "amount": str(remaining), "reference": ""}
            )

        create_sale(
            session=session,
            cashier_id=admin_id,
            items=lines,
            payments=payments,
            discount=discount,
            client=client,
            customer_name=client.company_name if client else "",
            notes=spec.get("note", ""),
            sold_at=sold_at,
        )
