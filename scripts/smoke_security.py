"""Security, MFA, password recovery and audit-trail smoke checks.

Everything runs inside a transaction that is rolled back, so seeded accounts
and settings are left untouched. Outbound mail is captured in memory.
"""

import os
import re
import sys
import time
from datetime import timedelta

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core import views as core_views
from apps.core.crypto import decrypt_secret
from apps.core.models import LoginAttempt, RequestAudit, User, UserMFA
from apps.core.security import _hotp
from apps.saas.models import PlatformSetting, Tenant, TenantMembership

mails = []


def fake_mail(subject, body, recipients, html=None):
    mails.append({"subject": subject, "body": body, "recipients": list(recipients)})
    return True


core_views.send_platform_mail = fake_mail

results = []


def check(label, response, expected=(200,), contains=None, missing=None):
    text = response.content.decode("utf-8", "replace")
    ok = response.status_code in expected
    if ok and contains and contains not in text:
        ok = False
        print(f"FAIL {label}: missing text {contains!r}")
    if ok and missing and missing in text:
        ok = False
        print(f"FAIL {label}: unexpected text {missing!r}")
    results.append((label, response.status_code, ok))
    if not ok:
        print(f"FAIL {label}: {response.status_code} {text[:400]}")
    return response


def expect(label, condition, detail=""):
    results.append((label, "-", bool(condition)))
    if not condition:
        print(f"FAIL {label}: {detail}")


def location(response):
    return response.headers.get("Location", "")


def current_code(secret):
    return _hotp(secret, int(time.time()) // 30)


def cleanup():
    """Remove records written by HTTP requests, which bypass the script transaction."""
    from apps.core.tenancy import all_tenants_context

    with all_tenants_context():
        User.objects.filter(email__startswith="security.").delete()
    LoginAttempt.objects.filter(email__startswith="security.").delete()
    RequestAudit.objects.filter(actor_email__startswith="security.").delete()
    PlatformSetting.objects.filter(key="security_mfa_required_admins").update(value="false")


cleanup()
stamp = int(timezone.now().timestamp())
password = "SmokeLock123!"
new_password = "SmokeFresh456!"
reset_password = "SmokeReset789!"
locked_email = f"security.locked.{stamp}@example.com"
user_email = f"security.user.{stamp}@example.com"
session_email = f"security.session.{stamp}@example.com"
admin_email = f"security.admin.{stamp}@example.com"

with transaction.atomic():
    locked_user = User.objects.create_user(
        email=locked_email, username=f"locked{stamp}", password=password, role="sales"
    )
    user = User.objects.create_user(
        email=user_email, username=f"user{stamp}", password=password, role="sales"
    )
    session_user = User.objects.create_user(
        email=session_email, username=f"session{stamp}", password=password, role="employee"
    )
    admin = User.objects.create_user(
        email=admin_email,
        username=f"admin{stamp}",
        password=password,
        role="super_admin",
        is_platform_admin=True,
    )
    workspace = Tenant.objects.filter(status="active").order_by("id").first()
    if workspace:
        TenantMembership.objects.create(
            tenant=workspace, user=session_user, role="member", status="active"
        )

    public = TestClient(headers={"host": "localhost"})
    client = TestClient(headers={"host": "localhost"})
    admin_client = TestClient(headers={"host": "localhost"})

    # ------------------------------------------------------------------
    # Login page: DB copy, no leaked demo credentials
    # ------------------------------------------------------------------
    check("login_page", public.get("/login"), contains="Welcome back")
    check(
        "login_no_demo_credentials",
        public.get("/login"),
        missing="admin123",
    )
    check(
        "login_no_plaintext_seed",
        public.get("/login"),
        missing="Demo accounts",
    )
    check("password_reset_page", public.get("/password-reset"), contains="Reset your password")
    check("robots", public.get("/robots.txt"), contains="Disallow: /")
    check("django_admin_gone", public.get("/django-admin/"), expected=(404,))

    # ------------------------------------------------------------------
    # Security headers
    # ------------------------------------------------------------------
    login_response = public.get("/login")
    csp = login_response.headers.get("Content-Security-Policy", "")
    expect("csp_frame_ancestors", "frame-ancestors 'none'" in csp, csp)
    expect("csp_object_none", "object-src 'none'" in csp, csp)
    expect(
        "x_frame_deny",
        login_response.headers.get("X-Frame-Options") == "DENY",
        login_response.headers.get("X-Frame-Options"),
    )
    expect(
        "referrer_policy",
        login_response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin",
    )
    expect(
        "noindex_login",
        "noindex" in (login_response.headers.get("X-Robots-Tag") or ""),
    )
    expect(
        "cross_domain_policy_header",
        login_response.headers.get("X-Permitted-Cross-Domain-Policies") == "none",
        login_response.headers.get("X-Permitted-Cross-Domain-Policies"),
    )
    public_home = public.get("/")
    expect(
        "public_site_indexable",
        not public_home.headers.get("X-Robots-Tag"),
        public_home.headers.get("X-Robots-Tag"),
    )

    # ------------------------------------------------------------------
    # Brute-force lockout
    # ------------------------------------------------------------------
    for attempt in range(5):
        response = client.post(
            "/login", {"email": locked_email, "password": "WrongPass!123"}
        )
        expect(f"invalid_login_{attempt + 1}", response.status_code == 200, response.status_code)
    blocked = client.post("/login", {"email": locked_email, "password": password})
    check("login_lockout", blocked, expected=(429,), contains="Too many failed attempts")
    expect(
        "lockout_recorded",
        LoginAttempt.objects.filter(email=locked_email, reason="blocked").exists(),
    )
    expect(
        "passwords_never_logged",
        not LoginAttempt.objects.filter(reason__icontains="WrongPass").exists(),
    )

    # ------------------------------------------------------------------
    # Successful login and audit trail
    # ------------------------------------------------------------------
    login = client.post("/login", {"email": user_email, "password": password})
    check("login_success", login, expected=(302,))
    expect(
        "login_landing",
        location(login) == "/billing/?no_workspace=1",
        location(login),
    )
    check("dashboard_after_login", client.get("/security/"), contains="Security centre")
    before_audits = RequestAudit.objects.filter(actor=user).count()
    client.get("/security/")
    expect(
        "audit_ignores_reads",
        RequestAudit.objects.filter(actor=user).count() == before_audits,
    )

    # ------------------------------------------------------------------
    # Security centre and password change
    # ------------------------------------------------------------------
    check("security_center", client.get("/security/"), contains="Security centre")
    check(
        "security_center_attempts",
        client.get("/security/"),
        contains="Recent sign-in activity",
    )
    changed = client.post(
        "/security/password?token=should-not-be-audited",
        {
            "current_password": password,
            "password": new_password,
            "confirm_password": new_password,
        },
    )
    check("password_changed", changed, expected=(302,))
    expect(
        "password_changed_target",
        location(changed) == "/security/",
        location(changed),
    )
    user.refresh_from_db()
    expect("password_changed_saved", user.check_password(new_password))
    expect(
        "audit_write_recorded",
        RequestAudit.objects.filter(actor=user, path="/security/password").exists(),
    )
    audit_row = RequestAudit.objects.filter(actor=user, path="/security/password").first()
    expect("audit_request_id", bool(audit_row and audit_row.request_id), audit_row.request_id if audit_row else None)
    expect("audit_duration", bool(audit_row and audit_row.duration_ms >= 0))
    expect(
        "audit_omits_query_string",
        bool(audit_row and "?" not in audit_row.path),
        audit_row.path if audit_row else None,
    )
    expect(
        "session_survives_password_change",
        client.get("/security/").status_code == 200,
    )

    # ------------------------------------------------------------------
    # Password reset end to end
    # ------------------------------------------------------------------
    password_client = TestClient(headers={"host": "localhost"})
    mails.clear()
    response = password_client.post("/password-reset", {"email": user_email})
    check("reset_request", response, contains="Check your inbox")
    expect("reset_mail_sent", len(mails) == 1, mails)
    body = mails[0]["body"] if mails else ""
    match = re.search(r"/password-reset/confirm/[^\s]+", body)
    expect("reset_link_present", bool(match), body[:200])
    reset_path = (match.group(0) if match else "/password-reset").rstrip("/")
    check("reset_confirm_page", password_client.get(reset_path), contains="Choose a new password")
    reset = password_client.post(
        reset_path,
        {"password": reset_password, "confirm_password": reset_password},
    )
    check("reset_submitted", reset, expected=(302,))
    expect("reset_redirect_login", location(reset) == "/login", location(reset))
    user.refresh_from_db()
    expect("reset_password_saved", user.check_password(reset_password))
    check("reset_reuse_blocked", password_client.get(reset_path), expected=(400,), contains="expired")
    mails.clear()
    response = password_client.post("/password-reset", {"email": f"nobody.{stamp}@example.com"})
    check("reset_unknown_generic", response, contains="Check your inbox")
    expect("reset_unknown_no_mail", len(mails) == 0, mails)

    # Rate limit password reset requests per account.
    password_client.post("/password-reset", {"email": user_email})
    password_client.post("/password-reset", {"email": user_email})
    password_client.post("/password-reset", {"email": user_email})
    password_client.post("/password-reset", {"email": user_email})
    mails.clear()
    password_client.post("/password-reset", {"email": user_email})
    expect("reset_rate_limited", len(mails) == 0, len(mails))

    # ------------------------------------------------------------------
    # MFA enrolment
    # ------------------------------------------------------------------
    client = TestClient(headers={"host": "localhost"})
    check(
        "mfa_login_after_reset",
        client.post("/login", {"email": user_email, "password": reset_password}),
        expected=(302,),
    )
    check("mfa_setup_page", client.get("/security/mfa"), contains="Set up your authenticator")
    secret = decrypt_secret(client.session.get("sora_mfa_setup_secret", ""))
    expect("mfa_secret_generated", len(secret) >= 16, secret)
    bad_enable = client.post("/security/mfa", {"action": "enable", "code": "000000"})
    check("mfa_bad_code_rejected", bad_enable, contains="not valid")
    enabled = client.post(
        "/security/mfa", {"action": "enable", "code": current_code(secret)}
    )
    check("mfa_enabled", enabled, expected=(302,))
    mfa = UserMFA.objects.get(user=user)
    expect("mfa_enabled_flag", mfa.enabled)
    expect("mfa_secret_encrypted", mfa.secret.startswith("enc:v1:"), mfa.secret[:10])
    expect("mfa_backup_codes_hashed", all(code.startswith("pbkdf2_") for code in mfa.backup_codes))
    codes_page = check(
        "mfa_backup_codes_once",
        client.get("/security/mfa"),
        contains="Save your backup codes",
    )
    page = codes_page.content.decode("utf-8")
    backup_codes = re.findall(r"\b[A-Z2-7]{4}-[A-Z2-7]{4}\b", page)
    expect("mfa_backup_code_count", len(backup_codes) == 10, len(backup_codes))
    expect(
        "mfa_backup_codes_not_repeated",
        "Save your backup codes" not in client.get("/security/mfa").content.decode("utf-8"),
    )

    # ------------------------------------------------------------------
    # MFA login challenge
    # ------------------------------------------------------------------
    client.get("/logout")
    challenge = client.post("/login", {"email": user_email, "password": reset_password})
    check("mfa_challenge_redirect", challenge, expected=(302,))
    expect("mfa_challenge_target", location(challenge) == "/login/verify", location(challenge))
    check("mfa_challenge_page", client.get("/login/verify"), contains="Verify it is you")
    client.post("/login/verify", {"code": "000000"})
    expect(
        "mfa_page_still_signed_out",
        location(client.get("/security/")).startswith("/login"),
        location(client.get("/security/")),
    )
    login_code = current_code(secret)
    verified = client.post("/login/verify", {"code": login_code})
    check("mfa_verified", verified, expected=(302,))
    expect(
        "mfa_session_verified",
        client.session.get("sora_mfa_verified") is True,
        client.session.get("sora_mfa_verified"),
    )
    check("mfa_protected_page", client.get("/security/"), contains="Enabled")
    mfa.refresh_from_db()
    expect("mfa_counter_recorded", mfa.last_totp_counter is not None, mfa.last_totp_counter)

    # A code already accepted for this time-step cannot be replayed.
    client.get("/logout")
    client.post("/login", {"email": user_email, "password": reset_password})
    replay = client.post("/login/verify", {"code": login_code})
    check("mfa_totp_replay_blocked", replay, contains="not valid")
    expect(
        "mfa_totp_replay_signed_out",
        location(client.get("/security/")).startswith("/login"),
        location(client.get("/security/")),
    )

    # Backup code can be used instead of the app.
    client.get("/logout")
    client.post("/login", {"email": user_email, "password": reset_password})
    used_code = backup_codes[0]
    backup_login = client.post("/login/verify", {"code": used_code})
    check("mfa_backup_login", backup_login, expected=(302,))
    mfa.refresh_from_db()
    expect("mfa_backup_consumed", len(mfa.backup_codes) == 9, len(mfa.backup_codes))
    client.get("/logout")
    client.post("/login", {"email": user_email, "password": reset_password})
    reused = client.post("/login/verify", {"code": used_code})
    expect("mfa_backup_single_use", reused.status_code == 200, reused.status_code)

    # ------------------------------------------------------------------
    # MFA policy enforcement for platform administrators
    # ------------------------------------------------------------------
    setting = PlatformSetting.objects.get(key="security_mfa_required_admins")
    setting.value = "true"
    setting.save(update_fields=["value"])
    admin_client.force_login(admin)
    forced = admin_client.get("/dashboard")
    check("admin_mfa_forced", forced, expected=(302,))
    expect(
        "admin_mfa_forced_target",
        location(forced).startswith("/security/mfa"),
        location(forced),
    )
    check("admin_mfa_setup_required", admin_client.get("/security/mfa?required=1"), contains="mandated")
    setting.value = "false"
    setting.save(update_fields=["value"])

    # A verified MFA user with a bare session is logged out.
    admin_client.force_login(user)
    stale = admin_client.get("/cloud/")
    check("stale_mfa_session_logged_out", stale, expected=(302,))
    expect("stale_mfa_target", location(stale) == "/login?mfa=1", location(stale))
    expect("stale_session_flushed", not admin_client.session.get("_auth_user_id"))

    # ------------------------------------------------------------------
    # Idle session expiry
    # ------------------------------------------------------------------
    idle_client = TestClient(headers={"host": "localhost"})
    idle_client.force_login(session_user)
    session = idle_client.session
    session["sora_last_activity"] = (timezone.now() - timedelta(hours=9)).timestamp()
    session.save()
    expired = idle_client.get("/security/")
    check("idle_session_expired", expired, expected=(302,))
    expect("idle_timeout_target", location(expired) == "/login?timeout=1", location(expired))
    check("timeout_message", idle_client.get("/login?timeout=1"), contains="session expired")

    # ------------------------------------------------------------------
    # Cloud security settings page
    # ------------------------------------------------------------------
    cloud = TestClient(headers={"host": "localhost"})
    cloud.force_login(User.objects.get(email="admin@sora.com"))
    check("cloud_security_settings", cloud.get("/cloud/settings/security/"), contains="Security")

    transaction.set_rollback(True)

cleanup()
passed = sum(1 for _, _, ok in results if ok)
total = len(results)
print(f"{passed}/{total} security checks passed; test records removed.")
if passed != total:
    sys.exit(1)
