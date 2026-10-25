"""Seed helpdesk requesters and conversation replies for the demo workspace."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import SlaContract, SlaTicket, User
from apps.core.tenancy import default_tenant, tenant_context
from apps.helpdesk.models import TicketReply
from apps.saas.models import SaasModule, TenantModule

REQUESTERS = [
    ("Grace Mwangi", "grace.mwangi@client.example", "+254 712 345 001", "email", "incident"),
    ("David Otieno", "david.otieno@client.example", "+254 722 345 002", "web", "service_request"),
    ("Amina Hassan", "amina.hassan@client.example", "+254 733 345 003", "phone", "problem"),
    ("Peter Kamau", "peter.kamau@client.example", "+254 744 345 004", "web", "change"),
]


class Command(BaseCommand):
    help = "Enrich demo SLA tickets with requester details and seed conversation replies."

    def handle(self, *args, **options):
        tenant = default_tenant()
        if tenant is None:
            raise CommandError("No workspace exists. Apply migrations before seeding.")
        module = SaasModule.objects.filter(code="SORA_HELPDESK_SLA").first()
        if module is not None and not module.is_available:
            module.is_available = True
            module.save(update_fields=["is_available"])
        if module is not None:
            TenantModule.objects.get_or_create(
                tenant=tenant,
                module=module,
                defaults={"is_enabled": True, "source": "manual", "note": "Demo showcase"},
            )
        with tenant_context(tenant):
            with transaction.atomic():
                self._seed()

    def _seed(self):
        if not SlaContract.objects.exists():
            self.stdout.write(self.style.WARNING(
                "No SLA contracts found. Run seed_pm first to create the demo SLA policy."
            ))
            return
        admin = User.objects.filter(email="admin@sora.com").first()
        tickets = list(SlaTicket.objects.order_by("-created_at", "-id")[:6])
        updated = 0
        for index, ticket in enumerate(tickets):
            if ticket.requester_name:
                continue
            name, email, phone, channel, category = REQUESTERS[index % len(REQUESTERS)]
            ticket.requester_name = name
            ticket.requester_email = email
            ticket.requester_phone = phone
            ticket.channel = channel
            ticket.category = category
            ticket.save(
                update_fields=[
                    "requester_name",
                    "requester_email",
                    "requester_phone",
                    "channel",
                    "category",
                ]
            )
            updated += 1

        replies = 0
        if tickets and admin is not None:
            newest = tickets[0]
            if not TicketReply.objects.filter(ticket_id=newest.id).exists():
                now = timezone.now()
                TicketReply.objects.create(
                    ticket_id=newest.id,
                    author=admin,
                    body=(
                        "Thanks for flagging this. We have reproduced the issue and assigned an "
                        "engineer. Next update within the response window."
                    ),
                    channel="email",
                    is_internal=False,
                    created_at=now,
                )
                TicketReply.objects.create(
                    ticket_id=newest.id,
                    author=admin,
                    body="Checked logs: the upstream credential rotated overnight. Rotate the secret and retry.",
                    channel="internal",
                    is_internal=True,
                    created_at=now,
                )
                replies = 2
        self.stdout.write(self.style.SUCCESS(
            f"Helpdesk demo ready: {updated} ticket(s) enriched, {replies} replies added."
        ))
