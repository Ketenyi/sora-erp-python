"""URL routes for Settings."""

from django.urls import path

from apps.settings_center import views

app_name = "settings_center"

urlpatterns = [
    path("", views.index, name="index"),
    path("company", views.company, name="company"),
    path("users", views.users, name="users"),
    path("users/create", views.user_create, name="user_create"),
    path("users/store", views.user_create, name="user_store"),
    path("users/edit/<int:id>", views.user_edit, name="user_edit"),
    path("users/update/<int:id>", views.user_edit, name="user_update"),
    path("users/delete/<int:id>", views.user_delete, name="user_delete"),
    path("roles", views.roles, name="roles"),
    path("roles/create", views.role_create, name="role_create"),
    path("roles/store", views.role_create, name="role_store"),
    path("roles/edit/<int:id>", views.role_edit, name="role_edit"),
    path("roles/update/<int:id>", views.role_edit, name="role_update"),
    path("roles/delete/<int:id>", views.role_delete, name="role_delete"),
    path("roles/assign", views.multi_roles, name="role_assign"),
    path("roles/store-assignment", views.multi_roles, name="role_store_assignment"),
    path("multi-roles", views.multi_roles, name="multi_roles"),
    path("multi-roles/update", views.multi_roles, name="multi_roles_update"),
    path("access", views.module_access, name="module_access"),
    path("access/create", views.module_access_form, name="module_access_create"),
    path(
        "access/edit/<int:id>",
        views.module_access_form,
        name="module_access_edit",
    ),
    path(
        "access/delete/<int:id>",
        views.module_access_delete,
        name="module_access_delete",
    ),
    path("email", views.email_settings, name="email"),
    path("email/update", views.email_settings, name="email_update"),
    path("email/test", views.email_settings, name="email_test"),
    path("email-identities", views.email_identities, name="email_identities"),
    path(
        "email-identities/create",
        views.email_identity_form,
        name="email_identity_create",
    ),
    path(
        "email-identities/edit/<int:id>",
        views.email_identity_form,
        name="email_identity_edit",
    ),
    path(
        "email-identities/delete/<int:id>",
        views.email_identity_delete,
        name="email_identity_delete",
    ),
    path(
        "email-identities/test/<int:id>",
        views.email_identity_test,
        name="email_identity_test",
    ),
    path("email-templates", views.email_templates, name="email_templates"),
    path(
        "email-templates/edit/<int:id>",
        views.email_template_edit,
        name="email_template_edit",
    ),
    path(
        "email-templates/update/<int:id>",
        views.email_template_edit,
        name="email_template_update",
    ),
    path("etims", views.etims, name="etims"),
    path("pos", views.pos_settings, name="pos"),
    path("agents", views.agent_settings, name="agents"),
    path("openai", views.openai_settings, name="openai"),
    path("sso", views.sso, name="sso"),
    path("zoho", views.zoho, name="zoho"),
    path("system", views.system, name="system"),
]
