"""Seed realistic HR demo data for the SORA ERP Python port.

Usage:
    python manage.py seed_hr

The command is idempotent: it reuses departments, positions, employees and
documents by their natural keys so it can be re-run safely.
"""

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import (
    Approver,
    Attendance,
    Candidate,
    Department,
    Employee,
    EmployeeDocument,
    EmployeeFeedback,
    EmployeeMovement,
    EmployeeRecognition,
    EmployeeSkill,
    EmployeeTraining,
    Goal,
    Interview,
    JobPosting,
    LeaveBalance,
    LeaveRequest,
    LeaveType,
    OvertimeRequest,
    PayrollItem,
    PayrollPeriod,
    PerformanceReview,
    Position,
    RecognitionBadge,
    Skill,
    SuccessionPlan,
    TrainingProgram,
    User,
    WellnessCheckin,
)
from apps.core.tenancy import default_tenant, tenant_context
from apps.core.uploads import tenant_media_prefix

DEPARTMENTS = [
    ("EXEC", "Executive", "Company leadership and strategy", "CC-100"),
    ("HR", "Human Resources", "People operations, payroll and culture", "CC-200"),
    ("SLS", "Sales & Marketing", "Revenue generation and customer success", "CC-300"),
    ("FIN", "Finance", "Accounting, treasury and reporting", "CC-400"),
    ("OPS", "Operations", "Service delivery and logistics", "CC-500"),
    ("ICT", "ICT", "Technology, systems and support", "CC-600"),
]

POSITIONS = [
    ("EXEC-001", "Chief Executive Officer", "Executive", "E1", 350000, 600000),
    ("HR-001", "HR Manager", "Human Resources", "M2", 180000, 280000),
    ("HR-002", "HR Officer", "Human Resources", "P2", 70000, 120000),
    ("SLS-001", "Sales Executive", "Sales & Marketing", "P2", 80000, 150000),
    ("SLS-002", "Customer Success Officer", "Sales & Marketing", "P2", 65000, 110000),
    ("FIN-001", "Accountant", "Finance", "P3", 110000, 180000),
    ("FIN-002", "Finance Analyst", "Finance", "P2", 90000, 140000),
    ("OPS-001", "Operations Manager", "Operations", "M2", 200000, 300000),
    ("OPS-002", "Warehouse Supervisor", "Operations", "P2", 60000, 95000),
    ("OPS-003", "Driver", "Operations", "P1", 45000, 65000),
    ("ICT-001", "Software Engineer", "ICT", "P3", 140000, 230000),
]

EMPLOYEES = [
    {
        "key": "admin",
        "code": "EMP-2026-0001",
        "first": "Amina",
        "last": "Hassan",
        "email": "admin@sora.com",
        "dept": "EXEC",
        "position": "EXEC-001",
        "title": "Chief Executive Officer",
        "salary": 450000,
        "hire": date(2018, 3, 5),
        "gender": "female",
        "dob": date(1981, 7, 14),
        "phone": "+254 700 100 001",
        "city": "Nairobi",
        "manager": None,
        "status": "active",
        "employment": "active",
    },
    {
        "key": "hr",
        "code": "EMP-2026-0002",
        "first": "Grace",
        "last": "Wanjiku",
        "email": "hr@sora.com",
        "dept": "HR",
        "position": "HR-001",
        "title": "HR Manager",
        "salary": 220000,
        "hire": date(2020, 1, 13),
        "gender": "female",
        "dob": date(1987, 4, 22),
        "phone": "+254 700 100 002",
        "city": "Nairobi",
        "manager": "admin",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "manager",
        "code": "EMP-2026-0003",
        "first": "Peter",
        "last": "Ochieng",
        "email": "manager@sora.com",
        "dept": "OPS",
        "position": "OPS-001",
        "title": "Operations Manager",
        "salary": 250000,
        "hire": date(2019, 6, 3),
        "gender": "male",
        "dob": date(1984, 11, 2),
        "phone": "+254 700 100 003",
        "city": "Nairobi",
        "manager": "admin",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "sales",
        "code": "EMP-2026-0004",
        "first": "James",
        "last": "Mwangi",
        "email": "sales@sora.com",
        "dept": "SLS",
        "position": "SLS-001",
        "title": "Sales Executive",
        "salary": 120000,
        "hire": date(2022, 2, 14),
        "gender": "male",
        "dob": date(1992, 9, 18),
        "phone": "+254 700 100 004",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "accounts",
        "code": "EMP-2026-0005",
        "first": "Faith",
        "last": "Njeri",
        "email": "accounts@sora.com",
        "dept": "FIN",
        "position": "FIN-001",
        "title": "Accountant",
        "salary": 150000,
        "hire": date(2021, 8, 9),
        "gender": "female",
        "dob": date(1990, 1, 27),
        "phone": "+254 700 100 005",
        "city": "Nairobi",
        "manager": "admin",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "brian",
        "code": "EMP-2026-0006",
        "first": "Brian",
        "last": "Otieno",
        "email": "brian.otieno@sora.com",
        "dept": "ICT",
        "position": "ICT-001",
        "title": "Software Engineer",
        "salary": 180000,
        "hire": date(2023, 10, 2),
        "gender": "male",
        "dob": date(1994, 5, 6),
        "phone": "+254 700 100 006",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "mercy",
        "code": "EMP-2026-0007",
        "first": "Mercy",
        "last": "Achieng",
        "email": "mercy.achieng@sora.com",
        "dept": "SLS",
        "position": "SLS-001",
        "title": "Sales Executive",
        "salary": 110000,
        "hire": date(2024, 3, 18),
        "gender": "female",
        "dob": date(1995, 12, 9),
        "phone": "+254 700 100 007",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "david",
        "code": "EMP-2026-0008",
        "first": "David",
        "last": "Kiprop",
        "email": "david.kiprop@sora.com",
        "dept": "OPS",
        "position": "OPS-002",
        "title": "Warehouse Supervisor",
        "salary": 85000,
        "hire": date(2024, 7, 1),
        "gender": "male",
        "dob": date(1991, 2, 21),
        "phone": "+254 700 100 008",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "lucy",
        "code": "EMP-2026-0009",
        "first": "Lucy",
        "last": "Wambui",
        "email": "lucy.wambui@sora.com",
        "dept": "HR",
        "position": "HR-002",
        "title": "HR Officer",
        "salary": 95000,
        "hire": date(2025, 1, 20),
        "gender": "female",
        "dob": date(1997, 8, 30),
        "phone": "+254 700 100 009",
        "city": "Nairobi",
        "manager": "hr",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "samuel",
        "code": "EMP-2026-0010",
        "first": "Samuel",
        "last": "Mutua",
        "email": "samuel.mutua@sora.com",
        "dept": "FIN",
        "position": "FIN-002",
        "title": "Finance Analyst",
        "salary": 120000,
        "hire": date(2025, 6, 16),
        "gender": "male",
        "dob": date(1996, 3, 11),
        "phone": "+254 700 100 010",
        "city": "Nairobi",
        "manager": "accounts",
        "status": "active",
        "employment": "probation",
    },
    {
        "key": "esther",
        "code": "EMP-2026-0011",
        "first": "Esther",
        "last": "Chebet",
        "email": "esther.chebet@sora.com",
        "dept": "SLS",
        "position": "SLS-002",
        "title": "Customer Success Officer",
        "salary": 90000,
        "hire": date(2026, 2, 2),
        "gender": "female",
        "dob": date(1998, 10, 5),
        "phone": "+254 700 100 011",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "john",
        "code": "EMP-2026-0012",
        "first": "John",
        "last": "Kamau",
        "email": "john.kamau@sora.com",
        "dept": "OPS",
        "position": "OPS-003",
        "title": "Driver",
        "salary": 55000,
        "hire": date(2023, 4, 10),
        "gender": "male",
        "dob": date(1989, 6, 25),
        "phone": "+254 700 100 012",
        "city": "Nairobi",
        "manager": "manager",
        "status": "active",
        "employment": "active",
    },
    {
        "key": "mary",
        "code": "EMP-2026-0013",
        "first": "Mary",
        "last": "Njoki",
        "email": "mary.njoki@sora.com",
        "dept": "FIN",
        "position": "FIN-002",
        "title": "Finance Analyst",
        "salary": 100000,
        "hire": date(2022, 9, 5),
        "gender": "female",
        "dob": date(1993, 4, 17),
        "phone": "+254 700 100 013",
        "city": "Nairobi",
        "manager": "accounts",
        "status": "inactive",
        "employment": "resigned",
        "termination": date(2026, 6, 30),
    },
]

LEAVE_TYPES = [
    ("ANN", "Annual Leave", 21, "#FFD700", 1, 1, 5),
    ("SICK", "Sick Leave", 10, "#ef4444", 1, 0, 0),
    ("MAT", "Maternity Leave", 90, "#ec4899", 1, 0, 0),
    ("PAT", "Paternity Leave", 14, "#3b82f6", 1, 0, 0),
    ("COMP", "Compassionate Leave", 5, "#64748b", 1, 0, 0),
    ("STUDY", "Study Leave", 10, "#10b981", 1, 0, 0),
]

BADGES = [
    ("Team Player", "fa-people-group", "bronze", 10, "Consistently supports teammates and shares credit."),
    ("Customer Hero", "fa-handshake", "silver", 25, "Goes above and beyond for clients."),
    ("Innovator", "fa-lightbulb", "gold", 50, "Ships ideas that make the business better."),
    ("Leadership Excellence", "fa-crown", "platinum", 100, "Models outstanding leadership every day."),
    ("Above & Beyond", "fa-rocket", "gold", 40, "Delivers exceptional outcomes under pressure."),
    ("Wellness Champion", "fa-heart-pulse", "silver", 20, "Champions wellbeing across the team."),
]

TRAININGS = [
    ("TRN-2026-0001", "Project Management Essentials", 40, 25000, "Nairobi Institute of Business", "PMP preparation"),
    ("TRN-2026-0002", "Leadership Foundations", 24, 18000, "Kenya Management Academy", "Certificate of completion"),
    ("TRN-2026-0003", "Advanced Excel & Data Analysis", 16, 12000, "Data Skills Africa", "Excel advanced certificate"),
    ("TRN-2026-0004", "Customer Experience Excellence", 12, 8000, "ServicePro Kenya", "CX practitioner"),
    ("TRN-2026-0005", "Occupational Health & Safety", 8, 5000, "SafeWork Kenya", "OSH awareness"),
]

SKILLS = [
    ("SK-001", "Python", "Technical"),
    ("SK-002", "Project Management", "Delivery"),
    ("SK-003", "Financial Analysis", "Finance"),
    ("SK-004", "Customer Service", "Commercial"),
    ("SK-005", "Leadership", "People"),
    ("SK-006", "Data Analysis", "Analytics"),
]

LEAVE_REQUESTS = [
    ("sales", "ANN", -10, -6, "approved", "Family holiday in Mombasa."),
    ("lucy", "SICK", -3, -2, "approved", "Flu and doctor's recommendation to rest."),
    ("brian", "ANN", 5, 9, "pending", "Annual leave for a family wedding upcountry."),
    ("mercy", "COMP", -1, 1, "pending", "Bereavement in the family."),
    ("accounts", "ANN", -20, -18, "approved", "Short break after month-end close."),
    ("esther", "ANN", 10, 14, "pending", "Planned annual leave."),
    ("david", "ANN", -30, -26, "rejected", "Requested during peak stock-count week."),
    ("hr", "ANN", -40, -36, "approved", "Annual leave carried over from last year."),
    ("samuel", "STUDY", 14, 16, "pending", "Professional exams preparation."),
]

OVERTIME = [
    ("brian", 2, 3, "pending", "Production incident and deployment."),
    ("mercy", 1, 2.5, "pending", "Quarter-end sales reporting."),
    ("david", -3, 4, "approved", "Stock take and reconciliation."),
    ("sales", -5, 2, "approved", "Client proposal preparation."),
    ("john", -7, 3, "approved", "Late deliveries during peak week."),
]

REVIEWS = [
    ("brian", "manager", -300, -210, "annual", 4.5, "completed", "Excellent technical delivery and mentoring."),
    ("mercy", "manager", -90, -1, "quarterly", 4.0, "completed", "Strong client relationships and pipeline growth."),
    ("sales", "manager", -330, -240, "annual", 3.8, "approved", "Consistent performer; needs stronger forecasting discipline."),
    ("lucy", "hr", -120, -30, "quarterly", 4.2, "reviewed", "Great onboarding improvements this quarter."),
    ("accounts", "admin", -340, -250, "annual", 4.6, "completed", "High integrity and excellent month-end control."),
    ("samuel", "accounts", -30, 0, "monthly", 3.5, "submitted", "Good analytical work, still learning the ERP."),
    ("david", "manager", -60, -1, "probation", 3.9, "draft", "Reliable warehouse leadership during probation."),
]

GOALS = [
    ("brian", "Complete ERP mobile app", "Ship the employee self-service app to both stores.", "company", -150, 45, 65, "in_progress", "manager"),
    ("brian", "Reduce deployment time", "Cut average deployment time to under 10 minutes.", "team", -100, 60, 40, "in_progress", "manager"),
    ("sales", "Grow enterprise pipeline", "Build KES 8M qualified enterprise pipeline.", "individual", -180, 30, 55, "in_progress", "manager"),
    ("mercy", "Onboard 15 new clients", "Onboard at least 15 new SMEs this year.", "individual", -200, 20, 80, "in_progress", "manager"),
    ("lucy", "Complete HRIS roll-out", "Digitise all personnel files.", "team", -120, 70, 100, "completed", "hr"),
    ("accounts", "Close month-end in 3 days", "Reduce month-end close cycle to three working days.", "team", -200, 90, 70, "in_progress", "admin"),
    ("esther", "Improve CSAT to 90%", "Raise customer satisfaction above 90%.", "individual", -100, 80, 30, "in_progress", "manager"),
    ("david", "Stock accuracy above 99%", "Achieve 99% inventory accuracy across warehouses.", "individual", -60, 120, 25, "in_progress", "manager"),
    ("samuel", "Automate reconciliations", "Automate bank reconciliations for all accounts.", "team", -45, 75, 10, "in_progress", "accounts"),
    ("john", "Zero preventable accidents", "Maintain zero preventable driving incidents.", "individual", -220, 100, 100, "completed", "manager"),
]

JOBS = [
    {
        "title": "Senior Software Engineer",
        "dept": "ICT",
        "position": "ICT-001",
        "type": "full_time",
        "location": "Nairobi (Hybrid)",
        "posted": -12,
        "closing": 18,
        "status": "published",
        "salary": (180000, 260000),
        "description": "We are looking for a senior engineer to lead development of our ERP platform and mentor the engineering team.",
        "requirements": "5+ years of software engineering experience. Strong Python, Django and PostgreSQL skills. Experience with APIs and modern front ends.",
        "responsibilities": "Design and deliver product features, review code, mentor engineers and improve engineering practice.",
    },
    {
        "title": "Sales Executive",
        "dept": "SLS",
        "position": "SLS-001",
        "type": "full_time",
        "location": "Nairobi",
        "posted": -25,
        "closing": 8,
        "status": "published",
        "salary": (90000, 140000),
        "description": "Drive new business across SMEs and mid-market clients in Kenya.",
        "requirements": "3+ years of B2B sales experience, strong CRM discipline and a proven record of meeting targets.",
        "responsibilities": "Own the sales cycle, build pipeline, run demos and close deals.",
    },
    {
        "title": "HR Officer",
        "dept": "HR",
        "position": "HR-002",
        "type": "full_time",
        "location": "Nairobi",
        "posted": -3,
        "closing": 30,
        "status": "draft",
        "salary": (70000, 100000),
        "description": "Support the HR team with recruitment, onboarding and employee records.",
        "requirements": "2+ years of HR generalist experience and familiarity with Kenyan labour law.",
        "responsibilities": "Coordinate recruitment, maintain records and support employee queries.",
    },
    {
        "title": "Accountant",
        "dept": "FIN",
        "position": "FIN-001",
        "type": "full_time",
        "location": "Nairobi",
        "posted": -70,
        "closing": -40,
        "status": "closed",
        "salary": (110000, 150000),
        "description": "Own day-to-day accounting and statutory reporting.",
        "requirements": "CPA finalist or qualified accountant with 4+ years of experience.",
        "responsibilities": "Manage ledgers, reconciliations and tax filings.",
    },
]

CANDIDATES = [
    ("Alice", "Wanjiru", "alice.wanjiru@example.com", "+254 711 200 001", 0, "interview", 92, "LinkedIn", "Strong Django background and ERP experience."),
    ("Kevin", "Odhiambo", "kevin.odhiambo@example.com", "+254 711 200 002", 0, "screening", 84, "Referral", "Solid backend skills, needs frontend depth."),
    ("Naomi", "Kilonzo", "naomi.kilonzo@example.com", "+254 711 200 003", 0, "new", 76, "Careers page", "Promising mid-level engineer."),
    ("Collins", "Kiptoo", "collins.kiptoo@example.com", "+254 711 200 004", 1, "assessment", 88, "LinkedIn", "Strong B2B sales record in software."),
    ("Diana", "Akinyi", "diana.akinyi@example.com", "+254 711 200 005", 1, "offered", 90, "Referral", "Excellent client management references."),
    ("George", "Maina", "george.maina@example.com", "+254 711 200 006", 1, "hired", 93, "Careers page", "Accepted offer, starts next month."),
    ("Sarah", "Muthoni", "sarah.muthoni@example.com", "+254 711 200 007", 2, "new", 81, "LinkedIn", "HR generalist with payroll experience."),
    ("Paul", "Onyango", "paul.onyango@example.com", "+254 711 200 008", 3, "rejected", 62, "Careers page", "Experience below the required level."),
]


class Command(BaseCommand):
    help = "Seed departments, employees, leave, payroll, recruitment and engagement demo data."

    def handle(self, *args, **options):
        tenant = default_tenant()
        if tenant is None:
            raise CommandError("No workspace exists. Apply migrations before seeding.")
        with tenant_context(tenant):
            self._seed()

    def _seed(self):
        self.now = timezone.now()
        self.today = timezone.localdate()
        self.admin_user = User.objects.filter(email="admin@sora.com").first()
        if self.admin_user is None:
            self.stderr.write("Run `python manage.py seed_data` before seed_hr.")
            return
        self.admin_id = self.admin_user.id

        self.departments = self._seed_departments()
        self.positions = self._seed_positions()
        self.employees = self._seed_employees()
        self._link_users()
        self.leave_types = self._seed_leave_types()
        self._seed_balances()
        self._seed_leave_requests()
        self._seed_approvers()
        self._seed_attendance()
        self._seed_overtime()
        self._seed_reviews_and_goals()
        self._seed_recruitment()
        self._seed_payroll()
        self._seed_recognition()
        self._seed_wellness()
        self._seed_training()
        self._seed_succession()
        self._seed_documents_skills_movements()

        self.stdout.write(self.style.SUCCESS("HR demo data ready."))
        self.stdout.write(
            f"  {len(self.employees)} employees, {len(self.leave_types)} leave types, "
            f"{JobPosting.objects.count()} job openings, {PayrollPeriod.objects.count()} payroll periods."
        )

    # ------------------------------------------------------------------ helpers
    def _seed_departments(self):
        departments = {}
        for code, name, description, cost_center in DEPARTMENTS:
            department, _ = Department.objects.update_or_create(
                department_code=code,
                defaults={
                    "department_name": name,
                    "description": description,
                    "cost_center": cost_center,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                },
            )
            departments[code] = department
        return departments

    def _seed_positions(self):
        positions = {}
        for code, name, family, grade, minimum, maximum in POSITIONS:
            position, _ = Position.objects.update_or_create(
                position_code=code,
                defaults={
                    "position_name": name,
                    "job_family": family,
                    "job_grade": grade,
                    "minimum_salary": Decimal(minimum),
                    "maximum_salary": Decimal(maximum),
                    "status": "active",
                    "created_at": self.now,
                },
            )
            positions[code] = position
        return positions

    def _seed_employees(self):
        employees = {}
        for spec in EMPLOYEES:
            department = self.departments[spec["dept"]]
            position = self.positions[spec["position"]]
            employee, created = Employee.objects.update_or_create(
                email=spec["email"],
                defaults={
                    "employee_code": spec["code"],
                    "first_name": spec["first"],
                    "last_name": spec["last"],
                    "phone": spec["phone"],
                    "national_id": f"1234{spec['code'][-4:]}",
                    "date_of_birth": spec["dob"],
                    "gender": spec["gender"],
                    "marital_status": "married",
                    "nationality": "Kenyan",
                    "address": "Westlands",
                    "city": spec["city"],
                    "state": "Nairobi County",
                    "country": "Kenya",
                    "hire_date": spec["hire"],
                    "termination_date": spec.get("termination"),
                    "employment_status": spec["employment"],
                    "employee_type": "full_time",
                    "work_location": "Head Office",
                    "department_id": department.id,
                    "position_id": position.id,
                    "job_title": spec["title"],
                    "grade": position.job_grade,
                    "level": "senior" if spec["salary"] >= 200000 else "mid",
                    "bank_name": "Equity Bank",
                    "bank_account": f"0170{spec['code'][-4:]}",
                    "bank_branch": "Westlands",
                    "salary": Decimal(spec["salary"]),
                    "salary_currency": "KES",
                    "payment_method": "bank",
                    "kra_pin": f"A00{spec['code'][-4:]}Z",
                    "nssf_number": f"NSSF{spec['code'][-4:]}",
                    "nhif_number": f"NHIF{spec['code'][-4:]}",
                    "benefits": json.dumps(
                        [
                            {"name": "House allowance", "amount": str(Decimal(spec["salary"]) * Decimal("0.15"))},
                            {"name": "Transport allowance", "amount": "10000"},
                        ]
                    ),
                    "deductions": json.dumps([{"name": "SACCO contribution", "amount": "5000"}]),
                    "emergency_contact_name": "Family contact",
                    "emergency_contact_phone": "+254 700 999 000",
                    "emergency_contact_relationship": "Spouse",
                    "status": spec["status"],
                    "created_by": self.admin_id,
                    "created_at": self.now,
                    "updated_at": self.now,
                },
            )
            employees[spec["key"]] = employee

        for spec in EMPLOYEES:
            employee = employees[spec["key"]]
            manager_key = spec.get("manager")
            if manager_key and manager_key in employees:
                employee.reports_to = employees[manager_key].id
            if spec["dept"] in ("EXEC", "HR", "SLS", "FIN"):
                department = self.departments[spec["dept"]]
                if department.manager_id != employee.id:
                    Department.objects.filter(pk=department.id).update(manager_id=employee.id)
            employee.save(update_fields=["reports_to"])

        for code, department in self.departments.items():
            head = next(
                (employees[spec["key"]] for spec in EMPLOYEES if spec["dept"] == code),
                None,
            )
            if head:
                Department.objects.filter(pk=department.id).update(manager_id=head.id)
        return employees

    def _link_users(self):
        mapping = {
            "admin": "admin@sora.com",
            "hr": "hr@sora.com",
            "manager": "manager@sora.com",
            "sales": "sales@sora.com",
            "accounts": "accounts@sora.com",
            "brian": "brian.otieno@sora.com",
        }
        for key, email in mapping.items():
            employee = self.employees[key]
            User.objects.filter(email=email).update(employee_id=employee.id)

    def _seed_leave_types(self):
        leave_types = {}
        for code, name, days, color, paid, carry, max_carry in LEAVE_TYPES:
            leave_type, _ = LeaveType.objects.update_or_create(
                leave_code=code,
                defaults={
                    "leave_name": name,
                    "description": f"{name} for eligible employees.",
                    "days_allowed": days,
                    "default_days": days,
                    "is_paid": paid,
                    "is_carry_over": carry,
                    "carry_over": carry,
                    "max_carry_over": max_carry,
                    "requires_documentation": 1 if code == "SICK" else 0,
                    "requires_document": 1 if code == "SICK" else 0,
                    "requires_approval": 1,
                    "color": color,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                },
            )
            leave_types[code] = leave_type
        return leave_types

    def _seed_balances(self):
        year = self.today.year
        offset = 0
        for employee in self.employees.values():
            if employee.employment_status in ("terminated", "resigned", "retired"):
                continue
            offset += 1
            for index, leave_type in enumerate(self.leave_types.values()):
                allowed = Decimal(leave_type.days_allowed)
                used = Decimal(min(8, (offset + index * 2) % 8))
                LeaveBalance.objects.update_or_create(
                    employee_id=employee.id,
                    leave_type_id=leave_type.id,
                    year=year,
                    defaults={
                        "total_days": allowed,
                        "total_earned": allowed,
                        "used_days": used,
                        "remaining_days": allowed - used,
                        "pending_days": Decimal("0"),
                        "carried_over": Decimal("0"),
                        "adjusted_days": Decimal("0"),
                        "created_at": self.now,
                        "updated_at": self.now,
                    },
                )

    def _weekdays(self, start, end):
        count = 0
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                count += 1
            cursor += timedelta(days=1)
        return max(count, 1)

    def _seed_leave_requests(self):
        for email_key, code, start_offset, end_offset, status, reason in LEAVE_REQUESTS:
            employee = self.employees[email_key]
            leave_type = self.leave_types[code]
            start = self.today + timedelta(days=start_offset)
            end = self.today + timedelta(days=end_offset)
            approver_email = "admin@sora.com" if employee.department_id == self.departments["HR"].id else "hr@sora.com"
            approved_by = (
                User.objects.filter(email=approver_email).values_list("id", flat=True).first()
                if status == "approved"
                else None
            )
            approved_at = self.now - timedelta(days=2) if status == "approved" else None
            LeaveRequest.objects.update_or_create(
                employee_id=employee.id,
                leave_type_id=leave_type.id,
                start_date=start,
                end_date=end,
                defaults={
                    "duration_days": Decimal(self._weekdays(start, end)),
                    "reason": reason,
                    "status": status,
                    "approved_by": approved_by,
                    "approved_at": approved_at,
                    "rejection_reason": "Operational requirements during stock count." if status == "rejected" else "",
                    "created_at": self.now - timedelta(days=abs(start_offset) + 6),
                },
            )

    def _seed_approvers(self):
        rows = [
            ("hr", "all", self.departments["HR"].id, 1, 1),
            ("manager", "overtime", self.departments["OPS"].id, 1, 1),
            ("admin", "leave", None, 2, 1),
        ]
        for key, approver_type, department_id, priority, required in rows:
            Approver.objects.update_or_create(
                employee_id=self.employees[key].id,
                approver_type=approver_type,
                defaults={
                    "department_id": department_id,
                    "priority": priority,
                    "approval_level": priority,
                    "is_required": required,
                    "status": "active",
                    "created_at": self.now,
                    "updated_at": self.now,
                },
            )

    def _seed_attendance(self):
        active = [
            employee
            for employee in self.employees.values()
            if employee.employment_status in ("active", "probation", "on_leave")
        ]
        for index, employee in enumerate(active):
            for day in range(0, 45):
                current = self.today - timedelta(days=day)
                if current.weekday() >= 5:
                    continue
                marker = (index * 3 + day) % 13
                if marker == 0:
                    status = "absent"
                    clock_in = None
                    clock_out = None
                    hours = Decimal("0")
                    overtime = Decimal("0")
                    late_minutes = 0
                elif marker in (4, 9):
                    status = "late"
                    clock_in = time(9, 12 + index % 10)
                    clock_out = time(17, 30)
                    hours = Decimal("8.0")
                    overtime = Decimal("0")
                    late_minutes = 15 + index % 10
                else:
                    status = "present"
                    clock_in = time(8, 45 + index % 10)
                    overtime = Decimal("2.0") if (index + day) % 9 == 0 else Decimal("0")
                    clock_out = time(18, 30) if overtime else time(17, 30)
                    hours = Decimal("9.0") if overtime else Decimal("8.0")
                    late_minutes = 0
                Attendance.objects.update_or_create(
                    employee_id=employee.id,
                    date=current,
                    defaults={
                        "clock_in": clock_in,
                        "clock_out": clock_out,
                        "total_hours": hours,
                        "overtime_hours": overtime,
                        "status": status,
                        "late_minutes": late_minutes,
                        "early_departure_minutes": 0,
                        "check_in_method": "web",
                        "check_out_method": "web",
                        "created_at": self.now,
                        "updated_at": self.now,
                    },
                )

    def _seed_overtime(self):
        for key, day_offset, hours, status, reason in OVERTIME:
            employee = self.employees[key]
            approver_user_id = User.objects.filter(email="manager@sora.com").values_list("id", flat=True).first()
            OvertimeRequest.objects.update_or_create(
                employee_id=employee.id,
                overtime_date=self.today + timedelta(days=day_offset),
                defaults={
                    "hours": Decimal(str(hours)),
                    "reason": reason,
                    "status": status,
                    "approved_by": approver_user_id if status == "approved" else None,
                    "approved_at": self.now - timedelta(days=2) if status == "approved" else None,
                    "converted_to_leave": 0,
                    "created_at": self.now - timedelta(days=abs(day_offset) + 2),
                    "updated_at": self.now,
                },
            )

    def _seed_reviews_and_goals(self):
        for key, reviewer_key, start_offset, end_offset, review_type, rating, status, strengths in REVIEWS:
            employee = self.employees[key]
            reviewer = self.employees[reviewer_key]
            review, _ = PerformanceReview.objects.update_or_create(
                employee_id=employee.id,
                reviewer_id=reviewer.id,
                review_period_start=self.today + timedelta(days=start_offset),
                review_period_end=self.today + timedelta(days=end_offset),
                defaults={
                    "review_type": review_type,
                    "overall_rating": Decimal(str(rating)),
                    "strengths": strengths,
                    "areas_for_improvement": "Deepen cross-functional collaboration and documentation.",
                    "goals_achieved": "Delivered the agreed quarterly objectives and improved key metrics.",
                    "future_goals": "Take ownership of a larger cross-functional initiative.",
                    "development_plan": "Leadership coaching and advanced technical training.",
                    "status": status,
                    "submitted_at": self.now - timedelta(days=10) if status != "draft" else None,
                    "reviewed_at": self.now - timedelta(days=6) if status in ("reviewed", "approved", "completed") else None,
                    "approved_at": self.now - timedelta(days=3) if status in ("approved", "completed") else None,
                    "created_at": self.now - timedelta(days=15),
                },
            )
            if status == "completed" and not review.goals_achieved:
                review.goals_achieved = "Completed all agreed goals."
                review.save(update_fields=["goals_achieved"])

        for key, name, description, category, start_offset, target_offset, progress, status, creator_key in GOALS:
            employee = self.employees[key]
            creator = self.employees[creator_key]
            Goal.objects.update_or_create(
                employee_id=employee.id,
                goal_name=name,
                defaults={
                    "description": description,
                    "category": category,
                    "start_date": self.today + timedelta(days=start_offset),
                    "target_date": self.today + timedelta(days=target_offset),
                    "weight": Decimal("20"),
                    "progress": Decimal(str(progress)),
                    "status": status,
                    "key_results": "Milestone reviews at 25%, 50% and 75% completion.",
                    "created_by": creator.id,
                    "created_at": self.now - timedelta(days=60),
                    "completed_at": self.now - timedelta(days=10) if status == "completed" else None,
                },
            )

    def _seed_recruitment(self):
        postings = []
        for spec in JOBS:
            posting, _ = JobPosting.objects.update_or_create(
                job_title=spec["title"],
                defaults={
                    "department_id": self.departments[spec["dept"]].id,
                    "position_id": self.positions[spec["position"]].id,
                    "description": spec["description"],
                    "requirements": spec["requirements"],
                    "responsibilities": spec["responsibilities"],
                    "location": spec["location"],
                    "employment_type": spec["type"],
                    "salary_range_min": Decimal(spec["salary"][0]),
                    "salary_range_max": Decimal(spec["salary"][1]),
                    "posting_date": self.today + timedelta(days=spec["posted"]),
                    "closing_date": self.today + timedelta(days=spec["closing"]),
                    "status": spec["status"],
                    "ai_match_threshold": 70,
                    "created_by": self.admin_id,
                    "created_at": self.now - timedelta(days=abs(spec["posted"])),
                },
            )
            postings.append(posting)

        candidates = []
        for index, (first, last, email, phone, job_index, status, score, source, insights) in enumerate(CANDIDATES):
            candidate, _ = Candidate.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "phone": phone,
                    "job_posting_id": postings[job_index].id,
                    "status": status,
                    "source": source,
                    "ai_score": Decimal(str(score)),
                    "ai_insights": insights,
                    "expected_salary": Decimal("150000") if job_index == 0 else Decimal("110000"),
                    "notice_period": 30,
                    "cover_letter": f"Dear Hiring Manager,\n\nI am excited to apply for the {postings[job_index].job_title} role. "
                    "My experience aligns closely with your requirements and I would welcome the opportunity to contribute.\n\nKind regards,\n"
                    + f"{first} {last}",
                    "created_at": self.now - timedelta(days=20 - index),
                },
            )
            candidates.append(candidate)

        interviews = [
            (0, "brian", "video", 3, time(10, 0), "scheduled", None, None),
            (1, "brian", "phone", 5, time(14, 0), "scheduled", None, None),
            (3, "manager", "technical", -2, time(11, 0), "completed", 4.2, "Strong sales methodology; needs product depth."),
            (4, "manager", "panel", 1, time(9, 30), "scheduled", None, None),
            (5, "manager", "video", -20, time(15, 0), "completed", 4.8, "Outstanding candidate, hired."),
        ]
        for candidate_index, interviewer_key, interview_type, day_offset, at_time, status, rating, feedback in interviews:
            candidate = candidates[candidate_index]
            scheduled = datetime.combine(
                self.today + timedelta(days=day_offset), at_time
            )
            scheduled = timezone.make_aware(scheduled, timezone.get_current_timezone())
            Interview.objects.update_or_create(
                candidate_id=candidate.id,
                job_posting_id=candidate.job_posting_id,
                scheduled_date=scheduled,
                defaults={
                    "interviewer_id": self.employees[interviewer_key].id,
                    "interview_type": interview_type,
                    "duration_minutes": 60,
                    "location": "Head Office" if interview_type != "video" else "Google Meet",
                    "meeting_link": "https://meet.google.com/sora-demo" if interview_type == "video" else "",
                    "feedback": feedback or "",
                    "rating": Decimal(str(rating)) if rating else None,
                    "status": status,
                    "created_at": self.now - timedelta(days=max(abs(day_offset), 1)),
                },
            )

    def _seed_payroll(self):
        from apps.hr.views import _calculate_payroll

        month_start = self.today.replace(day=1)
        periods = [
            (2, "paid"),
            (1, "approved"),
            (0, "draft"),
        ]
        for months_back, target_status in periods:
            month = month_start
            for _ in range(months_back):
                month = (month - timedelta(days=1)).replace(day=1)
            end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            name = month.strftime("%B %Y") + " Payroll"
            period, _ = PayrollPeriod.objects.get_or_create(
                period_name=name,
                defaults={
                    "start_date": month,
                    "end_date": end,
                    "payment_date": end,
                    "status": "draft",
                    "created_at": self.now - timedelta(days=60 if months_back == 2 else 30 if months_back == 1 else 0),
                },
            )
            if not PayrollItem.objects.filter(payroll_period_id=period.id).exists():
                _calculate_payroll(period, self.admin_user)
                period.refresh_from_db()
            if target_status == "approved" and period.status == "processing":
                period.status = "approved"
                period.approved_by = self.admin_id
                period.approved_at = self.now - timedelta(days=5)
                period.save(update_fields=["status", "approved_by", "approved_at"])
            elif target_status == "paid" and period.status in ("processing", "approved"):
                period.status = "paid"
                period.approved_by = self.admin_id
                period.approved_at = self.now - timedelta(days=35)
                period.save(update_fields=["status", "approved_by", "approved_at"])
                PayrollItem.objects.filter(payroll_period_id=period.id).update(
                    payment_status="paid",
                    paid_at=self.now - timedelta(days=32),
                    payment_reference=f"PAY-{period.id:04d}-BANK",
                    payslip_generated=1,
                )

    def _seed_recognition(self):
        badges = {}
        for name, icon, level, points, criteria in BADGES:
            badge, _ = RecognitionBadge.objects.update_or_create(
                badge_name=name,
                defaults={
                    "badge_icon": icon,
                    "level": level,
                    "points": points,
                    "criteria": criteria,
                    "description": criteria,
                    "created_at": self.now,
                },
            )
            badges[name] = badge

        awards = [
            ("brian", "Innovator", "manager", "Automated the deployment pipeline and cut release time by 70%.", "Thank you for making our releases effortless!"),
            ("mercy", "Customer Hero", "sales", "Recovered a key account and grew it by 30% this quarter.", "Clients love working with you."),
            ("lucy", "Team Player", "hr", "Coordinated a flawless onboarding week for six new hires.", "You make everyone feel welcome."),
            ("david", "Above & Beyond", "manager", "Led the stock-take through a system outage without losing a day.", "Exceptional composure under pressure."),
            ("accounts", "Leadership Excellence", "admin", "Guided the finance team through a clean external audit.", "Your leadership sets the standard."),
            ("esther", "Customer Hero", "sales", "Achieved the highest CSAT score in the company for three months.", "Customers consistently praise your care."),
            ("john", "Team Player", "manager", "Supported evening deliveries during the busiest week of the quarter.", "Reliable and always willing to help."),
            ("brian", "Wellness Champion", "hr", "Organised weekly walking meetings and a mental-health talk.", "Thank you for championing wellbeing."),
        ]
        for index, (key, badge_name, giver_key, reason, message) in enumerate(awards):
            EmployeeRecognition.objects.update_or_create(
                employee_id=self.employees[key].id,
                badge_id=badges[badge_name].id,
                reason=reason,
                defaults={
                    "given_by": self.employees[giver_key].id,
                    "message": message,
                    "points_earned": badges[badge_name].points,
                    "created_at": self.now - timedelta(days=index * 17 + 2),
                },
            )

    def _seed_wellness(self):
        active = [
            employee
            for employee in self.employees.values()
            if employee.employment_status in ("active", "probation", "on_leave")
        ]
        for index, employee in enumerate(active):
            for day_offset in range(0, 21):
                if (index + day_offset) % 5 == 0:
                    continue
                current = self.today - timedelta(days=day_offset)
                mood = 5 + (index * 3 + day_offset * 2) % 5
                stress = 3 + (index + day_offset) % 5
                energy = 5 + (index + day_offset * 3) % 5
                WellnessCheckin.objects.update_or_create(
                    employee_id=employee.id,
                    checkin_date=current,
                    defaults={
                        "mood_score": mood,
                        "stress_level": stress,
                        "energy_level": energy,
                        "sleep_hours": Decimal("6.5") + Decimal("0.5") * Decimal((index + day_offset) % 4),
                        "notes": "Busy day, but a good one." if day_offset % 6 == 0 else "",
                        "created_at": self.now - timedelta(days=day_offset),
                    },
                )

    def _seed_training(self):
        programs = {}
        for code, name, hours, cost, provider, certification in TRAININGS:
            program, _ = TrainingProgram.objects.update_or_create(
                program_code=code,
                defaults={
                    "program_name": name,
                    "description": f"{name} delivered by {provider}.",
                    "duration_hours": hours,
                    "cost": Decimal(cost),
                    "provider": provider,
                    "certification": certification,
                    "status": "active",
                    "ai_recommended": 1 if code in ("TRN-2026-0001", "TRN-2026-0003") else 0,
                    "created_at": self.now,
                },
            )
            programs[code] = program

        enrollments = [
            ("hr", "TRN-2026-0002", "completed", 88),
            ("lucy", "TRN-2026-0001", "in_progress", None),
            ("brian", "TRN-2026-0001", "in_progress", None),
            ("brian", "TRN-2026-0003", "completed", 92),
            ("mercy", "TRN-2026-0004", "completed", 85),
            ("esther", "TRN-2026-0004", "in_progress", None),
            ("accounts", "TRN-2026-0003", "completed", 90),
            ("samuel", "TRN-2026-0003", "enrolled", None),
            ("david", "TRN-2026-0005", "completed", 78),
            ("john", "TRN-2026-0005", "enrolled", None),
        ]
        for key, code, status, score in enrollments:
            EmployeeTraining.objects.update_or_create(
                employee_id=self.employees[key].id,
                training_id=programs[code].id,
                defaults={
                    "status": status,
                    "score": Decimal(str(score)) if score is not None else None,
                    "completion_date": self.today - timedelta(days=15) if status == "completed" else None,
                    "created_at": self.now - timedelta(days=40),
                },
            )

    def _seed_succession(self):
        plans = [
            (
                "EXEC-001",
                "admin",
                ["manager", "hr"],
                "ready_3_5_years",
                "critical",
                "Rotate Peter and Grace through strategy and board-facing assignments.",
            ),
            (
                "HR-001",
                "hr",
                ["lucy"],
                "ready_1_2_years",
                "high",
                "Expose Lucy to payroll, ER cases and HR analytics over the next 12 months.",
            ),
            (
                "OPS-001",
                "manager",
                ["david"],
                "ready_3_5_years",
                "medium",
                "Build David's exposure to supplier management and budgeting.",
            ),
        ]
        for position_code, holder_key, successor_keys, readiness, risk, development in plans:
            SuccessionPlan.objects.update_or_create(
                position_id=self.positions[position_code].id,
                defaults={
                    "current_holder_id": self.employees[holder_key].id,
                    "potential_successors": json.dumps(
                        [self.employees[key].id for key in successor_keys]
                    ),
                    "readiness_level": readiness,
                    "risk_level": risk,
                    "development_plan": development,
                    "ai_prediction": "Succession coverage is adequate if development milestones are met.",
                    "updated_at": self.now,
                },
            )

    def _seed_documents_skills_movements(self):
        document_path = f"{tenant_media_prefix()}/hr/employee-contract-sample.pdf"
        self._ensure_sample_file(
            document_path,
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
        )
        for key, document_type, name in [
            ("hr", "contract", "Employment Contract - Grace Wanjiku"),
            ("brian", "certificate", "Django Advanced Certificate"),
            ("sales", "id", "National ID Copy"),
        ]:
            EmployeeDocument.objects.get_or_create(
                employee_id=self.employees[key].id,
                document_name=name,
                defaults={
                    "document_type": document_type,
                    "file_path": document_path,
                    "file_size": 2048,
                    "mime_type": "application/pdf",
                    "uploaded_by": self.admin_id,
                    "created_at": self.now,
                },
            )

        skills = {}
        for code, name, category in SKILLS:
            skill, _ = Skill.objects.update_or_create(
                skill_code=code,
                defaults={
                    "skill_name": name,
                    "category": category,
                    "description": f"{name} capability.",
                    "is_certification": 0,
                    "created_at": self.now,
                },
            )
            skills[code] = skill

        rows = [
            ("brian", "SK-001", "expert", 6),
            ("brian", "SK-006", "advanced", 5),
            ("hr", "SK-005", "expert", 9),
            ("hr", "SK-002", "advanced", 7),
            ("manager", "SK-005", "advanced", 8),
            ("accounts", "SK-003", "expert", 8),
            ("mercy", "SK-004", "advanced", 5),
            ("lucy", "SK-002", "intermediate", 3),
        ]
        for key, skill_code, proficiency, years in rows:
            EmployeeSkill.objects.update_or_create(
                employee_id=self.employees[key].id,
                skill_id=skills[skill_code].id,
                defaults={
                    "proficiency": proficiency,
                    "years_of_experience": Decimal(years),
                    "certified": 0,
                    "created_at": self.now,
                },
            )

        movements = [
            ("manager", "promotion", "Operations Supervisor", "Operations Manager", -400, 150000, 250000),
            ("sales", "department_change", "Sales Representative", "Sales Executive", -500, 90000, 120000),
            ("lucy", "promotion", "HR Assistant", "HR Officer", -200, 70000, 95000),
        ]
        for key, movement_type, from_position, to_position, day_offset, from_salary, to_salary in movements:
            employee = self.employees[key]
            EmployeeMovement.objects.update_or_create(
                employee_id=employee.id,
                movement_type=movement_type,
                effective_date=self.today + timedelta(days=day_offset),
                defaults={
                    "from_position": from_position,
                    "to_position": to_position,
                    "from_department": "Operations",
                    "to_department": "Operations",
                    "from_salary": Decimal(from_salary),
                    "to_salary": Decimal(to_salary),
                    "reason": "Performance and expanded responsibilities.",
                    "approved_by": self.admin_id,
                    "created_at": self.now - timedelta(days=abs(day_offset)),
                },
            )

        feedback_rows = [
            ("manager", "brian", "praise", "Brian unblocked the finance integration at short notice. Great teamwork.", 1),
            ("hr", "mercy", "praise", "Mercy consistently lifts the team's energy and shares client insights.", 1),
            ("sales", "esther", "constructive", "Keep documenting customer follow-ups in the CRM.", 0),
        ]
        for from_key, to_key, feedback_type, message, public in feedback_rows:
            EmployeeFeedback.objects.update_or_create(
                from_employee_id=self.employees[from_key].id,
                to_employee_id=self.employees[to_key].id,
                message=message,
                defaults={
                    "feedback_type": feedback_type,
                    "is_public": public,
                    "created_at": self.now - timedelta(days=12),
                },
            )

    def _ensure_sample_file(self, path, content):
        if not default_storage.exists(path):
            default_storage.save(path, ContentFile(content))

