import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from datetime import timedelta

from django.test import Client as TestClient
from django.utils import timezone

from apps.core.models import (
    Attendance,
    Candidate,
    Employee,
    EmployeeRecognition,
    EmployeeTraining,
    Goal,
    Interview,
    LeaveBalance,
    LeaveRequest,
    LeaveTransaction,
    LeaveType,
    OvertimeRequest,
    PayrollItem,
    PayrollPeriod,
    RecognitionBadge,
    TrainingProgram,
    User,
    WellnessCheckin,
)
from apps.core.tenancy import default_tenant, set_current_tenant

set_current_tenant(default_tenant())


def login(email, password):
    client = TestClient(headers={"host": "localhost"})
    assert client.login(email=email, password=password), email
    return client


today = timezone.localdate()
brian = Employee.objects.get(email="brian.otieno@sora.com")

# Clean leftovers from previous runs so this script is repeatable.
stale = LeaveRequest.objects.filter(reason__startswith="Action test")
LeaveTransaction.objects.filter(reference__in=[f"LV-{pk}" for pk in stale.values_list("id", flat=True)]).delete()
stale.delete()
WellnessCheckin.objects.filter(employee_id=brian.id, checkin_date=today).delete()
Attendance.objects.filter(employee_id=brian.id, date=today).delete()

mgr = login("manager@sora.com", "manager123")
admin = login("admin@sora.com", "admin123")
bc = login("brian.otieno@sora.com", "employee123")
annual = LeaveType.objects.get(leave_code="ANN")

start = today + timedelta(days=30)
while start.weekday() >= 5:
    start += timedelta(days=1)
end = start + timedelta(days=1)
if end.weekday() >= 5:
    end = start
r = bc.post(
    "/hr/leave/request",
    {
        "leave_type": annual.id,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "reason": "Action test leave",
    },
    follow=True,
)
lr = LeaveRequest.objects.filter(employee_id=brian.id, reason="Action test leave").order_by("-id").first()
print("create leave", r.status_code, bool(lr), lr.status if lr else None, lr.duration_days if lr else None)
if lr:
    r = bc.post(f"/hr/leave/cancel/{lr.id}", follow=True)
    lr.refresh_from_db()
    print("cancel leave", r.status_code, lr.status)

start2 = start + timedelta(days=4)
while start2.weekday() >= 5:
    start2 += timedelta(days=1)
end2 = start2 + timedelta(days=1)
if end2.weekday() >= 5:
    end2 = start2
r = bc.post(
    "/hr/leave/request",
    {
        "leave_type": annual.id,
        "start_date": start2.isoformat(),
        "end_date": end2.isoformat(),
        "reason": "Action test approve",
    },
    follow=True,
)
lr2 = LeaveRequest.objects.filter(employee_id=brian.id, reason="Action test approve").order_by("-id").first()
bal = LeaveBalance.objects.get(employee_id=brian.id, leave_type_id=annual.id, year=start2.year)
before = bal.remaining_days
r = mgr.post(f"/hr/leave/approve/{lr2.id}", follow=True)
lr2.refresh_from_db()
bal.refresh_from_db()
print(
    "approve leave",
    r.status_code,
    lr2.status,
    "remaining",
    before,
    "->",
    bal.remaining_days,
    "txn",
    LeaveTransaction.objects.filter(reference=f"LV-{lr2.id}").exists(),
)
r = mgr.post(f"/hr/leave/approve/{lr2.id}", follow=True)
bal.refresh_from_db()
print("reapprove", r.status_code, "remaining", bal.remaining_days)

mercy_pending = (
    LeaveRequest.objects.filter(status="pending").exclude(reason__startswith="Action test").first()
)
if mercy_pending is None:
    mercy = Employee.objects.get(email="mercy.achieng@sora.com")
    reject_start = today + timedelta(days=90)
    while reject_start.weekday() >= 5:
        reject_start += timedelta(days=1)
    admin.post(
        "/hr/leave/request",
        {
            "employee": mercy.id,
            "leave_type": annual.id,
            "start_date": reject_start.isoformat(),
            "end_date": reject_start.isoformat(),
            "reason": "Action test reject",
        },
        follow=True,
    )
    mercy_pending = (
        LeaveRequest.objects.filter(employee_id=mercy.id, reason="Action test reject")
        .order_by("-id")
        .first()
    )
r = mgr.post(
    f"/hr/leave/reject/{mercy_pending.id}",
    {"rejection_reason": "Business critical week"},
    follow=True,
)
mercy_pending.refresh_from_db()
print("reject leave", r.status_code, mercy_pending.status, mercy_pending.rejection_reason)

ot = OvertimeRequest.objects.filter(status="pending").order_by("-id").first()
if ot is None:
    ot_date = today - timedelta(days=1)
    admin.post(
        "/hr/leave/overtime",
        {
            "employee": brian.id,
            "overtime_date": ot_date.isoformat(),
            "hours": "2.5",
            "reason": "Action test overtime",
        },
        follow=True,
    )
    ot = (
        OvertimeRequest.objects.filter(employee_id=brian.id, reason="Action test overtime")
        .order_by("-id")
        .first()
    )
ot_bal = LeaveBalance.objects.filter(
    employee_id=ot.employee_id, leave_type_id=annual.id, year=ot.overtime_date.year
).first()
before = ot_bal.remaining_days if ot_bal else None
r = admin.post(f"/hr/leave/overtime/approve/{ot.id}", {"leave_type": annual.id}, follow=True)
ot.refresh_from_db()
ot_bal = LeaveBalance.objects.get(
    employee_id=ot.employee_id, leave_type_id=annual.id, year=ot.overtime_date.year
)
print("approve overtime", r.status_code, ot.status, "remaining", before, "->", ot_bal.remaining_days)

r = bc.post(
    "/hr/wellness/checkin",
    {
        "mood_score": 8,
        "stress_level": 3,
        "energy_level": 8,
        "sleep_hours": "7.5",
        "notes": "Feeling great",
    },
    follow=True,
)
w = WellnessCheckin.objects.get(employee_id=brian.id, checkin_date=today)
print("wellness", r.status_code, w.mood_score, w.notes)

program = TrainingProgram.objects.get(program_code="TRN-2026-0002")
r = bc.post("/hr/training/enroll", {"program_id": program.id, "next": "/hr/training"}, follow=True)
enr = EmployeeTraining.objects.get(employee_id=brian.id, training_id=program.id)
print("enroll", r.status_code, enr.status)
r = bc.post(
    f"/hr/training/complete/{enr.id}",
    {"score": "87", "next": "/hr/training/my-trainings"},
    follow=True,
)
enr.refresh_from_db()
print("complete", r.status_code, enr.status, enr.score)

mercy = Employee.objects.get(email="mercy.achieng@sora.com")
badge = RecognitionBadge.objects.get(badge_name="Team Player")
r = bc.post(
    "/hr/recognition/give",
    {
        "employee": mercy.id,
        "badge": badge.id,
        "reason": "Helped with a client",
        "message": "Thanks Mercy!",
    },
    follow=True,
)
print(
    "recognition",
    r.status_code,
    EmployeeRecognition.objects.filter(
        employee_id=mercy.id, badge_id=badge.id, reason="Helped with a client"
    ).exists(),
)

g = Goal.objects.filter(employee_id=brian.id).first()
old = g.progress
r = bc.post("/hr/performance/goals", {"goal_id": g.id, "progress": "100"}, follow=True)
g.refresh_from_db()
print("goal", r.status_code, old, "->", g.progress, g.status)

Attendance.objects.filter(employee_id=brian.id, date=today).delete()
r = bc.post("/hr/attendance/clock-in", {"next": "/hr/attendance/my-time"}, follow=True)
a = Attendance.objects.filter(employee_id=brian.id, date=today).first()
print("clock in", r.status_code, bool(a and a.clock_in))
r = bc.post("/hr/attendance/clock-out", {"next": "/hr/attendance/my-time"}, follow=True)
a.refresh_from_db()
print("clock out", r.status_code, a.clock_out, a.total_hours)

alice = Candidate.objects.get(email="alice.wanjiru@example.com")
interview = Interview.objects.filter(candidate_id=alice.id).first()
r = admin.post(
    f"/hr/recruitment/interview/{interview.id}",
    {"feedback": "Excellent depth", "rating": "4.5", "status": "completed"},
    follow=True,
)
interview.refresh_from_db()
print("interview feedback", r.status_code, interview.status, interview.rating)

cand = Candidate.objects.get(email="naomi.kilonzo@example.com")
r = admin.post(f"/hr/recruitment/candidate/{cand.id}", {"status": "screening"}, follow=True)
cand.refresh_from_db()
print("candidate", r.status_code, cand.status)
r = admin.post(f"/hr/recruitment/candidate/{cand.id}", {"status": "hired"}, follow=True)
cand.refresh_from_db()
print("candidate invalid", r.status_code, cand.status)

nm = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
ne = (nm + timedelta(days=27)).replace(day=28) + timedelta(days=4)
ne = ne.replace(day=1) - timedelta(days=1)
r = admin.post(
    "/hr/payroll/create-period",
    {
        "period_name": nm.strftime("%B %Y") + " Payroll",
        "start_date": nm.isoformat(),
        "end_date": ne.isoformat(),
        "payment_date": ne.isoformat(),
    },
    follow=True,
)
p = PayrollPeriod.objects.filter(period_name=nm.strftime("%B %Y") + " Payroll").first()
print("create period", r.status_code, bool(p), p.status if p else None)
if p:
    r = admin.post(f"/hr/payroll/calculate/{p.id}", follow=True)
    p.refresh_from_db()
    print("calculate", r.status_code, p.status, PayrollItem.objects.filter(payroll_period_id=p.id).count())
    r = admin.post(f"/hr/payroll/approve/{p.id}", follow=True)
    p.refresh_from_db()
    print("approve payroll", r.status_code, p.status)
    r = admin.post(f"/hr/payroll/mark-paid/{p.id}", follow=True)
    p.refresh_from_db()
    print(
        "mark paid",
        r.status_code,
        p.status,
        PayrollItem.objects.filter(payroll_period_id=p.id, payment_status="paid").count(),
    )
