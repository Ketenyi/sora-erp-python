from django.urls import path

from apps.invoices import views

app_name = "invoices"

urlpatterns = [
    path("dashboard", views.dashboard, name="dashboard"),
    path("list", views.invoice_list, name="list"),
    path("create", views.invoice_create, name="create"),
    path("store", views.invoice_create, name="store"),
    path("view/<int:pk>", views.invoice_view, name="view"),
    path("edit/<int:pk>", views.invoice_edit, name="edit"),
    path("update/<int:pk>", views.invoice_edit, name="update"),
    path("pay/<int:pk>", views.invoice_pay, name="pay"),
    path("receivables", views.receivables, name="receivables"),
    path("reports", views.reports, name="reports"),
    path("export", views.invoice_export, name="export"),
]
