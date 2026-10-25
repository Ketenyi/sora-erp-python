"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone


class AdminUser(models.Model):
    """Legacy table: admin_users"""
    id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=50, unique=True)
    email = models.CharField(max_length=100, unique=True)
    password = models.CharField(max_length=255)
    full_name = models.CharField(max_length=100, null=True, blank=True)
    role = models.CharField(max_length=11, choices=[('super_admin', 'Super Admin'), ('admin', 'Admin')], null=True, blank=True, default='admin')
    is_active = models.BooleanField(null=True, default=1)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    last_login = models.DateTimeField(null=True)
    created_by = models.IntegerField(null=True)

    class Meta:
        db_table = "admin_users"

    def __str__(self):
        return f"AdminUser {self.pk}"


class MasterActivityLog(models.Model):
    """Legacy table: master_activity_log"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(null=True, db_index=True)
    company_id = models.IntegerField(null=True, db_index=True)
    action = models.CharField(max_length=100, null=True, blank=True)
    details = models.TextField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now, db_index=True)

    class Meta:
        db_table = "master_activity_log"

    def __str__(self):
        return f"MasterActivityLog {self.pk}"


class MasterCompany(models.Model):
    """Legacy table: master_companies"""
    id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=255)
    company_code = models.CharField(max_length=50, unique=True)
    company_email = models.CharField(max_length=255, null=True, blank=True)
    company_phone = models.CharField(max_length=50, null=True, blank=True)
    company_address = models.TextField(null=True, blank=True)
    database_name = models.CharField(max_length=100, unique=True)
    database_host = models.CharField(max_length=255, null=True, blank=True, default='localhost')
    database_user = models.CharField(max_length=100, null=True, blank=True, default='root')
    database_password = models.CharField(max_length=255, null=True, blank=True, default='')
    status = models.CharField(max_length=9, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('suspended', 'Suspended'), ('deleted', 'Deleted')], null=True, blank=True, default='active', db_index=True)
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    subscription_plan_id = models.IntegerField(null=True, default=1)
    subscription_status = models.CharField(max_length=9, choices=[('trial', 'Trial'), ('active', 'Active'), ('suspended', 'Suspended'), ('cancelled', 'Cancelled'), ('expired', 'Expired')], null=True, blank=True, default='trial')
    subscription_start_date = models.DateField(null=True)
    subscription_end_date = models.DateField(null=True)
    trial_ends_at = models.DateField(null=True)
    max_users = models.IntegerField(null=True, default=5)
    storage_used_mb = models.IntegerField(null=True, default=0)
    stripe_customer_id = models.CharField(max_length=100, null=True, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, null=True, blank=True)
    last_payment_date = models.DateField(null=True)
    last_payment_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    mpesa_paybill_no = models.CharField(max_length=20, null=True, blank=True)
    license_key = models.CharField(max_length=100, null=True, blank=True, unique=True)
    license_type = models.CharField(max_length=10, choices=[('standard', 'Standard'), ('custom', 'Custom'), ('promo', 'Promo'), ('lifetime', 'Lifetime'), ('enterprise', 'Enterprise')], null=True, blank=True, default='standard')
    license_valid_from = models.DateField(null=True)
    license_valid_until = models.DateField(null=True)
    license_modules = models.TextField(null=True, blank=True)
    license_last_checked = models.DateTimeField(null=True)
    license_violations = models.IntegerField(null=True, default=0)
    license_status = models.CharField(max_length=9, choices=[('active', 'Active'), ('suspended', 'Suspended'), ('expired', 'Expired'), ('revoked', 'Revoked')], null=True, blank=True, default='active')

    class Meta:
        db_table = "master_companies"

    def __str__(self):
        return f"MasterCompany {self.pk}"


class MasterInvitation(models.Model):
    """Legacy table: master_invitations"""
    id = models.AutoField(primary_key=True)
    email = models.CharField(max_length=255, db_index=True)
    company_id = models.IntegerField(db_index=True)
    invited_by = models.IntegerField()
    role = models.CharField(max_length=50, null=True, blank=True, default='user')
    token = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=8, choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('expired', 'Expired')], null=True, blank=True, default='pending')
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "master_invitations"

    def __str__(self):
        return f"MasterInvitation {self.pk}"


class MasterUser(models.Model):
    """Legacy table: master_users"""
    id = models.AutoField(primary_key=True)
    email = models.CharField(max_length=255, unique=True)
    password = models.CharField(max_length=255)
    first_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True)
    is_super_admin = models.BooleanField(null=True, default=0)
    status = models.CharField(max_length=9, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('suspended', 'Suspended')], null=True, blank=True, default='active')
    last_login = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "master_users"

    def __str__(self):
        return f"MasterUser {self.pk}"


class MasterUserCompany(models.Model):
    """Legacy table: master_user_companies"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    company_id = models.IntegerField(db_index=True)
    role_in_company = models.CharField(max_length=7, choices=[('owner', 'Owner'), ('admin', 'Admin'), ('manager', 'Manager'), ('user', 'User')], null=True, blank=True, default='user')
    is_default = models.BooleanField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    joined_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "master_user_companies"
        unique_together = (('user_id', 'company_id'),)

    def __str__(self):
        return f"MasterUserCompany {self.pk}"


class CustomLicense(models.Model):
    """Legacy table: custom_licenses"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField(db_index=True)
    license_key = models.CharField(max_length=100, unique=True)
    license_type = models.CharField(max_length=10, choices=[('custom', 'Custom'), ('promo', 'Promo'), ('lifetime', 'Lifetime'), ('enterprise', 'Enterprise')], null=True, blank=True, default='custom')
    modules_allowed = models.TextField(null=True, blank=True)
    max_users = models.IntegerField(null=True, default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    discount_percent = models.IntegerField(null=True, default=0)
    valid_from = models.DateField(null=True)
    valid_until = models.DateField(null=True)
    is_active = models.BooleanField(null=True, default=1)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "custom_licenses"

    def __str__(self):
        return f"CustomLicense {self.pk}"


class LicenseAuditLog(models.Model):
    """Legacy table: license_audit_log"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField(db_index=True)
    action = models.CharField(max_length=50)
    details = models.TextField(null=True, blank=True)
    ip_address = models.CharField(max_length=45, null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "license_audit_log"

    def __str__(self):
        return f"LicenseAuditLog {self.pk}"


class LicenseKey(models.Model):
    """Legacy table: license_keys"""
    id = models.AutoField(primary_key=True)
    license_key = models.CharField(max_length=100, unique=True)
    company_id = models.IntegerField(null=True, db_index=True)
    plan_id = models.IntegerField(null=True, db_index=True)
    max_companies = models.IntegerField(null=True, default=1)
    expires_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=7, choices=[('active', 'Active'), ('used', 'Used'), ('expired', 'Expired')], null=True, blank=True, default='active')
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "license_keys"

    def __str__(self):
        return f"LicenseKey {self.pk}"


class LicenseViolation(models.Model):
    """Legacy table: license_violations"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField(db_index=True)
    violation_type = models.CharField(max_length=20, choices=[('max_users_exceeded', 'Max Users Exceeded'), ('module_access_denied', 'Module Access Denied'), ('license_expired', 'License Expired'), ('license_mismatch', 'License Mismatch'), ('invalid_license_key', 'Invalid License Key')])
    violation_details = models.TextField(null=True, blank=True)
    severity = models.CharField(max_length=8, choices=[('warning', 'Warning'), ('critical', 'Critical')], null=True, blank=True, default='warning')
    resolved = models.BooleanField(null=True, default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    resolved_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "license_violations"

    def __str__(self):
        return f"LicenseViolation {self.pk}"


class SubscriptionPlan(models.Model):
    """Legacy table: subscription_plans"""
    id = models.AutoField(primary_key=True)
    plan_code = models.CharField(max_length=50, unique=True)
    plan_name = models.CharField(max_length=100)
    price_monthly = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    price_yearly = models.DecimalField(max_digits=10, decimal_places=2, null=True)
    max_users = models.IntegerField(null=True, default=5)
    max_companies = models.IntegerField(null=True, default=1)
    max_storage_mb = models.IntegerField(null=True, default=100)
    features = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(null=True, default=1)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    price_per_user = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    base_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, default=Decimal('0.00'))
    billing_type = models.CharField(max_length=8, choices=[('flat', 'Flat'), ('per_user', 'Per User'), ('hybrid', 'Hybrid')], null=True, blank=True, default='flat')
    allowed_modules = models.TextField(null=True, blank=True)
    custom_features = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "subscription_plans"

    def __str__(self):
        return f"SubscriptionPlan {self.pk}"


class SubscriptionTransaction(models.Model):
    """Legacy table: subscription_transactions"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField(db_index=True)
    plan_id = models.IntegerField(db_index=True)
    transaction_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, null=True, blank=True, default='KES')
    payment_method = models.CharField(max_length=13, choices=[('mpesa', 'Mpesa'), ('stripe', 'Stripe'), ('paypal', 'Paypal'), ('bank_transfer', 'Bank Transfer')])
    status = models.CharField(max_length=9, choices=[('pending', 'Pending'), ('completed', 'Completed'), ('failed', 'Failed'), ('refunded', 'Refunded')], null=True, blank=True, default='pending', db_index=True)
    mpesa_receipt = models.CharField(max_length=100, null=True, blank=True)
    stripe_payment_id = models.CharField(max_length=100, null=True, blank=True)
    invoice_url = models.TextField(null=True, blank=True)
    payment_details = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, default=timezone.now, db_index=True)

    class Meta:
        db_table = "subscription_transactions"

    def __str__(self):
        return f"SubscriptionTransaction {self.pk}"


class UsageTracking(models.Model):
    """Legacy table: usage_tracking"""
    id = models.AutoField(primary_key=True)
    company_id = models.IntegerField(db_index=True)
    month_year = models.DateField(db_index=True)
    quotes_used = models.IntegerField(null=True, default=0)
    invoices_used = models.IntegerField(null=True, default=0)
    clients_count = models.IntegerField(null=True, default=0)
    employees_count = models.IntegerField(null=True, default=0)
    projects_count = models.IntegerField(null=True, default=0)
    storage_used_mb = models.IntegerField(null=True, default=0)
    api_calls = models.IntegerField(null=True, default=0)
    created_at = models.DateTimeField(null=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "usage_tracking"
        unique_together = (('company_id', 'month_year'),)

    def __str__(self):
        return f"UsageTracking {self.pk}"


class MobileSession(models.Model):
    """Legacy table: mobile_sessions"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField(db_index=True)
    device_id = models.CharField(max_length=255)
    device_name = models.CharField(max_length=255, null=True, blank=True)
    push_token = models.CharField(max_length=255, null=True, blank=True)
    app_version = models.CharField(max_length=20, null=True, blank=True)
    last_active = models.DateTimeField(null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "mobile_sessions"

    def __str__(self):
        return f"MobileSession {self.pk}"


class OfflineSyncQueue(models.Model):
    """Legacy table: offline_sync_queue"""
    id = models.AutoField(primary_key=True)
    user_id = models.IntegerField()
    action = models.CharField(max_length=50, null=True, blank=True)
    table_name = models.CharField(max_length=50, null=True, blank=True)
    record_data = models.TextField(null=True, blank=True)
    sync_status = models.CharField(max_length=7, choices=[('pending', 'Pending'), ('synced', 'Synced'), ('failed', 'Failed')], null=True, blank=True, default='pending')
    created_at = models.DateTimeField(null=True)
    synced_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "offline_sync_queue"

    def __str__(self):
        return f"OfflineSyncQueue {self.pk}"


__all__ = ['AdminUser', 'MasterActivityLog', 'MasterCompany', 'MasterInvitation', 'MasterUser', 'MasterUserCompany', 'CustomLicense', 'LicenseAuditLog', 'LicenseKey', 'LicenseViolation', 'SubscriptionPlan', 'SubscriptionTransaction', 'UsageTracking', 'MobileSession', 'OfflineSyncQueue']
