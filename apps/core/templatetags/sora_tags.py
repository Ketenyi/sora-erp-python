"""Template helpers shared by all modules."""

from django import template

register = template.Library()

STATUS_CLASSES = {
    "active": "success",
    "approved": "success",
    "paid": "success",
    "completed": "success",
    "present": "success",
    "confirmed": "success",
    "reconciled": "success",
    "draft": "secondary",
    "inactive": "secondary",
    "cancelled": "secondary",
    "void": "secondary",
    "sent": "info",
    "posted": "info",
    "in_progress": "info",
    "processing": "info",
    "partial": "warning",
    "partially_received": "warning",
    "partially_paid": "warning",
    "received": "info",
    "fully_received": "success",
    "pending": "warning",
    "probation": "warning",
    "late": "warning",
    "overdue": "danger",
    "rejected": "danger",
    "failed": "danger",
    "terminated": "danger",
    "absent": "danger",
    "expired": "warning",
    "converted": "primary",
    "open": "info",
    "closed": "secondary",
    "locked": "secondary",
    "bidding": "info",
    "in_execution": "primary",
    "support_mode": "primary",
    "backlog": "secondary",
    "blocked": "danger",
    "in_qa": "warning",
    "complete": "success",
    "waiting_client": "warning",
    "resolved": "success",
    "scheduled": "info",
    "skipped": "secondary",
    "pending_approval": "warning",
    "on_hold": "warning",
    "expired": "warning",
    "critical": "danger",
    "high": "warning",
    "medium": "info",
    "low": "secondary",
    "available": "success",
    "in_use": "info",
    "under_maintenance": "warning",
    "retired": "secondary",
    "disposed": "danger",
    "new": "success",
    "good": "success",
    "fair": "info",
    "poor": "warning",
    "damaged": "danger",
    "preventive": "info",
    "corrective": "warning",
    "inspection": "info",
    "calibration": "info",
    "online": "success",
    "offline": "secondary",
    "stale": "warning",
    "enrolled": "info",
    "revoked": "danger",
    "used": "secondary",
}


@register.filter
def status_class(value):
    return STATUS_CLASSES.get(str(value or "").lower(), "secondary")


@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    request = context.get("request")
    if request is None:
        return ""
    query = request.GET.copy()
    for key, value in kwargs.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return query.urlencode()


@register.filter
def percentage(value, total):
    try:
        total = float(total)
        if not total:
            return 0
        return round(float(value) / total * 100, 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


@register.filter
def field_class(field, css):
    return field.as_widget(attrs={"class": css})


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def humanize_label(value):
    if not value:
        return "—"
    return str(value).replace("_", " ").title()
