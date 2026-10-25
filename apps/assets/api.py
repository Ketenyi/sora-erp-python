"""Endpoint-agent REST API: enrolment, tokens, inventory and deployment."""

from __future__ import annotations

import gzip
import json

from django.http import HttpResponse, JsonResponse
from django.utils.cache import add_never_cache_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.utils import timezone

from apps.assets import agent_packages, agent_services
from apps.assets.agent_services import AgentAPIError
from apps.core.security import client_ip
from apps.core.tenancy import tenant_context

MAX_ENROLL_BYTES = 32 * 1024
MAX_INVENTORY_BYTES = 3 * 1024 * 1024
MAX_HEARTBEAT_BYTES = 8 * 1024

try:  # Python 3.14 ships Zstandard in the standard library.
    from compression import zstd  # type: ignore
except ImportError:  # pragma: no cover - older runtimes
    zstd = None


def _error(error: AgentAPIError) -> JsonResponse:
    response = JsonResponse(
        {"ok": False, "error": error.code, "message": error.message},
        status=error.status,
    )
    add_never_cache_headers(response)
    return response


def _ok(payload: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse({"ok": True, **payload}, status=status)
    add_never_cache_headers(response)
    return response


def _decompress(body: bytes, encoding: str, max_bytes: int) -> bytes:
    if not encoding or encoding in {"identity", "none"}:
        return body
    if encoding in {"zstd", "zstandard"}:
        if zstd is None:
            raise AgentAPIError(
                "This server cannot read Zstandard bodies.",
                status=415,
                code="unsupported_encoding",
            )
        try:
            info = zstd.get_frame_info(body)
            if info.decompressed_size is not None and info.decompressed_size > max_bytes:
                raise AgentAPIError(
                    "The request body is too large.", status=413, code="payload_too_large"
                )
            data = zstd.decompress(body)
        except AgentAPIError:
            raise
        except Exception as exc:  # malformed frame
            raise AgentAPIError(
                "The compressed body is malformed.", code="invalid_compression"
            ) from exc
        if len(data) > max_bytes:
            raise AgentAPIError(
                "The request body is too large.", status=413, code="payload_too_large"
            )
        return data
    if encoding in {"gzip", "x-gzip"}:
        try:
            data = gzip.decompress(body)
        except Exception as exc:
            raise AgentAPIError(
                "The compressed body is malformed.", code="invalid_compression"
            ) from exc
        if len(data) > max_bytes:
            raise AgentAPIError(
                "The request body is too large.", status=413, code="payload_too_large"
            )
        return data
    raise AgentAPIError(
        "Use Content-Encoding: zstd or gzip.", status=415, code="unsupported_encoding"
    )


def _read_json(request, max_bytes: int) -> dict:
    content_type = (request.content_type or "").lower()
    if content_type and content_type != "application/json":
        raise AgentAPIError(
            "Send the payload as application/json.", status=415, code="unsupported_media_type"
        )
    content_encoding = (request.headers.get("Content-Encoding") or "").strip().lower()
    try:
        body = request.body
    except Exception as exc:
        raise AgentAPIError(
            "The request body is too large.", status=413, code="payload_too_large"
        ) from exc
    if not body:
        raise AgentAPIError("A JSON body is required.", code="empty_body")
    body = _decompress(body, content_encoding, max_bytes)
    if not body:
        raise AgentAPIError("A JSON body is required.", code="empty_body")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AgentAPIError("The JSON body is malformed.", code="invalid_json") from exc
    if not isinstance(payload, dict):
        raise AgentAPIError("The JSON body must be an object.", code="invalid_json")
    return payload


def _parse_quietly(request, max_bytes: int) -> dict:
    """Read an optional body (heartbeat) without rejecting empty requests."""
    content_length = (request.headers.get("Content-Length") or "").strip()
    if not content_length or content_length == "0":
        return {}
    return _read_json(request, max_bytes)


def _enrollment_response(agent, secret: str, *, status: int = 201) -> JsonResponse:
    with tenant_context(agent.tenant):
        access_token, expires_at = agent_services.issue_access_token(agent)
        policy = agent_services.agent_policy()
        return _ok(
            {
                "agent_id": agent.pk,
                "agent_key": secret,
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_at": expires_at.isoformat(),
                "policy": {
                    "heartbeat_seconds": policy["heartbeat_seconds"],
                    "inventory_minutes": policy["inventory_minutes"],
                    "snapshot_retention": policy["snapshot_retention"],
                    "software_limit": policy["software_limit"],
                },
                "hostname": agent.hostname,
                "workspace": {
                    "name": agent.tenant.brand_name or agent.tenant.company_name,
                    "slug": agent.tenant.slug,
                },
                "server_time": timezone.now().isoformat(),
                "message": (
                    "Agent enrolled. Store the agent_id, agent_key and access_token "
                    "securely; the key will not be shown again."
                ),
            },
            status=status,
        )


@csrf_exempt
@require_POST
def enroll(request):
    ip_address = client_ip(request)
    if agent_services.throttle("enroll", ip_address, 60, 60):
        return _error(
            AgentAPIError(
                "Too many enrolment attempts. Try again shortly.",
                status=429,
                code="rate_limited",
            )
        )
    try:
        payload = _read_json(request, MAX_ENROLL_BYTES)
        agent, secret = agent_services.enroll_agent(payload, ip_address=ip_address)
    except AgentAPIError as error:
        return _error(error)
    return _enrollment_response(agent, secret)


@csrf_exempt
@require_POST
def register(request):
    """Exchange a tenant bootstrap token for a hardware-bound access JWT."""
    ip_address = client_ip(request)
    if agent_services.throttle("enroll", ip_address, 60, 60):
        return _error(
            AgentAPIError(
                "Too many registration attempts. Try again shortly.",
                status=429,
                code="rate_limited",
            )
        )
    try:
        payload = _read_json(request, MAX_ENROLL_BYTES)
        payload.setdefault(
            "enrollment_token",
            payload.get("bootstrap_token") or payload.get("token"),
        )
        if not payload.get("hostname"):
            payload["hostname"] = payload.get("device_name") or payload.get("machine_id")
        payload.setdefault("provisioning_method", "portal")
        agent, secret = agent_services.enroll_agent(payload, ip_address=ip_address)
    except AgentAPIError as error:
        return _error(error)
    return _enrollment_response(agent, secret)


@csrf_exempt
@require_POST
def refresh(request):
    ip_address = client_ip(request)
    if agent_services.throttle("refresh", ip_address, 120, 60):
        return _error(
            AgentAPIError(
                "Too many token refreshes. Slow down.", status=429, code="rate_limited"
            )
        )
    try:
        agent = agent_services.authenticate_agent(request)
        with tenant_context(agent.tenant):
            access_token, expires_at = agent_services.issue_access_token(agent)
            policy = agent_services.agent_policy()
        return _ok(
            {
                "agent_id": agent.pk,
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_at": expires_at.isoformat(),
                "policy": {
                    "heartbeat_seconds": policy["heartbeat_seconds"],
                    "inventory_minutes": policy["inventory_minutes"],
                },
                "server_time": timezone.now().isoformat(),
            }
        )
    except AgentAPIError as error:
        return _error(error)


@csrf_exempt
@require_POST
def inventory(request):
    ip_address = client_ip(request)
    if agent_services.throttle("inventory", ip_address, 600, 60):
        return _error(
            AgentAPIError(
                "Too many inventory reports. Slow down.", status=429, code="rate_limited"
            )
        )
    try:
        agent = agent_services.authenticate_agent(request)
        payload = _read_json(request, MAX_INVENTORY_BYTES)
        with tenant_context(agent.tenant):
            snapshot = agent_services.record_inventory(
                agent, payload, ip_address=ip_address
            )
            return _ok(
                {
                    "agent_id": agent.pk,
                    "inventory_id": snapshot.pk,
                    "received_at": snapshot.received_at.isoformat(),
                    "collected_at": snapshot.collected_at.isoformat(),
                    "software_count": snapshot.software_count,
                    "server_time": timezone.now().isoformat(),
                }
            )
    except AgentAPIError as error:
        return _error(error)


@csrf_exempt
@require_POST
def heartbeat(request):
    ip_address = client_ip(request)
    if agent_services.throttle("heartbeat", ip_address, 600, 60):
        return _error(
            AgentAPIError(
                "Too many heartbeats. Slow down.", status=429, code="rate_limited"
            )
        )
    try:
        agent = agent_services.authenticate_agent(request)
        payload = _parse_quietly(request, MAX_HEARTBEAT_BYTES)
        with tenant_context(agent.tenant):
            agent_services.record_heartbeat(agent, payload, ip_address=ip_address)
            policy = agent_services.agent_policy()
            return _ok(
                {
                    "agent_id": agent.pk,
                    "last_seen_at": agent.last_seen_at.isoformat(),
                    "policy": {
                        "heartbeat_seconds": policy["heartbeat_seconds"],
                        "inventory_minutes": policy["inventory_minutes"],
                    },
                    "server_time": timezone.now().isoformat(),
                }
            )
    except AgentAPIError as error:
        return _error(error)


def _token_from_request(request) -> str:
    raw = (request.GET.get("token") or "").strip()
    if raw:
        return raw
    header = (request.headers.get("Authorization") or "").strip()
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _package_context(request, *, default_platform: str = "windows"):
    ip_address = client_ip(request)
    if agent_services.throttle("package", ip_address, 30, 60):
        raise AgentAPIError(
            "Too many package downloads. Try again shortly.",
            status=429,
            code="rate_limited",
        )
    raw_token = _token_from_request(request)
    token = agent_services.validate_install_token(raw_token)
    with tenant_context(token.tenant):
        platform = (request.GET.get("platform") or default_platform).strip().lower()
        if platform not in agent_packages.PLATFORMS:
            raise AgentAPIError(
                "platform must be windows or linux.",
                status=400,
                code="invalid_platform",
            )
        method = (request.GET.get("method") or "").strip().lower()
        if method not in agent_services.PROVISIONING_METHODS:
            method = "gpo" if platform == "windows" else "linux"
        config = agent_packages.package_config(
            server_url=request.build_absolute_uri("/").rstrip("/"),
            raw_token=raw_token,
            label=token.label,
            provisioning_method=method,
            organization=token.tenant.brand_name or token.tenant.company_name,
        )
    return token, raw_token, platform, config


@require_GET
def package(request):
    try:
        _token, _raw, platform, config = _package_context(request)
        archive = agent_packages.build_package_zip(config, platform)
    except AgentAPIError as error:
        return _error(error)
    except agent_packages.PackageError as error:
        return _error(AgentAPIError(str(error), status=503, code="package_unavailable"))
    response = HttpResponse(archive, content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="sora-asset-agent-{platform}.zip"'
    )
    add_never_cache_headers(response)
    return response


@require_GET
def bootstrap(request):
    """Return a one-line Linux bootstrap script for portal self-onboarding."""
    try:
        _token, raw_token, _platform, _config = _package_context(
            request, default_platform="linux"
        )
        script = agent_packages.linux_bootstrap_script(
            server_url=request.build_absolute_uri("/").rstrip("/"),
            raw_token=raw_token,
        )
    except AgentAPIError as error:
        return _error(error)
    except agent_packages.PackageError as error:
        return _error(AgentAPIError(str(error), status=503, code="package_unavailable"))
    response = HttpResponse(script, content_type="text/x-shellscript; charset=utf-8")
    response["Content-Disposition"] = 'inline; filename="sora-onboard.sh"'
    add_never_cache_headers(response)
    return response


__all__ = ["bootstrap", "enroll", "heartbeat", "inventory", "package", "refresh", "register"]
