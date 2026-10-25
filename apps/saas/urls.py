from django.urls import path

from apps.saas import views

urlpatterns = [
    # Public portal
    path("pricing", views.pricing, name="pricing"),
    path("contact", views.contact, name="contact"),
    path("signup", views.signup, name="signup"),
    # Licence gateway
    path("api/licence/manifest", views.licence_manifest_view, name="licence_manifest"),
    # Tenant billing
    path("billing/", views.billing, name="billing"),
    path("billing/domains/add/", views.billing_domain_add, name="billing_domain_add"),
    path(
        "billing/domains/<int:domain_id>/verify/",
        views.billing_domain_verify,
        name="billing_domain_verify",
    ),
    path(
        "billing/domains/<int:domain_id>/primary/",
        views.billing_domain_primary,
        name="billing_domain_primary",
    ),
    path(
        "billing/domains/<int:domain_id>/remove/",
        views.billing_domain_remove,
        name="billing_domain_remove",
    ),
    path("billing/receipts/<int:pk>/", views.billing_receipt, name="billing_receipt"),
    path(
        "billing/gateway/stripe/start/<int:pk>/",
        views.billing_stripe_start,
        name="billing_stripe_start",
    ),
    path("billing/gateway/stripe/return/", views.billing_stripe_return, name="billing_stripe_return"),
    path(
        "billing/gateway/mpesa/status/<int:pk>/",
        views.billing_mpesa_status,
        name="billing_mpesa_status",
    ),
    path("billing/gateway/stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
    path("billing/gateway/mpesa/callback/", views.mpesa_callback, name="mpesa_callback"),
    path("announcements/<int:pk>/dismiss/", views.dismiss_announcement, name="dismiss_announcement"),
    # SORA Cloud Control
    path("cloud/", views.cloud_dashboard, name="cloud_dashboard"),
    path("cloud/leads/", views.cloud_leads, name="cloud_leads"),
    path("cloud/leads/new/", views.cloud_lead_create, name="cloud_lead_create"),
    path("cloud/leads/<int:lead_id>/", views.cloud_lead_detail, name="cloud_lead_detail"),
    path("cloud/leads/<int:lead_id>/update/", views.cloud_lead_update, name="cloud_lead_update"),
    path("cloud/leads/<int:lead_id>/note/", views.cloud_lead_note, name="cloud_lead_note"),
    path("cloud/leads/<int:lead_id>/convert/", views.cloud_lead_convert, name="cloud_lead_convert"),
    path("cloud/tenants/", views.cloud_tenants, name="cloud_tenants"),
    path("cloud/tenants/new/", views.cloud_tenant_create, name="cloud_tenant_create"),
    path("cloud/tenants/<int:tenant_id>/", views.cloud_tenant_detail, name="cloud_tenant_detail"),
    path("cloud/tenants/<int:tenant_id>/edit/", views.cloud_tenant_edit, name="cloud_tenant_edit"),
    path(
        "cloud/tenants/<int:tenant_id>/modules/<int:module_id>/toggle/",
        views.cloud_toggle_module,
        name="cloud_toggle_module",
    ),
    path(
        "cloud/tenants/<int:tenant_id>/trial/extend/",
        views.cloud_extend_trial,
        name="cloud_extend_trial",
    ),
    path("cloud/tenants/<int:tenant_id>/status/", views.cloud_set_status, name="cloud_set_status"),
    path(
        "cloud/tenants/<int:tenant_id>/domains/<int:domain_id>/revoke/",
        views.cloud_tenant_domain_revoke,
        name="cloud_tenant_domain_revoke",
    ),
    path(
        "cloud/tenants/<int:tenant_id>/payments/record/",
        views.cloud_record_payment,
        name="cloud_record_payment",
    ),
    path(
        "cloud/tenants/<int:tenant_id>/impersonate/",
        views.cloud_impersonate,
        name="cloud_impersonate",
    ),
    path("cloud/payments/<int:payment_id>/confirm/", views.cloud_confirm_payment, name="cloud_confirm_payment"),
    path("cloud/billing/", views.cloud_billing, name="cloud_billing"),
    path("cloud/announcements/", views.cloud_announcements, name="cloud_announcements"),
    path(
        "cloud/announcements/<int:pk>/toggle/",
        views.cloud_announcement_toggle,
        name="cloud_announcement_toggle",
    ),
    path("cloud/plans/", views.cloud_plans, name="cloud_plans"),
    path("cloud/plans/<int:plan_id>/edit/", views.cloud_plan_edit, name="cloud_plan_edit"),
    path("cloud/settings/<str:group>/", views.cloud_settings, name="cloud_settings"),
    path("cloud/return/", views.cloud_return, name="cloud_return"),
]
