from django.urls import path

from apps.sales import views

app_name = "sales"

urlpatterns = [
    path("dashboard", views.dashboard, name="dashboard"),
    path("quotes", views.quote_list, name="quote_list"),
    path("quotes/create", views.quote_create, name="quote_create"),
    path("quotes/view/<int:pk>", views.quote_view, name="quote_view"),
    path("quotes/edit/<int:pk>", views.quote_edit, name="quote_edit"),
    path("quotes/approve/<int:pk>", views.quote_approve, name="quote_approve"),
    path("quotes/convert/<int:pk>", views.quote_convert, name="quote_convert"),
    path("quotes/delete/<int:pk>", views.quote_delete, name="quote_delete"),
    path("pipeline", views.pipeline, name="pipeline"),
    path("reports", views.reports, name="reports"),
    path("reports/sales", views.report_sales, name="report_sales"),
    path("reports/quotes", views.report_quotes, name="report_quotes"),
    path("reports/export", views.report_export, name="report_export"),
]
