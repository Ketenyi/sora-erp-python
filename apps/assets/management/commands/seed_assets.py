"""Seed a demo asset register, custody, maintenance jobs and depreciation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.assets.models import (
    Asset,
    AssetAssignment,
    AssetCategory,
    AssetDepreciation,
    AssetMaintenance,
)
from apps.assets.services import post_depreciation
from apps.core.models import Employee, User
from apps.core.tenancy import default_tenant, tenant_context
from apps.saas.models import SaasModule, TenantModule

CATEGORIES = [
    ("CAT-0001", "IT Equipment", 5, "20.00", "Laptops, servers, network and office IT hardware."),
    ("CAT-0002", "Vehicles", 8, "12.50", "Project vehicles, pickups and staff pool cars."),
    ("CAT-0003", "Office Furniture", 10, "10.00", "Desks, chairs, cabinets and fit-out."),
    ("CAT-0004", "Plant & Machinery", 7, "15.00", "Forklifts, compressors and site plant."),
    ("CAT-0005", "Power Equipment", 5, "20.00", "Generators, UPS units and solar equipment."),
]

ASSETS = [
    {
        "asset_code": "AST-0001",
        "name": "Dell Latitude 5440 Laptop",
        "category_code": "CAT-0001",
        "serial_number": "DL5440-2024-0912",
        "model_name": "Latitude 5440",
        "manufacturer": "Dell",
        "location": "HQ · Finance",
        "purchase_date": "2024-03-18",
        "purchase_cost": "185000.00",
        "salvage_value": "15000.00",
        "useful_life_years": 5,
        "depreciation_method": "straight_line",
        "depreciation_rate": "20.00",
        "status": "in_use",
        "condition": "good",
        "holder_index": 0,
    },
    {
        "asset_code": "AST-0002",
        "name": "Toyota Hilux Double Cab",
        "category_code": "CAT-0002",
        "serial_number": "KDJ150-8891245",
        "model_name": "Hilux 2.8 GD-6",
        "manufacturer": "Toyota",
        "location": "Mombasa Road project",
        "purchase_date": "2023-06-05",
        "purchase_cost": "4850000.00",
        "salvage_value": "1200000.00",
        "useful_life_years": 8,
        "depreciation_method": "declining_balance",
        "depreciation_rate": "12.50",
        "status": "in_use",
        "condition": "good",
        "holder_index": 1,
    },
    {
        "asset_code": "AST-0003",
        "name": "Cummins 30 kVA Generator",
        "category_code": "CAT-0005",
        "serial_number": "C30D5-77120",
        "model_name": "C30D5",
        "manufacturer": "Cummins",
        "location": "HQ · Basement plant room",
        "purchase_date": "2022-11-02",
        "purchase_cost": "850000.00",
        "salvage_value": "85000.00",
        "useful_life_years": 5,
        "depreciation_method": "straight_line",
        "depreciation_rate": "20.00",
        "status": "under_maintenance",
        "condition": "fair",
        "holder_index": None,
    },
    {
        "asset_code": "AST-0004",
        "name": "Server rack & core switch",
        "category_code": "CAT-0001",
        "serial_number": "RACK-42U-3301",
        "model_name": "42U rack + Catalyst 9200",
        "manufacturer": "Cisco / APC",
        "location": "HQ · Server room",
        "purchase_date": "2024-09-10",
        "purchase_cost": "620000.00",
        "salvage_value": "60000.00",
        "useful_life_years": 5,
        "depreciation_method": "straight_line",
        "depreciation_rate": "20.00",
        "status": "available",
        "condition": "good",
        "holder_index": None,
    },
    {
        "asset_code": "AST-0005",
        "name": "Toyota forklift 2.5T",
        "category_code": "CAT-0004",
        "serial_number": "8FG25-33210",
        "model_name": "8FG25",
        "manufacturer": "Toyota Material Handling",
        "location": "Warehouse · Industrial Area",
        "purchase_date": "2021-08-23",
        "purchase_cost": "2300000.00",
        "salvage_value": "300000.00",
        "useful_life_years": 7,
        "depreciation_method": "straight_line",
        "depreciation_rate": "15.00",
        "status": "available",
        "condition": "fair",
        "holder_index": None,
    },
    {
        "asset_code": "AST-0006",
        "name": "Executive meeting table",
        "category_code": "CAT-0003",
        "serial_number": "",
        "model_name": "12-seater boardroom",
        "manufacturer": "Local fit-out",
        "location": "HQ · Boardroom",
        "purchase_date": "2025-01-15",
        "purchase_cost": "240000.00",
        "salvage_value": "20000.00",
        "useful_life_years": 10,
        "depreciation_method": "straight_line",
        "depreciation_rate": "10.00",
        "status": "available",
        "condition": "new",
        "holder_index": None,
    },
]


class Command(BaseCommand):
    help = "Create demo asset categories, assets, custody and depreciation entries."

    def handle(self, *args, **options):
        tenant = default_tenant()
        if tenant is None:
            raise CommandError("No workspace exists. Apply migrations before seeding.")
        module = SaasModule.objects.filter(code="SORA_ASSET_MANAGEMENT").first()
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
                self._seed()

    def _seed(self):
        categories = {}
        for code, name, life, rate, description in CATEGORIES:
            category, _created = AssetCategory.objects.get_or_create(
                category_code=code,
                defaults={
                    "category_name": name,
                    "description": description,
                    "useful_life_years": life,
                    "depreciation_rate": Decimal(rate),
                    "status": "active",
                },
            )
            categories[code] = category

        employees = list(Employee.objects.filter(status="active").order_by("id")[:4])
        today = timezone.localdate()
        created_assets = 0
        assets = {}
        for spec in ASSETS:
            defaults = {
                key: value
                for key, value in spec.items()
                if key not in {"asset_code", "category_code", "holder_index", "purchase_date"}
            }
            defaults["category"] = categories[spec["category_code"]]
            defaults["purchase_date"] = date.fromisoformat(spec["purchase_date"])
            asset, created = Asset.objects.get_or_create(
                asset_code=spec["asset_code"], defaults=defaults
            )
            assets[spec["asset_code"]] = asset
            if not created:
                continue
            created_assets += 1
            holder_index = spec["holder_index"]
            if holder_index is not None and employees:
                employee = employees[holder_index % len(employees)]
                assigned_at = timezone.now() - timedelta(days=45 - holder_index * 7)
                AssetAssignment.objects.create(
                    asset=asset,
                    employee=employee,
                    custodian_name="",
                    assigned_at=assigned_at,
                    expected_return_date=today + timedelta(days=30 + holder_index * 10),
                    condition_out=asset.condition,
                    notes="Demo custody record.",
                )
                asset.assigned_to = employee
                asset.assigned_at = assigned_at
                asset.status = "in_use"
                asset.save(update_fields=["assigned_to", "assigned_at", "status", "updated_at"])

        admin = User.objects.filter(email="admin@sora.com").first()
        admin_id = admin.id if admin else None
        if not AssetMaintenance.objects.exists():
            generator = assets.get("AST-0003")
            vehicle = assets.get("AST-0002")
            laptop = assets.get("AST-0001")
            if generator:
                AssetMaintenance.objects.create(
                    asset=generator,
                    maintenance_type="corrective",
                    title="Generator failing to hold load",
                    description="Radiator leak and load transfer switch fault reported during monthly test.",
                    scheduled_date=today - timedelta(days=3),
                    vendor="Cummins East Africa",
                    cost=Decimal("78000.00"),
                    status="in_progress",
                    created_by=admin_id,
                )
            if vehicle:
                AssetMaintenance.objects.create(
                    asset=vehicle,
                    maintenance_type="preventive",
                    title="40,000 km service",
                    description="Full service, brake inspection and tyre rotation.",
                    scheduled_date=today + timedelta(days=12),
                    vendor="Toyota Kenya",
                    cost=Decimal("42000.00"),
                    status="scheduled",
                    created_by=admin_id,
                )
            if laptop:
                AssetMaintenance.objects.create(
                    asset=laptop,
                    maintenance_type="inspection",
                    title="Battery health inspection",
                    description="Battery replaced under warranty after swelling reported.",
                    scheduled_date=today - timedelta(days=35),
                    completed_date=today - timedelta(days=32),
                    vendor="Dell Support",
                    cost=Decimal("4500.00"),
                    status="completed",
                    created_by=admin_id,
                )
            AssetMaintenance.objects.create(
                asset=assets["AST-0005"],
                maintenance_type="calibration",
                title="Annual load calibration",
                description="Load cell calibration and safety inspection.",
                scheduled_date=today + timedelta(days=25),
                vendor="Toyota Material Handling",
                cost=Decimal("18500.00"),
                status="scheduled",
                created_by=admin_id,
            )

        posted_total = 0
        if not AssetDepreciation.objects.exists():
            first_of_month = today.replace(day=1)
            periods = []
            cursor = first_of_month
            for _ in range(3):
                cursor = (cursor - timedelta(days=1)).replace(day=1)
                periods.append(cursor)
            for period in reversed(periods):
                summary = post_depreciation(period, admin_id)
                posted_total += summary["posted"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Asset demo ready: {len(categories)} categories, "
                f"{created_assets} new assets, {posted_total} depreciation entries."
            )
        )
