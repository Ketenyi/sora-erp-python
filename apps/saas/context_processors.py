"""Expose the GUI-managed platform configuration to every template."""

from apps.saas.services import platform_settings


def platform(request):
    return {"platform": platform_settings()}


def cloud_badges(request):
    if not request.path.startswith("/cloud/"):
        return {}
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not getattr(
        user, "is_platform_admin", False
    ):
        return {}
    from apps.saas.models import SalesLead

    return {"cloud_new_leads": SalesLead.objects.filter(status="new").count()}
