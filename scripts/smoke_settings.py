"""Smoke test the Settings module and Admin console on the configured database."""

from __future__ import annotations

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client as TestClient  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.core.crypto import decrypt_secret  # noqa: E402
from apps.core.mail import module_mail_config  # noqa: E402
from apps.core.models import (  # noqa: E402
    CompanySetting,
    EmailIdentity,
    EmailTemplate,
    Role,
    SsoSetting,
    User,
)
from apps.core.tenancy import default_tenant, set_current_tenant  # noqa: E402

PASSWORD = "admin123"
EMAIL = "admin@sora.com"

checks = 0
failures: list[str] = []


def check(label: str, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print(f"  ok   {label}")
    else:
        failures.append(f"{label} {detail}".strip())
        print(f"  FAIL {label} {detail}")


def login(client, email, password):
    response = client.post("/login", {"email": email, "password": password})
    return response


def cleanup():
    User.objects.filter(email__endswith="@smoke.test").delete()
    Role.objects.filter(name__startswith="smoke_").delete()
    SsoSetting.objects.filter(provider__startswith="smoketest").delete()
    EmailTemplate.objects.filter(template_key__startswith="smoke_").delete()
    EmailIdentity.objects.filter(from_email__endswith="@smoke.test").delete()
    CompanySetting.objects.filter(setting_key__startswith="smoke_").delete()


def main():
    tenant = default_tenant()
    set_current_tenant(tenant)
    cleanup()
    client = TestClient(headers={"host": "localhost"})

    print("Login")
    response = login(client, EMAIL, PASSWORD)
    check("admin login redirects to admin console", response.status_code == 302)
    check(
        "admin redirect target", response["Location"] == "/admin/dashboard", response["Location"]
    )

    print("Admin console")
    response = client.get("/admin/dashboard")
    check("admin dashboard 200", response.status_code == 200, str(response.status_code))
    if response.status_code == 200:
        body = response.content.decode()
        check("dashboard shows module overview", "Module overview" in body)
        check("dashboard shows audit trail", "Recent audit trail" in body)

    for path in ("/admin/", "/admin/users", "/admin/roles", "/admin/settings", "/admin/system"):
        response = client.get(path, follow=False)
        check(f"{path} redirects or renders", response.status_code in (200, 302), str(response.status_code))

    print("Settings pages")
    pages = [
        "/settings/",
        "/settings/company",
        "/settings/users",
        "/settings/users/create",
        "/settings/roles",
        "/settings/roles/create",
        "/settings/multi-roles",
        "/settings/email",
        "/settings/email-templates",
        "/settings/etims",
        "/settings/openai",
        "/settings/sso",
        "/settings/zoho",
        "/settings/system",
    ]
    for path in pages:
        response = client.get(path)
        check(f"GET {path}", response.status_code == 200, str(response.status_code))

    template = EmailTemplate.objects.order_by("id").first()
    if template:
        response = client.get(f"/settings/email-templates/edit/{template.id}")
        check("GET email template edit", response.status_code == 200, str(response.status_code))

    print("Company settings")
    response = client.post(
        "/settings/company",
        {
            "company_name": "SORA ERP",
            "company_email": "info@sora.com",
            "company_phone": "+254 700 000 000",
            "company_website": "",
            "company_address": "Nairobi, Kenya",
            "company_pin": "",
            "currency_symbol": "KSh",
            "vat_rate": "16",
            "invoice_due_days": "30",
            "quote_valid_days": "30",
            "default_inclusions": "",
            "default_exclusions": "",
            "default_assumptions": "",
            "default_financial_notes": "",
        },
    )
    check("company save redirects", response.status_code == 302, str(response.status_code))
    check(
        "company name persisted",
        CompanySetting.objects.filter(
            tenant=tenant, setting_key="company_name", setting_value="SORA ERP"
        ).exists(),
    )

    print("Email settings secrets")
    base_email = {
        "mail_enabled": "on",
        "mail_host": "smtp.example.com",
        "mail_port": "587",
        "mail_username": "smoke@example.com",
        "mail_encryption": "tls",
        "mail_from_address": "noreply@example.com",
        "mail_from_name": "SORA ERP",
    }
    client.post("/settings/email", {**base_email, "mail_password": "smoke-secret", "action": "save"})
    stored = CompanySetting.objects.get(tenant=tenant, setting_key="mail_password").setting_value
    check("mail password encrypted", stored.startswith("enc:v1:"), stored)
    check("mail password decrypts", decrypt_secret(stored) == "smoke-secret")
    client.post("/settings/email", {**base_email, "mail_password": "", "action": "save"})
    stored = CompanySetting.objects.get(tenant=tenant, setting_key="mail_password").setting_value
    check("blank mail password keeps stored", decrypt_secret(stored) == "smoke-secret")
    CompanySetting.objects.filter(tenant=tenant, setting_key="mail_password").delete()
    CompanySetting.objects.filter(tenant=tenant, setting_key__startswith="mail_").update(setting_value="")

    print("Sender identities")
    response = client.get("/settings/email-identities")
    check("GET sender identities", response.status_code == 200, str(response.status_code))
    response = client.get("/settings/email-identities/create")
    check("GET sender create", response.status_code == 200, str(response.status_code))
    response = client.post(
        "/settings/email-identities/create",
        {
            "name": "Smoke HR",
            "module": "hr",
            "from_name": "SORA HR",
            "from_email": "hr-no-reply@smoke.test",
            "reply_to": "hr@smoke.test",
            "smtp_host": "smtp.example.com",
            "smtp_port": "587",
            "smtp_username": "hr@smoke.test",
            "smtp_password": "identity-secret",
            "smtp_encryption": "tls",
            "imap_host": "",
            "imap_port": "993",
            "imap_username": "",
            "imap_password": "",
            "imap_encryption": "ssl",
            "imap_folder": "INBOX",
            "signature": "HR team",
            "is_default": "on",
            "is_active": "on",
        },
    )
    check("sender create redirects", response.status_code == 302, str(response.status_code))
    identity = EmailIdentity.objects.filter(from_email="hr-no-reply@smoke.test").first()
    check("sender created", identity is not None)
    if identity:
        check("sender password encrypted", identity.smtp_password.startswith("enc:v1:"))
        check("sender password decrypts", identity.smtp_password_plain == "identity-secret")
        config = module_mail_config("hr")
        check("module identity selected", config and config["from_address"] == "hr-no-reply@smoke.test")
        check("module reply-to carried", config and config["reply_to"] == ["hr@smoke.test"])
        response = client.get(f"/settings/email-identities/edit/{identity.id}")
        check("GET sender edit", response.status_code == 200, str(response.status_code))
        response = client.post(
            f"/settings/email-identities/edit/{identity.id}",
            {
                "name": "Smoke HR Updated",
                "module": "hr",
                "from_name": "SORA People",
                "from_email": "hr-no-reply@smoke.test",
                "reply_to": "hr@smoke.test",
                "smtp_host": "smtp.example.com",
                "smtp_port": "587",
                "smtp_username": "hr@smoke.test",
                "smtp_password": "",
                "smtp_encryption": "tls",
                "imap_host": "",
                "imap_port": "993",
                "imap_username": "",
                "imap_password": "",
                "imap_encryption": "ssl",
                "imap_folder": "INBOX",
                "signature": "HR team",
                "is_default": "on",
                "is_active": "on",
            },
        )
        check("sender update redirects", response.status_code == 302, str(response.status_code))
        identity.refresh_from_db()
        check("sender update kept password", identity.smtp_password_plain == "identity-secret")
        check("sender update saved name", identity.from_name == "SORA People")
        sales = TestClient(headers={"host": "localhost"})
        login(sales, "sales@sora.com", "sales123")
        response = sales.get("/settings/email-identities")
        check("sales blocked from senders", response.status_code == 403, str(response.status_code))
        response = client.post(f"/settings/email-identities/delete/{identity.id}")
        check("sender delete redirects", response.status_code == 302, str(response.status_code))
        check(
            "sender deleted",
            not EmailIdentity.objects.filter(from_email="hr-no-reply@smoke.test").exists(),
        )

    print("eTIMS secrets")
    etims_base = {
        "etims_enabled": "on",
        "etims_api_url": "https://etims.example.com/api",
        "etims_tin": "P000000000X",
        "etims_branch_code": "00",
        "etims_api_key": "smoke-key",
        "action": "save",
    }
    client.post("/settings/etims", {**etims_base, "etims_api_secret": "smoke-secret"})
    stored_secret = CompanySetting.objects.get(
        tenant=tenant, setting_key="etims_api_secret"
    ).setting_value
    check("etims secret stored encrypted", stored_secret.startswith("enc:v1:"), stored_secret)
    check("etims secret decrypts", decrypt_secret(stored_secret) == "smoke-secret")
    check("etims secret not plaintext", "smoke-secret" not in stored_secret)
    client.post("/settings/etims", {**etims_base, "etims_api_secret": ""})
    check(
        "blank etims secret keeps stored",
        CompanySetting.objects.get(
            tenant=tenant, setting_key="etims_api_secret"
        ).setting_value
        == stored_secret,
    )
    CompanySetting.objects.filter(tenant=tenant, setting_key__startswith="etims_").update(
        setting_value=""
    )

    print("User management")
    email = "settings-smoke@smoke.test"
    response = client.post(
        "/settings/users/create",
        {
            "username": "settings-smoke",
            "email": email,
            "first_name": "Settings",
            "last_name": "Smoke",
            "role": "employee",
            "status": "active",
            "password": "smokepass1",
            "system_roles": [],
        },
    )
    check("user create redirects", response.status_code == 302, str(response.status_code))
    created = User.objects.filter(email=email).first()
    check("user created", created is not None)
    if created:
        check("user password usable", created.check_password("smokepass1"))
        response = client.get(f"/settings/users/edit/{created.id}")
        check("edit user page", response.status_code == 200, str(response.status_code))
        response = client.post(f"/settings/users/delete/{created.id}")
        check("user delete redirects", response.status_code == 302, str(response.status_code))
        check("user deleted", not User.objects.filter(email=email).exists())

    print("Role management")
    response = client.post(
        "/settings/roles/create",
        {
            "name": "smoke_role",
            "display_name": "Smoke Role",
            "description": "Temporary role",
            "permissions": [],
        },
    )
    check("role create redirects", response.status_code == 302, str(response.status_code))
    role = Role.objects.filter(name="smoke_role").first()
    check("role created", role is not None)
    if role:
        response = client.get(f"/settings/roles/edit/{role.id}")
        check("edit role page", response.status_code == 200, str(response.status_code))
        response = client.post(f"/settings/roles/delete/{role.id}")
        check("role delete redirects", response.status_code == 302, str(response.status_code))
        check("role deleted", not Role.objects.filter(name="smoke_role").exists())

    print("Email template editing")
    temp = EmailTemplate.objects.create(
        template_key="smoke_template",
        name="Smoke Template",
        subject="Original",
        body="Original body",
        variables="name",
        status="active",
        created_at=timezone.now(),
        updated_at=timezone.now(),
    )
    response = client.post(
        f"/settings/email-templates/edit/{temp.id}",
        {"subject": "Updated subject", "body": "Updated body", "status": "inactive"},
    )
    check("template update redirects", response.status_code == 302, str(response.status_code))
    temp.refresh_from_db()
    check("template subject saved", temp.subject == "Updated subject")
    check("template status saved", temp.status == "inactive")
    temp.delete()

    print("SSO providers")
    client.post("/settings/sso", {"action": "add", "new_provider": "smoketest"})
    provider = SsoSetting.objects.filter(provider="smoketest").first()
    check("sso provider added", provider is not None)
    if provider:
        providers = list(SsoSetting.objects.all())
        payload = {"action": "save"}
        for row in providers:
            payload[f"enabled_{row.provider}"] = "1" if row.enabled else ""
            payload[f"client_id_{row.provider}"] = row.client_id or ""
            payload[f"redirect_uri_{row.provider}"] = row.redirect_uri or ""
            payload[f"client_secret_{row.provider}"] = (
                "smoketest-secret" if row.provider == "smoketest" else ""
            )
        response = client.post("/settings/sso", payload)
        check("sso save redirects", response.status_code == 302, str(response.status_code))
        provider.refresh_from_db()
        check("sso secret stored", provider.client_secret == "smoketest-secret")
        payload[f"client_secret_smoketest"] = ""
        client.post("/settings/sso", payload)
        provider.refresh_from_db()
        check("blank sso secret keeps stored", provider.client_secret == "smoketest-secret")

    print("Non-admin access")
    sales = TestClient(headers={"host": "localhost"})
    login(sales, "sales@sora.com", "sales123")
    response = sales.get("/settings/")
    check("sales denied settings", response.status_code == 403, str(response.status_code))
    response = sales.get("/admin/dashboard")
    check(
        "sales redirected from admin console",
        response.status_code == 302 and response["Location"] == "/dashboard",
        f"{response.status_code} {response.get('Location')}",
    )

    print("System page content")
    response = client.get("/settings/system")
    body = response.content.decode()
    check("system shows database", "Database" in body)
    check("system shows runtime", "Runtime" in body)

    cleanup()
    print()
    if failures:
        print(f"{checks} checks, {len(failures)} failures")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print(f"{checks} checks, 0 failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
