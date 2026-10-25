"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class Department(TenantScopedModel):
    """Legacy table: departments"""
    id = models.AutoField(primary_key=True)
    department_code = models.CharField(max_length=20)
    department_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    manager_id = models.IntegerField(null=True, db_index=True)
    parent_department_id = models.IntegerField(null=True, db_index=True)
    budget = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    cost_center = models.CharField(max_length=50, null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "department_code"),)
        db_table = "departments"

    def __str__(self):
        return f"Department {self.pk}"


class Position(TenantScopedModel):
    """Legacy table: positions"""
    id = models.AutoField(primary_key=True)
    position_code = models.CharField(max_length=50)
    position_name = models.CharField(max_length=100)
    job_description = models.TextField(null=True, blank=True)
    responsibilities = models.TextField(null=True, blank=True)
    qualifications = models.TextField(null=True, blank=True)
    required_skills = models.TextField(null=True, blank=True)
    job_family = models.CharField(max_length=50, null=True, blank=True)
    job_grade = models.CharField(max_length=50, null=True, blank=True)
    minimum_salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    maximum_salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    salary_grade = models.CharField(max_length=20, null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "position_code"),)
        db_table = "positions"

    def __str__(self):
        return f"Position {self.pk}"


class Employee(TenantScopedModel):
    """Legacy table: employees"""
    id = models.AutoField(primary_key=True)
    employee_code = models.CharField(max_length=50)
    national_id = models.CharField(max_length=20, null=True, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    middle_name = models.CharField(max_length=100, null=True, blank=True)
    email = models.CharField(max_length=100)
    personal_email = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    alternative_phone = models.CharField(max_length=20, null=True, blank=True)
    date_of_birth = models.DateField(null=True)
    gender = models.CharField(max_length=6, choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')], null=True, blank=True)
    marital_status = models.CharField(max_length=8, choices=[('single', 'Single'), ('married', 'Married'), ('divorced', 'Divorced'), ('widowed', 'Widowed')], null=True, blank=True)
    nationality = models.CharField(max_length=50, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    postal_code = models.CharField(max_length=20, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True, default='Kenya')
    hire_date = models.DateField(db_index=True)
    confirmation_date = models.DateField(null=True)
    termination_date = models.DateField(null=True)
    employment_status = models.CharField(max_length=10, choices=[('active', 'Active'), ('probation', 'Probation'), ('terminated', 'Terminated'), ('resigned', 'Resigned'), ('retired', 'Retired'), ('on_leave', 'On Leave')], null=True, blank=True, default='probation', db_index=True)
    employee_type = models.CharField(max_length=9, choices=[('full_time', 'Full Time'), ('part_time', 'Part Time'), ('contract', 'Contract'), ('intern', 'Intern'), ('temporary', 'Temporary')], null=True, blank=True, default='full_time')
    work_shift = models.CharField(max_length=50, null=True, blank=True)
    work_location = models.CharField(max_length=100, null=True, blank=True)
    department_id = models.IntegerField(null=True, db_index=True)
    position_id = models.IntegerField(null=True)
    job_title = models.CharField(max_length=100, null=True, blank=True)
    reports_to = models.IntegerField(null=True, db_index=True)
    supervisor_id = models.IntegerField(null=True, db_index=True)
    level = models.CharField(max_length=50, null=True, blank=True)
    grade = models.CharField(max_length=20, null=True, blank=True)
    bank_name = models.CharField(max_length=100, null=True, blank=True)
    bank_account = models.CharField(max_length=50, null=True, blank=True)
    bank_branch = models.CharField(max_length=100, null=True, blank=True)
    salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    salary_currency = models.CharField(max_length=3, null=True, blank=True, default='KES')
    payment_method = models.CharField(max_length=12, choices=[('bank', 'Bank'), ('mobile_money', 'Mobile Money'), ('cash', 'Cash'), ('cheque', 'Cheque')], null=True, blank=True, default='bank')
    mobile_money_number = models.CharField(max_length=20, null=True, blank=True)
    kra_pin = models.CharField(max_length=20, null=True, blank=True)
    nssf_number = models.CharField(max_length=20, null=True, blank=True)
    nhif_number = models.CharField(max_length=20, null=True, blank=True)
    tax_identification = models.CharField(max_length=50, null=True, blank=True)
    benefits = models.TextField(null=True, blank=True)
    deductions = models.TextField(null=True, blank=True)
    emergency_contact_name = models.CharField(max_length=100, null=True, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, null=True, blank=True)
    emergency_contact_relationship = models.CharField(max_length=50, null=True, blank=True)
    profile_picture = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=9, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('suspended', 'Suspended')], null=True, blank=True, default='active', db_index=True)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)
    last_login = models.DateTimeField(null=True)
    zoho_user_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        unique_together = (
            ("tenant", "employee_code"),
            ("tenant", "email"),
        )
        db_table = "employees"

    def __str__(self):
        name = f"{self.first_name or ''} {self.last_name or ''}".strip()
        if not name:
            return self.email or f"Employee #{self.pk}"
        if self.employee_code:
            return f"{name} ({self.employee_code})"
        return name


class EmployeeDocument(TenantScopedModel):
    """Legacy table: employee_documents"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    document_type = models.CharField(max_length=50, db_index=True)
    document_name = models.CharField(max_length=200)
    file_path = models.CharField(max_length=255)
    file_size = models.IntegerField(null=True)
    mime_type = models.CharField(max_length=100, null=True, blank=True)
    expiry_date = models.DateField(null=True)
    notes = models.TextField(null=True, blank=True)
    uploaded_by = models.IntegerField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_documents"

    def __str__(self):
        return f"EmployeeDocument {self.pk}"


class EmployeeFeedback(TenantScopedModel):
    """Legacy table: employee_feedback"""
    id = models.AutoField(primary_key=True)
    from_employee_id = models.IntegerField(db_index=True)
    to_employee_id = models.IntegerField(db_index=True)
    feedback_type = models.CharField(max_length=12, choices=[('praise', 'Praise'), ('constructive', 'Constructive'), ('anonymous', 'Anonymous')])
    message = models.TextField()
    ai_analysis = models.TextField(null=True, blank=True)
    is_public = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_feedback"

    def __str__(self):
        return f"EmployeeFeedback {self.pk}"


class EmployeeKpiContract(TenantScopedModel):
    """Legacy table: employee_kpi_contract"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    kpi_item = models.CharField(max_length=255, null=True, blank=True)
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    target = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    measurement_type = models.CharField(max_length=10, choices=[('auto', 'Auto'), ('manual', 'Manual'), ('task_count', 'Task Count')], null=True, blank=True)
    source_table = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "employee_kpi_contract"

    def __str__(self):
        return f"EmployeeKpiContract {self.pk}"


class EmployeeMovement(TenantScopedModel):
    """Legacy table: employee_movements"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    movement_type = models.CharField(max_length=17, choices=[('promotion', 'Promotion'), ('transfer', 'Transfer'), ('demotion', 'Demotion'), ('department_change', 'Department Change'), ('location_change', 'Location Change')])
    from_position = models.CharField(max_length=100, null=True, blank=True)
    to_position = models.CharField(max_length=100, null=True, blank=True)
    from_department = models.CharField(max_length=100, null=True, blank=True)
    to_department = models.CharField(max_length=100, null=True, blank=True)
    from_salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    to_salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    effective_date = models.DateField()
    reason = models.TextField(null=True, blank=True)
    approved_by = models.IntegerField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_movements"

    def __str__(self):
        return f"EmployeeMovement {self.pk}"


class EmployeePerformance(TenantScopedModel):
    """Legacy table: employee_performance"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    period_start = models.DateField()
    period_end = models.DateField()
    period_type = models.CharField(max_length=9, choices=[('weekly', 'Weekly'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly')], null=True, blank=True, default='monthly')
    total_tasks = models.IntegerField(null=True, default=0)
    completed_tasks = models.IntegerField(null=True, default=0)
    excellent_tasks = models.IntegerField(null=True, default=0)
    average_tasks = models.IntegerField(null=True, default=0)
    poor_tasks = models.IntegerField(null=True, default=0)
    average_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "employee_performance"
        unique_together = (('employee_id', 'period_start', 'period_end'),)

    def __str__(self):
        return f"EmployeePerformance {self.pk}"


class EmployeeRecognition(TenantScopedModel):
    """Legacy table: employee_recognition"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    badge_id = models.IntegerField(db_index=True)
    given_by = models.IntegerField(db_index=True)
    reason = models.TextField(null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    points_earned = models.IntegerField(null=True, default=0)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_recognition"

    def __str__(self):
        return f"EmployeeRecognition {self.pk}"


class EmployeeSkill(TenantScopedModel):
    """Legacy table: employee_skills"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    skill_id = models.IntegerField(db_index=True)
    proficiency = models.CharField(max_length=12, choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced'), ('expert', 'Expert')], null=True, blank=True, default='beginner')
    years_of_experience = models.DecimalField(max_digits=3, decimal_places=1, null=True)
    certified = models.SmallIntegerField(null=True, default=0)
    certification_date = models.DateField(null=True)
    certification_expiry = models.DateField(null=True)
    assessed_by = models.IntegerField(null=True)
    assessment_date = models.DateField(null=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_skills"

    def __str__(self):
        return f"EmployeeSkill {self.pk}"


class EmployeeTraining(TenantScopedModel):
    """Legacy table: employee_training"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    training_id = models.IntegerField(db_index=True)
    completion_date = models.DateField(null=True)
    certificate_path = models.CharField(max_length=255, null=True, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    ai_assessment = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=11, choices=[('enrolled', 'Enrolled'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('failed', 'Failed')], null=True, blank=True, default='enrolled')
    created_at = models.DateTimeField()

    class Meta:
        db_table = "employee_training"

    def __str__(self):
        return f"EmployeeTraining {self.pk}"


class Attendance(TenantScopedModel):
    """Legacy table: attendance"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    date = models.DateField(db_index=True)
    clock_in = models.TimeField(null=True)
    clock_out = models.TimeField(null=True)
    break_start = models.TimeField(null=True)
    break_end = models.TimeField(null=True)
    total_break = models.IntegerField(null=True, default=0)
    total_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    status = models.CharField(max_length=8, choices=[('present', 'Present'), ('absent', 'Absent'), ('late', 'Late'), ('half_day', 'Half Day'), ('holiday', 'Holiday'), ('weekend', 'Weekend')], null=True, blank=True, default='present', db_index=True)
    late_minutes = models.IntegerField(null=True, default=0)
    early_departure_minutes = models.IntegerField(null=True, default=0)
    location_check_in = models.TextField(null=True, blank=True)
    location_check_out = models.TextField(null=True, blank=True)
    check_in_method = models.CharField(max_length=9, choices=[('biometric', 'Biometric'), ('mobile', 'Mobile'), ('web', 'Web'), ('manual', 'Manual')], null=True, blank=True)
    check_out_method = models.CharField(max_length=9, choices=[('biometric', 'Biometric'), ('mobile', 'Mobile'), ('web', 'Web'), ('manual', 'Manual')], null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(db_index=True)
    updated_at = models.DateTimeField(null=True)
    total_seconds = models.IntegerField(null=True, default=0)
    source = models.CharField(max_length=50, null=True, blank=True, default='manual')

    class Meta:
        db_table = "attendance"
        indexes = [
            models.Index(fields=["employee_id", "date"]),
        ]

    def __str__(self):
        return f"Attendance {self.pk}"


class LeaveBalance(TenantScopedModel):
    """Legacy table: leave_balances"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    leave_type_id = models.IntegerField(db_index=True)
    year = models.IntegerField()
    total_days = models.DecimalField(max_digits=5, decimal_places=2)
    total_earned = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    used_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    remaining_days = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    pending_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    carried_over = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    adjusted_days = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    notes = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "leave_balances"
        unique_together = (('employee_id', 'leave_type_id', 'year'),)

    def __str__(self):
        return f"LeaveBalance {self.pk}"


class LeaveRequest(TenantScopedModel):
    """Legacy table: leave_requests"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    leave_type_id = models.IntegerField(db_index=True)
    start_date = models.DateField()
    end_date = models.DateField()
    duration_days = models.DecimalField(max_digits=5, decimal_places=2)
    reason = models.TextField(null=True, blank=True)
    attachment = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=9, choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')], null=True, blank=True, default='pending', db_index=True)
    approved_by = models.IntegerField(null=True, db_index=True)
    approved_at = models.DateTimeField(null=True)
    rejection_reason = models.TextField(null=True, blank=True)
    adjustment_details = models.TextField(null=True, blank=True)
    cancelled_by = models.IntegerField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "leave_requests"
        indexes = [
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        return f"LeaveRequest {self.pk}"


class LeaveTransaction(TenantScopedModel):
    """Legacy table: leave_transactions"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    transaction_type = models.CharField(max_length=12, choices=[('earned', 'Earned'), ('used', 'Used'), ('adjusted', 'Adjusted'), ('carried_over', 'Carried Over')])
    days = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "leave_transactions"

    def __str__(self):
        return f"LeaveTransaction {self.pk}"


class LeaveType(TenantScopedModel):
    """Legacy table: leave_types"""
    id = models.AutoField(primary_key=True)
    leave_code = models.CharField(max_length=20)
    leave_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    default_days = models.IntegerField(null=True, default=0)
    days_allowed = models.IntegerField()
    is_paid = models.SmallIntegerField(null=True, default=1)
    is_carry_over = models.SmallIntegerField(null=True, default=0)
    carry_over = models.SmallIntegerField(null=True, default=0)
    max_carry_over = models.IntegerField(null=True, default=0)
    requires_documentation = models.SmallIntegerField(null=True, default=0)
    requires_approval = models.SmallIntegerField(null=True, default=1)
    requires_document = models.SmallIntegerField(null=True, default=0)
    color = models.CharField(max_length=7, null=True, blank=True, default='#FFD700')
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "leave_code"),)
        db_table = "leave_types"

    def __str__(self):
        return f"LeaveType {self.pk}"


class OvertimeRequest(TenantScopedModel):
    """Legacy table: overtime_requests"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    overtime_date = models.DateField()
    hours = models.DecimalField(max_digits=5, decimal_places=2)
    reason = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], null=True, blank=True, default='pending', db_index=True)
    approved_by = models.IntegerField(null=True, db_index=True)
    approved_at = models.DateTimeField(null=True)
    converted_to_leave = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)
    conversion_rate = models.DecimalField(max_digits=3, decimal_places=1, null=True, default=Decimal('8.0'))

    class Meta:
        db_table = "overtime_requests"

    def __str__(self):
        return f"OvertimeRequest {self.pk}"


class PayrollItem(TenantScopedModel):
    """Legacy table: payroll_items"""
    id = models.AutoField(primary_key=True)
    payroll_period_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(db_index=True)
    basic_salary = models.DecimalField(max_digits=15, decimal_places=2)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    overtime_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    allowances = models.TextField(null=True, blank=True)
    bonus = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    commission = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    gross_pay = models.DecimalField(max_digits=15, decimal_places=2)
    paye_tax = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    nssf = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    nhif = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    other_deductions = models.TextField(null=True, blank=True)
    total_deductions = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    net_pay = models.DecimalField(max_digits=15, decimal_places=2)
    bank_account = models.CharField(max_length=50, null=True, blank=True)
    mobile_money_number = models.CharField(max_length=20, null=True, blank=True)
    payment_status = models.CharField(max_length=10, choices=[('pending', 'Pending'), ('processing', 'Processing'), ('paid', 'Paid'), ('failed', 'Failed')], null=True, blank=True, default='pending', db_index=True)
    payment_reference = models.CharField(max_length=100, null=True, blank=True)
    paid_at = models.DateTimeField(null=True)
    payslip_generated = models.SmallIntegerField(null=True, default=0)
    payslip_path = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "payroll_items"

    def __str__(self):
        return f"PayrollItem {self.pk}"


class PayrollPeriod(TenantScopedModel):
    """Legacy table: payroll_periods"""
    id = models.AutoField(primary_key=True)
    period_name = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    payment_date = models.DateField()
    status = models.CharField(max_length=10, choices=[('draft', 'Draft'), ('processing', 'Processing'), ('approved', 'Approved'), ('paid', 'Paid'), ('closed', 'Closed')], null=True, blank=True, default='draft', db_index=True)
    total_gross = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_net = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_deductions = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    total_tax = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    processed_by = models.IntegerField(null=True)
    processed_at = models.DateTimeField(null=True)
    approved_by = models.IntegerField(null=True)
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "payroll_periods"
        indexes = [
            models.Index(fields=["start_date", "end_date"]),
        ]

    def __str__(self):
        return f"PayrollPeriod {self.pk}"


class PerformanceNotification(TenantScopedModel):
    """Legacy table: performance_notifications"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    task_id = models.IntegerField(null=True, db_index=True)
    notification_type = models.CharField(max_length=18, choices=[('task_assigned', 'Task Assigned'), ('deadline_warning', 'Deadline Warning'), ('deadline_passed', 'Deadline Passed'), ('task_completed', 'Task Completed'), ('performance_update', 'Performance Update')], null=True, blank=True, default='task_assigned')
    title = models.CharField(max_length=200, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    is_read = models.SmallIntegerField(null=True, default=0)
    read_at = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "performance_notifications"

    def __str__(self):
        return f"PerformanceNotification {self.pk}"


class PerformanceReview(TenantScopedModel):
    """Legacy table: performance_reviews"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    reviewer_id = models.IntegerField(db_index=True)
    review_period_start = models.DateField()
    review_period_end = models.DateField()
    review_type = models.CharField(max_length=9, choices=[('annual', 'Annual'), ('quarterly', 'Quarterly'), ('monthly', 'Monthly'), ('probation', 'Probation'), ('ad_hoc', 'Ad Hoc')])
    overall_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True)
    strengths = models.TextField(null=True, blank=True)
    areas_for_improvement = models.TextField(null=True, blank=True)
    goals_achieved = models.TextField(null=True, blank=True)
    future_goals = models.TextField(null=True, blank=True)
    development_plan = models.TextField(null=True, blank=True)
    ai_insights = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('submitted', 'Submitted'), ('reviewed', 'Reviewed'), ('approved', 'Approved'), ('completed', 'Completed')], null=True, blank=True, default='draft')
    submitted_at = models.DateTimeField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    approved_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "performance_reviews"
        indexes = [
            models.Index(fields=["review_period_start", "review_period_end"]),
        ]

    def __str__(self):
        return f"PerformanceReview {self.pk}"


class PerformanceTask(TenantScopedModel):
    """Legacy table: performance_tasks"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    task_date = models.DateField()
    description = models.TextField(null=True, blank=True)
    time_spent = models.IntegerField(null=True)
    kpi_id = models.IntegerField(null=True)
    ai_quality_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "performance_tasks"

    def __str__(self):
        return f"PerformanceTask {self.pk}"


class Goal(TenantScopedModel):
    """Legacy table: goals"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    goal_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    category = models.CharField(max_length=10, choices=[('individual', 'Individual'), ('team', 'Team'), ('company', 'Company')], null=True, blank=True, default='individual')
    start_date = models.DateField()
    target_date = models.DateField()
    weight = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('100.00'))
    progress = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('0.00'))
    status = models.CharField(max_length=11, choices=[('not_started', 'Not Started'), ('in_progress', 'In Progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='not_started', db_index=True)
    key_results = models.TextField(null=True, blank=True)
    ai_suggestions = models.TextField(null=True, blank=True)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "goals"

    def __str__(self):
        return f"Goal {self.pk}"


class KpiPerformance(TenantScopedModel):
    """Legacy table: kpi_performance"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    kpi_id = models.IntegerField(db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()
    achieved_value = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    achieved_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    evidence = models.TextField(null=True, blank=True)
    task_ids = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "kpi_performance"
        indexes = [
            models.Index(fields=["employee_id", "period_start"]),
        ]

    def __str__(self):
        return f"KpiPerformance {self.pk}"


class JobPosting(TenantScopedModel):
    """Legacy table: job_postings"""
    id = models.AutoField(primary_key=True)
    job_title = models.CharField(max_length=200)
    department_id = models.IntegerField(null=True, db_index=True)
    position_id = models.IntegerField(null=True, db_index=True)
    description = models.TextField(null=True, blank=True)
    requirements = models.TextField(null=True, blank=True)
    responsibilities = models.TextField(null=True, blank=True)
    location = models.CharField(max_length=100, null=True, blank=True)
    employment_type = models.CharField(max_length=9, choices=[('full_time', 'Full Time'), ('part_time', 'Part Time'), ('contract', 'Contract'), ('intern', 'Intern')], null=True, blank=True)
    salary_range_min = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    salary_range_max = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    posting_date = models.DateField()
    closing_date = models.DateField(null=True)
    status = models.CharField(max_length=9, choices=[('draft', 'Draft'), ('published', 'Published'), ('closed', 'Closed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='draft', db_index=True)
    ai_match_threshold = models.IntegerField(null=True, default=70)
    created_by = models.IntegerField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "job_postings"
        indexes = [
            models.Index(fields=["posting_date", "closing_date"]),
        ]

    def __str__(self):
        return f"JobPosting {self.pk}"


class Candidate(TenantScopedModel):
    """Legacy table: candidates"""
    id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.CharField(max_length=100, db_index=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    resume_path = models.CharField(max_length=255, null=True, blank=True)
    resume_text = models.TextField(null=True, blank=True)
    cover_letter = models.TextField(null=True, blank=True)
    linkedin_url = models.CharField(max_length=255, null=True, blank=True)
    portfolio_url = models.CharField(max_length=255, null=True, blank=True)
    source = models.CharField(max_length=50, null=True, blank=True)
    job_posting_id = models.IntegerField(null=True)
    expected_salary = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    notice_period = models.IntegerField(null=True)
    ai_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    ai_insights = models.TextField(null=True, blank=True)
    skills_match = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=[('new', 'New'), ('screening', 'Screening'), ('interview', 'Interview'), ('assessment', 'Assessment'), ('offered', 'Offered'), ('hired', 'Hired'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')], null=True, blank=True, default='new', db_index=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "candidates"

    def __str__(self):
        return f"Candidate {self.pk}"


class Interview(TenantScopedModel):
    """Legacy table: interviews"""
    id = models.AutoField(primary_key=True)
    candidate_id = models.IntegerField(db_index=True)
    job_posting_id = models.IntegerField(db_index=True)
    interviewer_id = models.IntegerField(null=True, db_index=True)
    interview_type = models.CharField(max_length=10, choices=[('phone', 'Phone'), ('video', 'Video'), ('technical', 'Technical'), ('behavioral', 'Behavioral'), ('panel', 'Panel')])
    scheduled_date = models.DateTimeField(db_index=True)
    duration_minutes = models.IntegerField(null=True, default=60)
    location = models.CharField(max_length=200, null=True, blank=True)
    meeting_link = models.CharField(max_length=255, null=True, blank=True)
    feedback = models.TextField(null=True, blank=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, null=True)
    ai_transcript = models.TextField(null=True, blank=True)
    ai_sentiment = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=11, choices=[('scheduled', 'Scheduled'), ('completed', 'Completed'), ('cancelled', 'Cancelled'), ('rescheduled', 'Rescheduled')], null=True, blank=True, default='scheduled')
    created_at = models.DateTimeField()

    class Meta:
        db_table = "interviews"

    def __str__(self):
        return f"Interview {self.pk}"


class Onboarding(TenantScopedModel):
    """Legacy table: onboarding"""
    id = models.AutoField(primary_key=True)
    candidate_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(null=True)
    start_date = models.DateField(null=True)
    status = models.CharField(max_length=9, choices=[('pending', 'Pending'), ('completed', 'Completed')], null=True, blank=True, default='pending', db_index=True)
    documents_submitted = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "onboarding"

    def __str__(self):
        return f"Onboarding {self.pk}"


class TrainingProgram(TenantScopedModel):
    """Legacy table: training_programs"""
    id = models.AutoField(primary_key=True)
    program_code = models.CharField(max_length=20)
    program_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    duration_hours = models.IntegerField(null=True)
    cost = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    provider = models.CharField(max_length=100, null=True, blank=True)
    certification = models.CharField(max_length=100, null=True, blank=True)
    ai_recommended = models.SmallIntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "program_code"),)
        db_table = "training_programs"

    def __str__(self):
        return f"TrainingProgram {self.pk}"


class SuccessionPlan(TenantScopedModel):
    """Legacy table: succession_plans"""
    id = models.AutoField(primary_key=True)
    position_id = models.IntegerField(db_index=True)
    current_holder_id = models.IntegerField(db_index=True)
    potential_successors = models.TextField(null=True, blank=True)
    readiness_level = models.CharField(max_length=15, choices=[('ready_now', 'Ready Now'), ('ready_1_2_years', 'Ready 1 2 Years'), ('ready_3_5_years', 'Ready 3 5 Years'), ('potential', 'Potential')], null=True, blank=True, default='potential')
    development_plan = models.TextField(null=True, blank=True)
    risk_level = models.CharField(max_length=8, choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical')], null=True, blank=True, default='medium')
    ai_prediction = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "succession_plans"

    def __str__(self):
        return f"SuccessionPlan {self.pk}"


class RecognitionBadge(TenantScopedModel):
    """Legacy table: recognition_badges"""
    id = models.AutoField(primary_key=True)
    badge_name = models.CharField(max_length=100)
    badge_icon = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    points = models.IntegerField(null=True, default=0)
    level = models.CharField(max_length=8, choices=[('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum')], null=True, blank=True, default='bronze')
    criteria = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "recognition_badges"

    def __str__(self):
        return f"RecognitionBadge {self.pk}"


class WellnessChallenge(TenantScopedModel):
    """Legacy table: wellness_challenges"""
    id = models.AutoField(primary_key=True)
    challenge_name = models.CharField(max_length=200)
    description = models.TextField(null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    target_metric = models.CharField(max_length=8, choices=[('steps', 'Steps'), ('mood', 'Mood'), ('sleep', 'Sleep'), ('checkins', 'Checkins')])
    target_value = models.IntegerField()
    points_reward = models.IntegerField(null=True, default=100)
    status = models.CharField(max_length=9, choices=[('active', 'Active'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()

    class Meta:
        db_table = "wellness_challenges"

    def __str__(self):
        return f"WellnessChallenge {self.pk}"


class WellnessChallengeProgress(TenantScopedModel):
    """Legacy table: wellness_challenge_progress"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    challenge_id = models.IntegerField(db_index=True)
    progress = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    completed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "wellness_challenge_progress"
        unique_together = (('employee_id', 'challenge_id'),)

    def __str__(self):
        return f"WellnessChallengeProgress {self.pk}"


class WellnessCheckin(TenantScopedModel):
    """Legacy table: wellness_checkins"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField()
    checkin_date = models.DateField()
    mood_score = models.SmallIntegerField(null=True)
    stress_level = models.SmallIntegerField(null=True)
    energy_level = models.SmallIntegerField(null=True)
    sleep_hours = models.DecimalField(max_digits=3, decimal_places=1, null=True)
    notes = models.TextField(null=True, blank=True)
    ai_sentiment = models.TextField(null=True, blank=True)
    ai_suggestion = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "wellness_checkins"
        indexes = [
            models.Index(fields=["employee_id", "checkin_date"]),
        ]

    def __str__(self):
        return f"WellnessCheckin {self.pk}"


class Skill(TenantScopedModel):
    """Legacy table: skills"""
    id = models.AutoField(primary_key=True)
    skill_code = models.CharField(max_length=20)
    skill_name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    is_certification = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "skill_code"),)
        db_table = "skills"

    def __str__(self):
        return f"Skill {self.pk}"


class Approver(TenantScopedModel):
    """Legacy table: approvers"""
    id = models.AutoField(primary_key=True)
    employee_id = models.IntegerField(db_index=True)
    approver_type = models.CharField(max_length=8, choices=[('leave', 'Leave'), ('overtime', 'Overtime'), ('all', 'All')], null=True, blank=True, default='leave', db_index=True)
    department_id = models.IntegerField(null=True, db_index=True)
    priority = models.IntegerField(null=True, default=1)
    approval_level = models.IntegerField(null=True, default=2)
    is_required = models.SmallIntegerField(null=True, default=1)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "approvers"

    def __str__(self):
        return f"Approver {self.pk}"


__all__ = ['Department', 'Position', 'Employee', 'EmployeeDocument', 'EmployeeFeedback', 'EmployeeKpiContract', 'EmployeeMovement', 'EmployeePerformance', 'EmployeeRecognition', 'EmployeeSkill', 'EmployeeTraining', 'Attendance', 'LeaveBalance', 'LeaveRequest', 'LeaveTransaction', 'LeaveType', 'OvertimeRequest', 'PayrollItem', 'PayrollPeriod', 'PerformanceNotification', 'PerformanceReview', 'PerformanceTask', 'Goal', 'KpiPerformance', 'JobPosting', 'Candidate', 'Interview', 'Onboarding', 'TrainingProgram', 'SuccessionPlan', 'RecognitionBadge', 'WellnessChallenge', 'WellnessChallengeProgress', 'WellnessCheckin', 'Skill', 'Approver']
