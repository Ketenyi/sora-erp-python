"""Asset valuation helpers and the monthly depreciation posting engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.assets.models import Asset, AssetDepreciation

ZERO = Decimal("0.00")
MONTH_LABELS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def money(value) -> Decimal:
    return (value or ZERO).quantize(Decimal("0.01"))


def month_bounds(period: date) -> tuple[date, date, str]:
    start = period.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    last_day = end.fromordinal(end.toordinal() - 1)
    label = f"{MONTH_LABELS[start.month - 1]} {start.year}"
    return start, last_day, label


def book_totals(assets) -> dict[str, Decimal]:
    totals = {
        "cost": ZERO,
        "salvage": ZERO,
        "accumulated": ZERO,
        "book_value": ZERO,
    }
    for asset in assets:
        totals["cost"] += asset.purchase_cost or ZERO
        totals["salvage"] += asset.salvage_value or ZERO
        totals["accumulated"] += asset.accumulated_depreciation or ZERO
        totals["book_value"] += asset.book_value
    return {key: money(value) for key, value in totals.items()}


def monthly_charge(asset: Asset) -> Decimal:
    """Current-month charge capped at the remaining depreciable amount."""
    if asset.depreciation_method == "none":
        return ZERO
    cost = asset.purchase_cost or ZERO
    salvage = min(asset.salvage_value or ZERO, cost)
    remaining = max(asset.book_value - salvage, ZERO)
    if remaining <= ZERO:
        return ZERO
    return money(min(asset.monthly_depreciation(), remaining))


def post_depreciation(period: date, user_id=None) -> dict[str, object]:
    """Post one month of depreciation, skipping existing or ineligible assets."""

    start, end, label = month_bounds(period)
    created = 0
    skipped_existing = 0
    skipped_ineligible = 0
    total = ZERO

    with transaction.atomic():
        posted_ids = set(
            AssetDepreciation.objects.filter(period_date=start).values_list(
                "asset_id", flat=True
            )
        )
        assets = Asset.objects.exclude(status__in=["retired", "disposed"]).select_related(
            "category"
        )
        for asset in assets:
            if asset.id in posted_ids:
                skipped_existing += 1
                continue
            acquired = asset.purchase_date or (
                timezone.localtime(asset.created_at).date() if asset.created_at else None
            )
            if acquired is None or acquired > end:
                skipped_ineligible += 1
                continue
            amount = monthly_charge(asset)
            if amount <= ZERO:
                skipped_ineligible += 1
                continue
            accumulated = money((asset.accumulated_depreciation or ZERO) + amount)
            AssetDepreciation.objects.create(
                asset=asset,
                period_date=start,
                period_label=label,
                amount=amount,
                accumulated_total=accumulated,
                created_by=user_id,
                created_at=timezone.now(),
            )
            asset.accumulated_depreciation = accumulated
            asset.save(update_fields=["accumulated_depreciation", "updated_at"])
            created += 1
            total += amount

    return {
        "period_date": start,
        "period_end": end,
        "period_label": label,
        "posted": created,
        "skipped_existing": skipped_existing,
        "skipped_ineligible": skipped_ineligible,
        "total": money(total),
    }


def maintenance_revert_status(asset: Asset) -> str:
    if asset.status in {"retired", "disposed"}:
        return asset.status
    return "in_use" if asset.assigned_to_id else "available"
