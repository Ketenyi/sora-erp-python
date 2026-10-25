"""Enrolment, authentication and inventory ingestion for endpoint agents."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from datetime import date, datetime, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.assets.models import (
    Asset,
    AssetAgent,
    AssetAgentEnrollmentToken,
    AssetAgentEvent,
    AssetInventorySnapshot,
    AssetSoftware,
)
from apps.core.tenancy import all_tenants_context, tenant_context

ENROLLMENT_PREFIX = "sora_enr_"
AGENT_KEY_PREFIX = "sora_agt_"
AGENT_TOKEN_ISSUER = "sora-erp"
AGENT_TOKEN_AUDIENCE = "sora-asset-agent"
ENROLLMENT_SALT = "sora-agent-enrollment-v1::"
AGENT_KEY_SALT = "sora-agent-key-v1::"
AGENT_TOKEN_SALT = "sora-agent-token-v1::"
MAX_PORTS = 256
MACHINE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
FINGERPRINT_RE = re.compile(r"^[A-Fa-f0-9]{32,64}$")
OS_FAMILIES = {"windows", "linux", "macos", "other"}
PROVISIONING_METHODS = {"manual", "gpo", "portal", "linux", "macos", "other"}

_TOKEN_DUMMY_HASH = hashlib.sha256(f"{AGENT_KEY_SALT}invalid".encode("utf-8")).hexdigest()


class AgentAPIError(Exception):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Secret material
# ---------------------------------------------------------------------------
def hash_enrollment_token(raw: str) -> str:
    return hashlib.sha256(f"{ENROLLMENT_SALT}{raw}".encode("utf-8")).hexdigest()


def hash_agent_key(raw: str) -> str:
    return hashlib.sha256(f"{AGENT_KEY_SALT}{raw}".encode("utf-8")).hexdigest()


def hash_machine_id(machine_id: str) -> str:
    return hashlib.sha256(f"{AGENT_TOKEN_SALT}{machine_id}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Workspace policy
# ---------------------------------------------------------------------------
def _policy_int(values: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        parsed = int(str(values.get(key) or default).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(parsed, high))


def agent_policy() -> dict:
    """Heartbeat, inventory, token and retention rules owned by the workspace."""
    try:
        from apps.core.services import get_company_settings

        values = get_company_settings()
    except Exception:
        values = {}
    return {
        "heartbeat_seconds": _policy_int(values, "agent_heartbeat_seconds", 60, 15, 3600),
        "inventory_minutes": _policy_int(values, "agent_inventory_minutes", 30, 1, 1440),
        "token_ttl_hours": _policy_int(values, "agent_token_ttl_hours", 24, 1, 720),
        "snapshot_retention": _policy_int(values, "agent_snapshot_retention", 60, 5, 365),
        "software_limit": _policy_int(values, "agent_software_limit", 2000, 0, 5000),
    }


def _signing_secret() -> bytes:
    secret = getattr(settings, "SORA_AGENT_JWT_SECRET", "") or settings.SECRET_KEY
    return str(secret).encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def fingerprint_for(payload: dict, machine_id: str) -> str:
    """Hardware fingerprint supplied by the agent, or a machine-id fallback."""
    raw = _clean_text(payload.get("fingerprint"), 64)
    if raw and FINGERPRINT_RE.match(raw):
        return raw.lower()
    return hashlib.sha256(
        f"{AGENT_TOKEN_SALT}fingerprint::{machine_id}".encode("utf-8")
    ).hexdigest()


def issue_access_token(agent: AssetAgent, *, ttl_hours: int | None = None) -> tuple[str, datetime]:
    """Mint a short-lived, hardware-bound JWT for an enrolled endpoint."""
    if ttl_hours is None:
        ttl_hours = agent_policy()["token_ttl_hours"]
    now = int(time.time())
    expires_at = timezone.now() + timedelta(hours=max(1, int(ttl_hours)))
    claims = {
        "iss": AGENT_TOKEN_ISSUER,
        "aud": AGENT_TOKEN_AUDIENCE,
        "sub": str(agent.pk),
        "tid": agent.tenant_id,
        "mid": hash_machine_id(agent.machine_id),
        "fp": agent.fingerprint or "",
        "ver": agent.token_version or 1,
        "jti": secrets.token_hex(8),
        "iat": now,
        "exp": int(expires_at.timestamp()),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        _signing_secret(), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    token = f"{signing_input}.{_b64url_encode(signature)}"
    AssetAgent.objects.filter(pk=agent.pk).update(last_token_at=timezone.now())
    agent.last_token_at = timezone.now()
    return token, expires_at


def verify_access_token(raw: str) -> dict:
    parts = (raw or "").split(".")
    if len(parts) != 3:
        raise AgentAPIError(
            "Agent access token is malformed.", status=401, code="invalid_token"
        )
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
        provided = _b64url_decode(parts[2])
    except (ValueError, TypeError, json.JSONDecodeError):
        raise AgentAPIError(
            "Agent access token is malformed.", status=401, code="invalid_token"
        )
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise AgentAPIError(
            "Agent access token uses an unsupported algorithm.",
            status=401,
            code="invalid_token",
        )
    expected = hmac.new(
        _signing_secret(),
        f"{parts[0]}.{parts[1]}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(expected, provided):
        raise AgentAPIError(
            "Agent access token signature is not valid.",
            status=401,
            code="invalid_token",
        )
    if not isinstance(claims, dict):
        raise AgentAPIError(
            "Agent access token is malformed.", status=401, code="invalid_token"
        )
    if claims.get("iss") != AGENT_TOKEN_ISSUER or claims.get("aud") != AGENT_TOKEN_AUDIENCE:
        raise AgentAPIError(
            "Agent access token was issued by another service.",
            status=401,
            code="invalid_token",
        )
    now = int(time.time())
    try:
        expires = int(claims.get("exp"))
        issued = int(claims.get("iat") or 0)
    except (TypeError, ValueError):
        raise AgentAPIError(
            "Agent access token is malformed.", status=401, code="invalid_token"
        )
    if expires <= now:
        raise AgentAPIError(
            "Agent access token has expired; refresh it and try again.",
            status=401,
            code="token_expired",
        )
    if issued > now + 300:
        raise AgentAPIError(
            "Agent access token is not valid yet.", status=401, code="invalid_token"
        )
    return claims


def validate_install_token(raw: str) -> AssetAgentEnrollmentToken:
    """Check an enrolment token for installer downloads without consuming it."""
    digest = hash_enrollment_token(_clean_text(raw, 200))
    with all_tenants_context():
        token = AssetAgentEnrollmentToken.objects.filter(token_hash=digest).first()
    if token is None:
        raise AgentAPIError(
            "That enrolment token is not valid.", status=401, code="invalid_token"
        )
    if token.is_revoked:
        raise AgentAPIError(
            "That enrolment token has been revoked.", status=403, code="token_revoked"
        )
    if token.is_expired:
        raise AgentAPIError(
            "That enrolment token has expired.", status=403, code="token_expired"
        )
    if token.is_exhausted:
        raise AgentAPIError(
            "That enrolment token has already been used.", status=403, code="token_exhausted"
        )
    if token.tenant.is_locked:
        raise AgentAPIError(
            "This workspace is locked; agents cannot enrol right now.",
            status=403,
            code="workspace_locked",
        )
    return token


def _new_enrollment_token() -> str:
    return f"{ENROLLMENT_PREFIX}{secrets.token_urlsafe(32)}"


def _new_agent_key() -> str:
    return f"{AGENT_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def issue_enrollment_token(
    *, label: str, expires_days: int, max_uses: int, notes: str = "", created_by: int | None = None
):
    raw = _new_enrollment_token()
    token = AssetAgentEnrollmentToken.objects.create(
        label=label,
        token_prefix=raw[:20],
        token_hash=hash_enrollment_token(raw),
        expires_at=timezone.now() + timedelta(days=max(1, min(expires_days, 90))),
        max_uses=max(1, min(max_uses, 100)),
        notes=notes,
        created_by=created_by,
    )
    AssetAgentEvent.objects.create(
        enrollment_token=token,
        kind="token_issued",
        detail=f"Enrolment token “{label}” issued for {token.max_uses} use(s).",
        actor_user_id=created_by,
    )
    return token, raw


def revoke_enrollment_token(token, actor_user_id: int | None = None) -> None:
    token.is_revoked = True
    token.save(update_fields=["is_revoked"])
    AssetAgentEvent.objects.create(
        enrollment_token=token,
        kind="token_revoked",
        detail=f"Enrolment token “{token.label}” revoked.",
        actor_user_id=actor_user_id,
    )


def throttle(bucket: str, ip_address: str, limit: int, window_seconds: int) -> bool:
    key = f"asset-agent:{bucket}:{ip_address or 'unknown'}"
    if cache.add(key, 1, window_seconds):
        return False
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window_seconds)
        count = 1
    return count > limit


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------
def _clean_text(value, max_length: int) -> str:
    if value is None:
        return ""
    text = str(value)
    text = "".join(char for char in text if char.isprintable())
    return text.strip()[:max_length]


@transaction.atomic
def enroll_agent(payload: dict, *, ip_address: str = "") -> tuple[AssetAgent, str]:
    raw_token = _clean_text(payload.get("enrollment_token"), 200)
    if not raw_token:
        raise AgentAPIError("An enrolment token is required.", status=401, code="missing_token")
    machine_id = _clean_text(payload.get("machine_id"), 128)
    if not MACHINE_ID_RE.match(machine_id):
        raise AgentAPIError(
            "machine_id must be 8–128 characters of letters, digits, dots, dashes, colons or underscores.",
            code="invalid_machine_id",
        )
    hostname = _clean_text(payload.get("hostname"), 200)
    if not hostname:
        raise AgentAPIError("hostname is required.", code="invalid_hostname")
    os_family = _clean_text(payload.get("os_family"), 10).lower()
    if os_family not in OS_FAMILIES:
        raise AgentAPIError("os_family must be windows, linux, macos or other.", code="invalid_os")
    provisioning = _clean_text(
        payload.get("provisioning_method") or payload.get("method") or "manual", 20
    ).lower()
    if provisioning not in PROVISIONING_METHODS:
        provisioning = "manual"
    fingerprint = fingerprint_for(payload, machine_id)

    token_digest = hash_enrollment_token(raw_token)
    now = timezone.now()
    with all_tenants_context():
        token = (
            AssetAgentEnrollmentToken.objects.select_for_update()
            .filter(token_hash=token_digest)
            .first()
        )
        if token is None:
            raise AgentAPIError(
                "That enrolment token is not valid.", status=401, code="invalid_token"
            )
        if token.is_revoked:
            raise AgentAPIError(
                "That enrolment token has been revoked.", status=403, code="token_revoked"
            )
        if token.is_expired:
            raise AgentAPIError(
                "That enrolment token has expired.", status=403, code="token_expired"
            )
        if token.is_exhausted:
            raise AgentAPIError(
                "That enrolment token has already been used.", status=403, code="token_exhausted"
            )
        tenant = token.tenant
        if tenant.is_locked:
            raise AgentAPIError(
                "This workspace is locked; agents cannot enrol right now.",
                status=403,
                code="workspace_locked",
            )

        with tenant_context(tenant):
            agent = AssetAgent.objects.filter(machine_id=machine_id).first()
            secret = _new_agent_key()
            is_new = agent is None
            if is_new:
                agent = AssetAgent(
                    machine_id=machine_id,
                    hostname=hostname,
                    os_family=os_family,
                    secret_hash=hash_agent_key(secret),
                    fingerprint=fingerprint,
                    enrolled_via=token,
                    enrolled_at=now,
                )
            else:
                agent.hostname = hostname
                agent.os_family = os_family
                agent.secret_hash = hash_agent_key(secret)
                agent.fingerprint = fingerprint
                agent.token_version = (agent.token_version or 1) + 1
                agent.is_active = True
                agent.enrolled_via = token
            agent.os_version = _clean_text(payload.get("os_version"), 120)
            agent.os_build = _clean_text(payload.get("os_build") or payload.get("build"), 120)
            agent.architecture = _clean_text(payload.get("architecture"), 40)
            agent.cpu_architecture = _clean_text(payload.get("cpu_architecture"), 40)
            agent.agent_version = _clean_text(payload.get("agent_version"), 40)
            agent.provisioning_method = provisioning
            agent.last_ip = (ip_address or "")[:45]
            agent.save()

            token.use_count = token.use_count + 1
            token.last_used_at = now
            token.save(update_fields=["use_count", "last_used_at"])
            AssetAgentEvent.objects.create(
                agent=agent,
                enrollment_token=token,
                kind="enrolled",
                detail=(
                    f"{'Re-enrolled' if not is_new else 'Enrolled'} {hostname} "
                    f"({os_family}) using token “{token.label}”."
                ),
                actor_user_id=None,
                ip_address=(ip_address or "")[:45],
            )
    return agent, secret


def authenticate_agent(request) -> AssetAgent:
    """Resolve a hardware-bound JWT or a legacy agent key presented by an endpoint."""

    header = (request.headers.get("Authorization") or "").strip()
    agent_id = ""
    secret = ""
    claims = None
    credential = ""
    if header.lower().startswith("bearer "):
        credential = header[7:].strip()
    if credential.count(".") == 2:
        claims = verify_access_token(credential)
        agent_id = str(claims.get("sub") or "")
    else:
        if credential and ":" in credential:
            agent_id, secret = credential.split(":", 1)
        if not agent_id:
            agent_id = (request.headers.get("X-SORA-Agent-Id") or "").strip()
            secret = (request.headers.get("X-SORA-Agent-Key") or "").strip()
    if not agent_id.isdigit() or (claims is None and not secret):
        raise AgentAPIError(
            "Agent credentials are required.", status=401, code="missing_credentials"
        )

    with all_tenants_context():
        agent = AssetAgent.objects.filter(pk=int(agent_id)).first()
    if claims is None:
        expected = agent.secret_hash if agent is not None else _TOKEN_DUMMY_HASH
        if not hmac.compare_digest(expected, hash_agent_key(secret)):
            raise AgentAPIError(
                "Agent credentials are not valid.", status=401, code="invalid_credentials"
            )
    else:
        if agent is None:
            raise AgentAPIError(
                "Agent credentials are not valid.", status=401, code="invalid_credentials"
            )
        if int(claims.get("tid") or 0) != agent.tenant_id:
            raise AgentAPIError(
                "Agent access token belongs to another workspace.",
                status=401,
                code="invalid_token",
            )
        if int(claims.get("ver") or 0) != int(agent.token_version or 1):
            raise AgentAPIError(
                "Agent access token has been revoked. Re-enrol this endpoint.",
                status=401,
                code="token_revoked",
            )
        machine_id = _clean_text(request.headers.get("X-SORA-Machine-Id"), 128)
        if not machine_id:
            raise AgentAPIError(
                "The X-SORA-Machine-Id header is required with a JWT.",
                status=401,
                code="missing_machine_id",
            )
        if not hmac.compare_digest(
            hash_machine_id(machine_id), str(claims.get("mid") or "")
        ):
            raise AgentAPIError(
                "This access token is bound to different hardware.",
                status=401,
                code="hardware_mismatch",
            )
        fingerprint = _clean_text(request.headers.get("X-SORA-Fingerprint"), 64).lower()
        if (
            fingerprint
            and agent.fingerprint
            and not hmac.compare_digest(fingerprint, agent.fingerprint.lower())
        ):
            raise AgentAPIError(
                "This access token is bound to different hardware.",
                status=401,
                code="hardware_mismatch",
            )
    if not agent.is_active:
        raise AgentAPIError(
            "This agent has been deactivated.", status=403, code="agent_disabled"
        )
    if agent.tenant.is_locked:
        raise AgentAPIError(
            "This workspace is locked; inventory is read-only right now.",
            status=403,
            code="workspace_locked",
        )
    return agent


# ---------------------------------------------------------------------------
# Inventory normalisation
# ---------------------------------------------------------------------------
def _safe_int(value, *, minimum: int = 0, maximum: int = 2_000_000_000):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(minimum, min(number, maximum))


def _safe_float(value, *, minimum: float = 0.0, maximum: float = 10_000_000.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(max(minimum, min(number, maximum)), 2)


def _safe_date(value):
    parsed = parse_date(str(value)[:10]) if value else None
    if parsed and date(2000, 1, 1) <= parsed <= timezone.localdate() + timedelta(days=1):
        return parsed.isoformat()
    return None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "ok", "running", "up"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "stopped", "down"}:
        return False
    return None


def _mac(value) -> str:
    raw = re.sub(r"[^0-9A-Fa-f]", "", _clean_text(value, 32))
    if len(raw) not in {12, 16}:
        return ""
    return ":".join(raw[index : index + 2] for index in range(0, len(raw), 2)).upper()


def normalize_inventory(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise AgentAPIError("Inventory must be a JSON object.", code="invalid_payload")
    collected_at = parse_datetime(str(payload.get("collected_at") or "")) if payload.get("collected_at") else None
    if collected_at is None:
        collected_at = timezone.now()
    elif timezone.is_naive(collected_at):
        collected_at = timezone.make_aware(collected_at, timezone.utc)
    now = timezone.now()
    if collected_at > now + timedelta(minutes=15) or collected_at < now - timedelta(days=30):
        collected_at = now

    os_info = _dict(payload.get("os"))
    system = _dict(payload.get("system"))
    cpu = _dict(payload.get("cpu"))
    memory = _dict(payload.get("memory"))

    disks = []
    for row in _list(payload.get("disks"))[:64]:
        row = _dict(row)
        total = _safe_float(row.get("total_gb"), maximum=10_000_000)
        free = _safe_float(row.get("free_gb"), maximum=10_000_000)
        if total is None:
            continue
        if free is not None:
            free = min(free, total)
        disks.append(
            {
                "device": _clean_text(row.get("device"), 80),
                "mountpoint": _clean_text(row.get("mountpoint"), 160),
                "filesystem": _clean_text(row.get("filesystem"), 40),
                "serial": _clean_text(row.get("serial"), 120),
                "model": _clean_text(row.get("model"), 120),
                "disk_type": _clean_text(row.get("disk_type") or row.get("type"), 20),
                "total_gb": total,
                "free_gb": free,
            }
        )

    network = []
    for row in _list(payload.get("network"))[:32]:
        row = _dict(row)
        ipv4 = [_clean_text(ip, 45) for ip in _list(row.get("ipv4"))[:8] if _clean_text(ip, 45)]
        ipv6 = [_clean_text(ip, 60) for ip in _list(row.get("ipv6"))[:8] if _clean_text(ip, 60)]
        name = _clean_text(row.get("name"), 120)
        mac = _mac(row.get("mac"))
        if not (name or mac or ipv4 or ipv6):
            continue
        network.append({"name": name, "mac": mac, "ipv4": ipv4, "ipv6": ipv6})

    ports = []
    seen_ports = set()
    for row in _list(payload.get("ports"))[:MAX_PORTS]:
        row = _dict(row)
        port = _safe_int(row.get("port"), minimum=0, maximum=65535)
        if not port:
            continue
        protocol = (_clean_text(row.get("protocol"), 8) or "tcp").lower()
        local_address = _clean_text(
            row.get("local_address") or row.get("address"), 80
        )
        process = _clean_text(row.get("process") or row.get("process_name"), 200)
        state = _clean_text(row.get("state"), 20)
        key = (protocol, local_address, port, process)
        if key in seen_ports:
            continue
        seen_ports.add(key)
        ports.append(
            {
                "protocol": protocol,
                "local_address": local_address,
                "port": port,
                "process": process,
                "state": state,
            }
        )

    raw_security = _dict(payload.get("security"))
    antivirus = []
    for row in _list(raw_security.get("antivirus"))[:16]:
        row = _dict(row)
        name = _clean_text(row.get("name"), 200)
        if not name:
            continue
        antivirus.append(
            {
                "name": name,
                "provider": _clean_text(row.get("provider"), 120),
                "enabled": _as_bool(row.get("enabled")),
                "up_to_date": _as_bool(row.get("up_to_date")),
            }
        )
    edr = []
    for row in _list(raw_security.get("edr") or raw_security.get("edr_agents"))[:16]:
        row = _dict(row)
        name = _clean_text(row.get("name"), 200)
        if not name:
            continue
        edr.append(
            {
                "name": name,
                "status": _clean_text(row.get("status"), 60),
                "version": _clean_text(row.get("version"), 60),
            }
        )
    firewall_raw = raw_security.get("firewall")
    if isinstance(firewall_raw, dict):
        firewall = {
            "name": _clean_text(firewall_raw.get("name"), 120),
            "enabled": _as_bool(firewall_raw.get("enabled")),
        }
    else:
        firewall = {"name": "", "enabled": _as_bool(firewall_raw)}
    encryption_raw = raw_security.get("disk_encryption") or raw_security.get("encryption")
    if isinstance(encryption_raw, dict):
        encryption = {
            "enabled": _as_bool(encryption_raw.get("enabled")),
            "solution": _clean_text(encryption_raw.get("solution"), 120),
        }
    else:
        encryption = {"enabled": _as_bool(encryption_raw), "solution": ""}
    security = {
        "antivirus": antivirus,
        "edr": edr,
        "firewall": firewall,
        "disk_encryption": encryption,
        "secure_boot": _as_bool(raw_security.get("secure_boot")),
    }

    software = []
    seen = set()
    software_limit = agent_policy()["software_limit"]
    for row in _list(payload.get("software"))[:software_limit]:
        row = _dict(row)
        name = _clean_text(row.get("name"), 255)
        if not name:
            continue
        version = _clean_text(row.get("version"), 120)
        key = (name.casefold(), version.casefold())
        if key in seen:
            continue
        seen.add(key)
        software.append(
            {
                "name": name,
                "version": version,
                "publisher": _clean_text(row.get("publisher"), 200),
                "source": _clean_text(row.get("source"), 40),
                "installed_on": _safe_date(row.get("installed_on")),
            }
        )

    normalized = {
        "collected_at": collected_at.isoformat(),
        "hostname": _clean_text(payload.get("hostname"), 200),
        "os": {
            "family": _clean_text(os_info.get("family"), 10).lower(),
            "name": _clean_text(os_info.get("name"), 200),
            "version": _clean_text(os_info.get("version"), 120),
            "build": _clean_text(os_info.get("build") or os_info.get("build_number"), 120),
            "architecture": _clean_text(os_info.get("architecture"), 40),
            "boot_time": _clean_text(os_info.get("boot_time"), 40),
        },
        "system": {
            "manufacturer": _clean_text(system.get("manufacturer"), 120),
            "model": _clean_text(system.get("model"), 120),
            "serial_number": _clean_text(system.get("serial_number"), 120),
            "uuid": _clean_text(system.get("uuid"), 120),
            "bios_version": _clean_text(system.get("bios_version"), 120),
            "bios_serial": _clean_text(system.get("bios_serial"), 120),
            "domain": _clean_text(system.get("domain"), 120),
            "logged_in_user": _clean_text(system.get("logged_in_user"), 150),
        },
        "cpu": {
            "model": _clean_text(cpu.get("model"), 200),
            "architecture": _clean_text(cpu.get("architecture"), 40),
            "physical_cores": _safe_int(cpu.get("physical_cores"), maximum=1024),
            "logical_cores": _safe_int(cpu.get("logical_cores"), maximum=4096),
            "max_clock_mhz": _safe_int(cpu.get("max_clock_mhz"), maximum=100_000),
        },
        "memory": {
            "total_mb": _safe_int(memory.get("total_mb"), maximum=100_000_000),
            "available_mb": _safe_int(memory.get("available_mb"), maximum=100_000_000),
        },
        "disks": disks,
        "network": network,
        "ports": ports,
        "security": security,
        "software": software,
        "agent": {
            "version": _clean_text(_dict(payload.get("agent")).get("version"), 40),
            "platform": _clean_text(_dict(payload.get("agent")).get("platform"), 40),
        },
    }
    if normalized["os"]["family"] not in OS_FAMILIES:
        normalized["os"]["family"] = "other"
    return normalized


def _refresh_software(agent: AssetAgent, items: list[dict]) -> int:
    now = timezone.now()
    existing = {
        (row.name.casefold(), row.version.casefold()): row
        for row in AssetSoftware.objects.filter(agent=agent)
    }
    kept_ids: list[int] = []
    created: list[AssetSoftware] = []
    updated: list[AssetSoftware] = []
    for item in items:
        key = (item["name"].casefold(), item["version"].casefold())
        row = existing.get(key)
        if row is None:
            created.append(
                AssetSoftware(
                    agent=agent,
                    name=item["name"],
                    version=item["version"],
                    publisher=item["publisher"],
                    source=item["source"],
                    installed_on=parse_date(item["installed_on"]) if item["installed_on"] else None,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            continue
        row.publisher = item["publisher"] or row.publisher
        row.source = item["source"] or row.source
        if item["installed_on"]:
            row.installed_on = parse_date(item["installed_on"])
        row.last_seen_at = now
        updated.append(row)
        kept_ids.append(row.pk)
    if kept_ids:
        AssetSoftware.objects.filter(agent=agent).exclude(pk__in=kept_ids).delete()
    else:
        AssetSoftware.objects.filter(agent=agent).delete()
    if created:
        AssetSoftware.objects.bulk_create(created, batch_size=500)
    if updated:
        AssetSoftware.objects.bulk_update(
            updated,
            ["publisher", "source", "installed_on", "last_seen_at"],
            batch_size=500,
        )
    return len(created) + len(updated)


def _sync_linked_asset(agent: AssetAgent) -> list[str]:
    asset = agent.asset
    if asset is None:
        return []
    updates = []
    for agent_field, asset_field in (
        ("serial_number", "serial_number"),
        ("manufacturer", "manufacturer"),
        ("model_name", "model_name"),
    ):
        incoming = getattr(agent, agent_field)
        if incoming and not getattr(asset, asset_field):
            setattr(asset, asset_field, incoming)
            updates.append(asset_field)
    if updates:
        asset.save(update_fields=updates + ["updated_at"])
    return updates


@transaction.atomic
def record_inventory(agent: AssetAgent, payload: dict, *, ip_address: str = "") -> AssetInventorySnapshot:
    normalized = normalize_inventory(payload)
    now = timezone.now()
    software_items = normalized["software"]
    disk_total = sum((disk["total_gb"] or 0) for disk in normalized["disks"])
    primary_ip = ""
    for interface in normalized["network"]:
        for address in interface["ipv4"]:
            if not address.startswith("127."):
                primary_ip = address
                break
        if primary_ip:
            break
    primary_ip = primary_ip or (ip_address or "")[:45]

    if normalized["hostname"]:
        agent.hostname = normalized["hostname"]
    os_info = normalized["os"]
    if os_info["family"] and os_info["family"] != "other":
        agent.os_family = os_info["family"]
    agent.os_name = os_info["name"] or agent.os_name
    agent.os_version = os_info["version"] or agent.os_version
    agent.os_build = os_info["build"] or agent.os_build
    agent.architecture = os_info["architecture"] or agent.architecture
    system = normalized["system"]
    agent.manufacturer = system["manufacturer"] or agent.manufacturer
    agent.model_name = system["model"] or agent.model_name
    agent.serial_number = system["serial_number"] or agent.serial_number
    agent.logged_in_user = system["logged_in_user"] or agent.logged_in_user
    cpu = normalized["cpu"]
    agent.cpu_model = cpu["model"] or agent.cpu_model
    agent.cpu_architecture = cpu["architecture"] or agent.cpu_architecture
    agent.cpu_cores = cpu["physical_cores"] or cpu["logical_cores"] or agent.cpu_cores
    agent.memory_total_mb = normalized["memory"]["total_mb"] or agent.memory_total_mb
    if disk_total:
        agent.disk_total_gb = round(disk_total, 2)
    agent.mac_addresses = [
        interface["mac"] for interface in normalized["network"] if interface["mac"]
    ][:16]
    agent.disk_serials = [
        disk["serial"] for disk in normalized["disks"] if disk["serial"]
    ][:32]
    agent.listening_ports = normalized["ports"]
    agent.security = normalized["security"]
    agent.primary_ip = primary_ip or agent.primary_ip
    if normalized["agent"]["version"]:
        agent.agent_version = normalized["agent"]["version"]
    agent.hardware = {
        "os": os_info,
        "system": system,
        "cpu": cpu,
        "memory": normalized["memory"],
        "disks": normalized["disks"],
        "network": normalized["network"],
        "ports": normalized["ports"],
        "security": normalized["security"],
    }
    agent.last_seen_at = now
    agent.last_inventory_at = now
    agent.last_ip = (ip_address or "")[:45]
    agent.save()

    checksum = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    snapshot = AssetInventorySnapshot.objects.create(
        agent=agent,
        collected_at=parse_datetime(normalized["collected_at"]) or now,
        received_at=now,
        payload=normalized,
        software_count=len(software_items),
        checksum=checksum,
    )
    _refresh_software(agent, software_items)
    AssetAgentEvent.objects.create(
        agent=agent,
        kind="inventory",
        detail=(
            f"Inventory received: {len(software_items)} application(s), "
            f"{len(normalized['disks'])} disk(s), {len(normalized['network'])} interface(s), "
            f"{len(normalized['ports'])} listening port(s)."
        ),
        ip_address=(ip_address or "")[:45],
    )

    retention = agent_policy()["snapshot_retention"]
    stale_ids = list(
        AssetInventorySnapshot.objects.filter(agent=agent)
        .order_by("-received_at", "-id")
        .values_list("id", flat=True)[retention:]
    )
    if stale_ids:
        AssetInventorySnapshot.objects.filter(agent=agent, pk__in=stale_ids).delete()

    filled = _sync_linked_asset(agent)
    if filled:
        AssetAgentEvent.objects.create(
            agent=agent,
            kind="linked",
            detail=f"Asset defaults refreshed from inventory: {', '.join(filled)}.",
        )
    return snapshot


def record_heartbeat(agent: AssetAgent, payload: dict, *, ip_address: str = "") -> None:
    agent.last_seen_at = timezone.now()
    agent.last_ip = (ip_address or "")[:45]
    version = _clean_text(payload.get("agent_version"), 40)
    if version:
        agent.agent_version = version
    agent.save(update_fields=["last_seen_at", "last_ip", "agent_version", "updated_at"])
    AssetAgentEvent.objects.create(
        agent=agent,
        kind="heartbeat",
        detail=f"Agent heartbeat from {agent.hostname}.",
        ip_address=(ip_address or "")[:45],
    )


# ---------------------------------------------------------------------------
# Custody helpers used by the workspace UI
# ---------------------------------------------------------------------------
def link_asset(agent: AssetAgent, asset: Asset, *, actor_user_id: int | None = None) -> None:
    agent.asset = asset
    agent.save(update_fields=["asset", "updated_at"])
    filled = _sync_linked_asset(agent)
    AssetAgentEvent.objects.create(
        agent=agent,
        kind="linked",
        detail=(
            f"Linked to asset {asset.asset_code} · {asset.name}."
            + (f" Defaults refreshed: {', '.join(filled)}." if filled else "")
        ),
        actor_user_id=actor_user_id,
    )


def unlink_asset(agent: AssetAgent, *, actor_user_id: int | None = None) -> None:
    asset_label = str(agent.asset) if agent.asset_id else "asset"
    agent.asset = None
    agent.save(update_fields=["asset", "updated_at"])
    AssetAgentEvent.objects.create(
        agent=agent,
        kind="unlinked",
        detail=f"Unlinked from {asset_label}.",
        actor_user_id=actor_user_id,
    )


def set_active(agent: AssetAgent, active: bool, *, actor_user_id: int | None = None) -> None:
    agent.is_active = active
    agent.save(update_fields=["is_active", "updated_at"])
    AssetAgentEvent.objects.create(
        agent=agent,
        kind="activated" if active else "deactivated",
        detail=f"Agent {'activated' if active else 'deactivated'} by an administrator.",
        actor_user_id=actor_user_id,
    )
