from django.urls import include, path

from apps.assets import views as asset_views
from apps.core import views as core_views

urlpatterns = [
    path("uploads/<path:path>", core_views.uploaded_file, name="uploaded_file"),
    path("api/assets/agent/", include("apps.assets.api_urls")),
    path("onboard-device", asset_views.onboard_device, name="onboard_device"),
    path("", include("apps.saas.urls")),
    path("", include("apps.core.urls")),
    path("sales/", include("apps.sales.urls")),
    path("crm/", include("apps.crm.urls")),
    path("invoices/", include("apps.invoices.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
    path("hr/", include("apps.hr.urls")),
    path("settings/", include("apps.settings_center.urls")),
    path("admin/", include("apps.admin_console.urls")),
    path("projects/", include("apps.project_management.urls")),
    path("helpdesk/", include("apps.helpdesk.urls")),
    path("assets/", include("apps.assets.urls")),
    path("pos/", include("apps.pos.urls")),
]
