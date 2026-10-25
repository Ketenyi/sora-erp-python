"""Settings module: company, users, roles, email and integrations."""

from __future__ import annotations

import os
import sys

from django import get_version
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage, get_connection
from django.db import connection
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.access_catalog import MODULE_CATALOG, MODULE_LABELS, submodule_label
from apps.core.mail import verify_identity
from apps.core.models import (
    AuditLog,
    Client,
    EMAIL_IDENTITY_MODULES,
    EmailIdentity,
    EmailTemplate,
    Employee,
    Invoice,
    ModuleAccessGrant,
    Permission,
    Project,
    Role,
    RolePermission,
    SsoSetting,
    User,
    UserRole,
)
from apps.core.permissions import admin_required, get_access
from apps.core.services import get_company_settings, set_company_setting
from apps.core.ui import paginate
from apps.core.uploads import save_tenant_upload
from apps.settings_center.forms import (
    AgentPolicyForm,
    CompanySettingsForm,
    EmailIdentityForm,
    EmailSettingsForm,
    EmailTemplateForm,
    EtimsForm,
    ModuleAccessGrantForm,
    OpenAiForm,
    PosPaymentMethodForm,
    PosSettingsForm,
    RoleForm,
    UserForm,
    ZohoForm,
)

LOGO_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}
SSO_PROVIDERS = ("google", "microsoft", "okta")


def _settings():
    return get_company_settings()


def _save_company_logo(upload) -> str | None:
    suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
    if suffix not in LOGO_TYPES:
        return None
    if upload.size > 2 * 1024 * 1024:
        return None
    path = save_tenant_upload("branding", upload)
    return "/uploads/" + path


@login_required
@admin_required
def index(request):
    templates_count = EmailTemplate.objects.filter(status="active").count()
    integration_keys = (
        "mail_enabled",
        "etims_enabled",
        "openai_enabled",
        "zoho_presence_enabled",
    )
    settings = _settings()
    enabled_integrations = sum(
        1
        for key in integration_keys
        if (settings.get(key) or "").lower() in {"1", "true", "yes", "on"}
    )
    stats = {
        "users": User.objects.filter(status="active").count(),
        "roles": Role.objects.count(),
        "permissions": Permission.objects.count(),
        "templates": templates_count,
        "integrations": enabled_integrations,
        "sso": SsoSetting.objects.filter(enabled=1).count(),
    }
    groups = [
        {
            "title": "General",
            "icon": "sliders-h",
            "cards": [
                ("Company Settings", "Identity, currency, tax and document defaults.", "/settings/company", "building", "brand"),
                ("User Management", "Create accounts, assign roles and control status.", "/settings/users", "users", "blue"),
                ("Roles & Permissions", "Define roles and module-level permissions.", "/settings/roles", "user-shield", "violet"),
                ("Multi-Role Manager", "Give users additional roles across modules.", "/settings/multi-roles", "user-tag", "amber"),
                ("Module Access", "Per-user module, submodule and project scopes.", "/settings/access", "key", "blue"),
            ],
        },
        {
            "title": "Communication",
            "icon": "envelope",
            "cards": [
                ("Email Settings", "SMTP server, sender identity and delivery test.", "/settings/email", "envelope-open-text", "green"),
                ("Sender Identities", "A no-reply address and mailbox per module.", "/settings/email-identities", "at", "violet"),
                ("Email Templates", "Customise transactional email subjects and bodies.", "/settings/email-templates", "file-lines", "blue"),
            ],
        },
        {
            "title": "Integrations",
            "icon": "plug",
            "cards": [
                ("Zoho Presence", "Sync mailbox presence with attendance.", "/settings/zoho", "cloud", "red"),
                ("eTIMS", "KRA electronic tax invoice configuration.", "/settings/etims", "receipt", "green"),
                ("POS & Tills", "Tender types, discounts, refunds and receipt policy.", "/settings/pos", "cash-register", "blue"),
                ("Endpoint Agents", "Discovery cadence, token lifetime and retention.", "/settings/agents", "desktop", "green"),
                ("AI Assistant", "OpenAI credentials, model and availability.", "/settings/openai", "robot", "violet"),
                ("Single Sign-On", "OAuth providers for external identity.", "/settings/sso", "id-card", "amber"),
            ],
        },
        {
            "title": "System",
            "icon": "server",
            "cards": [
                ("System Information", "Runtime, database and data volume.", "/settings/system", "database", "blue"),
                ("Admin Console", "System-wide activity and module overview.", "/admin/dashboard", "shield-halved", "brand"),
            ],
        },
    ]
    return render(
        request,
        "settings/index.html",
        {
            "page_title": "Settings - SORA ERP",
            "settings": settings,
            "stats": stats,
            "groups": groups,
        },
    )


@login_required
@admin_required
def company(request):
    settings = _settings()
    if request.method == "POST":
        form = CompanySettingsForm(request.POST, initial_settings=settings)
        if form.is_valid():
            form.save()
            upload = request.FILES.get("company_logo")
            if upload:
                logo_path = _save_company_logo(upload)
                if logo_path:
                    set_company_setting("company_logo", logo_path)
                    tenant = getattr(request, "tenant", None)
                    if tenant is not None:
                        tenant.logo_url = logo_path
                        tenant.save(update_fields=["logo_url", "updated_at"])
                else:
                    messages.warning(
                        request,
                        "Logo must be a PNG, JPG, WEBP or GIF file up to 2 MB.",
                    )
            messages.success(request, "Company settings saved.")
            return redirect("/settings/company")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = CompanySettingsForm(initial_settings=settings)
    return render(
        request,
        "settings/company.html",
        {
            "page_title": "Company Settings - SORA ERP",
            "form": form,
            "settings": settings,
            "current_url": "settings/company",
        },
    )


@login_required
@admin_required
def users(request):
    query = (request.GET.get("q") or "").strip()
    role_filter = request.GET.get("role") or ""
    status_filter = request.GET.get("status") or ""
    queryset = User.objects.all()
    if query:
        queryset = queryset.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    if role_filter:
        queryset = queryset.filter(role=role_filter)
    if status_filter:
        queryset = queryset.filter(status=status_filter)
    page_obj = paginate(request, queryset.order_by("first_name", "last_name", "id"), 15)
    role_names = dict(Role.objects.values_list("id", "display_name"))
    assignments: dict[int, list[str]] = {}
    for user_id, role_id in UserRole.objects.filter(
        user_id__in=[user.id for user in page_obj]
    ).values_list("user_id", "role_id"):
        assignments.setdefault(user_id, []).append(role_names.get(role_id, str(role_id)))
    for user in page_obj:
        user.assigned_roles = assignments.get(user.id, [])
        user.employee = (
            Employee.objects.filter(id=user.employee_id).first()
            if user.employee_id
            else None
        )
    return render(
        request,
        "settings/users.html",
        {
            "page_title": "User Management - SORA ERP",
            "page_obj": page_obj,
            "users": page_obj.object_list,
            "query": query,
            "role_filter": role_filter,
            "status_filter": status_filter,
            "role_choices": User.ROLE_CHOICES,
            "status_choices": User.STATUS_CHOICES,
        },
    )


def _user_form(request, user: User | None = None):
    if request.method == "POST":
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            saved = form.save()
            action = "updated" if user else "created"
            messages.success(request, f"User {saved.email} {action}.")
            return redirect("/settings/users")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = UserForm(instance=user)
    return render(
        request,
        "settings/user_form.html",
        {
            "page_title": ("Edit User" if user else "Create User") + " - SORA ERP",
            "form": form,
            "user_obj": user,
            "current_url": "settings/users",
        },
    )


@login_required
@admin_required
def user_create(request):
    return _user_form(request)


@login_required
@admin_required
def user_edit(request, id):
    return _user_form(request, get_object_or_404(User, pk=id))


@login_required
@admin_required
def user_delete(request, id):
    if request.method != "POST":
        return redirect("/settings/users")
    user = get_object_or_404(User, pk=id)
    if user.id == request.user.id:
        messages.error(request, "You cannot delete your own account.")
        return redirect("/settings/users")
    if user.role in {"admin", "super_admin"} and User.objects.filter(
        role__in=["admin", "super_admin"], status="active"
    ).count() <= 1:
        messages.error(request, "At least one active administrator must remain.")
        return redirect("/settings/users")
    UserRole.objects.filter(user_id=user.id).delete()
    email = user.email
    user.delete()
    messages.success(request, f"User {email} deleted.")
    return redirect("/settings/users")


def _permission_groups():
    groups: dict[str, list[Permission]] = {}
    for permission in Permission.objects.order_by("module", "action"):
        groups.setdefault(permission.module, []).append(permission)
    return [
        {"module": module.title(), "key": module, "permissions": perms}
        for module, perms in groups.items()
    ]


@login_required
@admin_required
def roles(request):
    rows = list(Role.objects.order_by("name"))
    assigned_counts = dict(
        UserRole.objects.values("role_id").annotate(total=Count("id")).values_list(
            "role_id", "total"
        )
    )
    permission_counts = dict(
        RolePermission.objects.values("role_id")
        .annotate(total=Count("id"))
        .values_list("role_id", "total")
    )
    for role in rows:
        role.user_count = assigned_counts.get(role.id, 0)
        role.permission_count = permission_counts.get(role.id, 0)
    return render(
        request,
        "settings/roles.html",
        {
            "page_title": "Roles & Permissions - SORA ERP",
            "roles": rows,
            "permission_groups": _permission_groups(),
            "current_url": "settings/roles",
        },
    )


def _role_form(request, role: Role | None = None):
    if request.method == "POST":
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            saved = form.save()
            messages.success(request, f"Role {saved.display_name} saved.")
            return redirect("/settings/roles")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = RoleForm(instance=role)
    selected = set(form["permissions"].value() or []) if form.is_bound else set(
        form.fields["permissions"].initial or []
    )
    try:
        selected = {int(value) for value in selected}
    except (TypeError, ValueError):
        selected = set()
    return render(
        request,
        "settings/role_form.html",
        {
            "page_title": ("Edit Role" if role else "Create Role") + " - SORA ERP",
            "form": form,
            "role": role,
            "permission_groups": _permission_groups(),
            "selected_permissions": selected,
            "current_url": "settings/roles",
        },
    )


@login_required
@admin_required
def role_create(request):
    return _role_form(request)


@login_required
@admin_required
def role_edit(request, id):
    return _role_form(request, get_object_or_404(Role, pk=id))


@login_required
@admin_required
def role_delete(request, id):
    if request.method != "POST":
        return redirect("/settings/roles")
    role = get_object_or_404(Role, pk=id)
    if UserRole.objects.filter(role_id=role.id).exists():
        messages.error(request, "This role is assigned to users; unassign it first.")
        return redirect("/settings/roles")
    RolePermission.objects.filter(role_id=role.id).delete()
    name = role.display_name
    role.delete()
    messages.success(request, f"Role {name} deleted.")
    return redirect("/settings/roles")


@login_required
@admin_required
def multi_roles(request):
    if request.method == "POST":
        user = get_object_or_404(User, pk=request.POST.get("user_id"))
        primary_role = request.POST.get("primary_role") or ""
        valid_roles = {choice[0] for choice in User.ROLE_CHOICES}
        if primary_role not in valid_roles:
            messages.error(request, "Select a valid primary role.")
            return redirect("/settings/multi-roles")
        user.role = primary_role
        user.save(update_fields=["role", "updated_at"])
        role_ids = request.POST.getlist("additional_roles")
        valid_ids = set(
            Role.objects.filter(id__in=role_ids).values_list("id", flat=True)
        )
        UserRole.objects.filter(user_id=user.id).delete()
        for role_id in valid_ids:
            UserRole.objects.get_or_create(user_id=user.id, role_id=role_id)
        messages.success(request, f"Roles updated for {user.get_full_name()}.")
        return redirect("/settings/multi-roles")

    rows = []
    role_choices = list(Role.objects.order_by("display_name"))
    users_qs = User.objects.order_by("first_name", "last_name", "id")
    for user in users_qs:
        rows.append(
            {
                "user": user,
                "selected_role_ids": set(
                    UserRole.objects.filter(user_id=user.id).values_list(
                        "role_id", flat=True
                    )
                ),
                "access": get_access(user),
            }
        )
    return render(
        request,
        "settings/multi_roles.html",
        {
            "page_title": "Multi-Role Manager - SORA ERP",
            "rows": rows,
            "roles": role_choices,
            "primary_roles": User.ROLE_CHOICES,
            "current_url": "settings/multi-roles",
        },
    )


def _submodule_map() -> dict:
    return {
        key: [
            {"value": code, "label": label}
            for code, label in row["submodules"]
        ]
        for key, row in MODULE_CATALOG.items()
    }


@login_required
@admin_required
def module_access(request):
    grants = list(
        ModuleAccessGrant.objects.select_related("user", "project").order_by(
            "user__first_name", "user__last_name", "module", "submodule"
        )
    )
    user_filter = request.GET.get("user") or ""
    module_filter = request.GET.get("module") or ""
    if user_filter.isdigit():
        grants = [row for row in grants if row.user_id == int(user_filter)]
    if module_filter:
        grants = [row for row in grants if row.module == module_filter]
    for grant in grants:
        grant.module_label = MODULE_LABELS.get(grant.module, grant.module.title())
        grant.submodule_label = (
            submodule_label(grant.module, grant.submodule)
            if grant.submodule
            else "Entire module"
        )
    grant_user_ids = {row.user_id for row in grants}
    users = User.objects.filter(status="active").order_by(
        "first_name", "last_name", "email"
    )
    return render(
        request,
        "settings/module_access.html",
        {
            "page_title": "Module Access - SORA ERP",
            "grants": grants,
            "users": users,
            "modules": MODULE_CATALOG,
            "user_filter": user_filter,
            "module_filter": module_filter,
            "stats": {
                "total": len(grants),
                "users": len(grant_user_ids),
                "modules": len({row.module for row in grants}),
                "project": sum(1 for row in grants if row.scope != "tenant"),
            },
            "current_url": "settings/access",
        },
    )


@login_required
@admin_required
def module_access_form(request, id=None):
    grant = get_object_or_404(ModuleAccessGrant, pk=id) if id else None
    if request.method == "POST":
        form = ModuleAccessGrantForm(
            request.POST, instance=grant, granted_by=request.user
        )
        if form.is_valid():
            saved = form.save()
            messages.success(
                request,
                f"Module access for {saved.user.get_full_name() or saved.user.email} saved.",
            )
            return redirect("/settings/access")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = ModuleAccessGrantForm(instance=grant)
    return render(
        request,
        "settings/module_access_form.html",
        {
            "page_title": (
                "Edit Module Access - SORA ERP"
                if grant
                else "New Module Access - SORA ERP"
            ),
            "form": form,
            "grant": grant,
            "submodule_map": _submodule_map(),
            "current_url": "settings/access",
        },
    )


@login_required
@admin_required
@require_POST
def module_access_delete(request, id):
    grant = get_object_or_404(ModuleAccessGrant, pk=id)
    label = f"{grant.user.email} · {MODULE_LABELS.get(grant.module, grant.module)}"
    grant.delete()
    messages.success(request, f"Access grant removed for {label}.")
    return redirect("/settings/access")


def _send_test_email(config: dict, recipient: str):
    connection_obj = get_connection(
        host=config["host"],
        port=config["port"],
        username=config["username"],
        password=config["password"],
        use_tls=config["use_tls"],
        use_ssl=config["use_ssl"],
        timeout=15,
    )
    message = EmailMessage(
        subject="Test email from SORA ERP",
        body=(
            "This is a test message from SORA ERP.\n\n"
            f"Sent at {timezone.localtime():%Y-%m-%d %H:%M:%S}."
        ),
        from_email=f"{config['from_name']} <{config['from_address']}>",
        to=[recipient],
        connection=connection_obj,
    )
    message.send(fail_silently=False)


@login_required
@admin_required
def email_settings(request):
    settings = _settings()
    if request.method == "POST":
        form = EmailSettingsForm(request.POST, initial_settings=settings)
        if form.is_valid():
            form.save()
            if request.POST.get("action") == "test":
                recipient = (
                    request.POST.get("test_email_address") or request.user.email
                ).strip()
                if not form.cleaned_data.get("mail_enabled"):
                    messages.error(request, "Enable email before sending a test message.")
                elif not form.cleaned_data.get("mail_host"):
                    messages.error(request, "Set an SMTP host before sending a test message.")
                else:
                    try:
                        _send_test_email(form.smtp_config(), recipient)
                        messages.success(request, f"Test email sent to {recipient}.")
                    except Exception as exc:  # pragma: no cover - external SMTP
                        messages.error(request, f"SMTP test failed: {exc}")
            else:
                messages.success(request, "Email settings saved.")
            return redirect("/settings/email")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = EmailSettingsForm(initial_settings=settings)
    return render(
        request,
        "settings/email.html",
        {
            "page_title": "Email Settings - SORA ERP",
            "form": form,
            "settings": settings,
            "identity_count": EmailIdentity.objects.count(),
            "current_url": "settings/email",
        },
    )


@login_required
@admin_required
def email_identities(request):
    identities = list(
        EmailIdentity.objects.order_by("module", "-is_default", "name")
    )
    labels = dict(EMAIL_IDENTITY_MODULES)
    for identity in identities:
        identity.module_label = labels.get(identity.module, identity.module.title())
    return render(
        request,
        "settings/email_identities.html",
        {
            "page_title": "Sender Identities - SORA ERP",
            "identities": identities,
            "stats": {
                "total": len(identities),
                "active": sum(1 for row in identities if row.is_active),
                "outbound": sum(1 for row in identities if row.can_send),
                "inbound": sum(1 for row in identities if row.imap_host),
                "modules": len({row.module for row in identities}),
            },
            "module_count": len(EMAIL_IDENTITY_MODULES),
            "current_url": "settings/email-identities",
        },
    )


@login_required
@admin_required
def email_identity_form(request, id=None):
    identity = get_object_or_404(EmailIdentity, pk=id) if id else None
    if request.method == "POST":
        form = EmailIdentityForm(request.POST, instance=identity)
        if form.is_valid():
            saved = form.save()
            messages.success(request, f"Sender identity {saved.name} saved.")
            return redirect("/settings/email-identities")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = EmailIdentityForm(instance=identity)
    return render(
        request,
        "settings/email_identity_form.html",
        {
            "page_title": (
                "Edit Sender Identity - SORA ERP"
                if identity
                else "New Sender Identity - SORA ERP"
            ),
            "form": form,
            "identity": identity,
            "current_url": "settings/email-identities",
        },
    )


@login_required
@admin_required
@require_POST
def email_identity_delete(request, id):
    identity = get_object_or_404(EmailIdentity, pk=id)
    name = identity.name
    identity.delete()
    messages.success(request, f"Sender identity {name} deleted.")
    return redirect("/settings/email-identities")


@login_required
@admin_required
@require_POST
def email_identity_test(request, id):
    identity = get_object_or_404(EmailIdentity, pk=id)
    recipient = (request.POST.get("recipient") or request.user.email or "").strip()
    ok, detail = verify_identity(identity, recipient or None)
    if ok:
        messages.success(request, detail)
    else:
        messages.error(request, f"Verification failed. {detail}")
    target = request.POST.get("next") or ""
    if not target.startswith("/settings/email-identities"):
        target = "/settings/email-identities"
    return redirect(target)


@login_required
@admin_required
def email_templates(request):
    templates = EmailTemplate.objects.order_by("name")
    return render(
        request,
        "settings/email_templates.html",
        {
            "page_title": "Email Templates - SORA ERP",
            "templates": templates,
            "current_url": "settings/email-templates",
        },
    )


@login_required
@admin_required
def email_template_edit(request, id):
    template = get_object_or_404(EmailTemplate, pk=id)
    if request.method == "POST":
        form = EmailTemplateForm(request.POST, instance=template)
        if form.is_valid():
            form.save()
            messages.success(request, f"Template {template.name} updated.")
            return redirect("/settings/email-templates")
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = EmailTemplateForm(instance=template)
    return render(
        request,
        "settings/email_template_form.html",
        {
            "page_title": "Edit Email Template - SORA ERP",
            "form": form,
            "template": template,
            "current_url": "settings/email-templates",
        },
    )


def _integration_view(
    request,
    form_class,
    title: str,
    template: str,
    test_check,
    success_message: str,
):
    settings = _settings()
    if request.method == "POST":
        form = form_class(request.POST, initial_settings=settings)
        if form.is_valid():
            form.save()
            if request.POST.get("action") == "test":
                ok, message = test_check(form.cleaned_data)
                (messages.success if ok else messages.error)(request, message)
            else:
                messages.success(request, success_message)
            return redirect(request.path)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = form_class(initial_settings=settings)
    return render(
        request,
        template,
        {
            "page_title": f"{title} - SORA ERP",
            "form": form,
            "settings": settings,
            "current_url": request.path.strip("/"),
        },
    )


@login_required
@admin_required
def etims(request):
    from apps.core.etims import (
        get_config,
        reset_token_cache,
        retry_submission,
        submit_submission,
        test_connection,
    )
    from apps.core.models import EtimsSubmission

    settings = _settings()
    if request.method == "POST" and request.POST.get("action") == "retry":
        submission = get_object_or_404(
            EtimsSubmission, pk=request.POST.get("submission_id")
        )
        if submission.status == "submitted":
            messages.info(request, "That receipt is already accepted by KRA.")
            return redirect("/settings/etims")
        retry_submission(submission)
        status = submit_submission(submission.id)
        submission.refresh_from_db()
        if status == "submitted":
            messages.success(
                request,
                f"{submission.document_number} submitted. KRA receipt "
                f"{submission.kra_receipt_number or 'issued'}.",
            )
        elif status == "pending":
            messages.warning(
                request,
                f"{submission.document_number} is queued until the eTIMS credentials are complete.",
            )
        else:
            messages.error(
                request,
                f"{submission.document_number} could not be sent to KRA. "
                "Review the error in the transmission queue.",
            )
        return redirect("/settings/etims")

    if request.method == "POST":
        form = EtimsForm(request.POST, initial_settings=settings)
        if form.is_valid():
            form.save()
            if request.POST.get("action") == "test":
                reset_token_cache()
                ok, message = test_connection()
                (messages.success if ok else messages.error)(request, message)
            else:
                messages.success(request, "eTIMS settings saved.")
            return redirect(request.path)
        messages.error(request, "Please correct the highlighted fields.")
    else:
        form = EtimsForm(initial_settings=settings)

    config = get_config()
    submissions = list(
        EtimsSubmission.objects.select_related("pos_sale").order_by("-created_at", "-id")[:12]
    )
    stats = {
        "pending": EtimsSubmission.objects.filter(status="pending").count(),
        "processing": EtimsSubmission.objects.filter(status="processing").count(),
        "submitted": EtimsSubmission.objects.filter(status="submitted").count(),
        "failed": EtimsSubmission.objects.filter(status="failed").count(),
        "rejected": EtimsSubmission.objects.filter(status="rejected").count(),
        "cancelled": EtimsSubmission.objects.filter(status="cancelled").count(),
    }
    return render(
        request,
        "settings/etims.html",
        {
            "page_title": "eTIMS Settings - SORA ERP",
            "form": form,
            "settings": settings,
            "current_url": "settings/etims",
            "config": config,
            "submissions": submissions,
            "stats": stats,
            "device_ready": bool(config.cmc_key),
        },
    )


@login_required
@admin_required
def pos_settings(request):
    from apps.pos.models import PosPaymentMethod
    from apps.pos.services import sync_payment_methods

    sync_payment_methods()
    settings = _settings()
    policy_form = PosSettingsForm(initial_settings=settings)
    method_form = PosPaymentMethodForm(
        is_new=True, initial={"enabled": True, "sort_order": 10}
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "policy":
            policy_form = PosSettingsForm(request.POST, initial_settings=settings)
            if policy_form.is_valid():
                policy_form.save()
                messages.success(request, "POS policy saved.")
                return redirect("/settings/pos")
            messages.error(request, "Please correct the POS policy fields.")
        elif action == "method":
            instance = None
            method_id = request.POST.get("method_id")
            if method_id:
                instance = get_object_or_404(PosPaymentMethod, pk=method_id)
            method_form = PosPaymentMethodForm(
                request.POST, instance=instance, is_new=instance is None
            )
            if method_form.is_valid():
                method = method_form.save(commit=False)
                if (
                    method.pk
                    and not method.enabled
                    and not PosPaymentMethod.objects.filter(enabled=True)
                    .exclude(pk=method.pk)
                    .exists()
                ):
                    messages.error(request, "Keep at least one tender enabled.")
                else:
                    method.save()
                    messages.success(request, f"Tender {method.label} saved.")
                    return redirect("/settings/pos")
            else:
                messages.error(request, "Please correct the tender details.")
        elif action == "seed":
            created = 0
            from apps.pos.models import DEFAULT_PAYMENT_METHODS

            for spec in DEFAULT_PAYMENT_METHODS:
                _, was_created = PosPaymentMethod.objects.get_or_create(
                    code=spec["code"], defaults=spec
                )
                created += int(was_created)
            messages.success(
                request,
                f"{created} default tender(s) restored."
                if created
                else "All default tenders are already present.",
            )
            return redirect("/settings/pos")

    methods = list(PosPaymentMethod.objects.order_by("sort_order", "id"))
    return render(
        request,
        "settings/pos.html",
        {
            "page_title": "POS Settings - SORA ERP",
            "policy_form": policy_form,
            "method_form": method_form,
            "methods": methods,
            "current_url": "settings/pos",
        },
    )


@login_required
@admin_required
def agent_settings(request):
    from apps.assets import agent_services
    from apps.assets.models import AssetAgent, AssetSoftware

    settings_values = _settings()
    form = AgentPolicyForm(initial_settings=settings_values)
    if request.method == "POST":
        form = AgentPolicyForm(request.POST, initial_settings=settings_values)
        if form.is_valid():
            form.save()
            messages.success(request, "Endpoint agent policy saved.")
            return redirect("/settings/agents")
        messages.error(request, "Please correct the agent policy fields.")

    return render(
        request,
        "settings/agents.html",
        {
            "page_title": "Endpoint Agent Settings - SORA ERP",
            "form": form,
            "policy": agent_services.agent_policy(),
            "server_url": request.build_absolute_uri("/").rstrip("/"),
            "agent_count": AssetAgent.objects.count(),
            "software_count": AssetSoftware.objects.values("name").distinct().count(),
            "current_url": "settings/agents",
        },
    )


@login_required
@admin_required
def openai_settings(request):
    def test(values):
        if not values.get("openai_enabled"):
            return False, "Enable the AI assistant before testing."
        key = values.get("openai_api_key") or ""
        if not key:
            return False, "Add an OpenAI API key."
        return True, "Credentials are present. External API calls require network access."

    return _integration_view(
        request,
        OpenAiForm,
        "AI Assistant Settings",
        "settings/openai.html",
        test,
        "AI assistant settings saved.",
    )


@login_required
@admin_required
def zoho(request):
    def test(values):
        if not values.get("zoho_presence_enabled"):
            return False, "Enable Zoho presence before testing."
        if not values.get("zoho_client_id") or not values.get("zoho_client_secret"):
            return False, "Client ID and client secret are required."
        return True, "Zoho credentials are present. Full presence sync requires a refresh token."

    return _integration_view(
        request,
        ZohoForm,
        "Zoho Presence Settings",
        "settings/zoho.html",
        test,
        "Zoho settings saved.",
    )


@login_required
@admin_required
def sso(request):
    providers = list(SsoSetting.objects.order_by("provider"))
    if request.method == "POST":
        if request.POST.get("action") == "add":
            provider = (request.POST.get("new_provider") or "").strip().lower()
            if not provider.replace("-", "").replace("_", "").isalnum():
                messages.error(request, "Use a simple provider name.")
            elif SsoSetting.objects.filter(provider=provider).exists():
                messages.error(request, "That provider already exists.")
            else:
                SsoSetting.objects.create(
                    provider=provider,
                    enabled=0,
                    redirect_uri=f"/auth/{provider}/callback",
                )
                messages.success(request, f"Provider {provider} added.")
            return redirect("/settings/sso")

        for provider in providers:
            name = provider.provider
            provider.enabled = 1 if request.POST.get(f"enabled_{name}") else 0
            provider.client_id = request.POST.get(f"client_id_{name}") or ""
            secret = (request.POST.get(f"client_secret_{name}") or "").strip()
            if secret:
                provider.client_secret = secret
            provider.redirect_uri = request.POST.get(f"redirect_uri_{name}") or ""
            provider.save()
        if request.POST.get("action") == "test":
            ready = [
                provider.provider
                for provider in providers
                if provider.enabled and provider.client_id and provider.client_secret
            ]
            if ready:
                messages.success(
                    request,
                    "Configured provider(s): " + ", ".join(ready) + ". OAuth sign-in is not enabled in this build.",
                )
            else:
                messages.error(request, "No enabled provider has a client ID and secret.")
        else:
            messages.success(request, "SSO settings saved.")
        return redirect("/settings/sso")

    return render(
        request,
        "settings/sso.html",
        {
            "page_title": "Single Sign-On - SORA ERP",
            "providers": providers,
            "current_url": "settings/sso",
        },
    )


def _database_info():
    info = {
        "vendor": connection.vendor,
        "name": str(connection.settings_dict.get("NAME") or ""),
        "version": "",
        "tables": 0,
        "size": "",
    }
    try:
        info["tables"] = len(connection.introspection.table_names())
        with connection.cursor() as cursor:
            if connection.vendor == "postgresql":
                cursor.execute("SELECT version()")
                info["version"] = cursor.fetchone()[0].split(",")[0]
                cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                info["size"] = cursor.fetchone()[0]
            elif connection.vendor == "sqlite":
                cursor.execute("SELECT sqlite_version()")
                info["version"] = "SQLite " + cursor.fetchone()[0]
    except Exception:
        pass
    return info


@login_required
@admin_required
def system(request):
    permissions = get_access(request.user)
    module_rows = [
        ("Human Resources", "/hr/dashboard", permissions.has_module("hr")),
        ("Sales", "/sales/dashboard", permissions.has_module("sales")),
        ("CRM", "/crm/dashboard", permissions.has_module("crm")),
        ("Invoices", "/invoices/dashboard", permissions.has_module("accounts") or permissions.has_module("sales")),
        ("Accounts", "/accounts/dashboard", permissions.has_module("accounts")),
        ("Inventory", "/inventory/dashboard", permissions.has_module("inventory")),
        ("Purchasing", "/purchasing/dashboard", permissions.has_module("purchasing")),
        ("Settings", "/settings", permissions.has_module("settings")),
    ]
    counts = {
        "users": User.objects.count(),
        "active_users": User.objects.filter(status="active").count(),
        "roles": Role.objects.count(),
        "permissions": Permission.objects.count(),
        "clients": Client.objects.filter(deleted_at__isnull=True).count(),
        "invoices": Invoice.objects.count(),
        "employees": Employee.objects.filter(status="active").count(),
        "audit_entries": AuditLog.objects.count(),
    }
    recent_audit = list(AuditLog.objects.order_by("-created_at")[:10])
    user_names = dict(User.objects.values_list("id", "email"))
    for row in recent_audit:
        row.user_email = user_names.get(row.user_id, f"User #{row.user_id}")
    return render(
        request,
        "settings/system.html",
        {
            "page_title": "System Information - SORA ERP",
            "database": _database_info(),
            "counts": counts,
            "modules": module_rows,
            "recent_audit": recent_audit,
            "versions": {
                "django": get_version(),
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "app": _settings().get("app_version", "1.0.0"),
            },
            "debug": django_settings.DEBUG,
            "timezone": os.environ.get("TIME_ZONE", "Africa/Nairobi"),
            "now": timezone.localtime(),
            "current_url": "settings/system",
        },
    )
