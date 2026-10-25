"""Helpdesk automation: SLA targets, routing, mailbox ingestion and CSAT."""

from __future__ import annotations

import hashlib
import imaplib
import logging
import re
from datetime import timedelta
from email import message_from_bytes
from email.utils import parseaddr, parsedate_to_datetime
from html import unescape

from django.conf import settings
from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.formats import date_format

from apps.core.mail import (
    MailNotConfigured,
    get_identity,
    mail_is_configured,
    render_email_template,
    send_module_email,
)
from apps.core.models import EmailIdentity, Employee, SlaContract, SlaTicket
from apps.core.services import get_company_settings, next_sequence
from apps.core.tenancy import all_tenants_context, tenant_context
from apps.helpdesk.models import (
    CATEGORY_CHOICES,
    PRIORITY_CHOICES,
    SLA_DEFAULTS,
    HelpdeskAgent,
    HelpdeskEvent,
    HelpdeskInboundMessage,
    HelpdeskRoutingRule,
    HelpdeskSetting,
    HelpdeskSlaPolicy,
    HelpdeskSurveyResponse,
    TicketReply,
)

logger = logging.getLogger(__name__)

CLIENT_SALT = "helpdesk.client-portal.v1"
CLOSED_STATUSES = {"resolved", "closed"}
SLA_FACTORS = {"critical": 0.25, "high": 0.5, "medium": 1.0, "low": 1.5}
PRIORITY_LABELS = dict(PRIORITY_CHOICES)
CATEGORY_LABELS = dict(CATEGORY_CHOICES)
STATUS_LABELS = dict(SlaTicket._meta.get_field("status").choices)
CHANNEL_LABELS = dict(SlaTicket._meta.get_field("channel").choices)
TICKET_NUMBER_RE = re.compile(r"\b(?:HD|TKT)-\d+(?:-\d+)?\b", re.IGNORECASE)
SUBJECT_PREFIX_RE = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")


# ---------------------------------------------------------------------------
# Settings and SLA policies
# ---------------------------------------------------------------------------
def helpdesk_setting(create: bool = False) -> HelpdeskSetting | None:
    row = HelpdeskSetting.objects.order_by("id").first()
    if row is None and create:
        row = HelpdeskSetting.objects.create()
    return row


def ensure_sla_policies() -> dict[str, HelpdeskSlaPolicy]:
    existing = {row.priority: row for row in HelpdeskSlaPolicy.objects.all()}
    for priority, values in SLA_DEFAULTS.items():
        if priority in existing:
            continue
        existing[priority] = HelpdeskSlaPolicy.objects.create(
            priority=priority,
            response_minutes=values["response"],
            resolution_minutes=values["resolution"],
        )
    return existing


def save_sla_policies(values: dict[str, tuple[int, int]]) -> None:
    for priority, (response, resolution) in values.items():
        HelpdeskSlaPolicy.objects.update_or_create(
            priority=priority,
            defaults={
                "response_minutes": max(5, int(response)),
                "resolution_minutes": max(15, int(resolution)),
                "is_active": True,
            },
        )


def sla_targets(contract, priority: str, start=None) -> tuple:
    """Return (response_due, resolution_due) for a new ticket."""

    start = start or timezone.now()
    policy = HelpdeskSlaPolicy.objects.filter(priority=priority, is_active=True).first()
    if policy is not None:
        response_minutes = policy.response_minutes
        resolution_minutes = policy.resolution_minutes
    else:
        factor = SLA_FACTORS.get(priority, 1.0)
        response_hours = (getattr(contract, "response_target_hours", None) or 8) * factor
        resolution_hours = (getattr(contract, "resolution_target_hours", None) or 48) * factor
        response_minutes = max(5, round(response_hours * 60))
        resolution_minutes = max(15, round(resolution_hours * 60))
    return (
        start + timedelta(minutes=response_minutes),
        start + timedelta(minutes=resolution_minutes),
    )


def mark_breaches() -> None:
    now = timezone.now()
    SlaTicket.objects.filter(
        response_breached=False,
        first_response_at__isnull=True,
        response_due_at__lt=now,
    ).exclude(status__in=CLOSED_STATUSES).update(response_breached=True)
    SlaTicket.objects.filter(breached=False, sla_due_at__lt=now).exclude(
        status__in=CLOSED_STATUSES
    ).update(breached=True)


# ---------------------------------------------------------------------------
# Assignment and routing
# ---------------------------------------------------------------------------
def ensure_agent_pool() -> None:
    existing = set(
        HelpdeskAgent.objects.values_list("employee_id", flat=True)
    )
    employee_ids = list(
        Employee.objects.filter(status="active").values_list("id", flat=True)
    )
    rows = [
        HelpdeskAgent(employee_id=employee_id)
        for employee_id in employee_ids
        if employee_id not in existing
    ]
    if rows:
        HelpdeskAgent.objects.bulk_create(rows, ignore_conflicts=True)


def pick_agent() -> int | None:
    """Return the next employee in round-robin order and advance the cursor."""

    with transaction.atomic():
        ensure_agent_pool()
        agents = list(
            HelpdeskAgent.objects.select_for_update()
            .filter(is_active=True)
            .order_by(F("last_assigned_at").asc(nulls_first=True), "id")
        )
        if not agents:
            return None
        agent = agents[0]
        agent.last_assigned_at = timezone.now()
        agent.save(update_fields=["last_assigned_at", "updated_at"])
        return agent.employee_id


def _rule_matches(rule: HelpdeskRoutingRule, ticket, subject: str, body: str, sender: str) -> bool:
    if rule.match_channel and rule.match_channel != ticket.channel:
        return False
    keywords = [word.strip().lower() for word in (rule.keywords or "").split(",") if word.strip()]
    if keywords:
        haystack = f"{subject}\n{body}".lower()
        if not any(word in haystack for word in keywords):
            return False
    senders = [item.strip().lower() for item in (rule.from_contains or "").split(",") if item.strip()]
    if senders:
        sender = (sender or "").lower()
        if not any(item in sender for item in senders):
            return False
    return True


def apply_routing(ticket, subject: str, body: str, sender: str) -> dict:
    """Apply the first matching rule chain and auto-assign when configured."""

    applied = []
    notify_client = True
    for rule in HelpdeskRoutingRule.objects.filter(is_active=True).order_by("order", "id"):
        if not _rule_matches(rule, ticket, subject, body, sender):
            continue
        if rule.set_priority:
            ticket.priority = rule.set_priority
        if rule.set_category:
            ticket.category = rule.set_category
        if rule.assignee_id:
            ticket.assigned_to = rule.assignee_id
        elif rule.round_robin:
            ticket.assigned_to = pick_agent()
        if not rule.notify_client:
            notify_client = False
        applied.append(rule)
        if rule.stop_processing:
            break

    setting = helpdesk_setting()
    if ticket.assigned_to is None and setting is not None and setting.auto_assign:
        ticket.assigned_to = pick_agent()
    if setting is not None and not setting.notify_client:
        notify_client = False
    return {"rules": applied, "notify_client": notify_client}


# ---------------------------------------------------------------------------
# Client links and communication
# ---------------------------------------------------------------------------
def _base_url(request=None) -> str:
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return (getattr(settings, "SORA_PUBLIC_BASE_URL", "") or "").rstrip("/")


def status_token(ticket) -> str:
    return signing.dumps({"ticket": ticket.pk}, salt=CLIENT_SALT, compress=True)


def status_url(ticket, request=None) -> str:
    path = f"/helpdesk/status/{ticket.pk}/{status_token(ticket)}/"
    base = _base_url(request)
    return f"{base}{path}" if base else path


def survey_url(ticket, request=None) -> str:
    return f"{status_url(ticket, request)}?survey=1#survey"


def load_client_ticket(ticket_id: int, token: str):
    """Validate a signed client link and return the ticket plus its tenant."""

    with all_tenants_context():
        ticket = SlaTicket.objects.select_related("tenant").filter(pk=ticket_id).first()
    if ticket is None:
        raise signing.BadSignature("Unknown ticket")
    with tenant_context(ticket.tenant):
        setting = helpdesk_setting()
        days = setting.status_link_days if setting else 30
        data = signing.loads(token, salt=CLIENT_SALT, max_age=max(1, days) * 86400)
        if int(data.get("ticket") or 0) != ticket.pk:
            raise signing.BadSignature("Ticket mismatch")
    return ticket


def _format_dt(value) -> str:
    if not value:
        return ""
    return date_format(timezone.localtime(value), "j M Y, H:i")


def _employee_name(employee_id) -> str:
    if not employee_id:
        return "Support team"
    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        return "Support team"
    name = f"{employee.first_name or ''} {employee.last_name or ''}".strip()
    return name or employee.email or "Support team"


def _company_name() -> str:
    return get_company_settings().get("company_name") or "SORA ERP"


def _mail_context(ticket, request=None, note: str = "") -> dict:
    return {
        "ticket_number": ticket.ticket_number,
        "subject": ticket.title,
        "requester_name": ticket.requester_name or "there",
        "agent_name": _employee_name(ticket.assigned_to),
        "priority": PRIORITY_LABELS.get(ticket.priority, ticket.priority.title()),
        "status": STATUS_LABELS.get(ticket.status, ticket.status.replace("_", " ").title()),
        "note": note,
        "response_due": _format_dt(ticket.response_due_at),
        "resolution_due": _format_dt(ticket.sla_due_at),
        "status_url": status_url(ticket, request),
        "survey_url": survey_url(ticket, request),
        "company_name": _company_name(),
    }


def _log_event(ticket, kind: str, detail: str = "", actor_user_id=None) -> None:
    HelpdeskEvent.objects.create(
        ticket=ticket,
        kind=kind,
        detail=detail,
        actor_user_id=actor_user_id,
    )


def send_client_email(
    ticket,
    template_key: str,
    *,
    request=None,
    note: str = "",
    event_kind: str = "email_sent",
) -> bool:
    """Render and deliver one client email through the helpdesk identity."""

    if not ticket.requester_email:
        _log_event(ticket, "email_skipped", "Requester has no email address.")
        return False
    if not mail_is_configured("helpdesk"):
        _log_event(
            ticket,
            "email_skipped",
            "No helpdesk sender identity or SMTP server is configured.",
        )
        return False
    context = _mail_context(ticket, request, note=note)
    subject, body = render_email_template(template_key, context)
    if not subject or not body:
        _log_event(
            ticket,
            "email_skipped",
            f"Email template '{template_key}' is missing or inactive.",
        )
        return False
    try:
        sent = send_module_email(
            "helpdesk",
            subject,
            body,
            [ticket.requester_email],
            fail_silently=True,
        )
    except MailNotConfigured:
        sent = 0
    if sent:
        _log_event(ticket, event_kind, f"{template_key} → {ticket.requester_email}")
        return True
    _log_event(ticket, "email_failed", f"{template_key} could not be delivered.")
    return False


def notify_ticket_received(ticket, request=None) -> bool:
    return send_client_email(
        ticket,
        "helpdesk_ticket_received",
        request=request,
        event_kind="confirmation_sent",
    )


def notify_agent_assigned(ticket, request=None) -> bool:
    if not ticket.assigned_to:
        return False
    return send_client_email(
        ticket,
        "helpdesk_agent_assigned",
        request=request,
        event_kind="assignment_sent",
    )


def notify_status_update(ticket, request=None, note: str = "") -> bool:
    return send_client_email(
        ticket,
        "helpdesk_status_update",
        request=request,
        note=note,
        event_kind="status_sent",
    )


def send_survey(ticket, request=None, *, force: bool = False) -> bool:
    setting = helpdesk_setting()
    if setting is not None and not setting.survey_enabled:
        return False
    if ticket.status not in CLOSED_STATUSES:
        return False
    if HelpdeskSurveyResponse.objects.filter(ticket_id=ticket.pk).exists():
        return False
    if ticket.survey_sent_at and not force:
        return False
    sent = send_client_email(
        ticket,
        "helpdesk_satisfaction_survey",
        request=request,
        event_kind="survey_sent",
    )
    if sent:
        ticket.survey_sent_at = timezone.now()
        ticket.save(update_fields=["survey_sent_at", "updated_at"])
    return sent


def confirm_resolution(ticket, request=None) -> bool:
    if ticket.client_confirmed_at:
        return False
    now = timezone.now()
    ticket.client_confirmed_at = now
    ticket.status = "closed"
    ticket.resolved_at = ticket.resolved_at or now
    ticket.save(
        update_fields=[
            "client_confirmed_at",
            "status",
            "resolved_at",
            "updated_at",
        ]
    )
    _log_event(ticket, "client_confirmed", "Client confirmed the resolution.")
    return True


def record_survey(ticket, rating: int, comment: str, request=None) -> HelpdeskSurveyResponse | None:
    if rating not in {1, 2, 3, 4, 5}:
        return None
    try:
        response = HelpdeskSurveyResponse.objects.create(
            ticket=ticket,
            rating=rating,
            comment=(comment or "").strip(),
            ip_address=(request.META.get("REMOTE_ADDR") if request else "") or "",
            user_agent=((request.META.get("HTTP_USER_AGENT") if request else "") or "")[:500],
        )
    except IntegrityError:
        return None
    _log_event(ticket, "survey_response", f"Client rated the resolution {rating}/5.")
    return response


# ---------------------------------------------------------------------------
# Inbound email ingestion
# ---------------------------------------------------------------------------
def _decode_part(part) -> str:
    payload = part.get_payload(decode=True) or b""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = HTML_TAG_RE.sub(" ", text)
    return unescape(text)


def extract_body(message) -> str:
    """Prefer text/plain, falling back to a tag-stripped HTML part."""

    plain_parts, html_parts = [], []
    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(_decode_part(part))
            elif content_type == "text/html":
                html_parts.append(_decode_part(part))
    elif message.get_content_type() == "text/html":
        html_parts.append(_decode_part(message))
    else:
        plain_parts.append(_decode_part(message))
    if plain_parts:
        return "\n".join(plain_parts).strip()[:20000]
    if html_parts:
        return _strip_html("\n".join(html_parts)).strip()[:20000]
    return ""


def _clean_subject(subject: str) -> str:
    value = SUBJECT_PREFIX_RE.sub("", subject or "").strip()
    return value[:255] or "(No subject)"


def _message_identifier(message, raw: bytes) -> str:
    value = (message.get("Message-ID") or "").strip().strip("<>")
    if value:
        return value[:255]
    digest = hashlib.sha256(raw[:200000]).hexdigest()
    return f"generated.{digest}"


def _received_at(message):
    value = message.get("Date")
    if value:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.get_current_timezone())
            return parsed
        except (TypeError, ValueError, OverflowError):
            pass
    return timezone.now()


def _ticket_for_message(subject: str, in_reply_to: str, references: str):
    if in_reply_to:
        row = HelpdeskInboundMessage.objects.filter(
            message_id=in_reply_to.strip().strip("<>")
        ).first()
        if row and row.ticket_id:
            return SlaTicket.objects.filter(pk=row.ticket_id).first()
    match = TICKET_NUMBER_RE.search(subject or "")
    if match:
        return SlaTicket.objects.filter(ticket_number__iexact=match.group(0)).first()
    for reference in (references or "").split():
        row = HelpdeskInboundMessage.objects.filter(
            message_id=reference.strip().strip("<>")
        ).first()
        if row and row.ticket_id:
            return SlaTicket.objects.filter(pk=row.ticket_id).first()
    return None


def _record_inbound(
    *,
    message_id: str,
    mailbox: EmailIdentity | None,
    ticket=None,
    sender: str,
    subject: str,
    status: str,
    detail: str = "",
    received_at=None,
) -> HelpdeskInboundMessage:
    return HelpdeskInboundMessage.objects.create(
        message_id=message_id,
        mailbox=mailbox,
        ticket=ticket,
        sender_email=sender[:254],
        subject=(subject or "")[:255],
        status=status,
        detail=detail[:4000],
        received_at=received_at,
    )


def ingest_email(
    mailbox: EmailIdentity,
    raw: bytes,
    *,
    setting: HelpdeskSetting | None = None,
    request=None,
) -> dict:
    """Turn one RFC 822 message into a ticket or a conversation reply.

    Duplicate Message-IDs, autoresponders and bulk mail are ignored. The
    caller is responsible for an active tenant context.
    """

    message = message_from_bytes(raw)
    sender_name, sender_email = parseaddr(message.get("From") or "")
    sender_email = (sender_email or "").strip().lower()
    subject_raw = message.get("Subject") or ""
    subject = _clean_subject(subject_raw)
    message_id = _message_identifier(message, raw)
    received_at = _received_at(message)
    setting = setting or helpdesk_setting(create=True)

    if HelpdeskInboundMessage.objects.filter(message_id=message_id).exists():
        return {"status": "duplicate", "message_id": message_id}

    own_addresses = {
        (mailbox.from_email or "").lower(),
        (mailbox.reply_to or "").lower(),
    }
    auto_submitted = (message.get("Auto-Submitted") or "no").lower()
    precedence = (message.get("Precedence") or "").lower()
    if (
        not sender_email
        or sender_email in own_addresses
        or auto_submitted not in {"", "no"}
        or precedence in {"bulk", "list", "junk"}
        or message.get("X-Autoreply")
    ):
        row = _record_inbound(
            message_id=message_id,
            mailbox=mailbox,
            sender=sender_email,
            subject=subject,
            status="ignored",
            detail="Automated, bulk or self-addressed message.",
            received_at=received_at,
        )
        return {"status": "ignored", "message_id": message_id, "row": row}

    body = extract_body(message)
    if not body:
        body = "(No message body)"
    ticket = _ticket_for_message(
        subject,
        message.get("In-Reply-To") or "",
        message.get("References") or "",
    )
    created = False
    if ticket is None:
        contract = setting.default_contract if setting else None
        if contract is None:
            row = _record_inbound(
                message_id=message_id,
                mailbox=mailbox,
                sender=sender_email,
                subject=subject,
                status="ignored",
                detail="No default SLA contract is configured for the helpdesk mailbox.",
                received_at=received_at,
            )
            return {"status": "ignored", "message_id": message_id, "row": row}
        now = timezone.now()
        with transaction.atomic():
            ticket = SlaTicket.objects.create(
                contract_id=contract.id,
                ticket_number=next_sequence("HD", "pm_sla_tickets", "ticket_number"),
                title=subject,
                description=body,
                requester_name=(sender_name or sender_email)[:150],
                requester_email=sender_email,
                channel="email",
                category="incident",
                priority="medium",
                status="open",
                created_at=now,
            )
            routing = apply_routing(ticket, subject, body, sender_email)
            ticket.response_due_at, ticket.sla_due_at = sla_targets(
                contract, ticket.priority, now
            )
            if ticket.assigned_to:
                ticket.status = "in_progress"
            ticket.save()
            TicketReply.objects.create(
                ticket=ticket,
                author=None,
                body=body,
                channel="email",
                is_internal=False,
            )
        created = True
        _log_event(ticket, "created", f"Created from email {sender_email}.")
        if routing["rules"]:
            _log_event(
                ticket,
                "routing",
                "Applied: " + ", ".join(rule.name for rule in routing["rules"]),
            )
        if routing["notify_client"]:
            notify_ticket_received(ticket, request)
            if ticket.assigned_to:
                notify_agent_assigned(ticket, request)
    else:
        now = timezone.now()
        reply = TicketReply.objects.create(
            ticket=ticket,
            author=None,
            body=body,
            channel="email",
            is_internal=False,
        )
        if ticket.status in {"resolved", "closed"}:
            ticket.status = "in_progress"
            ticket.resolved_at = None
        elif ticket.status == "waiting_client":
            ticket.status = "in_progress"
        if ticket.assigned_to is None and setting and setting.auto_assign:
            ticket.assigned_to = pick_agent()
            if ticket.assigned_to and setting.notify_client:
                notify_agent_assigned(ticket, request)
        ticket.save()
        _log_event(
            ticket,
            "inbound",
            f"Client email from {sender_email} added as reply #{reply.id}.",
        )
        created = False

    try:
        row = _record_inbound(
            message_id=message_id,
            mailbox=mailbox,
            ticket=ticket,
            sender=sender_email,
            subject=subject,
            status="processed",
            detail="Created ticket." if created else "Added reply to existing ticket.",
            received_at=received_at,
        )
    except IntegrityError:
        return {"status": "duplicate", "message_id": message_id}
    return {
        "status": "processed",
        "message_id": message_id,
        "ticket": ticket,
        "created": created,
        "row": row,
    }


# ---------------------------------------------------------------------------
# IMAP polling
# ---------------------------------------------------------------------------
def _connect(identity: EmailIdentity):
    if identity.imap_encryption == "ssl":
        mailbox = imaplib.IMAP4_SSL(
            identity.imap_host, identity.imap_port or 993, timeout=30
        )
    else:
        mailbox = imaplib.IMAP4(
            identity.imap_host, identity.imap_port or 143, timeout=30
        )
        if identity.imap_encryption == "tls":
            mailbox.starttls()
    mailbox.login(identity.imap_username, identity.imap_password_plain)
    mailbox.select(identity.imap_folder or "INBOX")
    return mailbox


def _uid_validity(mailbox) -> int | None:
    value = mailbox.untagged_responses.get("UIDVALIDITY")
    if not value:
        return None
    try:
        return int(value[0])
    except (TypeError, ValueError, IndexError):
        return None


def poll_mailbox(setting: HelpdeskSetting, limit: int = 50) -> dict:
    """Fetch and ingest new messages for one configured mailbox."""

    summary = {"processed": 0, "created": 0, "duplicates": 0, "ignored": 0, "errors": 0}
    identity = setting.mailbox_identity
    if identity is None or not identity.imap_host:
        setting.last_poll_error = "No IMAP mailbox is linked to the helpdesk settings."
        setting.save(update_fields=["last_poll_error", "updated_at"])
        return summary

    mailbox = None
    try:
        mailbox = _connect(identity)
        uid_validity = _uid_validity(mailbox)
        if uid_validity is not None and setting.uid_validity not in (None, uid_validity):
            setting.last_uid = 0
        if uid_validity is not None:
            setting.uid_validity = uid_validity
        criteria = f"UID {setting.last_uid + 1}:*" if setting.last_uid else "UNSEEN"
        status, data = mailbox.uid("search", None, criteria)
        uids = []
        if status == "OK" and data and data[0]:
            uids = sorted(
                int(value)
                for value in data[0].split()
                if value.isdigit() and int(value) > setting.last_uid
            )[:limit]
        for uid in uids:
            status, parts = mailbox.uid("fetch", str(uid), "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next(
                (part[1] for part in parts if isinstance(part, tuple)), None
            )
            if not raw:
                continue
            result = ingest_email(identity, raw, setting=setting)
            if result["status"] == "processed":
                summary["processed"] += 1
                summary["created"] += int(bool(result.get("created")))
            elif result["status"] == "duplicate":
                summary["duplicates"] += 1
            else:
                summary["ignored"] += 1
            setting.last_uid = uid
            setting.save(update_fields=["last_uid", "updated_at"])
        setting.last_polled_at = timezone.now()
        setting.last_poll_error = ""
        setting.save(
            update_fields=[
                "last_polled_at",
                "last_poll_error",
                "last_uid",
                "uid_validity",
                "updated_at",
            ]
        )
    except Exception as exc:  # pragma: no cover - network failure path
        logger.exception("Helpdesk mailbox poll failed")
        summary["errors"] += 1
        setting.last_polled_at = timezone.now()
        setting.last_poll_error = str(exc)[:4000]
        setting.save(
            update_fields=["last_polled_at", "last_poll_error", "updated_at"]
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass
    return summary


def poll_all_mailboxes(limit: int = 50) -> list[dict]:
    results = []
    with all_tenants_context():
        settings_rows = list(
            HelpdeskSetting.objects.filter(
                mailbox_active=True, mailbox_identity__isnull=False
            ).select_related("tenant", "mailbox_identity")
        )
    for row in settings_rows:
        with tenant_context(row.tenant):
            fresh = HelpdeskSetting.objects.filter(pk=row.pk).select_related(
                "mailbox_identity"
            ).first()
            if fresh is None:
                continue
            summary = poll_mailbox(fresh, limit=limit)
            results.append(
                {
                    "tenant": row.tenant.slug,
                    "mailbox": row.mailbox_identity.from_email if row.mailbox_identity else "",
                    **summary,
                }
            )
    return results


__all__ = [
    "helpdesk_setting",
    "ensure_sla_policies",
    "save_sla_policies",
    "sla_targets",
    "mark_breaches",
    "ensure_agent_pool",
    "pick_agent",
    "apply_routing",
    "status_url",
    "survey_url",
    "load_client_ticket",
    "notify_ticket_received",
    "notify_agent_assigned",
    "notify_status_update",
    "send_survey",
    "confirm_resolution",
    "record_survey",
    "ingest_email",
    "poll_mailbox",
    "poll_all_mailboxes",
]
