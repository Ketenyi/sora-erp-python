"""Seed the client-facing helpdesk email templates."""

from django.db import migrations
from django.utils import timezone

TEMPLATES = [
    {
        "template_key": "helpdesk_ticket_received",
        "name": "Helpdesk — request received",
        "subject": "[{{ ticket_number }}] We received your request",
        "variables": (
            "ticket_number, subject, requester_name, priority, response_due, "
            "status_url, company_name"
        ),
        "body": (
            "Hi {{ requester_name }},\n\n"
            "Thank you for contacting {{ company_name }} support. We have logged your request:\n\n"
            "Ticket: {{ ticket_number }}\n"
            "Subject: {{ subject }}\n"
            "Priority: {{ priority }}\n"
            "Target response: {{ response_due }}\n\n"
            "Follow progress and confirm resolution at any time:\n{{ status_url }}\n\n"
            "Kind regards,\n{{ company_name }} Support"
        ),
    },
    {
        "template_key": "helpdesk_agent_assigned",
        "name": "Helpdesk — technician assigned",
        "subject": "[{{ ticket_number }}] {{ agent_name }} is handling your request",
        "variables": (
            "ticket_number, subject, requester_name, agent_name, priority, "
            "response_due, status_url, company_name"
        ),
        "body": (
            "Hi {{ requester_name }},\n\n"
            "{{ agent_name }} has been assigned to your support request {{ ticket_number }}.\n\n"
            "Priority: {{ priority }}\n"
            "Target response: {{ response_due }}\n\n"
            "Track progress or send an update:\n{{ status_url }}\n\n"
            "Kind regards,\n{{ company_name }} Support"
        ),
    },
    {
        "template_key": "helpdesk_status_update",
        "name": "Helpdesk — status update",
        "subject": "[{{ ticket_number }}] Update: {{ status }}",
        "variables": (
            "ticket_number, subject, requester_name, status, priority, agent_name, "
            "note, status_url, company_name"
        ),
        "body": (
            "Hi {{ requester_name }},\n\n"
            "Your support request {{ ticket_number }} is now: {{ status }}.\n\n"
            "{{ note }}\n\n"
            "View the latest status and conversation:\n{{ status_url }}\n\n"
            "Kind regards,\n{{ company_name }} Support"
        ),
    },
    {
        "template_key": "helpdesk_satisfaction_survey",
        "name": "Helpdesk — satisfaction survey",
        "subject": "How did we do? [{{ ticket_number }}]",
        "variables": (
            "ticket_number, subject, requester_name, agent_name, survey_url, "
            "status_url, company_name"
        ),
        "body": (
            "Hi {{ requester_name }},\n\n"
            "Your support request {{ ticket_number }} has been marked resolved.\n\n"
            "We would love your feedback — it takes less than a minute:\n{{ survey_url }}\n\n"
            "If the issue is not fully resolved, review the ticket and let us know:\n"
            "{{ status_url }}\n\n"
            "Kind regards,\n{{ company_name }} Support"
        ),
    },
]


def seed_templates(apps, schema_editor):
    EmailTemplate = apps.get_model("core", "EmailTemplate")
    now = timezone.now()
    for spec in TEMPLATES:
        EmailTemplate.objects.get_or_create(
            template_key=spec["template_key"],
            defaults={
                "name": spec["name"],
                "subject": spec["subject"],
                "body": spec["body"],
                "variables": spec["variables"],
                "status": "active",
                "created_at": now,
                "updated_at": now,
            },
        )


def unseed_templates(apps, schema_editor):
    EmailTemplate = apps.get_model("core", "EmailTemplate")
    EmailTemplate.objects.filter(
        template_key__in=[spec["template_key"] for spec in TEMPLATES]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("helpdesk", "0002_helpdeskevent_helpdeskroutingrule_and_more"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
