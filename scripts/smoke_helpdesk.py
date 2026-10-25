"""End-to-end smoke test for helpdesk automation, ingestion and CSAT.

Everything runs inside one transaction that is rolled back at the end.
"""

import os
from datetime import timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from io import StringIO
from unittest import mock

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import EmailIdentity, Employee, SlaContract, SlaTicket
from apps.core.tenancy import default_tenant, tenant_context
from apps.helpdesk import services
from apps.helpdesk.models import (
    HelpdeskAgent,
    HelpdeskEvent,
    HelpdeskInboundMessage,
    HelpdeskRoutingRule,
    HelpdeskSetting,
    HelpdeskSlaPolicy,
    HelpdeskSurveyResponse,
    TicketReply,
)

MAILBOX = "helpdesk.smoke@example.com"


class Rollback(Exception):
    pass


class EmptyIMAP:
    """Minimal IMAP stand-in that reports no new messages."""

    untagged_responses = {"UIDVALIDITY": [b"77"]}

    def __init__(self, messages=None):
        self.messages = messages or {}

    def login(self, username, password):
        return "OK", [b"logged in"]

    def select(self, folder):
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(self.messages))]
        if command == "fetch":
            uid = int(args[0])
            raw = self.messages.get(uid)
            if raw is None:
                return "NO", [b"missing"]
            return "OK", [(b"1 (BODY[] {%d}" % len(raw), raw), b")"]
        return "NO", [b"unsupported"]

    def logout(self):
        return "BYE", [b"bye"]


def build_email(*, sender, subject, body, message_id, in_reply_to="", auto_submitted="", precedence=""):
    message = EmailMessage()
    message["From"] = sender
    message["To"] = MAILBOX
    message["Subject"] = subject
    message["Date"] = format_datetime(timezone.now())
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if auto_submitted:
        message["Auto-Submitted"] = auto_submitted
    if precedence:
        message["Precedence"] = precedence
    message.set_content(body)
    return message.as_bytes()


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")
guest = TestClient(headers={"host": "localhost"})
tenant = default_tenant()
failures = []
outbox = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


def mail_configured(module="system"):
    return True


def send_mail(module, subject, body, recipients, **kwargs):
    outbox.append(
        {
            "module": module,
            "subject": subject,
            "body": body,
            "recipients": list(recipients),
        }
    )
    return len(recipients or [])


def html(response):
    return response.content.decode("utf-8", errors="replace")


original_mail_configured = services.mail_is_configured
original_send_mail = services.send_module_email
services.mail_is_configured = mail_configured
services.send_module_email = send_mail

try:
    with transaction.atomic():
        with tenant_context(tenant):
            employee = (
                Employee.objects.filter(status="active").order_by("id").first()
            )
            contract = SlaContract.objects.filter(status="active").order_by("id").first()
            if contract is None:
                contract = SlaContract.objects.create(
                    contract_number="HD-SMOKE",
                    title="Smoke support retainer",
                    client_name="Smoke Client",
                    start_date=timezone.localdate(),
                    response_target_hours=8,
                    resolution_target_hours=48,
                    status="active",
                )
            identity = EmailIdentity.objects.filter(module="helpdesk").first()
            if identity is None:
                identity = EmailIdentity.objects.create(
                    name="Smoke Helpdesk Mailbox",
                    module="helpdesk",
                    from_name="Smoke Support",
                    from_email=MAILBOX,
                    reply_to=MAILBOX,
                    smtp_host="smtp.example.com",
                    smtp_username=MAILBOX,
                    imap_host="imap.example.com",
                    imap_username=MAILBOX,
                    is_default=True,
                    is_active=True,
                )
            setting = services.helpdesk_setting(create=True)
            contract_id, identity_id, employee_id = contract.id, identity.id, employee.id

        check("fixture employee exists", employee_id is not None)

        # ------------------------------------------------------------------
        # Staff automation settings
        # ------------------------------------------------------------------
        response = client.get("/helpdesk/settings")
        body = html(response)
        check("GET helpdesk settings", response.status_code == 200, response.status_code)
        check("settings shows unlinked mailbox", "No shared mailbox is linked yet" in body)
        check("settings shows SLA section", "Priority SLA targets" in body)

        response = client.post(
            "/helpdesk/settings/mailbox",
            {
                "mailbox_identity": identity_id,
                "default_contract": contract_id,
                "status_link_days": "30",
                "mailbox_active": "on",
                "auto_reply": "on",
                "auto_assign": "on",
                "notify_client": "on",
            },
        )
        check("POST mailbox settings", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            setting = services.helpdesk_setting()
            check(
                "mailbox linked",
                setting.mailbox_identity_id == identity_id
                and setting.default_contract_id == contract_id,
            )
            check(
                "mailbox automation flags",
                setting.mailbox_active
                and setting.auto_reply
                and setting.auto_assign
                and setting.notify_client,
            )

        response = client.post(
            "/helpdesk/settings/sla",
            {
                "critical_response": "20",
                "critical_resolution": "600",
                "high_response": "45",
                "high_resolution": "1200",
                "medium_response": "240",
                "medium_resolution": "2880",
                "low_response": "480",
                "low_resolution": "4320",
            },
        )
        check("POST SLA policies", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            policies = {
                row.priority: (row.response_minutes, row.resolution_minutes)
                for row in HelpdeskSlaPolicy.objects.all()
            }
            check("SLA policy count", len(policies) == 4, policies)
            check("critical SLA saved", policies.get("critical") == (20, 600), policies)
            check("high SLA saved", policies.get("high") == (45, 1200), policies)
            check(
                "SLA rows tenant scoped",
                all(
                    row.tenant_id == tenant.id
                    for row in HelpdeskSlaPolicy.objects.all()
                ),
            )

        response = client.post(
            "/helpdesk/settings/survey",
            {
                "survey_enabled": "on",
                "survey_title": "Smoke CSAT",
                "survey_intro": "Tell us how the fix went.",
                "survey_rating_label": "Rate the resolution",
                "survey_comment_label": "Anything else?",
                "survey_comment_enabled": "on",
                "survey_thanks": "Thanks for helping us improve.",
            },
        )
        check("POST survey settings", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            setting = services.helpdesk_setting()
            check(
                "survey copy saved",
                setting.survey_enabled
                and setting.survey_title == "Smoke CSAT"
                and setting.survey_thanks == "Thanks for helping us improve.",
            )
            agent_count = HelpdeskAgent.objects.count()
            active_employees = Employee.objects.filter(status="active").count()
            check("agent pool seeded", agent_count == active_employees, agent_count)
            check(
                "agent pool tenant scoped",
                all(row.tenant_id == tenant.id for row in HelpdeskAgent.objects.all()),
            )

        response = client.get("/helpdesk/settings")
        body = html(response)
        check("GET settings after save", response.status_code == 200)
        check("settings shows survey title", "Smoke CSAT" in body)
        check("settings shows agent pool", "Smoke Helpdesk Mailbox" in body)

        # ------------------------------------------------------------------
        # Routing rules
        # ------------------------------------------------------------------
        response = client.post(
            "/helpdesk/settings/routing/new",
            {
                "name": "VIP outage",
                "order": "5",
                "is_active": "on",
                "match_channel": "email",
                "keywords": "outage",
                "from_contains": "vip.example",
                "set_priority": "critical",
                "set_category": "incident",
                "assignee": employee_id,
                "round_robin": "",
                "notify_client": "on",
                "stop_processing": "on",
            },
        )
        check("POST routing rule", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            rule = HelpdeskRoutingRule.objects.filter(name="VIP outage").first()
            check("routing rule saved", rule is not None)
            check(
                "routing rule outcome",
                rule
                and rule.assignee_id == employee_id
                and rule.set_priority == "critical"
                and rule.match_channel == "email",
            )

        response = client.get("/helpdesk/settings/routing")
        check(
            "GET routing rules",
            response.status_code == 200 and "VIP outage" in html(response),
            response.status_code,
        )

        response = client.get("/helpdesk/tickets/new")
        check("GET new ticket form", response.status_code == 200, response.status_code)

        # ------------------------------------------------------------------
        # Staff ticket flow
        # ------------------------------------------------------------------
        outbox.clear()
        response = client.post(
            "/helpdesk/tickets/new",
            {
                "contract": contract_id,
                "project": "",
                "title": "VPN drops every hour",
                "description": "The tunnel restarts repeatedly for the finance team.",
                "requester_name": "Smoke Client",
                "requester_email": "smoke.client@example.com",
                "requester_phone": "+254700000001",
                "channel": "web",
                "category": "incident",
                "priority": "high",
                "assigned_to": "",
            },
        )
        check("POST create ticket", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            ticket = SlaTicket.objects.order_by("-id").first()
            ticket_id = ticket.id
            check("ticket auto assigned", ticket.assigned_to is not None, ticket.assigned_to)
            check("ticket in progress", ticket.status == "in_progress", ticket.status)
            check("ticket first response open", ticket.first_response_at is None)
            response_due = (
                ticket.response_due_at - ticket.created_at
                if ticket.response_due_at and ticket.created_at
                else None
            )
            resolution_due = (
                ticket.sla_due_at - ticket.created_at
                if ticket.sla_due_at and ticket.created_at
                else None
            )
            check(
                "ticket high response SLA",
                response_due is not None
                and abs(response_due - timedelta(minutes=45)) < timedelta(seconds=5),
                response_due,
            )
            check(
                "ticket high resolution SLA",
                resolution_due is not None
                and abs(resolution_due - timedelta(minutes=1200)) < timedelta(
                    seconds=5
                ),
                resolution_due,
            )
            check(
                "ticket created event",
                HelpdeskEvent.objects.filter(
                    ticket_id=ticket_id, kind="created"
                ).exists(),
            )
            ticket_number = ticket.ticket_number
        check("confirmation email sent", len(outbox) == 2, len(outbox))
        check(
            "confirmation subject",
            outbox
            and ticket_number in outbox[0]["subject"]
            and "received" in outbox[0]["subject"].lower(),
            outbox[0]["subject"] if outbox else "",
        )
        check(
            "confirmation recipient",
            outbox and outbox[0]["recipients"] == ["smoke.client@example.com"],
            outbox[0]["recipients"] if outbox else "",
        )
        check(
            "assignment email names agent",
            len(outbox) > 1 and "handling" in outbox[1]["subject"].lower(),
            outbox[1]["subject"] if len(outbox) > 1 else "",
        )

        response = client.get(f"/helpdesk/tickets/{ticket_id}")
        body = html(response)
        check("GET ticket detail", response.status_code == 200, response.status_code)
        check("ticket detail timeline", "Automation log" in body)
        check("ticket detail client updates", "Client updates" in body)
        check("ticket detail shows created event", "Ticket logged" in body)

        outbox.clear()
        response = client.post(
            f"/helpdesk/tickets/{ticket_id}/reply",
            {
                "body": "We are investigating the tunnel resets.",
                "channel": "web",
                "is_internal": "",
                "notify_client": "on",
            },
        )
        check("POST ticket reply", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            ticket = SlaTicket.objects.get(pk=ticket_id)
            check("reply stored", TicketReply.objects.filter(ticket_id=ticket_id).count() == 1)
            check("reply stamps first response", ticket.first_response_at is not None)
            check(
                "reply event logged",
                HelpdeskEvent.objects.filter(ticket_id=ticket_id, kind="reply").exists(),
            )
        check("reply email sent", len(outbox) == 1, len(outbox))
        check(
            "reply email links status page",
            outbox and f"/helpdesk/status/{ticket_id}/" in outbox[0]["body"],
        )

        # ------------------------------------------------------------------
        # Round robin
        # ------------------------------------------------------------------
        with tenant_context(tenant):
            agents = list(HelpdeskAgent.objects.filter(is_active=True).order_by("id"))
            picks = [services.pick_agent() for _ in range(min(4, len(agents)))]
            check("round robin returns agents", all(picks), picks)
            if len(agents) >= 2:
                check("round robin rotates", len(set(picks[:2])) == 2, picks)
            check(
                "round robin advances cursor",
                all(
                    row.last_assigned_at is not None
                    for row in HelpdeskAgent.objects.filter(
                        employee_id__in=[value for value in picks if value]
                    )
                ),
            )

        # ------------------------------------------------------------------
        # Inbound email ingestion
        # ------------------------------------------------------------------
        raw_new = build_email(
            sender="VIP Client <client@vip.example>",
            subject="Network outage at HQ",
            body="Our whole office is offline. Please help.",
            message_id="<smoke-ingest-1@example.com>",
        )
        outbox.clear()
        with tenant_context(tenant):
            setting = services.helpdesk_setting()
            identity = EmailIdentity.objects.get(pk=identity_id)
            result = services.ingest_email(identity, raw_new, setting=setting)
            check("ingest creates ticket", result["status"] == "processed" and result["created"], result)
            ingested = result["ticket"]
            ingest_id = ingested.id
            check("ingest subject cleaned", ingested.title == "Network outage at HQ", ingested.title)
            check("ingest uses email channel", ingested.channel == "email", ingested.channel)
            check(
                "routing rule applied",
                ingested.priority == "critical"
                and ingested.category == "incident"
                and ingested.assigned_to == employee_id,
                (ingested.priority, ingested.category, ingested.assigned_to),
            )
            check("inbound ticket in progress", ingested.status == "in_progress", ingested.status)
            check(
                "inbound reply stored",
                TicketReply.objects.filter(ticket_id=ingest_id).count() == 1,
            )
            check(
                "inbound row processed",
                HelpdeskInboundMessage.objects.filter(
                    message_id="smoke-ingest-1@example.com", status="processed"
                ).exists(),
            )
            check(
                "routing event logged",
                HelpdeskEvent.objects.filter(ticket_id=ingest_id, kind="routing").exists(),
            )
            check(
                "inbound confirmation logged",
                HelpdeskEvent.objects.filter(
                    ticket_id=ingest_id, kind="confirmation_sent"
                ).exists(),
            )
            response_due = ingested.response_due_at - ingested.created_at
            check(
                "ingest critical response SLA",
                abs(response_due - timedelta(minutes=20)) < timedelta(seconds=5),
                response_due,
            )
        check("ingest sends two client emails", len(outbox) == 2, len(outbox))

        with tenant_context(tenant):
            identity = EmailIdentity.objects.get(pk=identity_id)
            setting = services.helpdesk_setting()
            duplicate = services.ingest_email(identity, raw_new, setting=setting)
            check("duplicate message ignored", duplicate["status"] == "duplicate", duplicate)
            check(
                "duplicate did not duplicate rows",
                HelpdeskInboundMessage.objects.filter(
                    message_id="smoke-ingest-1@example.com"
                ).count()
                == 1,
            )

            auto_raw = build_email(
                sender="Robot <robot@example.com>",
                subject="Out of office",
                body="I am away.",
                message_id="<smoke-auto-1@example.com>",
                auto_submitted="auto-replied",
            )
            auto = services.ingest_email(identity, auto_raw, setting=setting)
            check("auto reply ignored", auto["status"] == "ignored", auto)
            check(
                "ignored message retained",
                HelpdeskInboundMessage.objects.filter(
                    message_id="smoke-auto-1@example.com", status="ignored"
                ).exists(),
            )

            ticket = SlaTicket.objects.get(pk=ingest_id)
            ticket.status = "resolved"
            ticket.resolved_at = timezone.now()
            ticket.save(update_fields=["status", "resolved_at", "updated_at"])
            reply_raw = build_email(
                sender="VIP Client <client@vip.example>",
                subject=f"Re: [{ticket.ticket_number}] Network outage at HQ",
                body="Any update on this?",
                message_id="<smoke-ingest-2@example.com>",
                in_reply_to="<smoke-ingest-1@example.com>",
            )
            threaded = services.ingest_email(identity, reply_raw, setting=setting)
            check(
                "threaded reply processed",
                threaded["status"] == "processed" and not threaded["created"],
                threaded,
            )
            check("threaded reply same ticket", threaded["ticket"].id == ingest_id)
            ticket = SlaTicket.objects.get(pk=ingest_id)
            check("client reply reopens ticket", ticket.status == "in_progress", ticket.status)
            check("client reply clears resolved", ticket.resolved_at is None)
            check(
                "threaded reply stored",
                TicketReply.objects.filter(ticket_id=ingest_id).count() == 2,
            )
            check(
                "threaded inbound event",
                HelpdeskEvent.objects.filter(ticket_id=ingest_id, kind="inbound").exists(),
            )

            subject_raw = build_email(
                sender="VIP Client <client@vip.example>",
                subject=f"Re: [{ticket.ticket_number}] Network outage at HQ",
                body="Following up by email subject only.",
                message_id="<smoke-ingest-3@example.com>",
            )
            subject_thread = services.ingest_email(identity, subject_raw, setting=setting)
            check(
                "subject threading works",
                subject_thread["status"] == "processed"
                and not subject_thread["created"]
                and subject_thread["ticket"].id == ingest_id,
                subject_thread,
            )
            check(
                "subject reply stored",
                TicketReply.objects.filter(ticket_id=ingest_id).count() == 3,
            )

        # ------------------------------------------------------------------
        # IMAP polling and management command
        # ------------------------------------------------------------------
        poll_raw = build_email(
            sender="Poll Client <poll.client@example.com>",
            subject="Printer offline",
            body="The shared printer is jammed.",
            message_id="<smoke-poll-1@example.com>",
        )
        fake = EmptyIMAP({7: poll_raw})
        with tenant_context(tenant):
            setting = services.helpdesk_setting()
            identity = EmailIdentity.objects.get(pk=identity_id)
            EmailIdentity.objects.filter(pk=identity_id).update(
                imap_host="imap.example.com", imap_username=MAILBOX
            )
            with mock.patch.object(services, "_connect", return_value=fake):
                summary = services.poll_mailbox(setting, limit=10)
            check(
                "poll summary",
                summary["processed"] == 1 and summary["created"] == 1 and not summary["errors"],
                summary,
            )
            setting.refresh_from_db()
            check("poll stores cursor", setting.last_uid == 7, setting.last_uid)
            check("poll stores uidvalidity", setting.uid_validity == 77, setting.uid_validity)
            check("poll stamps last polled", setting.last_polled_at is not None)
            polled = SlaTicket.objects.filter(title="Printer offline").first()
            check("poll creates ticket", polled is not None)
            check(
                "poll ticket assigned",
                polled is not None and polled.assigned_to is not None,
                getattr(polled, "assigned_to", None),
            )
            polled_id = polled.id if polled else None

        buffer = StringIO()
        with mock.patch.object(services, "_connect", return_value=EmptyIMAP()):
            call_command(
                "poll_helpdesk_mailbox",
                "--tenant",
                tenant.slug,
                "--limit",
                "5",
                stdout=buffer,
            )
        check(
            "poll command reports summary",
            f"[{tenant.slug}] processed=0" in buffer.getvalue(),
            buffer.getvalue().strip(),
        )

        try:
            call_command("poll_helpdesk_mailbox", "--tenant", "missing-smoke-tenant")
            check("poll command rejects unknown tenant", False)
        except CommandError as exc:
            check("poll command rejects unknown tenant", "Unknown workspace" in str(exc), exc)

        buffer = StringIO()
        with mock.patch.object(
            services, "_connect", side_effect=RuntimeError("smoke offline")
        ), mock.patch.object(services.logger, "exception"):
            try:
                call_command(
                    "poll_helpdesk_mailbox",
                    "--tenant",
                    tenant.slug,
                    "--limit",
                    "5",
                    stdout=buffer,
                )
                check("poll command surfaces failures", False)
            except CommandError as exc:
                check(
                    "poll command surfaces failures",
                    "smoke offline" in str(exc),
                    str(exc),
                )
        with tenant_context(tenant):
            setting = services.helpdesk_setting()
            check("poll failure stored", "smoke offline" in setting.last_poll_error)
        response = client.get("/helpdesk/settings")
        check("settings shows poll failure", "smoke offline" in html(response))

        # ------------------------------------------------------------------
        # Resolution, survey and signed client portal
        # ------------------------------------------------------------------
        with tenant_context(tenant):
            ticket = SlaTicket.objects.get(pk=ticket_id)
            assignee = ticket.assigned_to or ""
            ticket_number = ticket.ticket_number
        outbox.clear()
        response = client.post(
            f"/helpdesk/tickets/{ticket_id}/update",
            {
                "status": "resolved",
                "priority": "high",
                "assigned_to": assignee,
                "note": "Tunnel MTU corrected.",
                "notify_client": "on",
            },
        )
        check("POST resolve ticket", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            ticket = SlaTicket.objects.get(pk=ticket_id)
            check("ticket resolved", ticket.status == "resolved", ticket.status)
            check("resolved timestamp set", ticket.resolved_at is not None)
            check("survey marked sent", ticket.survey_sent_at is not None)
            check(
                "update event logged",
                HelpdeskEvent.objects.filter(ticket_id=ticket_id, kind="updated").exists(),
            )
            check(
                "status email logged",
                HelpdeskEvent.objects.filter(ticket_id=ticket_id, kind="status_sent").exists(),
            )
            check(
                "survey email logged",
                HelpdeskEvent.objects.filter(ticket_id=ticket_id, kind="survey_sent").exists(),
            )
        subjects = [row["subject"] for row in outbox]
        check("resolve sends status email", any(ticket_number in s and "Update" in s for s in subjects), subjects)
        check(
            "resolve sends survey email",
            any("How did we do?" in subject for subject in subjects),
            subjects,
        )

        with tenant_context(tenant):
            ticket = SlaTicket.objects.get(pk=ticket_id)
            token = services.status_token(ticket)
            status_path = f"/helpdesk/status/{ticket_id}/{token}/"

        response = guest.get(status_path)
        body = html(response)
        check("public status without login", response.status_code == 200, response.status_code)
        check("public status shows ticket", ticket_number in body)
        check("public status offers confirmation", "Confirm resolution" in body)
        check("public status shows survey title", "Smoke CSAT" in body)
        check("public status marks secure link", "Secure client link" in body)

        response = guest.post(status_path, {"action": "confirm"})
        check("public confirm redirects", response.status_code == 302, response.status_code)
        check("public confirm redirect flag", "confirmed=1" in response.headers.get("Location", ""))
        with tenant_context(tenant):
            ticket = SlaTicket.objects.get(pk=ticket_id)
            check("client confirmation stored", ticket.client_confirmed_at is not None)
            check("confirmed ticket closed", ticket.status == "closed", ticket.status)
            check(
                "confirmation event logged",
                HelpdeskEvent.objects.filter(
                    ticket_id=ticket_id, kind="client_confirmed"
                ).exists(),
            )

        response = guest.get(f"{status_path}?confirmed=1")
        check(
            "public confirmation notice",
            "confirmation has been recorded" in html(response),
        )

        response = guest.post(status_path, {"action": "survey", "rating": "9"})
        check(
            "invalid rating rejected",
            response.status_code == 302
            and "survey=invalid" in response.headers.get("Location", ""),
            response.headers.get("Location", ""),
        )
        with tenant_context(tenant):
            check(
                "invalid rating not stored",
                not HelpdeskSurveyResponse.objects.filter(ticket_id=ticket_id).exists(),
            )

        response = guest.post(
            status_path,
            {"action": "survey", "rating": "5", "comment": "Fast and clear."},
        )
        check(
            "survey submission redirects",
            response.status_code == 302
            and "survey=done" in response.headers.get("Location", ""),
            response.headers.get("Location", ""),
        )
        with tenant_context(tenant):
            survey = HelpdeskSurveyResponse.objects.filter(ticket_id=ticket_id).first()
            check(
                "survey stored",
                survey is not None and survey.rating == 5 and survey.comment == "Fast and clear.",
                survey,
            )
            check(
                "survey event logged",
                HelpdeskEvent.objects.filter(
                    ticket_id=ticket_id, kind="survey_response"
                ).exists(),
            )
        response = guest.get(f"{status_path}?survey=done")
        body = html(response)
        check("survey thanks shown", "Thanks for helping us improve." in body)
        check("survey rating shown", "You rated the resolution" in body and "5/5" in body)

        response = guest.get(f"/helpdesk/status/{ticket_id}/not-a-valid-token/")
        check("invalid token rejected", response.status_code == 400, response.status_code)
        check("invalid token page", "invalid or has expired" in html(response))

        with tenant_context(tenant):
            other_token = services.status_token(SlaTicket.objects.get(pk=ingest_id))
        response = guest.get(f"/helpdesk/status/{ticket_id}/{other_token}/")
        check("mismatched token rejected", response.status_code == 400, response.status_code)

        # ------------------------------------------------------------------
        # Manual client notifications and survey resend guard
        # ------------------------------------------------------------------
        with tenant_context(tenant):
            ingested = SlaTicket.objects.get(pk=ingest_id)
            ingest_number = ingested.ticket_number
        outbox.clear()
        response = client.post(f"/helpdesk/tickets/{ingest_id}/notify", {"action": "status"})
        check("POST manual status email", response.status_code == 302, response.status_code)
        check("manual status email sent", len(outbox) == 1, len(outbox))
        check(
            "manual status email subject",
            outbox and ingest_number in outbox[0]["subject"],
            outbox[0]["subject"] if outbox else "",
        )

        with tenant_context(tenant):
            polled = SlaTicket.objects.get(pk=polled_id)
            polled_assignee = polled.assigned_to or ""
        response = client.post(
            f"/helpdesk/tickets/{polled_id}/update",
            {
                "status": "resolved",
                "priority": "medium",
                "assigned_to": polled_assignee,
                "note": "",
                "notify_client": "",
            },
        )
        check("POST resolve polled ticket", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            polled = SlaTicket.objects.get(pk=polled_id)
            check("polled ticket resolved", polled.status == "resolved" and polled.survey_sent_at)
        before = len(outbox)
        response = client.post(f"/helpdesk/tickets/{polled_id}/notify", {"action": "survey"})
        check("POST resend survey", response.status_code == 302, response.status_code)
        check("resend sends survey again", len(outbox) == before + 1, len(outbox) - before)
        check(
            "resend survey subject",
            outbox and "How did we do?" in outbox[-1]["subject"],
            outbox[-1]["subject"] if outbox else "",
        )

        before = len(outbox)
        response = client.post(f"/helpdesk/tickets/{ticket_id}/notify", {"action": "survey"})
        check("survey response blocks resend", response.status_code == 302)
        check("no email after survey response", len(outbox) == before, len(outbox) - before)

        # ------------------------------------------------------------------
        # Validation and dashboard
        # ------------------------------------------------------------------
        with tenant_context(tenant):
            rules_before = HelpdeskRoutingRule.objects.count()
        response = client.post(
            "/helpdesk/settings/routing/new",
            {
                "name": "Invalid rule",
                "order": "10",
                "is_active": "on",
                "match_channel": "",
                "keywords": "",
                "from_contains": "",
                "set_priority": "",
                "set_category": "",
                "assignee": employee_id,
                "round_robin": "on",
                "notify_client": "on",
                "stop_processing": "on",
            },
        )
        check(
            "routing conflict rejected",
            response.status_code == 200
            and "Choose a fixed assignee or round robin" in html(response),
            response.status_code,
        )
        with tenant_context(tenant):
            check(
                "invalid rule not saved",
                HelpdeskRoutingRule.objects.count() == rules_before,
            )

        response = client.post(
            "/helpdesk/settings/mailbox",
            {
                "mailbox_identity": "",
                "default_contract": contract_id,
                "status_link_days": "30",
                "mailbox_active": "on",
                "auto_reply": "on",
                "auto_assign": "on",
                "notify_client": "on",
            },
        )
        check(
            "mailbox requires identity",
            response.status_code == 200
            and "Link a mailbox before enabling polling." in html(response),
            response.status_code,
        )

        response = client.get("/helpdesk/dashboard")
        body = html(response)
        check("GET helpdesk dashboard", response.status_code == 200, response.status_code)
        check("dashboard shows CSAT", "CSAT" in body)
        check("dashboard automation link", "/helpdesk/settings" in body)

        response = client.get(f"/helpdesk/tickets/{ticket_id}")
        body = html(response)
        check("ticket detail survey state", "Resend survey" in body)
        check("ticket detail automation events", "Survey Response" in body)

        response = client.get("/helpdesk/settings")
        body = html(response)
        check("settings lists recent inbound", "Recent inbound mail" in body)
        check("settings lists routing preview", "VIP outage" in body)

        raise Rollback
except Rollback:
    pass
finally:
    services.mail_is_configured = original_mail_configured
    services.send_module_email = original_send_mail

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
