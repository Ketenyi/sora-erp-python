"""Shared helpers used across modules."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db import connection
from django.utils import timezone

from apps.core.tenancy import ALL_TENANTS, current_tenant


def _settings_tenant():
    tenant = current_tenant()
    if tenant is ALL_TENANTS:
        return None
    return tenant


def get_company_settings() -> dict[str, str]:
    """Return platform defaults merged with the active workspace overrides."""
    from apps.core.models import CompanySetting

    try:
        rows = list(CompanySetting.objects.filter(tenant__isnull=True))
        tenant = _settings_tenant()
        if tenant is not None:
            rows.extend(CompanySetting.objects.filter(tenant=tenant))
        return {row.setting_key: row.setting_value for row in rows}
    except Exception:
        return {}


def save_company_settings(mapping: dict[str, str], *, tenant="current") -> None:
    """Persist settings for the active workspace.

    Pass ``tenant=None`` to update the platform-wide fallback rows.
    """
    from apps.core.models import CompanySetting

    target = _settings_tenant() if tenant == "current" else tenant
    for key, value in mapping.items():
        CompanySetting.objects.update_or_create(
            tenant=target,
            setting_key=key,
            defaults={"setting_value": value},
        )


def set_company_setting(key: str, value: str, *, tenant="current") -> None:
    save_company_settings({key: value}, tenant=tenant)


CURRENCY_SYMBOLS = {
    "KES": "KSh",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "TZS": "TSh",
    "UGX": "USh",
    "ZAR": "R",
    "NGN": "₦",
}


def tenant_default_settings(tenant) -> dict[str, str]:
    """Baseline workspace settings written when a tenant is provisioned."""
    symbol = CURRENCY_SYMBOLS.get((tenant.currency or "").upper(), tenant.currency or "KSh")
    return {
        "company_name": tenant.company_name,
        "company_email": tenant.admin_email,
        "company_phone": tenant.phone_number or "",
        "currency_symbol": symbol,
        "currency_code": tenant.currency or "KES",
    }


def provision_company_settings(tenant) -> None:
    from apps.core.models import CompanySetting

    values = tenant_default_settings(tenant)
    for key, value in values.items():
        CompanySetting.objects.update_or_create(
            tenant=tenant,
            setting_key=key,
            defaults={"setting_value": value},
        )


def get_currency_symbol(settings: dict | None = None) -> str:
    settings = settings if settings is not None else get_company_settings()
    return settings.get("currency_symbol") or settings.get("currency_code") or "KSh"


def to_decimal(value, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip() or default)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def parse_date(value, default=None):
    if not value:
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return default


def _sequence_scope() -> str:
    tenant = current_tenant()
    if tenant is None or tenant is ALL_TENANTS:
        return "global"
    return f"tenant:{tenant.pk}"


def _increment_counter(scope: str, key: str, seed_query) -> int:
    """Atomically increment a document counter, seeding it from existing rows."""
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE document_sequences SET value = value + 1, updated_at = %s "
            "WHERE scope = %s AND sequence_key = %s RETURNING value",
            [now, scope, key],
        )
        row = cursor.fetchone()
        if row is not None:
            return int(row[0])
        seed = max(1, int(seed_query(cursor) or 1))
        cursor.execute(
            "INSERT INTO document_sequences (scope, sequence_key, value, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (scope, sequence_key) "
            "DO UPDATE SET value = document_sequences.value + 1, updated_at = %s "
            "RETURNING value",
            [scope, key, seed, now, now, now],
        )
        row = cursor.fetchone()
    return int(row[0])


def next_number(key: str) -> int:
    """Allocate a concurrency-safe integer from a named workspace counter."""
    return _increment_counter(_sequence_scope(), f"number:{key}", lambda cursor: 1)


def _tenant_clause(tenant) -> tuple[str, list[object]]:
    if tenant is None or tenant is ALL_TENANTS:
        return "", []
    return " AND tenant_id = %s", [tenant.pk]


def next_sequence(prefix: str, table: str, column: str) -> str:
    """Generate a document number like QUO-20261023-0007.

    Numbering uses an atomic counter row so concurrent checkouts cannot
    allocate the same number. The counter is seeded from the newest existing
    document the first time a prefix is used.
    """
    today = timezone.localdate()
    scope = _sequence_scope()
    key = f"{prefix}:{today:%Y%m%d}"
    tenant = current_tenant()
    like = f"{prefix}-{today:%Y%m%d}-%"

    def seed(cursor):
        clause, params = _tenant_clause(tenant)
        cursor.execute(
            f"SELECT {column} FROM {table} WHERE {column} LIKE %s{clause} "
            f"ORDER BY LENGTH({column}) DESC, {column} DESC LIMIT 1",
            [like, *params],
        )
        row = cursor.fetchone()
        if not row or not row[0]:
            return 1
        try:
            return int(str(row[0]).rsplit("-", 1)[-1]) + 1
        except (ValueError, IndexError):
            return 1

    number = _increment_counter(scope, key, seed)
    return f"{prefix}-{today:%Y%m%d}-{number:04d}"


def next_code(prefix: str, table: str, column: str, width: int = 4) -> str:
    """Generate a sequential code like CLI-0007 (no date component)."""
    scope = _sequence_scope()
    tenant = current_tenant()

    def seed(cursor):
        clause, params = _tenant_clause(tenant)
        cursor.execute(
            f"SELECT {column} FROM {table} WHERE {column} LIKE %s{clause}",
            [f"{prefix}-%", *params],
        )
        highest = 0
        for (value,) in cursor.fetchall():
            suffix = str(value or "").rpartition("-")[2]
            if suffix.isdigit():
                highest = max(highest, int(suffix))
        return highest + 1

    number = _increment_counter(scope, f"code:{prefix}", seed)
    return f"{prefix}-{number:0{width}d}"


def daterange(start, end):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)
