"""Helpdesk conversation thread, automation and client-survey models."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel

PRIORITY_CHOICES = [
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
    ("critical", "Critical"),
]
CHANNEL_CHOICES = [
    ("web", "Web / Portal"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("walk_in", "Walk-in"),
    ("internal", "Internal"),
]
CATEGORY_CHOICES = [
    ("incident", "Incident"),
    ("service_request", "Service Request"),
    ("problem", "Problem"),
    ("change", "Change"),
]

# Fallback response/resolution minutes per priority. Stored policies override
# these, while the UI seeds them as editable rows.
SLA_DEFAULTS = {
    "critical": {"response": 30, "resolution": 720},
    "high": {"response": 60, "resolution": 1440},
    "medium": {"response": 240, "resolution": 2880},
    "low": {"response": 480, "resolution": 4320},
}


class TicketReply(TenantScopedModel):
    ticket = models.ForeignKey(
        "core.SlaTicket", on_delete=models.CASCADE, related_name="helpdesk_replies"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    body = models.TextField()
    channel = models.CharField(
        max_length=12,
        choices=[
            ("web", "Web / Portal"),
            ("email", "Email"),
            ("phone", "Phone"),
            ("internal", "Internal note"),
        ],
        default="web",
    )
    is_internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "helpdesk_ticket_replies"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"Reply on ticket {self.ticket_id}"


class HelpdeskSetting(TenantScopedModel):
    """Per-workspace mailbox, assignment, SLA-link and survey configuration."""

    mailbox_identity = models.ForeignKey(
        "core.EmailIdentity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    mailbox_active = models.BooleanField(default=False)
    auto_reply = models.BooleanField(default=True)
    auto_assign = models.BooleanField(default=True)
    notify_client = models.BooleanField(default=True)
    default_contract = models.ForeignKey(
        "core.SlaContract",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    status_link_days = models.PositiveIntegerField(default=30)

    survey_enabled = models.BooleanField(default=True)
    survey_title = models.CharField(max_length=150, default="How did we do?")
    survey_intro = models.TextField(
        default=(
            "Your feedback helps us improve the support we provide. "
            "It takes less than a minute."
        )
    )
    survey_rating_label = models.CharField(
        max_length=200, default="How satisfied are you with the resolution?"
    )
    survey_comment_label = models.CharField(
        max_length=200, default="Anything else you want to share?"
    )
    survey_comment_enabled = models.BooleanField(default=True)
    survey_thanks = models.TextField(
        default="Thank you — your feedback has been recorded."
    )

    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_poll_error = models.TextField(blank=True)
    last_uid = models.BigIntegerField(default=0)
    uid_validity = models.BigIntegerField(null=True, blank=True)
    updated_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "helpdesk_settings"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"], name="uniq_helpdesk_setting_tenant"
            )
        ]

    def __str__(self):
        return f"Helpdesk settings for tenant {self.tenant_id}"


class HelpdeskSlaPolicy(TenantScopedModel):
    """Priority-specific response and resolution targets, in minutes."""

    priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, db_index=True)
    response_minutes = models.PositiveIntegerField(default=240)
    resolution_minutes = models.PositiveIntegerField(default=2880)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "helpdesk_sla_policies"
        ordering = ["priority"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "priority"], name="uniq_helpdesk_sla_priority"
            )
        ]

    def __str__(self):
        return f"{self.get_priority_display()} SLA"


class HelpdeskAgent(TenantScopedModel):
    """Eligibility and round-robin cursor for one support employee."""

    employee_id = models.IntegerField(db_index=True)
    is_active = models.BooleanField(default=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "helpdesk_agents"
        ordering = ["last_assigned_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "employee_id"], name="uniq_helpdesk_agent_employee"
            )
        ]

    def __str__(self):
        return f"Helpdesk agent {self.employee_id}"


class HelpdeskRoutingRule(TenantScopedModel):
    """Keyword/sender rule that sets routing, priority or category."""

    name = models.CharField(max_length=120)
    order = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    match_channel = models.CharField(
        max_length=12, choices=CHANNEL_CHOICES, blank=True
    )
    keywords = models.TextField(
        blank=True, help_text="Comma-separated words matched against subject and body."
    )
    from_contains = models.CharField(
        max_length=255,
        blank=True,
        help_text="Comma-separated sender emails or domains.",
    )
    set_priority = models.CharField(max_length=8, choices=PRIORITY_CHOICES, blank=True)
    set_category = models.CharField(max_length=16, choices=CATEGORY_CHOICES, blank=True)
    assignee_id = models.IntegerField(null=True, blank=True)
    round_robin = models.BooleanField(default=False)
    notify_client = models.BooleanField(default=True)
    stop_processing = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "helpdesk_routing_rules"
        ordering = ["order", "id"]

    def __str__(self):
        return self.name


class HelpdeskInboundMessage(TenantScopedModel):
    """Processed inbound email, retained for deduplication and threading."""

    message_id = models.CharField(max_length=255, db_index=True)
    mailbox = models.ForeignKey(
        "core.EmailIdentity",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    ticket = models.ForeignKey(
        "core.SlaTicket",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_messages",
    )
    sender_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=12,
        choices=[
            ("processed", "Processed"),
            ("duplicate", "Duplicate"),
            ("ignored", "Ignored"),
            ("error", "Error"),
        ],
        default="processed",
    )
    detail = models.TextField(blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "helpdesk_inbound_messages"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "message_id"],
                name="uniq_helpdesk_inbound_message",
            )
        ]

    def __str__(self):
        return self.message_id


class HelpdeskEvent(TenantScopedModel):
    """Automation and client-portal audit trail for a ticket."""

    ticket = models.ForeignKey(
        "core.SlaTicket",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="helpdesk_events",
    )
    kind = models.CharField(max_length=40, db_index=True)
    detail = models.TextField(blank=True)
    actor_user_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "helpdesk_events"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.kind} on ticket {self.ticket_id}"

    @property
    def kind_label(self) -> str:
        return self.kind.replace("_", " ").title()


class HelpdeskSurveyResponse(TenantScopedModel):
    """One post-resolution satisfaction response per ticket."""

    ticket = models.OneToOneField(
        "core.SlaTicket", on_delete=models.CASCADE, related_name="survey_response"
    )
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    ip_address = models.CharField(max_length=45, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = "helpdesk_survey_responses"

    def __str__(self):
        return f"Survey {self.rating}/5 for ticket {self.ticket_id}"


__all__ = [
    "TicketReply",
    "HelpdeskSetting",
    "HelpdeskSlaPolicy",
    "HelpdeskAgent",
    "HelpdeskRoutingRule",
    "HelpdeskInboundMessage",
    "HelpdeskEvent",
    "HelpdeskSurveyResponse",
    "PRIORITY_CHOICES",
    "CHANNEL_CHOICES",
    "CATEGORY_CHOICES",
    "SLA_DEFAULTS",
]
