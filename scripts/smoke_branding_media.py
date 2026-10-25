"""Smoke-test tenant branding, tenant-scoped settings and private media."""

import os
import time

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import Client as TestClient

from apps.core.models import CompanySetting
from apps.core.services import get_company_settings
from apps.core.tenancy import default_tenant, tenant_context
from apps.saas.services import provision_tenant

failures = []


class Rollback(Exception):
    pass


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")
tenant = default_tenant()
uploaded_path = ""

try:
    with transaction.atomic():
        response = client.get("/settings/company")
        body = response.content.decode()
        check("company settings open", response.status_code == 200, response.status_code)
        check("accent colour field", 'name="primary_color"' in body)

        response = client.post(
            "/settings/company",
            data={
                "company_name": "Brand Test Ltd",
                "company_email": "hello@brandtest.example",
                "company_phone": "+254 700 000 111",
                "company_website": "",
                "company_address": "1 Test Way",
                "company_pin": "P000000000X",
                "primary_color": "#1A73E8",
                "currency_symbol": "KSh",
                "vat_rate": "16",
                "invoice_due_days": "30",
                "quote_valid_days": "30",
                "default_inclusions": "",
                "default_exclusions": "",
                "default_assumptions": "",
                "default_financial_notes": "",
            },
        )
        check("company settings save", response.status_code == 302, response.status_code)
        tenant.refresh_from_db()
        check("tenant name synced", tenant.company_name == "Brand Test Ltd", tenant.company_name)
        check("tenant colour synced", tenant.primary_color.upper() == "#1A73E8", tenant.primary_color)
        with tenant_context(tenant):
            settings_now = get_company_settings()
        check("tenant setting overrides", settings_now.get("company_name") == "Brand Test Ltd")
        global_name = (
            CompanySetting.objects.filter(tenant__isnull=True, setting_key="company_name")
            .values_list("setting_value", flat=True)
            .first()
        )
        check("global setting untouched", global_name == "SORA ERP", global_name)

        response = client.get("/dashboard")
        body = response.content.decode()
        check("brand colour applied", "--brand: #1A73E8;" in body, "")
        check("tenant brand name shown", "Brand Test Ltd" in body)

        fake_logo = SimpleUploadedFile(
            "brand.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, content_type="image/png"
        )
        response = client.post(
            "/settings/company",
            data={
                "company_name": "Brand Test Ltd",
                "company_email": "hello@brandtest.example",
                "company_phone": "+254 700 000 111",
                "company_website": "",
                "company_address": "1 Test Way",
                "company_pin": "P000000000X",
                "primary_color": "#1A73E8",
                "currency_symbol": "KSh",
                "vat_rate": "16",
                "invoice_due_days": "30",
                "quote_valid_days": "30",
                "default_inclusions": "",
                "default_exclusions": "",
                "default_assumptions": "",
                "default_financial_notes": "",
                "company_logo": fake_logo,
            },
        )
        check("logo upload accepted", response.status_code == 302, response.status_code)
        tenant.refresh_from_db()
        uploaded_path = tenant.logo_url
        check(
            "logo stored per tenant",
            uploaded_path.startswith(f"/uploads/tenants/{tenant.id}/branding/"),
            uploaded_path,
        )
        check("logo file exists", os.path.isfile(f"uploads/{uploaded_path[len('/uploads/'):]}"))
        tenant_logo_setting = (
            CompanySetting.objects.filter(tenant=tenant, setting_key="company_logo")
            .values_list("setting_value", flat=True)
            .first()
        )
        check("logo setting scoped", tenant_logo_setting == uploaded_path, tenant_logo_setting)

        response = client.get(uploaded_path)
        check("own media served", response.status_code == 200, response.status_code)
        check(
            "media headers hardened",
            response["X-Content-Type-Options"] == "nosniff"
            and response["Cache-Control"].startswith("private"),
        )
        check("media content type", response["Content-Type"].startswith("image/png"), response["Content-Type"])

        document = client.get(f"/uploads/tenants/{tenant.id}/hr/employee-contract-sample.pdf")
        check("relocated HR document served", document.status_code == 200, document.status_code)

        legacy = client.get("/uploads/hr/employee-contract-sample.pdf")
        check("legacy unscoped path denied", legacy.status_code == 404, legacy.status_code)

        anon = TestClient(headers={"host": "localhost"})
        response = anon.get(uploaded_path)
        check(
            "anonymous media redirected",
            response.status_code == 302 and "/login" in response["Location"],
            f"{response.status_code} {response.get('Location')}",
        )

        result = provision_tenant(
            company_name="Other Workspace Ltd",
            admin_email="other-owner@example.com",
            password="OtherPass!234",
            first_name="Other",
            last_name="Owner",
            module_codes=["SORA_PROJECTS"],
        )
        other = result["tenant"]
        with tenant_context(other):
            other_settings = get_company_settings()
        check(
            "other tenant starts isolated",
            other_settings.get("company_name") == "Other Workspace Ltd",
            other_settings.get("company_name"),
        )
        other_client = TestClient(headers={"host": "localhost"})
        assert other_client.login(email="other-owner@example.com", password="OtherPass!234")
        response = other_client.get(uploaded_path)
        check("cross-tenant media denied", response.status_code == 404, response.status_code)
        response = other_client.get(f"/uploads/tenants/{other.id}/branding/missing.png")
        check("missing media 404", response.status_code == 404, response.status_code)

        response = client.post(
            "/billing/",
            data={
                "action": "profile",
                "company_name": "Brand Test Ltd",
                "brand_name": "Brand Test",
                "phone_number": "+254 700 000 111",
                "logo_url": "/uploads/tenants/999/branding/other.png",
                "primary_color": "#1A73E8",
                "currency": "KES",
                "timezone_name": "Africa/Nairobi",
            },
        )
        body = response.content.decode()
        check(
            "foreign media logo rejected",
            "Use a hosted https logo URL" in body and response.status_code == 200,
            response.status_code,
        )
        tenant.refresh_from_db()
        check("logo unchanged after reject", tenant.logo_url == uploaded_path)

        response = client.post(
            "/billing/",
            data={
                "action": "profile",
                "company_name": "Brand Test Ltd",
                "brand_name": "Brand Test",
                "phone_number": "+254 700 000 111",
                "logo_url": "https://cdn.example.com/logo.png",
                "primary_color": "#1A73E8",
                "currency": "KES",
                "timezone_name": "Africa/Nairobi",
            },
        )
        check("billing profile saved", response.status_code == 302, response.status_code)
        tenant.refresh_from_db()
        check("billing brand applied", tenant.brand_name == "Brand Test")

        raise Rollback
except Rollback:
    pass

if uploaded_path:
    import gc

    response = None
    document = None
    gc.collect()
    disk_path = os.path.join("uploads", uploaded_path[len("/uploads/"):].replace("/", os.sep))
    if os.path.isfile(disk_path):
        for attempt in range(20):
            try:
                os.unlink(disk_path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)
    parent = os.path.dirname(disk_path)
    while parent and parent != "uploads" and os.path.isdir(parent) and not os.listdir(parent):
        os.rmdir(parent)
        parent = os.path.dirname(parent)

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
