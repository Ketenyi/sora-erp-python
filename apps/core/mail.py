"""Module-aware outbound mail.

Every business module can have its own sender identity (for example
``hr-no-reply@`` or ``sales-no-reply@``). When a module has no usable identity
the legacy workspace SMTP settings are used so existing installations keep
working during migration.

Credentials on :class:`~apps.core.models.EmailIdentity` are encrypted at rest;
this module only decrypts them inside :func:`module_mail_config`.
"""

from __future__ import annotations

import imaplib
import logging
from email.utils import formataddr

from django.core.mail import EmailMultiAlternatives, get_connection
from django.template import Context, Template
from django.template.exceptions import TemplateSyntaxError
from django.utils import timezone

from apps.core.crypto import decrypt_secret
from apps.core.tenancy import TenantContextRequired

logger = logging.getLogger(__name__)

DEFAULT_MODULE = "system"


class MailNotConfigured(RuntimeError):
    """Raised when a module has neither an identity nor a fallback SMTP host."""


def _normalise_recipients(recipients) -> list[str]:
    if isinstance(recipients, str):
        recipients = [recipients]
    cleaned: list[str] = []
    for value in recipients or []:
        address = str(value or "").strip()
        if address and address not in cleaned:
            cleaned.append(address)
    return cleaned


def get_identity(module: str = DEFAULT_MODULE):
    """Return the active sender identity a module should use, if any."""

    from apps.core.models import EmailIdentity

    try:
        candidates = EmailIdentity.objects.filter(module=module, is_active=True).order_by(
            "-is_default", "id"
        )
        for identity in candidates:
            if identity.can_send:
                return identity
    except TenantContextRequired:
        return None
    return None


def _legacy_config() -> dict | None:
    from apps.core.services import get_company_settings

    values = get_company_settings()
    if values.get("mail_enabled") != "true" or not values.get("mail_host"):
        return None
    encryption = values.get("mail_encryption", "tls")
    address = values.get("mail_from_address") or "noreply@sora.com"
    name = values.get("mail_from_name") or "SORA ERP"
    return {
        "source": "workspace",
        "identity": None,
        "host": values.get("mail_host"),
        "port": int(values.get("mail_port") or 587),
        "username": values.get("mail_username") or "",
        "password": decrypt_secret(values.get("mail_password") or ""),
        "use_tls": encryption == "tls",
        "use_ssl": encryption == "ssl",
        "from_address": address,
        "from_name": name,
        "from_email": formataddr((name, address)),
        "reply_to": [],
    }


def module_mail_config(module: str = DEFAULT_MODULE) -> dict | None:
    """Resolve SMTP settings for ``module`` with a legacy settings fallback."""

    identity = get_identity(module)
    if identity is not None:
        reply_to = [identity.reply_to] if identity.reply_to else []
        return {
            "source": "identity",
            "identity": identity,
            "host": identity.smtp_host,
            "port": identity.smtp_port or 587,
            "username": identity.smtp_username,
            "password": identity.smtp_password_plain,
            "use_tls": identity.smtp_encryption == "tls",
            "use_ssl": identity.smtp_encryption == "ssl",
            "from_address": identity.from_email,
            "from_name": identity.from_name,
            "from_email": formataddr((identity.from_name, identity.from_email)),
            "reply_to": reply_to,
        }
    return _legacy_config()


def mail_is_configured(module: str = DEFAULT_MODULE) -> bool:
    return module_mail_config(module) is not None


def _connection_for(config: dict):
    return get_connection(
        host=config["host"],
        port=config["port"],
        username=config["username"],
        password=config["password"],
        use_tls=config["use_tls"],
        use_ssl=config["use_ssl"],
        timeout=20,
    )


def send_module_email(
    module: str,
    subject: str,
    body: str,
    recipients,
    *,
    html_message: str | None = None,
    reply_to=None,
    attachments=None,
    fail_silently: bool = False,
) -> int:
    """Send one message using the sender identity assigned to ``module``.

    Returns the number of messages accepted by the server. Raises
    :class:`MailNotConfigured` when no identity or SMTP server exists unless
    ``fail_silently`` is set.
    """

    recipients = _normalise_recipients(recipients)
    if not recipients:
        return 0
    config = module_mail_config(module)
    if config is None:
        message = (
            f"No sender identity or SMTP server is configured for the '{module}' module."
        )
        if fail_silently:
            logger.warning(message)
            return 0
        raise MailNotConfigured(message)

    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=body,
            from_email=config["from_email"],
            to=recipients,
            reply_to=list(reply_to) if reply_to else (config["reply_to"] or None),
            connection=_connection_for(config),
        )
        if html_message:
            message.attach_alternative(html_message, "text/html")
        for attachment in attachments or []:
            filename, content, mimetype = attachment
            message.attach(filename, content, mimetype)
        return message.send(fail_silently=False)
    except Exception:
        if fail_silently:
            logger.exception("Could not send '%s' mail via module %s", subject, module)
            return 0
        raise


def render_email_template(
    template_key: str, context: dict, *, default_subject: str = "", default_body: str = ""
) -> tuple[str, str]:
    """Render a stored :class:`EmailTemplate`; fall back when it is missing."""

    from apps.core.models import EmailTemplate

    template = EmailTemplate.objects.filter(
        template_key=template_key, status="active"
    ).first()
    if template is None:
        return default_subject, default_body
    try:
        render_context = Context(context, autoescape=False)
        subject = Template(template.subject).render(render_context)
        body = Template(template.body).render(render_context)
    except TemplateSyntaxError:
        logger.exception("Email template %s contains invalid syntax", template_key)
        return default_subject, default_body
    return subject, body


def send_template_email(
    module: str,
    template_key: str,
    context: dict,
    recipients,
    *,
    default_subject: str = "",
    default_body: str = "",
    html_message: str | None = None,
    fail_silently: bool = False,
) -> int:
    subject, body = render_email_template(
        template_key,
        context,
        default_subject=default_subject,
        default_body=default_body,
    )
    return send_module_email(
        module,
        subject,
        body,
        recipients,
        html_message=html_message,
        fail_silently=fail_silently,
    )


def verify_identity(identity, recipient: str | None = None) -> tuple[bool, str]:
    """Check SMTP (and IMAP when configured), optionally sending a test mail."""

    problems: list[str] = []
    smtp_ok = False
    imap_ok = not identity.imap_host

    if identity.smtp_host:
        try:
            connection = get_connection(
                host=identity.smtp_host,
                port=identity.smtp_port or 587,
                username=identity.smtp_username,
                password=identity.smtp_password_plain,
                use_tls=identity.smtp_encryption == "tls",
                use_ssl=identity.smtp_encryption == "ssl",
                timeout=20,
            )
            connection.open()
            connection.close()
            smtp_ok = True
        except Exception as exc:
            problems.append(f"SMTP: {exc}")
    else:
        problems.append("SMTP host is not configured.")

    if identity.imap_host:
        try:
            if identity.imap_encryption == "ssl":
                mailbox = imaplib.IMAP4_SSL(
                    identity.imap_host, identity.imap_port or 993, timeout=20
                )
            else:
                mailbox = imaplib.IMAP4(
                    identity.imap_host, identity.imap_port or 143, timeout=20
                )
                if identity.imap_encryption == "tls":
                    mailbox.starttls()
            try:
                mailbox.login(identity.imap_username, identity.imap_password_plain)
                mailbox.select(identity.imap_folder or "INBOX", readonly=True)
            finally:
                try:
                    mailbox.logout()
                except Exception:
                    pass
            imap_ok = True
        except Exception as exc:
            problems.append(f"IMAP: {exc}")

    sent_note = ""
    if smtp_ok and recipient:
        try:
            connection = get_connection(
                host=identity.smtp_host,
                port=identity.smtp_port or 587,
                username=identity.smtp_username,
                password=identity.smtp_password_plain,
                use_tls=identity.smtp_encryption == "tls",
                use_ssl=identity.smtp_encryption == "ssl",
                timeout=20,
            )
            message = EmailMultiAlternatives(
                subject=f"Test message from {identity.from_name}",
                body=(
                    "This is a delivery test from SORA ERP.\n\n"
                    f"Sender identity: {identity.name} <{identity.from_email}>\n"
                    f"Module: {identity.get_module_display()}\n"
                ),
                from_email=formataddr((identity.from_name, identity.from_email)),
                to=[recipient],
                connection=connection,
            )
            message.send(fail_silently=False)
            sent_note = f" Test message sent to {recipient}."
        except Exception as exc:
            problems.append(f"Send: {exc}")

    ok = smtp_ok and imap_ok and not problems
    identity.last_error = "\n".join(problems)[:4000]
    identity.last_verified_at = timezone.now() if ok else None
    identity.save(update_fields=["last_error", "last_verified_at", "updated_at"])
    if ok:
        return True, f"Sender verified.{sent_note}"
    return False, " ".join(problems)
