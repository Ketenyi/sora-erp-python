from django.urls import path

from apps.core import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login", views.login_view, name="login"),
    path("login/verify", views.login_verify, name="login_verify"),
    path("logout", views.logout_view, name="logout"),
    path("password-reset", views.password_reset_request, name="password_reset"),
    path(
        "password-reset/confirm/<uidb64>/<token>",
        views.password_reset_confirm,
        name="password_reset_confirm",
    ),
    path("security/", views.security_center, name="security_center"),
    path("security/mfa", views.security_mfa, name="security_mfa"),
    path("security/password", views.security_password, name="security_password"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("dashboard", views.dashboard, name="dashboard"),
    path("api/presence/status", views.presence_status, name="presence_status"),
    path("api/presence/update", views.presence_update, name="presence_update"),
    path("api/attendance/clock-in", views.attendance_clock_in, name="clock_in"),
    path("api/attendance/clock-out", views.attendance_clock_out, name="clock_out"),
    path("health", views.health, name="health"),
]

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
