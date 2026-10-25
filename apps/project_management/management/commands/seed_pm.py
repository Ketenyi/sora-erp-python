"""Seed the project management module with a realistic demo portfolio."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.models import (
    Employee,
    PmDashboard,
    PmReportDefinition,
    Project,
    ProjectAuditLog,
    ProjectChangeRequest,
    ProjectExpense,
    ProjectHandover,
    ProjectHandoverItem,
    ProjectMom,
    ProjectTask,
    ProjectTeam,
    ProjectTimeEntry,
    SlaContract,
    SlaHealthCheck,
    SlaTicket,
    User,
)
from apps.core.tenancy import default_tenant, tenant_context
from apps.project_management.services import ensure_feature_flags

DEMO_PROJECT_CODE = "PRJ-2026-001"


def money(value) -> Decimal:
    return Decimal(str(value))


class Command(BaseCommand):
    help = "Create a demo project portfolio, tasks, approvals, expenses and SLA contracts."

    def handle(self, *args, **options):
        tenant = default_tenant()
        if tenant is None:
            raise CommandError("No workspace exists. Apply migrations before seeding.")
        with tenant_context(tenant):
            self._seed()

    @transaction.atomic
    def _seed(self):
        ensure_feature_flags()

        if Project.objects.filter(project_code=DEMO_PROJECT_CODE).exists():
            self.stdout.write(self.style.WARNING(
                "Demo project portfolio already exists — nothing to do."
            ))
            return

        admin = User.objects.filter(role="admin").order_by("id").first() or User.objects.first()
        if admin is None:
            raise CommandError("Create at least one user before seeding the project module.")

        employees = list(Employee.objects.filter(status="active").order_by("id")[:5])
        if len(employees) < 2:
            raise CommandError(
                "Seed at least two active employees first (run: python manage.py seed_hr)."
            )

        today = timezone.localdate()
        now = timezone.now()
        stamp = now.replace(microsecond=0)

        def task(project, name, assignee, status, priority, start, due, estimate, actual,
                 percent, order, completed_days_ago=None):
            completed_at = None
            completion_timestamp = None
            if status == "complete":
                completed_at = now - timedelta(days=completed_days_ago or 1)
                completion_timestamp = completed_at
            return ProjectTask.objects.create(
                project_id=project.id,
                task_name=name,
                description=f"Demo work item for {project.project_code}.",
                assigned_to=assignee.id if assignee else None,
                assigned_by=admin.id,
                start_date=start,
                end_date=due,
                deadline_date=due,
                estimated_hours=money(estimate),
                actual_hours=money(actual),
                estimated_cost=money(estimate) * money(3200),
                actual_cost=money(actual) * money(3200),
                status=status,
                priority=priority,
                completion_percent=money(percent),
                completion_timestamp=completion_timestamp,
                completed_at=completed_at,
                board_order=order,
                created_at=stamp - timedelta(days=max(1, 100 - order * 5)),
            )

        # ------------------------------------------------------------------
        # Projects
        # ------------------------------------------------------------------
        p1 = Project.objects.create(
            project_code=DEMO_PROJECT_CODE,
            project_name="Client Portal Modernization",
            description="Self-service customer portal with billing, support and account management.",
            project_type="external_client",
            client_name="Nairobi Water & Sewerage",
            client_email="projects@nairobiwater.example",
            start_date=today - timedelta(days=90),
            end_date=today + timedelta(days=90),
            baseline_start_date=today - timedelta(days=90),
            baseline_end_date=today + timedelta(days=90),
            baseline_locked_at=now - timedelta(days=85),
            budget_amount=money(4500000),
            contract_value=money(4500000),
            allocated_expense_budget=money(750000),
            actual_cost=money(956400),
            actual_revenue=money(1800000),
            status="in_execution",
            priority="high",
            manager_id=employees[0].id,
            created_by=admin.id,
            created_at=now - timedelta(days=95),
        )
        p2 = Project.objects.create(
            project_code="PRJ-2026-002",
            project_name="Warehouse Automation Rollout",
            description="Conveyor controls, robotics interface and site acceptance testing.",
            project_type="external_client",
            client_name="Twiga Logistics",
            client_email="pm@twigalogistics.example",
            start_date=today - timedelta(days=150),
            end_date=today + timedelta(days=30),
            baseline_start_date=today - timedelta(days=150),
            baseline_end_date=today + timedelta(days=30),
            baseline_locked_at=now - timedelta(days=145),
            budget_amount=money(2800000),
            contract_value=money(2800000),
            allocated_expense_budget=money(400000),
            actual_cost=money(423600),
            actual_revenue=money(900000),
            status="in_execution",
            priority="medium",
            manager_id=employees[1].id,
            created_by=admin.id,
            created_at=now - timedelta(days=155),
        )
        p3 = Project.objects.create(
            project_code="PRJ-2026-003",
            project_name="ERP Implementation — Coast Region",
            description="County-wide ERP rollout covering finance, procurement and HR.",
            project_type="external_client",
            client_name="County Government of Mombasa",
            client_email="tenders@mombasa.example",
            start_date=today + timedelta(days=30),
            end_date=today + timedelta(days=300),
            budget_amount=money(8200000),
            contract_value=money(8200000),
            allocated_expense_budget=money(1000000),
            actual_cost=money(0),
            actual_revenue=money(0),
            status="bidding",
            priority="high",
            manager_id=employees[0].id,
            created_by=admin.id,
            created_at=now - timedelta(days=20),
        )
        p4 = Project.objects.create(
            project_code="PRJ-2026-004",
            project_name="Internal Data Platform",
            description="Managed analytics platform now under a support retainer.",
            project_type="internal_initiative",
            start_date=today - timedelta(days=365),
            end_date=today - timedelta(days=10),
            baseline_start_date=today - timedelta(days=365),
            baseline_end_date=today - timedelta(days=10),
            baseline_locked_at=now - timedelta(days=360),
            budget_amount=money(1200000),
            contract_value=money(1200000),
            allocated_expense_budget=money(150000),
            actual_cost=money(360000),
            actual_revenue=money(1200000),
            status="support_mode",
            priority="medium",
            manager_id=employees[0].id,
            created_by=admin.id,
            created_at=now - timedelta(days=370),
        )

        # ------------------------------------------------------------------
        # Tasks
        # ------------------------------------------------------------------
        p1_tasks = [
            task(p1, "Discovery workshops & requirements sign-off", employees[0],
                 "complete", "high", today - timedelta(days=90), today - timedelta(days=70),
                 40, 38, 100, 1, completed_days_ago=72),
            task(p1, "Solution architecture & security review", employees[1],
                 "complete", "high", today - timedelta(days=75), today - timedelta(days=50),
                 60, 66, 100, 2, completed_days_ago=52),
            task(p1, "Customer self-service API", employees[1],
                 "in_progress", "high", today - timedelta(days=40), today + timedelta(days=10),
                 120, 70, 60, 3),
            task(p1, "Billing integration", employees[2],
                 "blocked", "high", today - timedelta(days=35), today + timedelta(days=5),
                 80, 30, 35, 4),
            task(p1, "Agent dashboard UI", employees[3],
                 "in_qa", "medium", today - timedelta(days=30), today + timedelta(days=12),
                 90, 75, 85, 5),
            task(p1, "UAT test scripts", employees[3],
                 "backlog", "medium", today + timedelta(days=5), today + timedelta(days=30),
                 30, 0, 0, 6),
            task(p1, "Go-live runbook & training", employees[0],
                 "backlog", "medium", today + timedelta(days=15), today + timedelta(days=45),
                 25, 0, 0, 7),
            task(p1, "Data migration rehearsal", employees[2],
                 "in_progress", "medium", today - timedelta(days=10), today + timedelta(days=20),
                 40, 12, 30, 8),
        ]
        p2_tasks = [
            task(p2, "Warehouse process mapping", employees[1],
                 "complete", "medium", today - timedelta(days=150), today - timedelta(days=120),
                 50, 48, 100, 1, completed_days_ago=122),
            task(p2, "Robotics interface design", employees[2],
                 "complete", "high", today - timedelta(days=130), today - timedelta(days=95),
                 70, 72, 100, 2, completed_days_ago=96),
            task(p2, "Conveyor control build", employees[2],
                 "in_progress", "high", today - timedelta(days=80), today + timedelta(days=8),
                 160, 110, 70, 3),
            task(p2, "Integration testing", employees[3],
                 "backlog", "medium", today + timedelta(days=5), today + timedelta(days=22),
                 60, 0, 0, 4),
            task(p2, "Site acceptance test", employees[3],
                 "backlog", "high", today + timedelta(days=20), today + timedelta(days=30),
                 40, 0, 0, 5),
        ]
        p3_tasks = [
            task(p3, "Bid preparation & compliance matrix", employees[0],
                 "in_progress", "high", today - timedelta(days=10), today + timedelta(days=14),
                 45, 12, 25, 1),
            task(p3, "Solution demo & proof of concept", employees[2],
                 "backlog", "medium", today + timedelta(days=5), today + timedelta(days=21),
                 50, 0, 0, 2),
            task(p3, "Pricing model & risk register", employees[0],
                 "backlog", "medium", today + timedelta(days=3), today + timedelta(days=18),
                 30, 0, 0, 3),
        ]
        p4_tasks = [
            task(p4, "Platform monitoring rollout", employees[0],
                 "complete", "medium", today - timedelta(days=300), today - timedelta(days=260),
                 30, 30, 100, 1, completed_days_ago=262),
            task(p4, "Quarterly security patch", employees[1],
                 "complete", "high", today - timedelta(days=120), today - timedelta(days=105),
                 20, 18, 100, 2, completed_days_ago=108),
            task(p4, "Data catalogue refresh", employees[1],
                 "in_progress", "medium", today - timedelta(days=15), today + timedelta(days=15),
                 35, 14, 40, 3),
            task(p4, "Legacy report decommission", employees[0],
                 "backlog", "low", today + timedelta(days=10), today + timedelta(days=40),
                 25, 0, 0, 4),
        ]

        # ------------------------------------------------------------------
        # Team and time entries
        # ------------------------------------------------------------------
        team_rows = [
            (p1, employees[0], "Project Manager", 100, 3500),
            (p1, employees[1], "Lead Engineer", 100, 2800),
            (p1, employees[2], "Backend Engineer", 80, 2400),
            (p1, employees[3], "QA Analyst", 60, 1800),
            (p2, employees[1], "Delivery Lead", 60, 2800),
            (p2, employees[2], "Automation Engineer", 100, 2400),
            (p2, employees[3], "QA Analyst", 80, 1800),
            (p2, employees[4], "Support Engineer", 100, 2000),
            (p3, employees[0], "Bid Manager", 40, 3500),
            (p3, employees[2], "Solution Architect", 30, 2400),
            (p4, employees[0], "Account Manager", 20, 3500),
            (p4, employees[1], "Support Lead", 30, 2800),
        ]
        for project, employee, role, allocation, rate in team_rows:
            ProjectTeam.objects.create(
                project_id=project.id,
                employee_id=employee.id,
                role=role,
                allocation_percentage=money(allocation),
                hourly_rate=money(rate),
                joined_at=today - timedelta(days=100),
                left_at=today - timedelta(days=30) if employee is employees[4] and project is p2 else None,
                created_at=stamp - timedelta(days=100),
            )

        time_rows = [
            (p1, employees[0], 40, 3500, "approved"),
            (p1, employees[1], 48, 2800, "approved"),
            (p1, employees[2], 30, 2400, "approved"),
            (p1, employees[3], 12, 1800, "pending"),
            (p2, employees[1], 24, 2800, "approved"),
            (p2, employees[2], 36, 2400, "approved"),
        ]
        for project, employee, hours, rate, status in time_rows:
            ProjectTimeEntry.objects.create(
                project_id=project.id,
                task_id=None,
                employee_id=employee.id,
                entry_date=today - timedelta(days=7),
                hours=money(hours),
                description="Demo delivery effort.",
                billable=1,
                hourly_rate=money(rate),
                total_amount=money(hours) * money(rate),
                status=status,
                approved_by=admin.id if status == "approved" else None,
                approved_at=now - timedelta(days=5) if status == "approved" else None,
                created_at=stamp - timedelta(days=7),
            )

        # ------------------------------------------------------------------
        # Field expenses
        # ------------------------------------------------------------------
        expense_rows = [
            (p1, "travel", "Client workshops in Mombasa", 240000, "approved", 40),
            (p1, "equipment", "Demo tablets and scanners", 150000, "approved", 35),
            (p1, "software", "Load-testing licences", 130000, "approved", 20),
            (p1, "accommodation", "UAT week accommodation", 90000, "approved", 10),
            (p1, "meals", "Go-live readiness workshop", 45000, "pending", 3),
            (p1, "other", "Parking and incidentals", 12000, "rejected", 2),
            (p2, "transport", "Site visits — Athi River", 45000, "approved", 25),
            (p2, "materials", "Sensor mounting kits", 75000, "approved", 18),
            (p2, "equipment", "Spare HMI panel", 25000, "pending", 4),
            (p3, "transport", "Tender clarification meeting", 8000, "pending", 2),
            (p4, "software", "Observability platform renewal", 60000, "approved", 15),
        ]
        for project, category, description, amount, status, days_ago in expense_rows:
            ProjectExpense.objects.create(
                project_id=project.id,
                expense_date=today - timedelta(days=days_ago),
                description=description,
                amount=money(amount),
                category=category,
                submitted_by=admin.id,
                approved_by=admin.id if status == "approved" else None,
                status=status,
                created_at=stamp - timedelta(days=days_ago),
            )

        # ------------------------------------------------------------------
        # Minutes, change requests, handover
        # ------------------------------------------------------------------
        ProjectMom.objects.create(
            project_id=p1.id,
            title="Weekly delivery review — sprint 8",
            meeting_date=today - timedelta(days=14),
            attendees="SORA delivery team, Nairobi Water project board",
            agenda="Sprint progress, billing integration blocker, UAT readiness.",
            minutes=(
                "The team reviewed sprint 8 progress. The self-service API is 60% complete. "
                "Billing integration remains blocked on the client's payment gateway sandbox "
                "credentials. QA confirmed the dashboard UI is ready for UAT once credentials arrive."
            ),
            decisions="Client to provide sandbox credentials by Friday; UAT starts the following Monday.",
            action_items="Amina — escalate credentials — due Friday; Grace — prepare UAT scripts — due Monday.",
            status="approved",
            approved_by_name="Nairobi Water Project Board",
            approved_at=now - timedelta(days=12),
            created_by=admin.id,
            created_at=stamp - timedelta(days=14),
        )
        ProjectMom.objects.create(
            project_id=p1.id,
            title="Go-live readiness review",
            meeting_date=today - timedelta(days=2),
            attendees="SORA delivery team, client operations",
            agenda="Cutover plan, training schedule, support model.",
            minutes=(
                "Current runbook covers cutover steps, rollback and support escalation. "
                "Training sessions still need scheduling for 40 agent users. Billing blocker remains open."
            ),
            decisions="Go-live date remains provisional pending billing sign-off.",
            action_items="Peter — finalise runbook — due next week; Faith — schedule training rooms.",
            status="pending_approval",
            created_by=admin.id,
            created_at=stamp - timedelta(days=2),
        )
        ProjectMom.objects.create(
            project_id=p2.id,
            title="Warehouse automation checkpoint",
            meeting_date=today - timedelta(days=30),
            attendees="Twiga logistics operations, SORA automation team",
            agenda="Conveyor build progress and site readiness.",
            minutes="Conveyor controls are 70% complete. Site power upgrades are complete. Testing starts next month.",
            decisions="Integration testing begins after the conveyor build finishes.",
            action_items="Peter — publish test plan; Grace — coordinate site access.",
            status="approved",
            approved_by_name="Twiga Logistics PMO",
            approved_at=now - timedelta(days=28),
            created_by=admin.id,
            created_at=stamp - timedelta(days=30),
        )

        ProjectChangeRequest.objects.create(
            project_id=p1.id,
            task_id=p1_tasks[2].id,
            cr_number="CR-2026-001",
            title="Add single sign-on for agent portal",
            description="Client requests SAML SSO for 40 agent users ahead of go-live.",
            justification="Security review requires SSO before production access can be granted.",
            cost_impact=money(350000),
            schedule_impact_days=14,
            status="pending_approval",
            requested_by=admin.id,
            requested_at=now - timedelta(days=4),
            created_at=stamp - timedelta(days=4),
        )
        ProjectChangeRequest.objects.create(
            project_id=p1.id,
            task_id=p1_tasks[5].id,
            cr_number="CR-2026-002",
            title="Extended UAT support window",
            description="Two additional weeks of UAT support for the client's agent team.",
            justification="Client requested extended parallel running before cutover.",
            cost_impact=money(180000),
            schedule_impact_days=21,
            status="approved",
            requested_by=admin.id,
            requested_at=now - timedelta(days=25),
            decided_by_name="Nairobi Water Project Board",
            decided_at=now - timedelta(days=20),
            decision_notes="Approved within the contingency budget.",
            created_at=stamp - timedelta(days=25),
        )
        ProjectChangeRequest.objects.create(
            project_id=p2.id,
            task_id=None,
            cr_number="CR-2026-003",
            title="Add an extra dispatch zone",
            description="Additional warehouse zone requires controls and testing.",
            justification="New client volume forecast.",
            cost_impact=money(0),
            schedule_impact_days=0,
            status="draft",
            requested_by=admin.id,
            requested_at=now - timedelta(days=3),
            created_at=stamp - timedelta(days=3),
        )

        handover = ProjectHandover.objects.create(
            project_id=p2.id,
            departing_employee_id=employees[4].id,
            incoming_employee_id=employees[3].id,
            status="completed",
            state_of_play=(
                "Support engineer rotated off the account. Conveyor controls are in build, "
                "integration testing is not started and the client is waiting on a test plan. "
                "All site access badges are with reception. Escalation path is Amina then Peter."
            ),
            asset_notes="Repository access transferred; HMI spare panel stored in the site cabin.",
            initiated_by=admin.id,
            initiated_at=now - timedelta(days=31),
            completed_at=now - timedelta(days=30),
            created_at=stamp - timedelta(days=31),
        )
        ProjectHandoverItem.objects.create(
            handover_id=handover.id,
            item_type="task",
            reference="Integration testing",
            notes="Reassigned from the departing support engineer.",
            to_employee_id=employees[3].id,
            created_at=stamp - timedelta(days=30),
        )
        ProjectHandoverItem.objects.create(
            handover_id=handover.id,
            item_type="asset",
            reference="Repository and VPN access",
            notes="Access transferred and verified.",
            to_employee_id=employees[3].id,
            created_at=stamp - timedelta(days=30),
        )

        # ------------------------------------------------------------------
        # Audit trail (including status changes for scorecards)
        # ------------------------------------------------------------------
        audit_rows = [
            (p1, None, "project_created", None, None, None,
             "Project created from signed contract.", 95),
            (p1, p1_tasks[0].id, "status_change", "status", "in_progress", "complete",
             "Discovery signed off by the client.", 72),
            (p1, p1_tasks[1].id, "status_change", "status", "in_progress", "complete",
             "Architecture and security review passed.", 52),
            (p1, p1_tasks[3].id, "status_change", "status", "in_progress", "blocked",
             "Waiting on payment gateway sandbox credentials.", 5),
            (p1, p1_tasks[3].id, "status_change", "status", "blocked", "in_progress",
             "Temporary workaround agreed; credentials still pending.", 4),
            (p1, p1_tasks[3].id, "status_change", "status", "in_progress", "blocked",
             "Workaround rejected by security; blocked again.", 0.5),
            (p1, p1_tasks[4].id, "status_change", "status", "in_progress", "in_qa",
             "Dashboard UI handed to QA.", 2),
            (p1, None, "expense_approved", "status", "pending", "approved",
             "Travel and equipment claims approved.", 20),
            (p1, None, "cr_approved", None, None, "CR-2026-002",
             "Extended UAT support window funded.", 20),
            (p2, None, "status_change", "status", "in_progress", "complete",
             "Process mapping complete.", 122),
            (p2, None, "handover_completed", None, None, "Peter Ochieng → Grace Wanjiku",
             "Support engineer handover completed with memo.", 30),
            (p4, None, "project_support_mode", None, None, "SLA-2026-001",
             "Project converted to managed support.", 330),
        ]
        for project, task_id, action, field_name, old_value, new_value, note, days_ago in audit_rows:
            ProjectAuditLog.objects.create(
                project_id=project.id,
                task_id=task_id,
                user_id=admin.id,
                action=action,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                note=note,
                created_at=stamp - timedelta(days=days_ago),
            )
        for project in (p1, p2, p3, p4):
            ProjectAuditLog.objects.create(
                project_id=project.id,
                user_id=admin.id,
                action="baseline_locked",
                note="Baseline locked; scope changes now require a change request.",
                created_at=stamp - timedelta(days=84),
            )

        # ------------------------------------------------------------------
        # SLA / support
        # ------------------------------------------------------------------
        contract = SlaContract.objects.create(
            project_id=p4.id,
            contract_number="SLA-2026-001",
            title="Internal Data Platform — Managed Support",
            client_name="SORA Internal",
            start_date=today - timedelta(days=330),
            end_date=today + timedelta(days=35),
            support_tier="priority",
            monthly_value=money(180000),
            response_target_hours=4,
            resolution_target_hours=24,
            status="active",
            auto_created_from_project=True,
            created_by=admin.id,
            created_at=stamp - timedelta(days=330),
        )
        SlaTicket.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            ticket_number="TKT-2026-001",
            title="Nightly warehouse feed missing",
            description="The 02:00 warehouse load did not run; finance reports are stale.",
            priority="critical",
            status="open",
            assigned_to=employees[1].id,
            sla_due_at=now - timedelta(days=1),
            created_by=admin.id,
            created_at=now - timedelta(days=2),
            breached=True,
        )
        SlaTicket.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            ticket_number="TKT-2026-002",
            title="Dashboard load time above target",
            description="Executive dashboard takes 18 seconds to load.",
            priority="high",
            status="in_progress",
            assigned_to=employees[0].id,
            sla_due_at=now + timedelta(hours=8),
            first_response_at=now - timedelta(hours=4),
            created_by=admin.id,
            created_at=now - timedelta(days=1),
        )
        SlaTicket.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            ticket_number="TKT-2026-003",
            title="Access request for new finance analyst",
            description="New finance analyst needs read access to the finance mart.",
            priority="medium",
            status="waiting_client",
            assigned_to=employees[1].id,
            sla_due_at=now + timedelta(days=2),
            first_response_at=now - timedelta(days=4),
            created_by=admin.id,
            created_at=now - timedelta(days=5),
        )
        SlaTicket.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            ticket_number="TKT-2026-004",
            title="Quarterly access review report",
            description="Report delivered and acknowledged by the security team.",
            priority="low",
            status="resolved",
            assigned_to=employees[0].id,
            sla_due_at=now - timedelta(days=17),
            first_response_at=now - timedelta(days=20),
            resolved_at=now - timedelta(days=18),
            created_by=admin.id,
            created_at=now - timedelta(days=20),
        )
        SlaHealthCheck.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            scheduled_date=today - timedelta(days=60),
            check_type="preventive",
            status="completed",
            findings="Patch levels verified; two low-risk findings remediated.",
            performed_by=employees[1].id,
            completed_at=now - timedelta(days=60),
            created_at=stamp - timedelta(days=90),
        )
        SlaHealthCheck.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            scheduled_date=today - timedelta(days=30),
            check_type="preventive",
            status="completed",
            findings="Backup restore test passed. Warehouse feed retry logic recommended.",
            performed_by=employees[1].id,
            completed_at=now - timedelta(days=30),
            created_at=stamp - timedelta(days=60),
        )
        SlaHealthCheck.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            scheduled_date=today + timedelta(days=7),
            check_type="preventive",
            status="scheduled",
            created_at=stamp - timedelta(days=2),
        )
        SlaHealthCheck.objects.create(
            contract_id=contract.id,
            project_id=p4.id,
            scheduled_date=today + timedelta(days=37),
            check_type="health_check",
            status="scheduled",
            created_at=stamp - timedelta(days=2),
        )

        # ------------------------------------------------------------------
        # Dashboards and saved reports
        # ------------------------------------------------------------------
        PmDashboard.objects.create(
            user_id=admin.id,
            title="Delivery Command Center",
            is_default=True,
            layout_config={
                "widgets": [
                    "portfolio_summary",
                    "financial_health",
                    "expense_burn",
                    "project_status",
                    "upcoming_deadlines",
                    "sla_snapshot",
                    "recent_activity",
                ]
            },
            created_at=stamp - timedelta(days=7),
        )
        PmDashboard.objects.create(
            user_id=admin.id,
            title="Support Watch",
            is_default=False,
            layout_config={
                "widgets": ["sla_snapshot", "upcoming_deadlines", "team_workload", "recent_activity"]
            },
            created_at=stamp - timedelta(days=3),
        )
        PmReportDefinition.objects.create(
            name="Projects by status",
            description="Portfolio distribution across delivery stages.",
            report_type="query",
            filters={"dataset": "projects", "dimension": "status", "metric": "count"},
            schedule="weekly",
            recipients="admin@sora.example",
            created_by=admin.id,
            created_at=stamp - timedelta(days=7),
        )
        PmReportDefinition.objects.create(
            name="Approved spend by category",
            description="Approved field expenses grouped by category.",
            report_type="query",
            filters={
                "dataset": "expenses",
                "dimension": "category",
                "metric": "sum_amount",
                "status": "approved",
            },
            schedule="monthly",
            recipients="finance@sora.example, admin@sora.example",
            created_by=admin.id,
            created_at=stamp - timedelta(days=7),
        )
        PmReportDefinition.objects.create(
            name="SLA ticket load by priority",
            description="Open support workload sliced by priority.",
            report_type="query",
            filters={"dataset": "tickets", "dimension": "priority", "metric": "count"},
            schedule="none",
            recipients="",
            created_by=admin.id,
            created_at=stamp - timedelta(days=2),
        )

        self.stdout.write(self.style.SUCCESS(
            "Project management demo data seeded: "
            f"4 projects, {len(p1_tasks) + len(p2_tasks) + len(p3_tasks) + len(p4_tasks)} tasks, "
            f"{len(expense_rows)} expenses, 3 MOMs, 3 change requests, 1 SLA contract, "
            "4 tickets, 4 health checks, 2 dashboards and 3 saved reports."
        ))
