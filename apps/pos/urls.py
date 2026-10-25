from django.urls import path

from apps.pos import views

urlpatterns = [
    path("", views.terminal),
    path("dashboard", views.dashboard),
    path("checkout", views.checkout),
    path("sales", views.sales),
    path("sales/<int:sale_id>", views.receipt),
    path("sales/<int:sale_id>/receipt", views.receipt),
    path("sales/<int:sale_id>/void", views.sale_void),
    path("sales/<int:sale_id>/refund", views.sale_refund),
    path("refunds/<int:refund_id>", views.refund_receipt),
    path("sessions", views.session_list),
    path("sessions/open", views.session_open),
    path("sessions/<int:session_id>", views.session_detail),
    path("sessions/<int:session_id>/close", views.session_close),
]
