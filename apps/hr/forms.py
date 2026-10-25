"""Forms for the HR module."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from apps.core.models import (
    Approver,
    Candidate,
    Department,
    Employee,
    Goal,
    Interview,
    JobPosting,
    LeaveRequest,
    LeaveType,
    OvertimeRequest,
    PayrollPeriod,
    PerformanceReview,
    Position,
    RecognitionBadge,
    SuccessionPlan,
    TrainingProgram,
    WellnessCheckin,
)

ACTIVE_EMPLOYEE_STATUSES = ("active", "probation", "on_leave")


class StyledFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(
                field.widget,
                (forms.CheckboxInput, forms.CheckboxSelectMultiple, forms.FileInput),
            ):
                continue
            if isinstance(field.widget, forms.SelectMultiple):
                field.widget.attrs.setdefault("class", "form-control")
                field.widget.attrs.setdefault("size", 6)
                continue
            css = field.widget.attrs.get("class", "")
            if "form-control" not in css:
                field.widget.attrs["class"] = (css + " form-control").strip()


def _employee_field(label="Employee", required=True):
    return forms.ModelChoiceField(
        queryset=Employee.objects.exclude(status="inactive")
        .exclude(employment_status__in=["terminated", "resigned", "retired"])
        .order_by("first_name", "last_name"),
        required=required,
        label=label,
        empty_label="Select employee",
    )


def _date_input():
    return forms.DateInput(attrs={"type": "date"})


def _datetime_input():
    return forms.DateTimeInput(
        attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
    )


class EmployeeForm(StyledFormMixin, forms.ModelForm):
    department = forms.ModelChoiceField(
        queryset=Department.objects.order_by("department_name"),
        required=False,
        empty_label="No department",
    )
    position = forms.ModelChoiceField(
        queryset=Position.objects.order_by("position_name"),
        required=False,
        empty_label="No position",
    )
    manager = forms.ModelChoiceField(
        queryset=Employee.objects.exclude(status="inactive").order_by(
            "first_name", "last_name"
        ),
        required=False,
        label="Reports to",
        empty_label="No manager",
    )

    class Meta:
        model = Employee
        fields = [
            "first_name",
            "middle_name",
            "last_name",
            "national_id",
            "email",
            "personal_email",
            "phone",
            "alternative_phone",
            "date_of_birth",
            "gender",
            "marital_status",
            "nationality",
            "address",
            "city",
            "state",
            "postal_code",
            "country",
            "hire_date",
            "confirmation_date",
            "employment_status",
            "employee_type",
            "work_shift",
            "work_location",
            "job_title",
            "level",
            "grade",
            "bank_name",
            "bank_account",
            "bank_branch",
            "salary",
            "salary_currency",
            "payment_method",
            "mobile_money_number",
            "kra_pin",
            "nssf_number",
            "nhif_number",
            "tax_identification",
            "benefits",
            "deductions",
            "emergency_contact_name",
            "emergency_contact_phone",
            "emergency_contact_relationship",
            "status",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            "hire_date": forms.DateInput(attrs={"type": "date"}),
            "confirmation_date": forms.DateInput(attrs={"type": "date"}),
            "address": forms.Textarea(attrs={"rows": 2}),
            "benefits": forms.Textarea(
                attrs={"rows": 3, "placeholder": '[{"name": "House allowance", "amount": 15000}]'}
            ),
            "deductions": forms.Textarea(
                attrs={"rows": 3, "placeholder": '[{"name": "Loan repayment", "amount": 5000}]'}
            ),
        }
        labels = {
            "national_id": "National ID",
            "kra_pin": "KRA PIN",
            "nssf_number": "NSSF number",
            "nhif_number": "SHIF/NHIF number",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["department"].initial = self.instance.department_id
            self.fields["position"].initial = self.instance.position_id
            self.fields["manager"].initial = self.instance.reports_to
            self.fields["manager"].queryset = self.fields["manager"].queryset.exclude(
                pk=self.instance.pk
            )
        for name in ("benefits", "deductions"):
            self.fields[name].help_text = "JSON list of {name, amount} or a free-text note."

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("hire_date")
        confirmation = cleaned.get("confirmation_date")
        if start and confirmation and confirmation < start:
            self.add_error("confirmation_date", "Confirmation date cannot precede hire date.")
        salary = cleaned.get("salary")
        if salary is not None and salary < 0:
            self.add_error("salary", "Salary cannot be negative.")
        for name in ("benefits", "deductions"):
            value = (cleaned.get(name) or "").strip()
            if value:
                try:
                    parsed = json.loads(value)
                except ValueError:
                    continue
                if not isinstance(parsed, (list, dict)):
                    self.add_error(name, "Use a JSON list/object or plain text.")
        return cleaned

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.department_id = (
            self.cleaned_data["department"].id if self.cleaned_data.get("department") else None
        )
        employee.position_id = (
            self.cleaned_data["position"].id if self.cleaned_data.get("position") else None
        )
        manager = self.cleaned_data.get("manager")
        employee.reports_to = manager.id if manager else None
        if commit:
            employee.save()
        return employee


class DepartmentForm(StyledFormMixin, forms.ModelForm):
    manager = forms.ModelChoiceField(
        queryset=Employee.objects.exclude(status="inactive").order_by(
            "first_name", "last_name"
        ),
        required=False,
        empty_label="No manager",
    )
    parent = forms.ModelChoiceField(
        queryset=Department.objects.order_by("department_name"),
        required=False,
        label="Parent department",
        empty_label="Top level",
    )

    class Meta:
        model = Department
        fields = [
            "department_code",
            "department_name",
            "description",
            "budget",
            "cost_center",
            "status",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["manager"].initial = self.instance.manager_id
            self.fields["parent"].initial = self.instance.parent_department_id
            self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(
                pk=self.instance.pk
            )

    def clean_department_code(self):
        code = (self.cleaned_data.get("department_code") or "").strip().upper()
        if not code:
            raise ValidationError("Department code is required.")
        return code

    def clean_budget(self):
        budget = self.cleaned_data.get("budget")
        if budget is not None and budget < 0:
            raise ValidationError("Budget cannot be negative.")
        return budget

    def save(self, commit=True):
        department = super().save(commit=False)
        manager = self.cleaned_data.get("manager")
        parent = self.cleaned_data.get("parent")
        department.manager_id = manager.id if manager else None
        department.parent_department_id = parent.id if parent else None
        if commit:
            department.save()
        return department


class PositionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Position
        fields = [
            "position_code",
            "position_name",
            "job_description",
            "responsibilities",
            "qualifications",
            "required_skills",
            "job_family",
            "job_grade",
            "salary_grade",
            "minimum_salary",
            "maximum_salary",
            "status",
        ]
        widgets = {
            "job_description": forms.Textarea(attrs={"rows": 3}),
            "responsibilities": forms.Textarea(attrs={"rows": 3}),
            "qualifications": forms.Textarea(attrs={"rows": 3}),
            "required_skills": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_position_code(self):
        return (self.cleaned_data.get("position_code") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("minimum_salary")
        maximum = cleaned.get("maximum_salary")
        if minimum is not None and maximum is not None and maximum < minimum:
            self.add_error("maximum_salary", "Maximum salary cannot be below the minimum.")
        return cleaned


class LeaveTypeForm(StyledFormMixin, forms.ModelForm):
    is_paid = forms.BooleanField(required=False, initial=True, label="Paid leave")
    is_carry_over = forms.BooleanField(required=False, label="Allow carry over")
    requires_documentation = forms.BooleanField(required=False, label="Require documentation")
    requires_approval = forms.BooleanField(required=False, initial=True, label="Requires approval")

    class Meta:
        model = LeaveType
        fields = [
            "leave_code",
            "leave_name",
            "description",
            "days_allowed",
            "is_paid",
            "is_carry_over",
            "max_carry_over",
            "requires_documentation",
            "requires_approval",
            "color",
            "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 2}),
            "color": forms.TextInput(attrs={"type": "color"}),
        }

    def clean_leave_code(self):
        return (self.cleaned_data.get("leave_code") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("is_carry_over") and not cleaned.get("max_carry_over"):
            self.add_error("max_carry_over", "Set a maximum carry-over when carry over is enabled.")
        if (cleaned.get("days_allowed") or 0) < 0:
            self.add_error("days_allowed", "Days allowed cannot be negative.")
        return cleaned

    def save(self, commit=True):
        leave_type = super().save(commit=False)
        for name in (
            "is_paid",
            "is_carry_over",
            "requires_documentation",
            "requires_approval",
        ):
            setattr(leave_type, name, 1 if self.cleaned_data.get(name) else 0)
        if commit:
            leave_type.save()
        return leave_type


class LeaveRequestForm(StyledFormMixin, forms.ModelForm):
    employee = _employee_field()
    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.objects.filter(status="active").order_by("leave_name"),
        empty_label="Select leave type",
    )
    start_date = forms.DateField(widget=_date_input())
    end_date = forms.DateField(widget=_date_input())
    attachment = forms.FileField(required=False, label="Supporting document")

    class Meta:
        model = LeaveRequest
        fields = ["employee", "leave_type", "start_date", "end_date", "reason"]
        widgets = {"reason": forms.Textarea(attrs={"rows": 3})}

    def clean_attachment(self):
        upload = self.cleaned_data.get("attachment")
        if not upload:
            return upload
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError("Attachment must be 5 MB or smaller.")
        suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if suffix not in {"pdf", "png", "jpg", "jpeg", "doc", "docx"}:
            raise ValidationError("Allowed attachment types: PDF, PNG, JPG, DOC, DOCX.")
        return upload

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date must be on or after the start date.")
        if start and end and start.weekday() > 4 and end.weekday() > 4:
            self.add_error("start_date", "The selected range contains no working days.")
        return cleaned

    def save(self, commit=True):
        leave = super().save(commit=False)
        leave.employee_id = self.cleaned_data["employee"].id
        leave.leave_type_id = self.cleaned_data["leave_type"].id
        if commit:
            leave.save()
        return leave


class OvertimeRequestForm(StyledFormMixin, forms.ModelForm):
    employee = _employee_field()

    class Meta:
        model = OvertimeRequest
        fields = ["employee", "overtime_date", "hours", "reason"]
        widgets = {
            "overtime_date": forms.DateInput(attrs={"type": "date"}),
            "reason": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_hours(self):
        hours = self.cleaned_data.get("hours")
        if hours is None or hours <= 0:
            raise ValidationError("Hours must be greater than zero.")
        if hours > 24:
            raise ValidationError("Overtime cannot exceed 24 hours in a day.")
        return hours

    def save(self, commit=True):
        overtime = super().save(commit=False)
        overtime.employee_id = self.cleaned_data["employee"].id
        if commit:
            overtime.save()
        return overtime


class ApproverForm(StyledFormMixin, forms.ModelForm):
    employee = _employee_field(label="Approver")

    class Meta:
        model = Approver
        fields = [
            "employee",
            "approver_type",
            "department_id",
            "priority",
            "approval_level",
            "is_required",
            "status",
        ]
        labels = {"department_id": "Department (optional)", "is_required": "Required approver"}
        widgets = {"department_id": forms.NumberInput(attrs={"placeholder": "All departments"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["department_id"].required = False

    def save(self, commit=True):
        approver = super().save(commit=False)
        approver.employee_id = self.cleaned_data["employee"].id
        if commit:
            approver.save()
        return approver


class PerformanceReviewForm(StyledFormMixin, forms.ModelForm):
    employee = _employee_field()
    reviewer = _employee_field(label="Reviewer")
    review_period_start = forms.DateField(widget=_date_input(), label="Period start")
    review_period_end = forms.DateField(widget=_date_input(), label="Period end")

    class Meta:
        model = PerformanceReview
        fields = [
            "employee",
            "reviewer",
            "review_period_start",
            "review_period_end",
            "review_type",
            "overall_rating",
            "strengths",
            "areas_for_improvement",
            "goals_achieved",
            "future_goals",
            "development_plan",
        ]
        widgets = {
            "strengths": forms.Textarea(attrs={"rows": 2}),
            "areas_for_improvement": forms.Textarea(attrs={"rows": 2}),
            "goals_achieved": forms.Textarea(attrs={"rows": 2}),
            "future_goals": forms.Textarea(attrs={"rows": 2}),
            "development_plan": forms.Textarea(attrs={"rows": 2}),
            "overall_rating": forms.NumberInput(attrs={"step": "0.1", "min": "1", "max": "5"}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("review_period_start")
        end = cleaned.get("review_period_end")
        if start and end and end < start:
            self.add_error("review_period_end", "Period end must be after the start.")
        employee = cleaned.get("employee")
        reviewer = cleaned.get("reviewer")
        if employee and reviewer and employee.id == reviewer.id:
            self.add_error("reviewer", "An employee cannot review themselves.")
        rating = cleaned.get("overall_rating")
        if rating is not None and not (Decimal("1") <= rating <= Decimal("5")):
            self.add_error("overall_rating", "Rating must be between 1 and 5.")
        return cleaned

    def save(self, commit=True):
        review = super().save(commit=False)
        review.employee_id = self.cleaned_data["employee"].id
        review.reviewer_id = self.cleaned_data["reviewer"].id
        if commit:
            review.save()
        return review


class GoalForm(StyledFormMixin, forms.ModelForm):
    employee = _employee_field()

    class Meta:
        model = Goal
        fields = [
            "employee",
            "goal_name",
            "description",
            "category",
            "start_date",
            "target_date",
            "weight",
            "progress",
            "key_results",
        ]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "target_date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "key_results": forms.Textarea(attrs={"rows": 2}),
            "progress": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "1"}),
            "weight": forms.NumberInput(attrs={"min": "0", "max": "100", "step": "0.01"}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("target_date")
        if start and end and end < start:
            self.add_error("target_date", "Target date must be on or after the start date.")
        progress = cleaned.get("progress")
        if progress is not None and not (0 <= progress <= 100):
            self.add_error("progress", "Progress must be between 0 and 100.")
        return cleaned

    def save(self, commit=True):
        goal = super().save(commit=False)
        goal.employee_id = self.cleaned_data["employee"].id
        if commit:
            goal.save()
        return goal


class FeedbackForm(StyledFormMixin, forms.Form):
    to_employee = _employee_field(label="Recipient")
    feedback_type = forms.ChoiceField(
        choices=[("praise", "Praise"), ("constructive", "Constructive"), ("anonymous", "Anonymous")]
    )
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    is_public = forms.BooleanField(required=False, label="Share with the team feed")


class JobPostingForm(StyledFormMixin, forms.ModelForm):
    department = forms.ModelChoiceField(
        queryset=Department.objects.order_by("department_name"),
        required=False,
        empty_label="No department",
    )
    position = forms.ModelChoiceField(
        queryset=Position.objects.order_by("position_name"),
        required=False,
        empty_label="No position",
    )
    posting_date = forms.DateField(widget=_date_input(), initial=date.today)
    closing_date = forms.DateField(widget=_date_input(), required=False)

    class Meta:
        model = JobPosting
        fields = [
            "job_title",
            "description",
            "requirements",
            "responsibilities",
            "location",
            "employment_type",
            "salary_range_min",
            "salary_range_max",
            "posting_date",
            "closing_date",
            "status",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "requirements": forms.Textarea(attrs={"rows": 3}),
            "responsibilities": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["department"].initial = self.instance.department_id
            self.fields["position"].initial = self.instance.position_id

    def clean(self):
        cleaned = super().clean()
        minimum = cleaned.get("salary_range_min")
        maximum = cleaned.get("salary_range_max")
        if minimum is not None and maximum is not None and maximum < minimum:
            self.add_error("salary_range_max", "Maximum salary cannot be below the minimum.")
        start = cleaned.get("posting_date")
        end = cleaned.get("closing_date")
        if start and end and end < start:
            self.add_error("closing_date", "Closing date must be on or after the posting date.")
        return cleaned

    def save(self, commit=True):
        posting = super().save(commit=False)
        department = self.cleaned_data.get("department")
        position = self.cleaned_data.get("position")
        posting.department_id = department.id if department else None
        posting.position_id = position.id if position else None
        if commit:
            posting.save()
        return posting


class CandidateForm(StyledFormMixin, forms.ModelForm):
    job_posting = forms.ModelChoiceField(
        queryset=JobPosting.objects.order_by("-posting_date"),
        label="Job opening",
        empty_label="Select job",
    )
    resume = forms.FileField(required=False, label="Resume")

    class Meta:
        model = Candidate
        fields = [
            "job_posting",
            "first_name",
            "last_name",
            "email",
            "phone",
            "source",
            "expected_salary",
            "notice_period",
            "linkedin_url",
            "portfolio_url",
            "cover_letter",
        ]
        widgets = {"cover_letter": forms.Textarea(attrs={"rows": 3})}

    def clean_resume(self):
        upload = self.cleaned_data.get("resume")
        if not upload:
            return upload
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError("Resume must be 5 MB or smaller.")
        suffix = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if suffix not in {"pdf", "doc", "docx"}:
            raise ValidationError("Allowed resume types: PDF, DOC, DOCX.")
        return upload

    def clean_expected_salary(self):
        salary = self.cleaned_data.get("expected_salary")
        if salary is not None and salary < 0:
            raise ValidationError("Expected salary cannot be negative.")
        return salary

    def save(self, commit=True):
        candidate = super().save(commit=False)
        candidate.job_posting_id = self.cleaned_data["job_posting"].id
        if commit:
            candidate.save()
        return candidate


class InterviewForm(StyledFormMixin, forms.ModelForm):
    candidate = forms.ModelChoiceField(
        queryset=Candidate.objects.exclude(status__in=["hired", "withdrawn"]).order_by(
            "first_name", "last_name"
        ),
        empty_label="Select candidate",
    )
    job_posting = forms.ModelChoiceField(
        queryset=JobPosting.objects.exclude(status__in=["cancelled"]).order_by("-posting_date"),
        label="Job opening",
        empty_label="Select job",
    )
    interviewer = _employee_field(label="Interviewer", required=False)
    scheduled_date = forms.DateTimeField(
        widget=_datetime_input(), input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"]
    )

    class Meta:
        model = Interview
        fields = [
            "candidate",
            "job_posting",
            "interviewer",
            "interview_type",
            "scheduled_date",
            "duration_minutes",
            "location",
            "meeting_link",
        ]

    def clean(self):
        cleaned = super().clean()
        scheduled = cleaned.get("scheduled_date")
        candidate = cleaned.get("candidate")
        job = cleaned.get("job_posting")
        if scheduled:
            from django.utils import timezone

            if timezone.is_naive(scheduled):
                scheduled = timezone.make_aware(scheduled)
                cleaned["scheduled_date"] = scheduled
            if scheduled < timezone.now():
                self.add_error("scheduled_date", "Interviews must be scheduled in the future.")
            if candidate and job:
                existing = Interview.objects.filter(
                    candidate_id=candidate.id,
                    job_posting_id=job.id,
                    scheduled_date=scheduled,
                    status__in=["scheduled", "rescheduled"],
                )
                if self.instance.pk:
                    existing = existing.exclude(pk=self.instance.pk)
                if existing.exists():
                    self.add_error("scheduled_date", "This candidate already has an interview at that time.")
        if candidate and job and candidate.job_posting_id and candidate.job_posting_id != job.id:
            self.add_error("job_posting", "The selected job does not match the candidate's application.")
        return cleaned

    def save(self, commit=True):
        interview = super().save(commit=False)
        interview.candidate_id = self.cleaned_data["candidate"].id
        interview.job_posting_id = self.cleaned_data["job_posting"].id
        interviewer = self.cleaned_data.get("interviewer")
        interview.interviewer_id = interviewer.id if interviewer else None
        if commit:
            interview.save()
        return interview


class InterviewFeedbackForm(StyledFormMixin, forms.ModelForm):
    rating = forms.DecimalField(
        min_value=Decimal("1"),
        max_value=Decimal("5"),
        max_digits=3,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.1", "min": "1", "max": "5"}),
    )

    class Meta:
        model = Interview
        fields = ["feedback", "rating", "status"]
        widgets = {"feedback": forms.Textarea(attrs={"rows": 4})}


class PayrollPeriodForm(StyledFormMixin, forms.ModelForm):
    start_date = forms.DateField(widget=_date_input())
    end_date = forms.DateField(widget=_date_input())
    payment_date = forms.DateField(widget=_date_input())

    class Meta:
        model = PayrollPeriod
        fields = ["period_name", "start_date", "end_date", "payment_date"]

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start_date")
        end = cleaned.get("end_date")
        payment = cleaned.get("payment_date")
        if start and end and end < start:
            self.add_error("end_date", "End date must be after the start date.")
        if start and end and (end - start).days > 62:
            self.add_error("end_date", "A payroll period cannot exceed 62 days.")
        if payment and end and payment < end:
            self.add_error("payment_date", "Payment date cannot precede the period end.")
        return cleaned


class TrainingProgramForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = TrainingProgram
        fields = [
            "program_name",
            "description",
            "duration_hours",
            "cost",
            "provider",
            "certification",
            "status",
        ]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_duration_hours(self):
        hours = self.cleaned_data.get("duration_hours")
        if hours is not None and hours < 0:
            raise ValidationError("Duration cannot be negative.")
        return hours


class SuccessionPlanForm(StyledFormMixin, forms.ModelForm):
    position = forms.ModelChoiceField(
        queryset=Position.objects.order_by("position_name"), empty_label="Select position"
    )
    current_holder = _employee_field(label="Current holder")
    potential_successors = forms.ModelMultipleChoiceField(
        queryset=Employee.objects.exclude(status="inactive").order_by(
            "first_name", "last_name"
        ),
        required=False,
        label="Potential successors",
    )

    class Meta:
        model = SuccessionPlan
        fields = [
            "position",
            "current_holder",
            "potential_successors",
            "readiness_level",
            "development_plan",
            "risk_level",
        ]
        widgets = {"development_plan": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            try:
                ids = json.loads(self.instance.potential_successors or "[]")
            except ValueError:
                ids = []
            if isinstance(ids, list):
                self.fields["potential_successors"].initial = ids
            existing = SuccessionPlan.objects.filter(position_id=self.instance.position_id)
            self.fields["position"].queryset = Position.objects.exclude(
                pk__in=existing.exclude(pk=self.instance.pk).values_list("position_id", flat=True)
            ).order_by("position_name")
        else:
            taken = SuccessionPlan.objects.values_list("position_id", flat=True)
            self.fields["position"].queryset = Position.objects.exclude(pk__in=taken).order_by(
                "position_name"
            )

    def clean(self):
        cleaned = super().clean()
        position = cleaned.get("position")
        holder = cleaned.get("current_holder")
        successors = cleaned.get("potential_successors")
        if position and holder and successors is not None:
            if successors.filter(pk=holder.pk).exists():
                self.add_error("potential_successors", "The current holder cannot be a successor.")
        return cleaned

    def save(self, commit=True):
        plan = super().save(commit=False)
        plan.position_id = self.cleaned_data["position"].id
        plan.current_holder_id = self.cleaned_data["current_holder"].id
        plan.potential_successors = json.dumps(
            [employee.id for employee in self.cleaned_data.get("potential_successors", [])]
        )
        if commit:
            plan.save()
        return plan


class RecognitionGiveForm(StyledFormMixin, forms.Form):
    employee = _employee_field(label="Recognise")
    badge = forms.ModelChoiceField(
        queryset=RecognitionBadge.objects.order_by("badge_name"), empty_label="Select badge"
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class WellnessCheckinForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = WellnessCheckin
        fields = ["mood_score", "stress_level", "energy_level", "sleep_hours", "notes"]
        labels = {
            "mood_score": "Mood (1–10)",
            "stress_level": "Stress (1–10)",
            "energy_level": "Energy (1–10)",
            "sleep_hours": "Sleep hours",
        }
        widgets = {
            "mood_score": forms.NumberInput(attrs={"min": "1", "max": "10"}),
            "stress_level": forms.NumberInput(attrs={"min": "1", "max": "10"}),
            "energy_level": forms.NumberInput(attrs={"min": "1", "max": "10"}),
            "sleep_hours": forms.NumberInput(attrs={"min": "0", "max": "12", "step": "0.5"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean(self):
        cleaned = super().clean()
        for name in ("mood_score", "stress_level", "energy_level"):
            value = cleaned.get(name)
            if value is None:
                self.add_error(name, "This score is required.")
            elif not (1 <= value <= 10):
                self.add_error(name, "Score must be between 1 and 10.")
        sleep = cleaned.get("sleep_hours")
        if sleep is not None and not (0 <= sleep <= 12):
            self.add_error("sleep_hours", "Sleep hours must be between 0 and 12.")
        return cleaned
