from django.urls import path

from apps.assets import api

urlpatterns = [
    path("enroll", api.enroll, name="asset_agent_enroll"),
    path("register", api.register, name="asset_agent_register"),
    path("refresh", api.refresh, name="asset_agent_refresh"),
    path("inventory", api.inventory, name="asset_agent_inventory"),
    path("heartbeat", api.heartbeat, name="asset_agent_heartbeat"),
    path("package", api.package, name="asset_agent_package"),
    path("bootstrap", api.bootstrap, name="asset_agent_bootstrap"),
]
