"""End-to-end smoke test for endpoint-agent enrolment, inventory and UI.

The whole run happens inside one transaction that is rolled back at the end.
"""

import hashlib
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core.cache import cache
from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.assets import agent_services
from apps.assets.agent_services import AgentAPIError, hash_agent_key, hash_enrollment_token
from apps.assets.models import (
    Asset,
    AssetAgent,
    AssetAgentEnrollmentToken,
    AssetAgentEvent,
    AssetInventorySnapshot,
    AssetSoftware,
)
from apps.core.tenancy import all_tenants_context, default_tenant, tenant_context
from apps.saas.models import Tenant

class Rollback(Exception):
    pass


client = TestClient(headers={"host": "localhost"})
guest = TestClient(headers={"host": "localhost"})
cache.clear()
assert client.login(email="admin@sora.com", password="admin123")
tenant = default_tenant()
admin_id = int(client.session["_auth_user_id"])
failures = []
ip_counter = {}


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


def html(response):
    return response.content.decode("utf-8", errors="replace")


def next_ip(group):
    ip_counter[group] = ip_counter.get(group, 10) + 1
    return f"10.60.{list(ip_counter).index(group)}.{ip_counter[group]}"


def post_json(path, payload, *, ip, raw=None, content_type="application/json", auth="", extra=None):
    kwargs = {"content_type": content_type, "REMOTE_ADDR": ip}
    if auth:
        kwargs["HTTP_AUTHORIZATION"] = auth
    if extra:
        kwargs.update(extra)
    data = raw if raw is not None else json.dumps(payload)
    return client.post(path, data=data, **kwargs)


def bearer(agent_id, key):
    return f"Bearer {agent_id}:{key}"


def error_code(response):
    try:
        return response.json().get("error", "")
    except (ValueError, attributeError):
        return ""


def issue(label, *, days=7, uses=1):
    with tenant_context(tenant):
        return agent_services.issue_enrollment_token(
            label=label, expires_days=days, max_uses=uses, created_by=admin_id
        )


try:
    with transaction.atomic():
        with tenant_context(tenant):
            asset = Asset.objects.create(
                asset_code="SMK-AGENT-001",
                name="Smoke Test Laptop",
                serial_number="",
                manufacturer="",
                model_name="",
                status="available",
            )
            asset2 = Asset.objects.create(
                asset_code="SMK-AGENT-002", name="Smoke Spare Laptop"
            )
            tokens_before = AssetAgentEnrollmentToken.objects.count()

        # ------------------------------------------------------------------
        # Token issuance through the workspace UI
        # ------------------------------------------------------------------
        response = client.post(
            "/assets/agents/enrollment",
            {
                "label": "Smoke rollout",
                "expires_days": "7",
                "max_uses": "1",
                "notes": "Smoke-test enrollment",
            },
        )
        body = html(response)
        check("issue token renders one-time page", response.status_code == 200, response.status_code)
        check("one-time page warns about storage", "Copy this token now" in body)
        check("one-time page includes agent command", "--enroll-token" in body and "--once" in body)
        check("one-time page links download", "/assets/agents/download" in body)
        match = re.search(r"sora_enr_[A-Za-z0-9_-]+", body)
        check("one-time page reveals raw token", bool(match))
        raw_token = match.group(0) if match else ""
        check("one-time page shows server url", "http://localhost" in body)

        with tenant_context(tenant):
            token_ui = AssetAgentEnrollmentToken.objects.get(
                token_hash=hash_enrollment_token(raw_token)
            )
            token_count = AssetAgentEnrollmentToken.objects.count()
            token_issued_event = AssetAgentEvent.objects.filter(
                enrollment_token=token_ui, kind="token_issued"
            ).first()
        check("token persisted", token_count == tokens_before + 1, token_count)
        check("raw token is not stored", token_ui.token_hash != raw_token)
        check("token hash is sha256", len(token_ui.token_hash) == 64)
        check("token prefix matches", token_ui.token_prefix == raw_token[:20])
        check("token starts active", token_ui.status == "active" and token_ui.is_usable)
        check("token remaining uses", token_ui.uses_remaining == 1)
        check("token issue audited", token_issued_event is not None)
        check("token issue actor recorded", token_issued_event.actor_user_id == admin_id)

        response = client.get("/assets/agents/enrollment")
        body = html(response)
        check("enrollment page lists token", "Smoke rollout" in body and token_ui.token_prefix in body)
        check("enrollment page hides raw token", raw_token not in body)
        check("enrollment page shows active status", "Active" in body)

        invalid_before = token_count
        response = client.post(
            "/assets/agents/enrollment",
            {"label": "", "expires_days": "0", "max_uses": "0"},
        )
        with tenant_context(tenant):
            invalid_after = AssetAgentEnrollmentToken.objects.count()
        check("invalid token form re-renders", response.status_code == 200)
        check("invalid token form rejected", invalid_after == invalid_before)
        check("invalid token form warns", "Please correct the token details" in html(response))

        response = guest.get("/assets/agents")
        check(
            "anonymous agent page redirects to login",
            response.status_code == 302 and "/login" in response["Location"],
            response.status_code,
        )

        # ------------------------------------------------------------------
        # API validation and token state
        # ------------------------------------------------------------------
        response = post_json("/api/assets/agent/enroll", {}, ip=next_ip("bad"))
        check("enroll missing token", response.status_code == 401 and error_code(response) == "missing_token")

        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": raw_token, "machine_id": "smoke-machine-0001", "hostname": "X", "os_family": "windows"},
            ip=next_ip("bad"),
            content_type="text/plain",
        )
        check(
            "enroll requires json content type",
            response.status_code == 415 and error_code(response) == "unsupported_media_type",
            response.status_code,
        )

        response = post_json("/api/assets/agent/enroll", None, ip=next_ip("bad"), raw="")
        check("enroll empty body", response.status_code == 400 and error_code(response) == "empty_body")

        response = post_json("/api/assets/agent/enroll", None, ip=next_ip("bad"), raw="{")
        check("enroll malformed json", response.status_code == 400 and error_code(response) == "invalid_json")

        response = post_json("/api/assets/agent/enroll", None, ip=next_ip("bad"), raw="[1, 2]")
        check("enroll rejects non-object", response.status_code == 400 and error_code(response) == "invalid_json")

        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": raw_token, "machine_id": "abc", "hostname": "X", "os_family": "windows"},
            ip=next_ip("bad"),
        )
        check(
            "enroll validates machine id",
            response.status_code == 400 and error_code(response) == "invalid_machine_id",
            response.status_code,
        )

        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": raw_token, "machine_id": "smoke-machine-0001", "hostname": "", "os_family": "windows"},
            ip=next_ip("bad"),
        )
        check(
            "enroll validates hostname",
            response.status_code == 400 and error_code(response) == "invalid_hostname",
            response.status_code,
        )

        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": raw_token, "machine_id": "smoke-machine-0001", "hostname": "X", "os_family": "solaris"},
            ip=next_ip("bad"),
        )
        check(
            "enroll validates os family",
            response.status_code == 400 and error_code(response) == "invalid_os",
            response.status_code,
        )

        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": "sora_enr_not-a-real-token", "machine_id": "smoke-machine-0001", "hostname": "X", "os_family": "windows"},
            ip=next_ip("bad"),
        )
        check(
            "enroll rejects unknown token",
            response.status_code == 401 and error_code(response) == "invalid_token",
            response.status_code,
        )

        response = post_json(
            "/api/assets/agent/enroll",
            None,
            ip=next_ip("bad"),
            raw=json.dumps({"enrollment_token": "x" * 40000}),
        )
        check(
            "enroll rejects oversized payload",
            response.status_code == 413 and error_code(response) == "payload_too_large",
            response.status_code,
        )

        response = client.get("/api/assets/agent/enroll")
        check("enroll rejects GET", response.status_code == 405)

        expired_token, expired_raw = issue("Expired smoke token")
        with tenant_context(tenant):
            expired_token.expires_at = timezone.now() - timedelta(minutes=1)
            expired_token.save(update_fields=["expires_at"])
        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": expired_raw, "machine_id": "smoke-machine-expired", "hostname": "X", "os_family": "linux"},
            ip=next_ip("bad"),
        )
        with tenant_context(tenant):
            expired_token.refresh_from_db()
        check(
            "enroll rejects expired token",
            response.status_code == 403 and error_code(response) == "token_expired",
            response.status_code,
        )
        check("expired token not consumed", expired_token.use_count == 0)

        revoked_token, revoked_raw = issue("Revoked smoke token")
        with tenant_context(tenant):
            agent_services.revoke_enrollment_token(revoked_token, actor_user_id=admin_id)
            revoked_event = AssetAgentEvent.objects.filter(
                enrollment_token=revoked_token, kind="token_revoked"
            ).exists()
        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": revoked_raw, "machine_id": "smoke-machine-revoked", "hostname": "X", "os_family": "linux"},
            ip=next_ip("bad"),
        )
        check(
            "enroll rejects revoked token",
            response.status_code == 403 and error_code(response) == "token_revoked",
            response.status_code,
        )
        check("revoke audited", revoked_event)

        locked_token, locked_raw = issue("Locked smoke token")
        original_status = tenant.status
        tenant.status = "suspended"
        tenant.save(update_fields=["status"])
        response = post_json(
            "/api/assets/agent/enroll",
            {"enrollment_token": locked_raw, "machine_id": "smoke-machine-0002", "hostname": "SMK-DESKTOP-02", "os_family": "linux"},
            ip=next_ip("enroll"),
        )
        tenant.status = original_status
        tenant.save(update_fields=["status"])
        with tenant_context(tenant):
            locked_token.refresh_from_db()
        check(
            "enroll blocked when workspace locked",
            response.status_code == 403 and error_code(response) == "workspace_locked",
            response.status_code,
        )
        check("locked attempt does not consume token", locked_token.use_count == 0)

        # ------------------------------------------------------------------
        # Successful enrolment and credential rotation
        # ------------------------------------------------------------------
        enroll_payload = {
            "enrollment_token": raw_token,
            "machine_id": "smoke-machine-0001",
            "hostname": "SMK-LAPTOP-01",
            "os_family": "windows",
            "os_version": "11 Pro",
            "architecture": "x86_64",
            "agent_version": "1.0.0",
        }
        response = post_json("/api/assets/agent/enroll", enroll_payload, ip=next_ip("enroll"))
        data = response.json() if response.status_code == 201 else {}
        agent_id = data.get("agent_id")
        agent_key = data.get("agent_key", "")
        check("enroll succeeds", response.status_code == 201, response.status_code)
        check("enroll returns agent id", bool(agent_id))
        check("enroll returns key", agent_key.startswith("sora_agt_"))
        check("enroll returns workspace", data.get("workspace", {}).get("slug") == tenant.slug)
        check("enroll response is not cacheable", "no-store" in response.get("Cache-Control", ""))

        with tenant_context(tenant):
            agent1 = AssetAgent.objects.get(pk=agent_id)
            token_ui.refresh_from_db()
            enrolled_event = AssetAgentEvent.objects.filter(agent=agent1, kind="enrolled").first()
        check("agent stored in tenant", agent1.tenant_id == tenant.id)
        check("agent secret stored hashed", agent1.secret_hash == hash_agent_key(agent_key))
        check("agent raw key not stored", agent1.secret_hash != agent_key)
        check("enrolment fields stored", agent1.hostname == "SMK-LAPTOP-01" and agent1.os_family == "windows")
        check("enrolment version stored", agent1.os_version == "11 Pro" and agent1.agent_version == "1.0.0")
        check("enrolment links token", agent1.enrolled_via_id == token_ui.id)
        check("token consumed", token_ui.use_count == 1 and token_ui.is_exhausted)
        check("token used timestamp", token_ui.last_used_at is not None)
        check("enrolment audited", enrolled_event is not None)
        check("enrolment ip audited", enrolled_event.ip_address.startswith("10.60."))

        rotate_token, rotate_raw = issue("Rotation smoke token", uses=2)
        response = post_json(
            "/api/assets/agent/enroll",
            {
                "enrollment_token": rotate_raw,
                "machine_id": "smoke-machine-0001",
                "hostname": "SMK-LAPTOP-01",
                "os_family": "windows",
                "agent_version": "1.0.1",
            },
            ip=next_ip("enroll"),
        )
        rotated = response.json() if response.status_code == 201 else {}
        rotated_key = rotated.get("agent_key", "")
        check("re-enrolment succeeds", response.status_code == 201, response.status_code)
        check("re-enrolment keeps agent", rotated.get("agent_id") == agent_id)
        check("re-enrolment rotates key", rotated_key and rotated_key != agent_key)
        with tenant_context(tenant):
            check(
                "re-enrolment does not duplicate machine",
                AssetAgent.objects.filter(machine_id="smoke-machine-0001").count() == 1,
            )
            agent1.refresh_from_db()
            check("agent points at rotation token", agent1.enrolled_via_id == rotate_token.id)
            check(
                "re-enrolment audited",
                AssetAgentEvent.objects.filter(agent=agent1, kind="enrolled").count() == 2,
            )

        response = post_json(
            "/api/assets/agent/heartbeat", {}, ip=next_ip("auth"), auth=bearer(agent_id, agent_key)
        )
        check(
            "rotated-out key rejected",
            response.status_code == 401 and error_code(response) == "invalid_credentials",
            response.status_code,
        )
        response = post_json(
            "/api/assets/agent/heartbeat", {}, ip=next_ip("auth"), auth=bearer(agent_id, rotated_key)
        )
        check("rotated key accepted", response.status_code == 200, response.status_code)

        response = post_json("/api/assets/agent/heartbeat", {}, ip=next_ip("auth"))
        check(
            "heartbeat needs credentials",
            response.status_code == 401 and error_code(response) == "missing_credentials",
            response.status_code,
        )
        response = post_json(
            "/api/assets/agent/heartbeat", {}, ip=next_ip("auth"), auth="Bearer not-a-number:key"
        )
        check("heartbeat rejects malformed credential", response.status_code == 401)
        response = post_json(
            "/api/assets/agent/heartbeat", {}, ip=next_ip("auth"), auth="Bearer 999999:key"
        )
        check(
            "heartbeat rejects unknown agent",
            response.status_code == 401 and error_code(response) == "invalid_credentials",
            response.status_code,
        )
        response = post_json(
            "/api/assets/agent/heartbeat",
            {},
            ip=next_ip("auth"),
            extra={"HTTP_X_SORA_AGENT_ID": str(agent_id), "HTTP_X_SORA_AGENT_KEY": rotated_key},
        )
        check("heartbeat accepts header credentials", response.status_code == 200, response.status_code)

        # ------------------------------------------------------------------
        # Linking an asset and the first inventory report
        # ------------------------------------------------------------------
        response = client.post(
            f"/assets/agents/{agent_id}/link", {"asset": asset.id}, follow=True
        )
        body = html(response)
        with tenant_context(tenant):
            agent1.refresh_from_db()
            asset.refresh_from_db()
            link_event = AssetAgentEvent.objects.filter(agent=agent1, kind="linked").first()
        check("link agent through UI", response.status_code == 200 and agent1.asset_id == asset.id)
        check("link confirmation shown", "linked to" in body.lower())
        check("link keeps blank asset serial", asset.serial_number == "")
        check("link audited", link_event is not None and link_event.actor_user_id == admin_id)

        inventory_payload = {
            "collected_at": timezone.now().isoformat(),
            "hostname": "SMK-LAPTOP-01",
            "os": {
                "family": "windows",
                "name": "Windows 11 Pro",
                "version": "10.0.22631",
                "architecture": "x86_64",
                "boot_time": "2026-10-20T08:00:00Z",
            },
            "system": {
                "manufacturer": "Dell Inc.",
                "model": "Latitude 7440",
                "serial_number": "SN-SMOKE-01",
                "uuid": "smoke-uuid-0001",
                "domain": "SORA",
                "logged_in_user": "smoke.user",
            },
            "cpu": {
                "model": "Intel Core i7-1365U",
                "physical_cores": 10,
                "logical_cores": 12,
                "max_clock_mhz": 5200,
            },
            "memory": {"total_mb": 32768, "available_mb": 16384},
            "disks": [
                {"device": "C:", "mountpoint": "C:\\", "filesystem": "NTFS", "total_gb": 512, "free_gb": 204},
                {"device": "D:", "mountpoint": "D:\\", "filesystem": "NTFS", "total_gb": 1024, "free_gb": 2048},
                {"device": "", "mountpoint": "", "filesystem": "", "free_gb": 10},
                "junk",
            ],
            "network": [
                {
                    "name": "Ethernet",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "ipv4": ["192.168.1.50", "10.0.0.5"],
                    "ipv6": ["fe80::1"],
                },
                {"name": "", "mac": "", "ipv4": [], "ipv6": []},
            ],
            "software": [
                {"name": "Microsoft 365", "version": "16.0.1", "publisher": "Microsoft", "source": "registry", "installed_on": "2026-01-15"},
                {"name": "Microsoft 365", "version": "16.0.1", "publisher": "Microsoft", "source": "registry"},
                {"name": "<script>alert(1)</script>", "version": "1.0", "publisher": "Smoke"},
                {"name": "", "version": "9"},
                {"name": "7-Zip", "version": "24.08", "publisher": "Igor Pavlov", "source": "registry", "installed_on": "2025-12-01"},
            ],
            "agent": {"version": "1.0.0", "platform": "windows"},
        }
        response = post_json(
            "/api/assets/agent/inventory",
            inventory_payload,
            ip=next_ip("inventory"),
            auth=bearer(agent_id, rotated_key),
        )
        inv = response.json() if response.status_code == 200 else {}
        snapshot_id = inv.get("inventory_id")
        check("inventory accepted", response.status_code == 200, response.status_code)
        check("inventory software count", inv.get("software_count") == 3, inv.get("software_count"))
        check("inventory returns snapshot id", bool(snapshot_id))

        with tenant_context(tenant):
            agent1.refresh_from_db()
            snapshot = AssetInventorySnapshot.objects.get(pk=snapshot_id)
            software_rows = list(AssetSoftware.objects.filter(agent=agent1).order_by("name"))
            asset.refresh_from_db()
            inventory_event = AssetAgentEvent.objects.filter(agent=agent1, kind="inventory").first()
            linked_event = (
                AssetAgentEvent.objects.filter(agent=agent1, kind="linked")
                .order_by("-id")
                .first()
            )
        check("agent hardware cached", agent1.manufacturer == "Dell Inc." and agent1.model_name == "Latitude 7440")
        check("agent serial cached", agent1.serial_number == "SN-SMOKE-01")
        check("agent cpu cached", agent1.cpu_model == "Intel Core i7-1365U" and agent1.cpu_cores == 10)
        check("agent memory cached", agent1.memory_total_mb == 32768 and agent1.memory_total_gb == 32.0)
        check("agent disk total cached", float(agent1.disk_total_gb) == 1536.0, agent1.disk_total_gb)
        check("agent disk free cached", agent1.disk_free_gb == 1228.0, agent1.disk_free_gb)
        check("agent primary ip cached", agent1.primary_ip == "192.168.1.50")
        check("agent os name cached", agent1.os_name == "Windows 11 Pro" and agent1.os_version == "10.0.22631")
        check("agent last seen", agent1.last_seen_at is not None and agent1.last_inventory_at is not None)
        check("snapshot normalises disks", len(snapshot.payload["disks"]) == 2)
        check("snapshot clamps free space", snapshot.payload["disks"][1]["free_gb"] == 1024.0)
        check("snapshot drops empty interface", len(snapshot.payload["network"]) == 1)
        check("snapshot normalises software", len(snapshot.payload["software"]) == 3)
        check("snapshot collects agent metadata", snapshot.payload["agent"]["version"] == "1.0.0")
        check("snapshot collected timestamp", snapshot.collected_at <= timezone.now())
        expected_checksum = hashlib.sha256(
            json.dumps(
                agent_services.normalize_inventory(inventory_payload),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        check("snapshot checksum", snapshot.checksum == expected_checksum and len(snapshot.checksum) == 64)
        check("software rows created", len(software_rows) == 3, len(software_rows))
        by_name = {row.name: row for row in software_rows}
        check("software metadata stored", by_name["7-Zip"].publisher == "Igor Pavlov")
        check("software install date parsed", str(by_name["7-Zip"].installed_on) == "2025-12-01")
        check("software first seen set", by_name["7-Zip"].first_seen_at is not None)
        check("inventory audited", inventory_event is not None)
        check("inventory event counts", "3 application(s)" in inventory_event.detail)
        check("asset defaults filled from inventory", asset.serial_number == "SN-SMOKE-01" and asset.manufacturer == "Dell Inc.")
        check("asset model filled from inventory", asset.model_name == "Latitude 7440")
        check("asset fill audited", linked_event is not None and "defaults refreshed" in linked_event.detail)

        # ------------------------------------------------------------------
        # Agent pages, search, export and snapshot payload
        # ------------------------------------------------------------------
        response = client.get("/assets/agents")
        body = html(response)
        check("agents page loads", response.status_code == 200, response.status_code)
        check("agents page heading", "Endpoint Agents" in body)
        check("agents page lists endpoint", "SMK-LAPTOP-01" in body)
        check("agents page lists hardware", "Intel Core i7-1365U" in body and "GB RAM" in body)
        check("agents page links asset", "SMK-AGENT-001" in body)
        check("agents page shows online badge", "badge-success" in body and "Online" in body)
        check("agents page hides secrets", "sora_agt_" not in body and "sora_enr_" not in body)

        response = client.get("/assets/agents?q=SMK-LAPTOP")
        check("agents search matches", "SMK-LAPTOP-01" in html(response))
        response = client.get("/assets/agents?q=zzz-no-endpoint")
        check("agents search empty state", "No endpoint agents match this view." in html(response))
        response = client.get("/assets/agents?os=windows")
        check("agents os filter includes window", "SMK-LAPTOP-01" in html(response))
        response = client.get("/assets/agents?os=linux")
        check("agents os filter excludes window", "No endpoint agents match this view." in html(response))
        response = client.get("/assets/agents?linked=yes")
        check("agents linked filter includes", "SMK-LAPTOP-01" in html(response))
        response = client.get("/assets/agents?linked=no")
        check("agents unlinked filter excludes", "No endpoint agents match this view." in html(response))
        response = client.get("/assets/agents?status=online")
        check("agents status filter includes online", "SMK-LAPTOP-01" in html(response))

        response = client.get(f"/assets/agents/{agent_id}")
        body = html(response)
        check("agent detail loads", response.status_code == 200, response.status_code)
        check("agent detail shows hostname", "SMK-LAPTOP-01" in body)
        check("agent detail shows system", "Dell Inc." in body and "Latitude 7440" in body)
        check("agent detail shows serial", "SN-SMOKE-01" in body)
        check("agent detail shows network", "192.168.1.50" in body and "Ethernet" in body)
        check("agent detail shows storage", "512.0 GB" in body and "1024.0 GB" in body)
        check("agent detail shows software", "Microsoft 365" in body and "7-Zip" in body)
        check("agent detail shows asset link", "SMK-AGENT-001" in body and "Asset link" in body)
        check("agent detail shows snapshots", "Inventory snapshots" in body)
        check("agent detail links snapshot payload", f"/assets/agents/{agent_id}/snapshots/{snapshot_id}" in body)
        check("agent detail shows activity", "Activity" in body and "Inventory received" in body)
        check("agent detail escapes software names", "&lt;script&gt;alert(1)&lt;/script&gt;" in body)
        check("agent detail does not render raw script", "<script>alert(1)</script>" not in body)
        check("agent detail hides agent key", rotated_key not in body)
        check("agent detail hides secret hash", agent1.secret_hash not in body)
        check("agent detail hides token", raw_token not in body)

        response = client.get(f"/assets/agents/{agent_id}?sw_q=7-Zip")
        body = html(response)
        check("software search includes match", "7-Zip" in body)
        check("software search hides non-match", "Microsoft 365" not in body)
        check("software search state retained", 'value="7-Zip"' in body)

        response = client.get(f"/assets/agents/{agent_id}/software/export")
        check("software export downloads csv", response.status_code == 200)
        check("software export content type", "text/csv" in response.get("Content-Type", ""))
        csv_body = response.content.decode("utf-8", errors="replace")
        check("software export header", "Application,Version" in csv_body)
        check("software export rows", "7-Zip" in csv_body and "Microsoft 365" in csv_body)

        response = client.get(f"/assets/agents/{agent_id}/snapshots/{snapshot_id}")
        payload = response.json() if response.status_code == 200 else {}
        check("snapshot json loads", response.status_code == 200, response.status_code)
        check("snapshot json payload", payload.get("payload", {}).get("hostname") == "SMK-LAPTOP-01")
        check("snapshot json checksum", payload.get("checksum") == expected_checksum)
        check("snapshot json agent id", payload.get("agent_id") == agent_id)

        response = client.get(f"/assets/{asset.id}")
        body = html(response)
        check("asset detail loads", response.status_code == 200, response.status_code)
        check("asset detail agent card", "Endpoint agent" in body and "SMK-LAPTOP-01" in body)
        check("asset detail agent link", f"/assets/agents/{agent_id}" in body)

        # ------------------------------------------------------------------
        # Second inventory: software diff-sync
        # ------------------------------------------------------------------
        with tenant_context(tenant):
            kept = AssetSoftware.objects.get(agent=agent1, name="7-Zip")
            kept_first_seen = kept.first_seen_at
            kept_installed = kept.installed_on
        response = post_json(
            "/api/assets/agent/inventory",
            {
                "hostname": "SMK-LAPTOP-01",
                "software": [
                    {"name": "7-Zip", "version": "24.08", "publisher": "Igor Pavlov", "source": "registry"},
                    {"name": "Slack", "version": "4.41.0", "publisher": "Slack Technologies", "source": "registry"},
                ],
            },
            ip=next_ip("inventory"),
            auth=bearer(agent_id, rotated_key),
        )
        inv2 = response.json() if response.status_code == 200 else {}
        check("second inventory accepted", response.status_code == 200, response.status_code)
        check("second inventory software count", inv2.get("software_count") == 2)
        with tenant_context(tenant):
            names = sorted(AssetSoftware.objects.filter(agent=agent1).values_list("name", flat=True))
            kept.refresh_from_db()
            snapshot_count = AssetInventorySnapshot.objects.filter(agent=agent1).count()
        check("software diff removes stale apps", "Microsoft 365" not in names and "<script>alert(1)</script>" not in names)
        check("software diff adds new apps", "Slack" in names and "7-Zip" in names, names)
        check("software diff keeps first seen", kept.first_seen_at == kept_first_seen)
        check("software diff keeps install date", kept.installed_on == kept_installed)
        check("second snapshot stored", snapshot_count == 2, snapshot_count)

        # ------------------------------------------------------------------
        # Second agent, link conflict, toggle and deletion
        # ------------------------------------------------------------------
        response = post_json(
            "/api/assets/agent/enroll",
            {
                "enrollment_token": locked_raw,
                "machine_id": "smoke-machine-0002",
                "hostname": "SMK-DESKTOP-02",
                "os_family": "linux",
                "agent_version": "1.0.0",
            },
            ip=next_ip("enroll"),
        )
        second = response.json() if response.status_code == 201 else {}
        agent2_id = second.get("agent_id")
        agent2_key = second.get("agent_key", "")
        check("second endpoint enrols", response.status_code == 201, response.status_code)
        with tenant_context(tenant):
            locked_token.refresh_from_db()
            agent2 = AssetAgent.objects.get(pk=agent2_id)
        check("locked token consumed after unlock", locked_token.use_count == 1)

        response = post_json(
            "/api/assets/agent/enroll",
            {
                "enrollment_token": locked_raw,
                "machine_id": "smoke-machine-0003",
                "hostname": "SMK-EXHAUSTED",
                "os_family": "linux",
            },
            ip=next_ip("enroll"),
        )
        check(
            "single-use token cannot enrol twice",
            response.status_code == 403 and error_code(response) == "token_exhausted",
            response.status_code,
        )
        with tenant_context(tenant):
            check(
                "exhausted token creates no agent",
                not AssetAgent.objects.filter(machine_id="smoke-machine-0003").exists(),
            )

        response = post_json(
            "/api/assets/agent/inventory",
            {
                "hostname": "SMK-DESKTOP-02",
                "os": {"family": "linux", "name": "Ubuntu", "version": "24.04 LTS"},
                "software": [{"name": "Ubuntu Desktop", "version": "24.04"}],
            },
            ip=next_ip("inventory"),
            auth=bearer(agent2_id, agent2_key),
        )
        check("linux agent reports inventory", response.status_code == 200, response.status_code)
        response = client.get("/assets/agents?os=linux")
        check("agents os filter includes linux", "SMK-DESKTOP-02" in html(response))
        response = client.get("/assets/agents?linked=no")
        check("agents unlinked filter includes new endpoint", "SMK-DESKTOP-02" in html(response))

        response = client.post(
            f"/assets/agents/{agent2_id}/link", {"asset": asset.id}, follow=True
        )
        body = html(response)
        with tenant_context(tenant):
            agent2.refresh_from_db()
        check("link rejects asset held by another agent", agent2.asset_id is None)
        check("link conflict message", "Choose an asset" in body)

        response = client.post(
            f"/assets/agents/{agent2_id}/link", {"asset": asset2.id}, follow=True
        )
        with tenant_context(tenant):
            agent2.refresh_from_db()
        check("second agent links to spare asset", agent2.asset_id == asset2.id)
        check("second link confirmation", "SMK-AGENT-002" in html(response))

        response = client.post(f"/assets/agents/{agent2_id}/unlink", follow=True)
        with tenant_context(tenant):
            agent2.refresh_from_db()
            unlinked = AssetAgentEvent.objects.filter(agent=agent2, kind="unlinked").exists()
        check("unlink clears asset", agent2.asset_id is None)
        check("unlink audited", unlinked)
        check("unlink confirmation", "unlinked" in html(response).lower())

        response = client.post(
            f"/assets/agents/{agent2_id}/toggle", {"active": "0"}, follow=True
        )
        with tenant_context(tenant):
            agent2.refresh_from_db()
            deactivated = AssetAgentEvent.objects.filter(agent=agent2, kind="deactivated").exists()
        check("toggle deactivates agent", agent2.is_active is False)
        check("deactivation audited", deactivated)
        check("deactivation confirmation", "deactivated" in html(response).lower())
        response = post_json(
            "/api/assets/agent/heartbeat",
            {},
            ip=next_ip("auth"),
            auth=bearer(agent2_id, agent2_key),
        )
        check(
            "inactive agent cannot report",
            response.status_code == 403 and error_code(response) == "agent_disabled",
            response.status_code,
        )
        response = client.post(
            f"/assets/agents/{agent2_id}/toggle", {"active": "1"}, follow=True
        )
        with tenant_context(tenant):
            agent2.refresh_from_db()
        check("toggle reactivates agent", agent2.is_active is True)
        response = post_json(
            "/api/assets/agent/heartbeat",
            {"agent_version": "1.0.2"},
            ip=next_ip("auth"),
            auth=bearer(agent2_id, agent2_key),
        )
        check("reactivated agent reports", response.status_code == 200, response.status_code)
        with tenant_context(tenant):
            agent2.refresh_from_db()
        check("heartbeat stores agent version", agent2.agent_version == "1.0.2")

        original_status = tenant.status
        tenant.status = "suspended"
        tenant.save(update_fields=["status"])
        response = post_json(
            "/api/assets/agent/heartbeat",
            {},
            ip=next_ip("auth"),
            auth=bearer(agent_id, rotated_key),
        )
        tenant.status = original_status
        tenant.save(update_fields=["status"])
        check(
            "heartbeat blocked when workspace locked",
            response.status_code == 403 and error_code(response) == "workspace_locked",
            response.status_code,
        )

        response = client.post(f"/assets/agents/{agent2_id}/delete", follow=True)
        with tenant_context(tenant):
            check("agent deleted", not AssetAgent.objects.filter(pk=agent2_id).exists())
            check(
                "agent inventory cascades on delete",
                not AssetInventorySnapshot.objects.filter(agent_id=agent2_id).exists()
                and not AssetSoftware.objects.filter(agent_id=agent2_id).exists(),
            )
        check("delete confirmation", "removed from the workspace" in html(response).lower())

        # ------------------------------------------------------------------
        # Revoking the used token and automation visibility
        # ------------------------------------------------------------------
        response = client.post(
            f"/assets/agents/enrollment/{token_ui.id}/revoke", follow=True
        )
        body = html(response)
        with tenant_context(tenant):
            token_ui.refresh_from_db()
        check("revoke token through UI", token_ui.is_revoked is True)
        check("revoked token status", token_ui.status == "revoked")
        check("revoke confirmation", "revoked" in body.lower())
        check("revoke page hides raw token", raw_token not in body)
        response = client.post(
            f"/assets/agents/enrollment/{token_ui.id}/revoke", follow=True
        )
        check("double revoke is idempotent", "already revoked" in html(response).lower())

        response = client.get("/assets/agents/download")
        check("agent download works", response.status_code == 200, response.status_code)
        check(
            "agent download is attachment",
            "attachment" in response.get("Content-Disposition", "").lower()
            and "sora_asset_agent.py" in response.get("Content-Disposition", ""),
        )
        script_bytes = b"".join(response.streaming_content)
        check("agent download contains agent", b"AGENT_VERSION" in script_bytes)
        check("agent download contains cli", b"--enroll-token" in script_bytes and b"--print-payload" in script_bytes)

        # ------------------------------------------------------------------
        # Tenant isolation
        # ------------------------------------------------------------------
        tenant_b = Tenant.objects.create(
            company_name="Smoke Agent Tenant",
            slug=f"smoke-agent-{uuid4().hex[:8]}",
            admin_email="smoke-agent@example.com",
            status="active",
        )
        with tenant_context(tenant_b):
            agent_b = AssetAgent.objects.create(
                machine_id="smoke-machine-tenant-b",
                hostname="SMK-TENANT-B",
                os_family="linux",
                secret_hash=hash_agent_key("smoke-tenant-b-secret"),
            )
        with tenant_context(tenant):
            check(
                "other tenant agent hidden from manager",
                not AssetAgent.objects.filter(pk=agent_b.pk).exists(),
            )
        with all_tenants_context():
            check("other tenant agent exists globally", AssetAgent.objects.filter(pk=agent_b.pk).exists())
        response = client.get(f"/assets/agents/{agent_b.id}")
        check("other tenant agent detail is 404", response.status_code == 404, response.status_code)
        response = client.get("/assets/agents?q=SMK-TENANT-B")
        body = html(response)
        check(
            "other tenant agent hidden from list",
            f"/assets/agents/{agent_b.id}" not in body,
        )
        check("other tenant list is empty", "No endpoint agents match this view." in body)

        response = post_json(
            "/api/assets/agent/inventory",
            {
                "hostname": "SMK-TENANT-B",
                "software": [{"name": "TenantBApp", "version": "1.0"}],
            },
            ip=next_ip("tenantb"),
            auth=bearer(agent_b.id, "smoke-tenant-b-secret"),
        )
        tenant_b_snapshot = response.json().get("inventory_id") if response.status_code == 200 else None
        check("other tenant agent can report", response.status_code == 200, response.status_code)
        with tenant_context(tenant):
            check(
                "other tenant snapshot hidden",
                not AssetInventorySnapshot.objects.filter(pk=tenant_b_snapshot).exists(),
            )
            check(
                "other tenant software hidden",
                AssetSoftware.objects.filter(name="TenantBApp").count() == 0,
            )
        with tenant_context(tenant_b):
            check(
                "other tenant snapshot stored in its workspace",
                AssetInventorySnapshot.objects.filter(pk=tenant_b_snapshot).exists(),
            )
            check("other tenant software stored", AssetSoftware.objects.filter(name="TenantBApp").count() == 1)

        # ------------------------------------------------------------------
        # API abuse limits
        # ------------------------------------------------------------------
        limited_ip = "10.99.99.99"
        cache.set(f"asset-agent:enroll:{limited_ip}", 61, 60)
        response = post_json(
            "/api/assets/agent/enroll",
            {
                "enrollment_token": rotate_raw,
                "machine_id": "smoke-machine-rate-limited",
                "hostname": "SMK-RATE",
                "os_family": "linux",
            },
            ip=limited_ip,
        )
        cache.delete(f"asset-agent:enroll:{limited_ip}")
        check(
            "enrolment rate limit enforced",
            response.status_code == 429 and error_code(response) == "rate_limited",
            response.status_code,
        )

        cache.delete("asset-agent:smoke:1.1.1.1")
        first = agent_services.throttle("smoke", "1.1.1.1", 2, 60)
        second = agent_services.throttle("smoke", "1.1.1.1", 2, 60)
        third = agent_services.throttle("smoke", "1.1.1.1", 2, 60)
        cache.delete("asset-agent:smoke:1.1.1.1")
        check("throttle allows within budget", first is False and second is False)
        check("throttle blocks over budget", third is True)

        response = post_json(
            "/api/assets/agent/inventory",
            {},
            ip=next_ip("inventory"),
            auth=bearer(agent_id, rotated_key),
            content_type="text/plain",
        )
        check(
            "inventory requires json",
            response.status_code == 415 and error_code(response) == "unsupported_media_type",
            response.status_code,
        )
        response = post_json(
            "/api/assets/agent/inventory",
            None,
            ip=next_ip("inventory"),
            raw="{",
            auth=bearer(agent_id, rotated_key),
        )
        check(
            "inventory rejects malformed json",
            response.status_code == 400 and error_code(response) == "invalid_json",
            response.status_code,
        )
        huge_inventory = json.dumps({"padding": "x" * (3 * 1024 * 1024 + 200)})
        response = post_json(
            "/api/assets/agent/inventory",
            None,
            ip=next_ip("inventory"),
            raw=huge_inventory,
            auth=bearer(agent_id, rotated_key),
        )
        check(
            "inventory rejects oversized payload",
            response.status_code == 413 and error_code(response) == "payload_too_large",
            response.status_code,
        )
        response = post_json(
            "/api/assets/agent/heartbeat",
            None,
            ip=next_ip("auth"),
            raw="",
            auth=bearer(agent_id, rotated_key),
        )
        check("heartbeat rejects empty body", response.status_code == 400 and error_code(response) == "empty_body")
        response = post_json(
            "/api/assets/agent/heartbeat",
            None,
            ip=next_ip("auth"),
            raw=json.dumps({"padding": "x" * 9000}),
            auth=bearer(agent_id, rotated_key),
        )
        check(
            "heartbeat rejects oversized payload",
            response.status_code == 413 and error_code(response) == "payload_too_large",
            response.status_code,
        )

        # ------------------------------------------------------------------
        # Normalisation edge cases and snapshot retention
        # ------------------------------------------------------------------
        normalised = agent_services.normalize_inventory(
            {
                "collected_at": (timezone.now() + timedelta(days=10)).isoformat(),
                "hostname": "H" * 500,
                "os": {"family": "plan9"},
                "disks": [{"total_gb": "10", "free_gb": "20"}],
                "software": [{"name": "A"}, {"name": "a"}],
            }
        )
        check("normalisation caps hostname", len(normalised["hostname"]) == 200)
        check("normalisation clamps timestamps", normalised["collected_at"] <= timezone.now().isoformat())
        check("normalisation forces known os family", normalised["os"]["family"] == "other")
        check("normalisation clamps free space", normalised["disks"][0]["free_gb"] == 10.0)
        check("normalisation dedupes software", len(normalised["software"]) == 1)
        normalised_old = agent_services.normalize_inventory(
            {"collected_at": (timezone.now() - timedelta(days=90)).isoformat()}
        )
        check(
            "normalisation clamps ancient timestamps",
            normalised_old["collected_at"] > (timezone.now() - timedelta(minutes=5)).isoformat(),
        )
        try:
            agent_services.normalize_inventory(["not", "an", "object"])
            rejected = False
        except AgentAPIError:
            rejected = True
        check("normalisation rejects non-object", rejected)

        with tenant_context(tenant):
            agent1 = AssetAgent.objects.get(pk=agent_id)
            oldest_snapshot = (
                AssetInventorySnapshot.objects.filter(agent=agent1)
                .order_by("received_at", "id")
                .first()
            )
            oldest_id = oldest_snapshot.id
            for _ in range(59):
                agent_services.record_inventory(
                    agent1, {"hostname": "SMK-LAPTOP-01"}, ip_address="10.70.0.1"
                )
            retained = AssetInventorySnapshot.objects.filter(agent=agent1).count()
            oldest_still_there = AssetInventorySnapshot.objects.filter(pk=oldest_id).exists()
        check("snapshots retained at cap", retained == 60, retained)
        check("oldest snapshot trimmed", oldest_still_there is False)
        response = client.get(f"/assets/agents/{agent_id}")
        body = html(response)
        check("agent detail survives cap", response.status_code == 200)
        check("agent detail reports cap", "60 of 60" in body)

        raise Rollback
except Rollback:
    pass

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
