from django.urls import path

from apps.crm import views

app_name = "crm"

urlpatterns = [
    path("dashboard", views.dashboard, name="dashboard"),
    path("clients", views.client_list, name="client_list"),
    path("clients/create", views.client_create, name="client_create"),
    path("clients/view/<int:pk>", views.client_view, name="client_view"),
    path("clients/edit/<int:pk>", views.client_edit, name="client_edit"),
    path("clients/delete/<int:pk>", views.client_delete, name="client_delete"),
    path("clients/export", views.client_export, name="client_export"),
    path("vendors", views.vendor_list, name="vendor_list"),
    path("vendors/create", views.vendor_create, name="vendor_create"),
    path("vendors/view/<int:pk>", views.vendor_view, name="vendor_view"),
    path("vendors/edit/<int:pk>", views.vendor_edit, name="vendor_edit"),
    path("vendors/delete/<int:pk>", views.vendor_delete, name="vendor_delete"),
    path("vendors/export", views.vendor_export, name="vendor_export"),
    path("reports", views.reports, name="reports"),
]
