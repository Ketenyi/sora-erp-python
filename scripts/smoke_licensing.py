"""Checks for standalone module licensing, the manifest and route gating.

Runs inside a rolled-back transaction so the seeded catalogue is untouched.
"""

import json
import os
import sys
from datetime import datetime, timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import User
from apps.saas.models import SaasModule, SaasPlan, Tenant, TenantModule
from apps.saas.services import (
    enabled_module_map,
    licence_manifest,
    provision_tenant,
    toggle_tenant_module,
)

results = []


def expect(label, condition, detail=""):
    results.append((label, "-", bool(condition)))
    if not condition:
        print(f"FAIL {label}: {detail}")


def check(label, response, expected=(200,), contains=None):
    text = response.content.decode("utf-8", "replace")
    ok = response.status_code in expected
    if ok and contains and contains not in text:
        ok = False
        print(f"FAIL {label}: missing text {contains!r}")
    results.append((label, response.status_code, ok))
    if not ok:
        print(f"FAIL {label}: {response.status_code} {text[:400]}")
    return response


def location(response):
    return response.headers.get("Location", "")


stamp = int(timezone.now().timestamp())

with transaction.atomic():
    outcome = provision_tenant(
        company_name="Smoke Standalone POS",
        admin_email=f"standalone.{stamp}@example.com",
        password="Standalone123!",
        first_name="Standalone",
        last_name="Owner",
        module_codes=["SORA_POS"],
        trial_days=14,
    )
    tenant = outcome["tenant"]
    owner = outcome["user"]
    client = TestClient(headers={"host": "localhost"})
    client.force_login(owner)

    # ------------------------------------------------------------------
    # Manifest for a single-module (standalone) workspace
    # ------------------------------------------------------------------
    response = check("manifest_standalone", client.get("/api/licence/manifest"))
    payload = json.loads(response.content.decode("utf-8"))
    expect("manifest_content_type", response["Content-Type"].startswith("application/json"), response["Content-Type"])
    expect("manifest_no_store", response["Cache-Control"] == "no-store", response.get("Cache-Control"))
    expect("manifest_tenant_id", payload["tenant_id"] == tenant.pk, payload.get("tenant_id"))
    expect("manifest_plan_type", payload["plan_type"] == "STANDALONE_POS", payload.get("plan_type"))
    expect("manifest_modules", payload["enabled_modules"] == ["SORA_POS"], payload.get("enabled_modules"))
    expect("manifest_is_trial", payload["is_trial"] is True, payload.get("is_trial"))
    expect(
        "manifest_trial_expiry",
        payload["trial_expires_at"] is not None
        and abs(
            (datetime.fromisoformat(payload["trial_expires_at"]) - tenant.trial_ends_at).total_seconds()
        )
        < 5,
        payload.get("trial_expires_at"),
    )
    expect(
        "manifest_service_matches",
        licence_manifest(tenant)["enabled_modules"] == ["SORA_POS"],
        licence_manifest(tenant),
    )

    # ------------------------------------------------------------------
    # Gateway: licensed routes open, everything else 403
    # ------------------------------------------------------------------
    check("standalone_pos_route", client.get("/pos/dashboard"), contains="POS Dashboard")
    check("standalone_hr_blocked", client.get("/hr/dashboard"), expected=(403,))
    check("standalone_finance_blocked", client.get("/accounts/dashboard"), expected=(403,))
    check("standalone_inventory_blocked", client.get("/inventory/dashboard"), expected=(403,))
    check("standalone_projects_blocked", client.get("/projects/dashboard"), expected=(403,))
    check(
        "standalone_pos_post_blocked",
        client.post("/hr/employees/create", {}),
        expected=(403,),
    )
    response = client.get("/dashboard")
    expect("standalone_landing_redirect", response.status_code == 302, response.status_code)
    expect("standalone_landing_target", location(response) == "/pos", location(response))

    # ------------------------------------------------------------------
    # Nav reflects the licence: no HR menu before the module is enabled
    # ------------------------------------------------------------------
    page = client.get("/pos/dashboard").content.decode("utf-8", "replace")
    expect("nav_shows_pos", "Point of Sale" in page)
    expect("nav_hides_hr", "Human Resources" not in page)

    # ------------------------------------------------------------------
    # Add a second module -> modular mix, landing moves to the dashboard
    # ------------------------------------------------------------------
    hr_module = SaasModule.objects.get(code="SORA_HR")
    toggle_tenant_module(tenant, hr_module, True, actor=owner)
    tenant.refresh_from_db()
    tenant._enabled_modules = None
    payload = json.loads(client.get("/api/licence/manifest").content.decode("utf-8"))
    expect("manifest_modular_plan", payload["plan_type"] == "MODULAR", payload.get("plan_type"))
    expect(
        "manifest_modular_modules",
        payload["enabled_modules"] == ["SORA_HR", "SORA_POS"],
        payload.get("enabled_modules"),
    )
    check("modular_hr_route", client.get("/hr/dashboard"), contains="HR Dashboard")
    response = client.get("/dashboard")
    expect("modular_landing_direct", response.status_code == 200, response.status_code)

    # ------------------------------------------------------------------
    # A commercial plan overrides the inferred plan type
    # ------------------------------------------------------------------
    plan = SaasPlan.objects.get(code="business")
    tenant.plan = plan
    tenant.save(update_fields=["plan"])
    tenant._enabled_modules = None
    payload = json.loads(client.get("/api/licence/manifest").content.decode("utf-8"))
    expect("manifest_plan_type", payload["plan_type"] == "BUSINESS", payload.get("plan_type"))
    expect(
        "manifest_plan_matches_enabled",
        payload["enabled_modules"] == list(enabled_module_map(tenant)),
        payload.get("enabled_modules"),
    )

    # ------------------------------------------------------------------
    # Suspended workspaces can still be inspected by the gateway
    # ------------------------------------------------------------------
    tenant.status = "suspended"
    tenant.suspended_reason = "Smoke suspension."
    tenant.save(update_fields=["status", "suspended_reason"])
    check("manifest_locked_available", client.get("/api/licence/manifest"), contains="BUSINESS")
    check("locked_app_redirects", client.get("/pos/dashboard"), expected=(302,))
    tenant.status = "trial"
    tenant.suspended_reason = ""
    tenant.save(update_fields=["status", "suspended_reason"])

    # ------------------------------------------------------------------
    # Disable every module -> NONE and all module routes blocked
    # ------------------------------------------------------------------
    TenantModule.objects.filter(tenant=tenant).update(is_enabled=False)
    tenant._enabled_modules = None
    payload = json.loads(client.get("/api/licence/manifest").content.decode("utf-8"))
    expect("manifest_none_plan", payload["plan_type"] == "NONE", payload.get("plan_type"))
    expect("manifest_none_modules", payload["enabled_modules"] == [], payload.get("enabled_modules"))
    check("none_pos_blocked", client.get("/pos/dashboard"), expected=(403,))

    # ------------------------------------------------------------------
    # Anonymous callers get JSON 401, never a redirect to HTML
    # ------------------------------------------------------------------
    anonymous = TestClient(headers={"host": "localhost"})
    response = check("manifest_anonymous", anonymous.get("/api/licence/manifest"), expected=(401,))
    expect(
        "manifest_anonymous_json",
        json.loads(response.content.decode("utf-8"))["error"] == "authentication_required",
    )

    # ------------------------------------------------------------------
    # Every catalogue module can be sold standalone
    # ------------------------------------------------------------------
    for module in SaasModule.objects.filter(is_available=True):
        code = module.code
        single = Tenant.objects.create(
            company_name=f"Standalone {code}",
            slug=f"standalone-{code.lower()}-{stamp}",
            admin_email=f"{code.lower()}.{stamp}@example.com",
            status="trial",
            trial_starts_at=timezone.now(),
            trial_ends_at=timezone.now() + timedelta(days=7),
        )
        TenantModule.objects.create(tenant=single, module=module, is_enabled=True, source="manual")
        manifest = licence_manifest(single)
        expect(
            f"standalone_{code.lower()}",
            manifest["plan_type"] == f"STANDALONE_{code.removeprefix('SORA_')}"
            and manifest["enabled_modules"] == [code],
            manifest,
        )

print(f"\n{sum(1 for _, _, ok in results if ok)}/{len(results)} checks passed")
failed = [label for label, _, ok in results if not ok]
if failed:
    print("Failed:", ", ".join(failed))
    sys.exit(1)
