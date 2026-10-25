"""End-to-end checks for custom tenant domains and DNS verification.

DNS resolution is stubbed. Every change runs inside one transaction that is
rolled back at the end, so the live workspace and platform settings survive.
"""

import io
import os
import sys
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.management import call_command
from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import User
from apps.saas import domains as domain_service
from apps.saas.domains import (
    DomainError,
    DomainLookupError,
    clean_host,
    domain_for_host,
    is_platform_host,
    normalize_hostname,
    verify_domain,
    verify_due_domains,
)
from apps.saas.models import (
    PlatformSetting,
    Tenant,
    TenantAuditLog,
    TenantDomain,
    TenantMembership,
)

HOST1 = "erp.smokebrand.com"
HOST2 = "portal.smokebrand.com"
HOST3 = "shop.smokebrand.com"
HOST4 = "claims.smokebrand.com"
OTHER_HOST = "erp.otherbrand.com"

dns_map = {}


def fake_lookup(name, *, timeout=5.0):
    key = clean_host(name)
    value = dns_map.get(key, [])
    if isinstance(value, Exception):
        raise value
    return value


domain_service.lookup_txt = fake_lookup

stamp = int(timezone.now().timestamp())
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


def html(response):
    return response.content.decode("utf-8", "replace")


def post(client, path, data=None, **kwargs):
    return client.post(path, data or {}, **kwargs)


def auth_client(host, user=None):
    client = TestClient(headers={"host": host}, raise_request_exception=False)
    if user is not None:
        client.force_login(user)
    return client


with transaction.atomic():
    # ------------------------------------------------------------------
    # Fixtures: two workspaces, members and an outsider.
    # ------------------------------------------------------------------
    tenant_a = Tenant.objects.create(
        company_name=f"Smoke Domain Co {stamp}",
        slug=f"smoke-domain-{stamp}",
        admin_email=f"owner-domain-{stamp}@example.com",
        brand_name=f"Smoke Brand {stamp}",
        status="active",
        currency="KES",
    )
    tenant_b = Tenant.objects.create(
        company_name=f"Other Domain Co {stamp}",
        slug=f"other-domain-{stamp}",
        admin_email=f"owner-other-{stamp}@example.com",
        brand_name=f"Other Brand {stamp}",
        status="active",
        currency="KES",
    )
    owner = User.objects.create_user(
        email=f"owner-domain-{stamp}@example.com",
        password="SmokeDomain!234",
        username=f"domain-owner-{stamp}",
        first_name="Dana",
        last_name="Owner",
        role="admin",
    )
    staff = User.objects.create_user(
        email=f"staff-domain-{stamp}@example.com",
        password="SmokeDomain!234",
        username=f"domain-staff-{stamp}",
        role="employee",
    )
    other_owner = User.objects.create_user(
        email=f"owner-other-{stamp}@example.com",
        password="SmokeDomain!234",
        username=f"other-owner-{stamp}",
        role="admin",
    )
    outsider = User.objects.create_user(
        email=f"outsider-{stamp}@example.com",
        password="SmokeDomain!234",
        username=f"outsider-{stamp}",
        role="employee",
    )
    TenantMembership.objects.create(tenant=tenant_a, user=owner, role="owner")
    TenantMembership.objects.create(tenant=tenant_a, user=staff, role="member")
    TenantMembership.objects.create(tenant=tenant_b, user=other_owner, role="owner")

    platform_admin = User.objects.get(email="admin@sora.com")
    owner_client = auth_client("localhost", owner)
    staff_client = auth_client("localhost", staff)
    other_client = auth_client("localhost", other_owner)
    cloud_client = auth_client("localhost", platform_admin)

    # ------------------------------------------------------------------
    # Hostname normalisation and reserved names.
    # ------------------------------------------------------------------
    check("clean_host_upper", clean_host("ERP.SmokeBrand.COM.:443") == HOST1)
    check(
        "normalise_idna",
        normalize_hostname("büro.smokebrand.com")
        == "büro.smokebrand.com".encode("idna").decode(),
    )
    for raw in [
        "",
        "smokebrand",
        "http://erp.smokebrand.com",
        "erp.smokebrand.com/path",
        "erp.smokebrand.com:443",
        "*.smokebrand.com",
        "bad_host.smokebrand.com",
        "127.0.0.1",
        "erp.local",
        "erp.test",
        "erp.123",
        "erp.c",
        "-erp.smokebrand.com",
        "erp-.smokebrand.com",
    ]:
        try:
            normalize_hostname(raw)
        except DomainError:
            continue
        check(f"reject_{raw or 'blank'}", False, f"{raw!r} was accepted")

    check("platform_host_localhost", is_platform_host("localhost"))
    check("not_platform_custom", not is_platform_host(HOST1))

    # ------------------------------------------------------------------
    # Claim through the workspace UI.
    # ------------------------------------------------------------------
    response = post(
        owner_client,
        "/billing/domains/add/",
        {"hostname": "ERP.SmokeBrand.COM."},
        follow=True,
    )
    text = html(response)
    check("claim_ui_response", response.status_code == 200, str(response.status_code))
    check("claim_ui_confirmation", "Add the DNS TXT record shown for" in text, text[:300])
    domain1 = TenantDomain.objects.filter(hostname=HOST1).first()
    check("claim_saved_normalised", domain1 is not None)
    check("claim_linked_tenant", domain1 is not None and domain1.tenant_id == tenant_a.id)
    check("claim_status_pending", domain1 is not None and domain1.status == "pending")
    check("claim_token_issued", domain1 is not None and len(domain1.verification_token) >= 20)
    check("claim_record_name", domain1 is not None and domain1.verification_record_name == f"_sora-verification.{HOST1}")
    check(
        "claim_record_value",
        domain1 is not None and domain1.verification_record_value.startswith("sora-verify="),
    )
    check("claim_txt_visible", domain1 is not None and domain1.verification_record_name in text)
    check("claim_no_primary", domain1 is not None and not domain1.is_primary)
    claim_audit = TenantAuditLog.objects.filter(
        tenant=tenant_a, action="domain.claimed", details__contains=HOST1
    ).first()
    check("claim_audit", claim_audit is not None and claim_audit.ip_address == "127.0.0.1")
    check("domain_lookup_service", domain_for_host(HOST1).pk == domain1.pk)

    response = post(owner_client, "/billing/domains/add/", {"hostname": HOST1}, follow=True)
    check(
        "claim_duplicate_rejected",
        "already registered" in html(response)
        and TenantDomain.objects.filter(hostname=HOST1).count() == 1,
    )

    # ------------------------------------------------------------------
    # Pending domain pages (HTML and JSON).
    # ------------------------------------------------------------------
    pending_client = auth_client(HOST1)
    response = pending_client.get("/")
    text = html(response)
    check("pending_host_404", response.status_code == 404, str(response.status_code))
    check("pending_page_brand", tenant_a.brand_name in text and "isn’t ready yet" in text)
    check("pending_page_noindex", "noindex" in text)
    response = pending_client.get("/", headers={"Accept": "application/json"})
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    check("pending_json_404", response.status_code == 404 and payload.get("error") == "domain_not_verified")

    unknown_client = auth_client("unknown.smokebrand.com")
    response = unknown_client.get("/")
    check("unknown_host_rejected", response.status_code == 400, str(response.status_code))

    # ------------------------------------------------------------------
    # Failed verification, cooldown and wrong token.
    # ------------------------------------------------------------------
    response = post(owner_client, f"/billing/domains/{domain1.id}/verify/", follow=True)
    domain1.refresh_from_db()
    text = html(response)
    check(
        "verify_missing_record",
        domain1.status == "pending"
        and domain1.check_count == 1
        and "No TXT record was found" in text,
        domain1.last_error,
    )
    response = post(owner_client, f"/billing/domains/{domain1.id}/verify/", follow=True)
    check("verify_cooldown_message", "checked moments ago" in html(response))
    check("verify_cooldown_count", TenantDomain.objects.get(pk=domain1.pk).check_count == 1)

    dns_map[domain1.verification_record_name] = DomainLookupError(
        "The DNS lookup timed out. Try again shortly."
    )
    result = verify_domain(domain1, force=True, ip_address="127.0.0.1")
    domain1.refresh_from_db()
    check(
        "verify_lookup_error",
        not result["matched"] and "timed out" in domain1.last_error,
        domain1.last_error,
    )

    dns_map[domain1.verification_record_name] = ["sora-verify=not-this-workspace"]
    TenantDomain.objects.filter(pk=domain1.pk).update(
        last_checked_at=timezone.now() - timedelta(minutes=5)
    )
    response = post(owner_client, f"/billing/domains/{domain1.id}/verify/", follow=True)
    domain1.refresh_from_db()
    check(
        "verify_wrong_token",
        domain1.status == "pending"
        and "does not contain this workspace" in html(response),
        domain1.last_error,
    )

    dns_map[domain1.verification_record_name] = [domain1.verification_record_value]
    dns_map[domain1.verification_record_name].append("unrelated-txt-value")
    TenantDomain.objects.filter(pk=domain1.pk).update(
        last_checked_at=timezone.now() - timedelta(minutes=5)
    )
    response = post(owner_client, f"/billing/domains/{domain1.id}/verify/", follow=True)
    domain1.refresh_from_db()
    check(
        "verify_success",
        domain1.status == "verified"
        and domain1.is_primary
        and domain1.verified_at is not None
        and domain1.last_error == ""
        and domain1.consecutive_failures == 0,
        f"{domain1.status} primary={domain1.is_primary} err={domain1.last_error}",
    )
    check("verify_success_message", "is verified and now routes to" in html(response))
    check(
        "verify_audit",
        TenantAuditLog.objects.filter(
            tenant=tenant_a, action="domain.verified", details__contains=HOST1
        ).exists(),
    )

    # ------------------------------------------------------------------
    # Branded login and workspace access on the custom domain.
    # ------------------------------------------------------------------
    domain_client = auth_client(HOST1)
    response = domain_client.get("/login")
    text = html(response)
    check("custom_login_200", response.status_code == 200, str(response.status_code))
    check(
        "custom_login_branded",
        tenant_a.brand_name in text and "Smoke Domain Co" not in text,
        f"brand_at={text.find(tenant_a.brand_name)} company_at={text.find('Smoke Domain Co')}",
    )
    check("custom_login_no_signup", 'href="/signup"' not in text)
    check("custom_login_support_note", "Contact" in text or "support" in text.lower())
    for path in ["/", "/pricing", "/signup", "/contact"]:
        response = domain_client.get(path)
        check(f"custom_root_redirect_{path}", response.status_code == 302 and response["Location"] == "/login")

    response = domain_client.get("/dashboard")
    check(
        "custom_anonymous_dashboard",
        response.status_code == 302 and response["Location"].startswith("/login"),
    )

    login_client = auth_client(HOST1)
    response = post(
        login_client,
        "/login",
        {"email": owner.email, "password": "SmokeDomain!234"},
    )
    check("custom_login_post", response.status_code == 302, str(response.status_code))
    response = login_client.get("/dashboard")
    check("custom_dashboard_200", response.status_code == 200, str(response.status_code))
    check("custom_dashboard_brand", tenant_a.brand_name in html(response))

    switched = TestClient(headers={"host": "localhost"}, raise_request_exception=False)
    for name, value in login_client.cookies.items():
        switched.cookies[name] = value
    response = switched.get("/dashboard")
    check("session_survives_host_switch", response.status_code == 200, str(response.status_code))

    response = login_client.get("/logout")
    check("custom_logout", response.status_code == 302 and response["Location"] == "/login")
    response = login_client.get("/dashboard")
    check("custom_logged_out", response.status_code == 302)

    wrong_client = auth_client(HOST1)
    response = post(
        wrong_client,
        "/login",
        {"email": other_owner.email, "password": "SmokeDomain!234"},
    )
    text = html(response)
    check(
        "wrong_workspace_blocked",
        response.status_code == 200
        and "does not have access to this workspace" in text,
        str(response.status_code),
    )
    check("wrong_workspace_not_logged_in", wrong_client.get("/login").status_code == 200)

    outsider_client = auth_client(HOST1, outsider)
    response = outsider_client.get("/dashboard")
    text = html(response)
    check("non_member_403", response.status_code == 403, str(response.status_code))
    check("non_member_page", "No access to this workspace" in text)
    response = outsider_client.get("/dashboard", headers={"Accept": "application/json"})
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    check(
        "non_member_json",
        response.status_code == 403 and payload.get("error") == "workspace_access_denied",
    )
    support_client = auth_client(HOST1, platform_admin)
    response = support_client.get("/cloud/")
    check(
        "platform_admin_can_reach_cloud",
        response.status_code == 200 and "Platform overview" in html(response),
        str(response.status_code),
    )

    response = post(staff_client, "/billing/domains/add/", {"hostname": HOST3})
    check("non_admin_cannot_claim", response.status_code == 403, str(response.status_code))
    check("non_admin_no_row", not TenantDomain.objects.filter(hostname=HOST3).exists())
    response = post(other_client, f"/billing/domains/{domain1.id}/verify/")
    check("cross_tenant_domain_hidden", response.status_code == 404, str(response.status_code))

    # ------------------------------------------------------------------
    # Second domain, primary switching.
    # ------------------------------------------------------------------
    response = post(owner_client, "/billing/domains/add/", {"hostname": HOST2}, follow=True)
    domain2 = TenantDomain.objects.filter(hostname=HOST2).first()
    check("claim_second", response.status_code == 200 and domain2 is not None)
    dns_map[domain2.verification_record_name] = [domain2.verification_record_value]
    response = post(owner_client, f"/billing/domains/{domain2.id}/verify/", follow=True)
    domain2.refresh_from_db()
    domain1.refresh_from_db()
    check(
        "second_verified",
        domain2.status == "verified" and not domain2.is_primary,
        domain2.status,
    )
    check("first_remains_primary", domain1.is_primary)

    response = post(owner_client, f"/billing/domains/{domain2.id}/primary/", follow=True)
    domain1.refresh_from_db()
    domain2.refresh_from_db()
    check(
        "primary_switch",
        domain2.is_primary and not domain1.is_primary,
        f"{domain1.is_primary}/{domain2.is_primary}",
    )
    check("primary_switch_message", "is now the primary domain" in html(response))
    check(
        "primary_audit",
        TenantAuditLog.objects.filter(
            tenant=tenant_a, action="domain.primary", details__contains=HOST2
        ).exists(),
    )
    response = owner_client.get("/billing/")
    text = html(response)
    check("billing_lists_domains", HOST1 in text and HOST2 in text and "Primary" in text)

    # ------------------------------------------------------------------
    # Limits, platform reservation and locked subscriptions.
    # ------------------------------------------------------------------
    limit_row = PlatformSetting.objects.filter(key="domain_max_per_tenant").first()
    check("limit_setting_seeded", limit_row is not None)
    if limit_row is not None:
        original_limit = limit_row.value
        limit_row.value = "2"
        limit_row.save(update_fields=["value", "updated_at"])
        response = post(owner_client, "/billing/domains/add/", {"hostname": HOST3}, follow=True)
        check(
            "domain_limit_enforced",
            "allows up to 2 custom domains" in html(response)
            and not TenantDomain.objects.filter(hostname=HOST3).exists(),
        )
        limit_row.value = original_limit
        limit_row.save(update_fields=["value", "updated_at"])

    platform_row = PlatformSetting.objects.filter(key="platform_domain").first()
    check("platform_domain_setting_seeded", platform_row is not None)
    if platform_row is not None:
        original_platform = platform_row.value
        platform_row.value = "sora-cloud.com"
        platform_row.save(update_fields=["value", "updated_at"])
        response = post(
            owner_client, "/billing/domains/add/", {"hostname": "erp.sora-cloud.com"}, follow=True
        )
        check(
            "platform_domain_reserved",
            "belongs to the SORA platform" in html(response)
            and not TenantDomain.objects.filter(hostname="erp.sora-cloud.com").exists(),
        )
        try:
            normalize_hostname("sora-cloud.com")
        except DomainError:
            check("platform_apex_reserved", True)
        else:
            check("platform_apex_reserved", False, "apex was accepted")
        platform_row.value = original_platform
        platform_row.save(update_fields=["value", "updated_at"])

    tenant_a.status = "suspended"
    tenant_a.save(update_fields=["status", "updated_at"])
    response = owner_client.get("/billing/")
    check("locked_billing_notice", "read-only until the subscription is renewed" in html(response))
    response = post(owner_client, "/billing/domains/add/", {"hostname": HOST3}, follow=True)
    check(
        "locked_domain_blocked",
        "read-only until the subscription is renewed" in html(response)
        and not TenantDomain.objects.filter(hostname=HOST3).exists(),
    )
    tenant_a.status = "active"
    tenant_a.save(update_fields=["status", "updated_at"])

    # ------------------------------------------------------------------
    # DNS health failures automatically stop routing.
    # ------------------------------------------------------------------
    dns_map[domain2.verification_record_name] = []
    for index in range(3):
        result = verify_domain(domain2, force=True, ip_address="127.0.0.1")
        check(f"health_failure_{index + 1}", not result["matched"])
        domain2.refresh_from_db()
    domain1.refresh_from_db()
    check(
        "auto_disabled_after_threshold",
        domain2.status == "disabled" and domain2.consecutive_failures >= 3,
        f"{domain2.status}/{domain2.consecutive_failures}",
    )
    check("auto_promote_remaining", domain1.is_primary)
    check(
        "auto_disable_audit",
        TenantAuditLog.objects.filter(
            tenant=tenant_a, action="domain.auto_disabled", details__contains=HOST2
        ).exists(),
    )
    response = auth_client(HOST2).get("/")
    check(
        "disabled_host_403",
        response.status_code == 403 and "This address is paused" in html(response),
        str(response.status_code),
    )
    response = auth_client(HOST2).get("/", headers={"Accept": "application/json"})
    check("disabled_host_json", response.status_code == 404)

    try:
        from apps.saas.domains import disable_domain

        disable_domain(domain2)
    except DomainError:
        check("disable_twice_rejected", True)
    else:
        check("disable_twice_rejected", False)

    dns_map[domain2.verification_record_name] = [domain2.verification_record_value]
    TenantDomain.objects.filter(pk=domain2.pk).update(
        last_checked_at=timezone.now() - timedelta(hours=1)
    )
    response = post(owner_client, f"/billing/domains/{domain2.id}/verify/", follow=True)
    domain2.refresh_from_db()
    domain1.refresh_from_db()
    check(
        "disabled_domain_reverified",
        domain2.status == "verified" and domain2.consecutive_failures == 0,
        domain2.status,
    )
    check("primary_untouched_by_reverify", domain1.is_primary)

    # ------------------------------------------------------------------
    # Scheduled command verifies pending domains.
    # ------------------------------------------------------------------
    response = post(other_client, "/billing/domains/add/", {"hostname": HOST4}, follow=True)
    domain4 = TenantDomain.objects.filter(hostname=HOST4).first()
    check("other_tenant_claim", response.status_code == 200 and domain4 is not None)
    check("other_tenant_audit", TenantAuditLog.objects.filter(tenant=tenant_b, action="domain.claimed").exists())
    dns_map[domain4.verification_record_name] = [domain4.verification_record_value]
    out = io.StringIO()
    call_command("verify_tenant_domains", "--force", stdout=out)
    output = out.getvalue()
    domain4.refresh_from_db()
    check("command_verified_pending", domain4.status == "verified", domain4.last_error)
    check("command_reports", "Domain verification complete" in output and "checked" in output, output)
    due = verify_due_domains(force=True)
    check("due_scan_result", isinstance(due, dict) and due["total"] >= 3, str(due))

    # ------------------------------------------------------------------
    # Removal frees the hostname for a new DNS owner.
    # ------------------------------------------------------------------
    response = post(owner_client, f"/billing/domains/{domain2.id}/remove/", follow=True)
    check("remove_domain", not TenantDomain.objects.filter(hostname=HOST2).exists())
    check("remove_message", "was removed from this workspace" in html(response))
    check(
        "remove_audit",
        TenantAuditLog.objects.filter(
            tenant=tenant_a, action="domain.removed", details__contains=HOST2
        ).exists(),
    )
    response = auth_client(HOST2).get("/")
    check(
        "removed_host_rejected",
        response.status_code == 400,
        f"{response.status_code} {html(response)[:300]}",
    )
    old_token = domain2.verification_token
    response = post(other_client, "/billing/domains/add/", {"hostname": HOST2}, follow=True)
    reclaimed = TenantDomain.objects.filter(hostname=HOST2).first()
    check(
        "reclaim_after_removal",
        reclaimed is not None
        and reclaimed.tenant_id == tenant_b.id
        and reclaimed.status == "pending",
    )
    check("reclaim_new_token", reclaimed is not None and reclaimed.verification_token != old_token)

    # ------------------------------------------------------------------
    # Cloud support revocation and recovery.
    # ------------------------------------------------------------------
    response = cloud_client.get(f"/cloud/tenants/{tenant_a.id}/")
    check("cloud_tenant_domains", response.status_code == 200 and HOST1 in html(response))
    response = post(
        cloud_client,
        f"/cloud/tenants/{tenant_a.id}/domains/{domain1.id}/revoke/",
        {"reason": "Smoke-test revocation."},
    )
    check("cloud_revoke_redirect", response.status_code == 302, str(response.status_code))
    domain1.refresh_from_db()
    check("cloud_revoke_state", domain1.status == "disabled" and not domain1.is_primary)
    check(
        "cloud_revoke_audit",
        TenantAuditLog.objects.filter(
            tenant=tenant_a, action="domain.revoked", details__contains=HOST1
        ).exists(),
    )
    response = auth_client(HOST1).get("/login")
    check(
        "revoked_host_paused",
        response.status_code == 403 and "This address is paused" in html(response),
        str(response.status_code),
    )
    response = post(cloud_client, f"/cloud/tenants/{tenant_a.id}/domains/{domain1.id}/revoke/")
    check("cloud_revoke_twice_safe", response.status_code == 302)

    dns_map[domain1.verification_record_name] = [domain1.verification_record_value]
    TenantDomain.objects.filter(pk=domain1.pk).update(
        last_checked_at=timezone.now() - timedelta(hours=1)
    )
    response = post(owner_client, f"/billing/domains/{domain1.id}/verify/", follow=True)
    domain1.refresh_from_db()
    primary_after = TenantDomain.objects.filter(tenant=tenant_a, is_primary=True).first()
    check(
        "revoked_domain_reverified",
        domain1.status == "verified"
        and primary_after is not None
        and primary_after.pk == domain1.pk,
        f"{domain1.status} primary={domain1.is_primary}",
    )
    response = auth_client(HOST1).get("/login")
    check("restored_host_login", response.status_code == 200, str(response.status_code))

    # ------------------------------------------------------------------
    # Audit trail summary.
    # ------------------------------------------------------------------
    actions = set(
        TenantAuditLog.objects.filter(tenant=tenant_a).values_list("action", flat=True)
    )
    check(
        "audit_actions_complete",
        {
            "domain.claimed",
            "domain.verified",
            "domain.primary",
            "domain.auto_disabled",
            "domain.removed",
            "domain.revoked",
        }.issubset(actions),
        str(sorted(actions)),
    )
    check(
        "audit_ips_recorded",
        not TenantAuditLog.objects.filter(
            tenant=tenant_a, action__startswith="domain.", ip_address=""
        ).exists(),
    )

    transaction.set_rollback(True)

print()
for label in failures:
    print(f"FAILED: {label}")
print(f"{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILURES'}; all changes rolled back.")
sys.exit(1 if failures else 0)
