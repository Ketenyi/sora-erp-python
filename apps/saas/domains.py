"""Custom tenant domains: validation, DNS proof and host routing.

Ownership is proven with a TXT record at ``_sora-verification.<hostname>``.
A hostname is globally unique and only a verified row routes to a workspace,
so releasing a domain forces the next claimant to prove DNS control again.
"""

from __future__ import annotations

import hmac
import ipaddress
import re
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.saas.models import TenantAuditLog, TenantDomain

MAX_HOSTNAME_LENGTH = 253
_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_BAD_CHARACTERS = set(' /\\@:?#*_<>"\'')
_RESERVED_TLDS = {
    "arpa",
    "corp",
    "example",
    "home",
    "internal",
    "intranet",
    "invalid",
    "lan",
    "local",
    "localhost",
    "localdomain",
    "onion",
    "test",
}
_TXT_PREFIX = "sora-verify="


class DomainError(ValueError):
    """A hostname cannot be claimed or used as requested."""


class DomainLookupError(RuntimeError):
    """The DNS resolver could not answer the TXT query."""


def clean_host(value: str) -> str:
    """Lowercase a host and remove brackets, port and trailing dot."""

    host = (value or "").strip().lower()
    if host.startswith("["):
        host = host.split("]", 1)[0].lstrip("[")
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def _normalise_syntax(raw: str) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        raise DomainError("Enter a domain name.")
    if len(candidate) > 1024:
        raise DomainError("That domain name is far too long.")
    if any(ch in _BAD_CHARACTERS for ch in candidate):
        raise DomainError(
            "Enter only the hostname, for example erp.yourcompany.com. "
            "Do not include a scheme, port, path or wildcard."
        )
    candidate = candidate.rstrip(".").lower()
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainError("That domain name contains invalid international characters.") from exc
    if len(candidate) > MAX_HOSTNAME_LENGTH:
        raise DomainError("Domain names cannot exceed 253 characters.")
    labels = candidate.split(".")
    if len(labels) < 2:
        raise DomainError("Use a full domain name, including the top-level domain.")
    for label in labels:
        if not _LABEL_RE.match(label):
            raise DomainError("Each part of the domain may only contain letters, digits and hyphens.")
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        pass
    else:
        raise DomainError("Enter a domain name, not an IP address.")
    tld = labels[-1]
    if len(tld) < 2 or tld.isdigit() or tld in _RESERVED_TLDS:
        raise DomainError("That top-level domain cannot host a workspace.")
    if not re.search(r"[a-z]", tld):
        raise DomainError("That top-level domain is not valid.")
    return candidate


def _setting_domain(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    try:
        return _normalise_syntax(raw)
    except DomainError:
        return ""


def _platform_reserved() -> list[str]:
    """Hosts owned by the platform; tenants may not claim them or their children."""

    from apps.saas.services import platform_settings

    reserved: set[str] = set()
    data = platform_settings()
    base = _setting_domain(data.get("platform_domain"))
    if base:
        reserved.add(base)
    public_host = urlparse(getattr(settings, "SORA_PUBLIC_BASE_URL", "") or "").hostname
    if public_host:
        try:
            reserved.add(_normalise_syntax(public_host))
        except DomainError:
            pass
    for allowed in getattr(settings, "ALLOWED_HOSTS", []):
        value = clean_host(allowed.lstrip("."))
        if not value or value == "*" or value == "testserver":
            continue
        try:
            reserved.add(_normalise_syntax(value))
        except DomainError:
            continue
    return sorted(reserved)


def _ensure_not_reserved(host: str) -> None:
    for reserved in _platform_reserved():
        if host == reserved or host.endswith(f".{reserved}"):
            raise DomainError(
                "That address belongs to the SORA platform. Choose a domain you control."
            )


def normalize_hostname(raw: str) -> str:
    host = _normalise_syntax(raw)
    _ensure_not_reserved(host)
    return host


def is_platform_host(host: str) -> bool:
    """Fast, DB-free check for hosts this deployment serves directly."""

    value = clean_host(host)
    if not value:
        return False
    for allowed in getattr(settings, "ALLOWED_HOSTS", []):
        if allowed == "*":
            continue
        candidate = clean_host(allowed)
        if candidate == value:
            return True
        if candidate.startswith(".") and (
            value == candidate[1:] or value.endswith(candidate)
        ):
            return True
    public_host = urlparse(getattr(settings, "SORA_PUBLIC_BASE_URL", "") or "").hostname
    if public_host and clean_host(public_host) == value:
        return True
    if getattr(settings, "DEBUG", False) and value in {"localhost", "127.0.0.1", "::1", "testserver"}:
        return True
    return False


def platform_origin() -> str:
    """Best available absolute origin for the main workspace site."""

    base = (getattr(settings, "SORA_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if base:
        return base
    from apps.saas.services import platform_settings

    configured = _setting_domain(platform_settings().get("platform_domain"))
    if configured:
        return f"https://{configured}"
    for allowed in getattr(settings, "ALLOWED_HOSTS", []):
        host = (allowed or "").strip()
        if not host or host == "*" or host.startswith("."):
            continue
        scheme = "http" if getattr(settings, "DEBUG", False) else "https"
        return f"{scheme}://{host}"
    return ""


def dns_target(request=None) -> str:
    from apps.saas.services import platform_settings

    configured = (platform_settings().get("domain_target") or "").strip()
    if configured:
        return clean_host(configured)
    base = _setting_domain(platform_settings().get("platform_domain"))
    if base:
        return base
    if request is not None:
        try:
            return clean_host(request.get_host())
        except Exception:
            return ""
    return ""


def domain_for_host(host: str) -> TenantDomain | None:
    value = clean_host(host)
    if not value:
        return None
    return (
        TenantDomain.objects.select_related("tenant", "tenant__plan")
        .filter(hostname=value)
        .first()
    )


def lookup_txt(name: str, *, timeout: float = 5.0) -> list[str]:
    """Return TXT values at ``name``. An empty list means no record."""

    try:
        import dns.exception
        import dns.resolver
    except ImportError as exc:
        raise DomainLookupError(
            "DNS verification is unavailable on this server. Contact platform support."
        ) from exc

    resolver = dns.resolver.Resolver(configure=True)
    resolver.lifetime = timeout
    resolver.timeout = min(timeout, 3.0)
    absolute = name.rstrip(".") + "."
    try:
        answers = resolver.resolve(absolute, "TXT", lifetime=timeout)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.resolver.LifetimeTimeout, dns.resolver.Timeout) as exc:
        raise DomainLookupError("The DNS lookup timed out. Try again shortly.") from exc
    except dns.exception.DNSException as exc:
        raise DomainLookupError("The DNS lookup failed. Try again shortly.") from exc

    values: list[str] = []
    for record in answers:
        chunks = getattr(record, "strings", None)
        if chunks:
            values.append(b"".join(chunks).decode("utf-8", "replace"))
        else:
            values.append(str(record).strip('"'))
    return values


def _audit(domain: TenantDomain, action: str, details: str, actor=None, ip_address: str = "") -> None:
    TenantAuditLog.objects.create(
        tenant=domain.tenant,
        actor=actor,
        action=action,
        details=details,
        ip_address=ip_address,
    )


def _ensure_primary(tenant) -> int | None:
    """Keep exactly one verified primary domain whenever one is available."""

    rows = list(TenantDomain.objects.select_for_update().filter(tenant=tenant).order_by("-verified_at", "-id"))
    if not rows:
        return None
    primary = next((row for row in rows if row.is_primary), None)
    verified = [row for row in rows if row.is_verified]
    if primary is not None and primary.is_verified:
        return primary.pk
    if primary is not None:
        primary.is_primary = False
        primary.save(update_fields=["is_primary", "updated_at"])
    if verified:
        target = verified[0]
        target.is_primary = True
        target.save(update_fields=["is_primary", "updated_at"])
        return target.pk
    return None


@transaction.atomic
def claim_domain(tenant, raw_hostname: str, *, actor=None, ip_address: str = "") -> TenantDomain:
    host = normalize_hostname(raw_hostname)
    limit = max(_setting_int("domain_max_per_tenant", 10), 1)
    if TenantDomain.objects.filter(tenant=tenant).count() >= limit:
        raise DomainError(f"Your plan allows up to {limit} custom domains.")
    if TenantDomain.objects.filter(hostname=host).exists():
        raise DomainError(
            "That domain is already registered. If you own it, remove the old claim or contact support."
        )
    domain = TenantDomain(
        tenant=tenant,
        hostname=host,
        verification_token=secrets.token_urlsafe(24),
        created_by=actor if getattr(actor, "pk", None) else None,
    )
    try:
        with transaction.atomic():
            domain.save()
    except IntegrityError as exc:
        raise DomainError("That domain has just been claimed. Choose another address.") from exc
    _audit(
        domain,
        "domain.claimed",
        f"Custom domain {host} added; waiting for the DNS TXT record.",
        actor,
        ip_address,
    )
    return domain


def _setting_int(key: str, default: int) -> int:
    from apps.saas.services import platform_settings

    raw = platform_settings().get(key)
    if raw in (None, ""):
        return default
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return default


@transaction.atomic
def verify_domain(domain: TenantDomain, *, actor=None, ip_address: str = "", force: bool = False) -> dict:
    """Check the TXT record and update state. Never raises for a missing record."""

    now = timezone.now()
    cooldown = _setting_int("domain_verify_cooldown", 30)
    if (
        not force
        and domain.last_checked_at is not None
        and (now - domain.last_checked_at).total_seconds() < cooldown
    ):
        remaining = int(cooldown - (now - domain.last_checked_at).total_seconds()) + 1
        raise DomainError(f"DNS was checked moments ago. Try again in {remaining} second(s).")

    matched = False
    error = ""
    try:
        values = lookup_txt(domain.verification_record_name)
        expected = domain.verification_record_value
        matched = any(
            hmac.compare_digest(value.strip(), expected) for value in values
        )
        if not matched and not values:
            error = "No TXT record was found at the verification name yet."
        elif not matched:
            error = "A TXT record exists but it does not contain this workspace's token."
    except DomainLookupError as exc:
        error = str(exc)
    except Exception:
        error = "The DNS lookup failed. Try again shortly."

    was_verified = domain.is_verified
    domain.last_checked_at = now
    domain.check_count += 1
    updated = ["last_checked_at", "check_count", "updated_at"]

    if matched:
        domain.status = "verified"
        domain.verified_at = domain.verified_at or now
        domain.last_verified_at = now
        domain.last_error = ""
        domain.consecutive_failures = 0
        updated += [
            "status",
            "verified_at",
            "last_verified_at",
            "last_error",
            "consecutive_failures",
        ]
        domain.save(update_fields=updated)
        primary_id = _ensure_primary(domain.tenant)
        domain.is_primary = primary_id == domain.pk
        if not was_verified:
            _audit(
                domain,
                "domain.verified",
                f"{domain.hostname} verified through DNS and is now routed to the workspace.",
                actor,
                ip_address,
            )
    else:
        domain.last_error = error[:255]
        updated.append("last_error")
        if was_verified:
            domain.consecutive_failures += 1
            updated.append("consecutive_failures")
            threshold = _setting_int("domain_failure_threshold", 3)
            if threshold and domain.consecutive_failures >= threshold:
                domain.status = "disabled"
                domain.is_primary = False
                updated += ["status", "is_primary"]
                _audit(
                    domain,
                    "domain.auto_disabled",
                    f"{domain.hostname} stopped answering DNS verification "
                    f"{domain.consecutive_failures} times; routing disabled.",
                    actor,
                    ip_address,
                )
        domain.save(update_fields=updated)
        if was_verified and domain.status == "disabled":
            _ensure_primary(domain.tenant)

    return {"matched": matched, "error": domain.last_error, "domain": domain}


@transaction.atomic
def set_primary_domain(domain: TenantDomain, *, actor=None, ip_address: str = "") -> None:
    if not domain.is_verified:
        raise DomainError("Verify the domain before making it primary.")
    if domain.is_primary:
        return
    TenantDomain.objects.filter(tenant=domain.tenant, is_primary=True).exclude(
        pk=domain.pk
    ).update(is_primary=False)
    domain.is_primary = True
    domain.save(update_fields=["is_primary", "updated_at"])
    _audit(
        domain,
        "domain.primary",
        f"{domain.hostname} is now the primary custom domain.",
        actor,
        ip_address,
    )


@transaction.atomic
def remove_domain(domain: TenantDomain, *, actor=None, ip_address: str = "") -> None:
    tenant = domain.tenant
    host = domain.hostname
    domain.delete()
    _ensure_primary(tenant)
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="domain.removed",
        details=f"Custom domain {host} removed from the workspace.",
        ip_address=ip_address,
    )


@transaction.atomic
def disable_domain(domain: TenantDomain, *, actor=None, reason: str = "", ip_address: str = "") -> None:
    if domain.status == "disabled":
        raise DomainError("That domain is already disabled.")
    host = domain.hostname
    domain.status = "disabled"
    domain.is_primary = False
    domain.save(update_fields=["status", "is_primary", "updated_at"])
    _ensure_primary(domain.tenant)
    _audit(
        domain,
        "domain.revoked",
        f"{host} disabled by SORA support. {reason}".strip(),
        actor,
        ip_address,
    )


def verify_due_domains(*, force: bool = False) -> dict:
    """Re-check pending claims and verified domains that are due for a health check."""

    hours = _setting_int("domain_reverify_hours", 24)
    now = timezone.now()
    rows = list(TenantDomain.objects.select_related("tenant").order_by("id"))
    checked = verified = failed = 0
    for domain in rows:
        if domain.status == "disabled":
            continue
        due = domain.is_pending
        if not due and domain.last_checked_at is not None:
            due = (now - domain.last_checked_at).total_seconds() >= hours * 3600
        if not due:
            continue
        result = verify_domain(domain, force=True)
        checked += 1
        if result["matched"]:
            verified += 1
        else:
            failed += 1
    return {"checked": checked, "verified": verified, "failed": failed, "total": len(rows)}
