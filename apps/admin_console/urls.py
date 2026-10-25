"""URL routes for the administration console."""

from django.urls import path

from apps.admin_console import views

app_name = "admin_console"

urlpatterns = [
    path("", views.dashboard, name="index"),
    path("dashboard", views.dashboard, name="dashboard"),
    path("users", views.users, name="users"),
    path("roles", views.roles, name="roles"),
    path("settings", views.settings_redirect, name="settings"),
    path("system", views.system_redirect, name="system"),
    path("reports", views.reports, name="reports"),
]
