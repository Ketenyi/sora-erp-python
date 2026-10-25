"""Auto-generated from information_schema metadata. Do not edit by hand."""

from decimal import Decimal

from django.db import models
from django.utils import timezone

from apps.core.tenancy import TenantScopedModel


class ChartOfAccount(TenantScopedModel):
    """Legacy table: chart_of_accounts"""
    id = models.AutoField(primary_key=True)
    account_code = models.CharField(max_length=20)
    account_name = models.CharField(max_length=100)
    account_type = models.CharField(max_length=9, choices=[('asset', 'Asset'), ('liability', 'Liability'), ('equity', 'Equity'), ('revenue', 'Revenue'), ('expense', 'Expense')], db_index=True)
    normal_balance = models.CharField(max_length=6, choices=[('debit', 'Debit'), ('credit', 'Credit')])
    description = models.TextField(null=True, blank=True)
    parent_account_id = models.IntegerField(null=True, db_index=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive'), ('deleted', 'Deleted')], null=True, blank=True, default='active')
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField(null=True)
    level = models.IntegerField(null=True, default=1)
    is_header = models.SmallIntegerField(null=True, default=0)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    current_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    parent_id = models.IntegerField(null=True)
    hierarchy_level = models.IntegerField(null=True, default=0)
    is_active = models.SmallIntegerField(null=True, default=1)

    class Meta:
        unique_together = (("tenant", "account_code"),)
        db_table = "chart_of_accounts"

    def __str__(self):
        return f"{self.account_code} · {self.account_name}"


class JournalEntry(TenantScopedModel):
    """Legacy table: journal_entries"""
    id = models.AutoField(primary_key=True)
    entry_number = models.CharField(max_length=50)
    entry_date = models.DateField(db_index=True)
    description = models.TextField(null=True, blank=True)
    reference = models.CharField(max_length=100, null=True, blank=True)
    status = models.CharField(max_length=6, choices=[('draft', 'Draft'), ('posted', 'Posted'), ('void', 'Void')], null=True, blank=True, default='draft', db_index=True)
    created_by = models.IntegerField(db_index=True)
    created_at = models.DateTimeField()
    posted_at = models.DateTimeField(null=True)
    posted_by = models.IntegerField(null=True, db_index=True)
    entry_type_id = models.IntegerField(null=True, db_index=True)
    is_recurring = models.SmallIntegerField(null=True, default=0)
    recurring_frequency = models.CharField(max_length=9, choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')], null=True, blank=True)
    recurring_end_date = models.DateField(null=True)
    is_reversing = models.SmallIntegerField(null=True, default=0)
    reversal_date = models.DateField(null=True)
    attachment = models.CharField(max_length=500, null=True, blank=True)
    approved_by = models.IntegerField(null=True, db_index=True)
    approved_at = models.DateTimeField(null=True)

    class Meta:
        unique_together = (("tenant", "entry_number"),)
        db_table = "journal_entries"
        indexes = [
            models.Index(fields=["status", "entry_date"]),
        ]

    def __str__(self):
        return f"JournalEntry {self.pk}"


class JournalEntryLine(TenantScopedModel):
    """Legacy table: journal_entry_lines"""
    id = models.AutoField(primary_key=True)
    journal_entry_id = models.IntegerField(db_index=True)
    account_id = models.IntegerField(db_index=True)
    entry_type = models.CharField(max_length=6, choices=[('debit', 'Debit'), ('credit', 'Credit')], db_index=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.TextField(null=True, blank=True)
    project_id = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "journal_entry_lines"

    def __str__(self):
        return f"JournalEntryLine {self.pk}"


class JournalEntryType(TenantScopedModel):
    """Legacy table: journal_entry_types"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    code = models.CharField(max_length=20, null=True, blank=True)
    is_system = models.SmallIntegerField(null=True, default=0)
    is_recurring = models.SmallIntegerField(null=True, default=0)

    class Meta:
        db_table = "journal_entry_types"

    def __str__(self):
        return f"JournalEntryType {self.pk}"


class FiscalYear(TenantScopedModel):
    """Legacy table: fiscal_years"""
    id = models.AutoField(primary_key=True)
    year_name = models.CharField(max_length=20)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=6, choices=[('open', 'Open'), ('closed', 'Closed'), ('locked', 'Locked')], null=True, blank=True, default='open')
    is_current = models.SmallIntegerField(null=True, default=0)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "fiscal_years"

    def __str__(self):
        return f"FiscalYear {self.pk}"


class FiscalPeriod(TenantScopedModel):
    """Legacy table: fiscal_periods"""
    id = models.AutoField(primary_key=True)
    fiscal_year_id = models.IntegerField(db_index=True)
    period_number = models.IntegerField(db_index=True)
    period_name = models.CharField(max_length=50, null=True, blank=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=6, choices=[('open', 'Open'), ('closed', 'Closed'), ('locked', 'Locked')], null=True, blank=True, default='open')
    created_at = models.DateTimeField()

    class Meta:
        db_table = "fiscal_periods"

    def __str__(self):
        return f"FiscalPeriod {self.pk}"


class Budget(TenantScopedModel):
    """Legacy table: budgets"""
    id = models.AutoField(primary_key=True)
    budget_name = models.CharField(max_length=100)
    fiscal_year_id = models.IntegerField(db_index=True)
    description = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=[('draft', 'Draft'), ('active', 'Active'), ('archived', 'Archived')], null=True, blank=True, default='draft')
    created_by = models.IntegerField()
    created_at = models.DateTimeField()
    account_id = models.IntegerField(null=True)
    amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    fiscal_year = models.IntegerField(null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "budgets"

    def __str__(self):
        return f"Budget {self.pk}"


class BudgetLine(TenantScopedModel):
    """Legacy table: budget_lines"""
    id = models.AutoField(primary_key=True)
    budget_id = models.IntegerField(db_index=True)
    account_id = models.IntegerField(db_index=True)
    period_id = models.IntegerField(db_index=True)
    budget_amount = models.DecimalField(max_digits=15, decimal_places=2)
    actual_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    variance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    created_at = models.DateTimeField(null=True, default=timezone.now)

    class Meta:
        db_table = "budget_lines"

    def __str__(self):
        return f"BudgetLine {self.pk}"


class FixedAsset(TenantScopedModel):
    """Legacy table: fixed_assets"""
    id = models.AutoField(primary_key=True)
    asset_code = models.CharField(max_length=50)
    asset_name = models.CharField(max_length=200)
    asset_category = models.CharField(max_length=50, null=True, blank=True)
    purchase_date = models.DateField()
    purchase_cost = models.DecimalField(max_digits=15, decimal_places=2)
    salvage_value = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    useful_life_years = models.IntegerField()
    depreciation_method = models.CharField(max_length=17, choices=[('straight_line', 'Straight Line'), ('declining_balance', 'Declining Balance')], null=True, blank=True, default='straight_line')
    current_value = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    accumulated_depreciation = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    status = models.CharField(max_length=17, choices=[('active', 'Active'), ('disposed', 'Disposed'), ('fully_depreciated', 'Fully Depreciated')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "asset_code"),)
        db_table = "fixed_assets"

    def __str__(self):
        return f"FixedAsset {self.pk}"


class DepreciationEntry(TenantScopedModel):
    """Legacy table: depreciation_entries"""
    id = models.AutoField(primary_key=True)
    asset_id = models.IntegerField(db_index=True)
    period_id = models.IntegerField(db_index=True)
    depreciation_amount = models.DecimalField(max_digits=15, decimal_places=2)
    journal_entry_id = models.IntegerField(null=True, db_index=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "depreciation_entries"

    def __str__(self):
        return f"DepreciationEntry {self.pk}"


class BankAccount(TenantScopedModel):
    """Legacy table: bank_accounts"""
    id = models.AutoField(primary_key=True)
    bank_name = models.CharField(max_length=255)
    branch = models.CharField(max_length=255, null=True, blank=True)
    account_name = models.CharField(max_length=255)
    account_number = models.CharField(max_length=100)
    swift_code = models.CharField(max_length=50, null=True, blank=True)
    currency = models.CharField(max_length=10, null=True, blank=True, default='KES')
    is_default = models.SmallIntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    account_currency = models.CharField(max_length=3, null=True, blank=True, default='KES')
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))

    class Meta:
        db_table = "bank_accounts"

    def __str__(self):
        return f"{self.bank_name} · {self.account_number}"


class BankStatement(TenantScopedModel):
    """Legacy table: bank_statements"""
    id = models.AutoField(primary_key=True)
    bank_account_id = models.IntegerField(db_index=True)
    statement_date = models.DateField(db_index=True)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    closing_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    uploaded_at = models.DateTimeField(null=True, default=timezone.now)
    reconciled_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=10, choices=[('pending', 'Pending'), ('reconciled', 'Reconciled'), ('completed', 'Completed')], null=True, blank=True, default='pending')

    class Meta:
        db_table = "bank_statements"

    def __str__(self):
        return f"BankStatement {self.pk}"


class BankStatementLine(TenantScopedModel):
    """Legacy table: bank_statement_lines"""
    id = models.AutoField(primary_key=True)
    statement_id = models.IntegerField(db_index=True)
    transaction_date = models.DateField(db_index=True)
    description = models.TextField(null=True, blank=True)
    debit = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    credit = models.DecimalField(max_digits=15, decimal_places=2, null=True, default=Decimal('0.00'))
    reference = models.CharField(max_length=100, null=True, blank=True)
    journal_entry_id = models.IntegerField(null=True)
    is_reconciled = models.SmallIntegerField(null=True, default=0, db_index=True)

    class Meta:
        db_table = "bank_statement_lines"

    def __str__(self):
        return f"BankStatementLine {self.pk}"


class BankRule(TenantScopedModel):
    """Legacy table: bank_rules"""
    id = models.AutoField(primary_key=True)
    bank_account_id = models.IntegerField(null=True, db_index=True)
    rule_name = models.CharField(max_length=100, null=True, blank=True)
    description_contains = models.CharField(max_length=255, null=True, blank=True)
    amount_min = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    amount_max = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    transaction_type = models.CharField(max_length=6, choices=[('debit', 'Debit'), ('credit', 'Credit'), ('both', 'Both')], null=True, blank=True)
    action_type = models.CharField(max_length=10, choices=[('categorize', 'Categorize'), ('match', 'Match'), ('ignore', 'Ignore')], null=True, blank=True)
    action_value = models.CharField(max_length=100, null=True, blank=True)
    priority = models.IntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "bank_rules"

    def __str__(self):
        return f"BankRule {self.pk}"


class TaxJurisdiction(TenantScopedModel):
    """Legacy table: tax_jurisdictions"""
    id = models.AutoField(primary_key=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    state = models.CharField(max_length=100, null=True, blank=True)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    class Meta:
        db_table = "tax_jurisdictions"

    def __str__(self):
        return f"TaxJurisdiction {self.pk}"


class TaxRate(TenantScopedModel):
    """Legacy table: tax_rates"""
    id = models.AutoField(primary_key=True)
    tax_code = models.CharField(max_length=20)
    tax_name = models.CharField(max_length=100)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    tax_type = models.CharField(max_length=11, choices=[('vat_input', 'Vat Input'), ('vat_output', 'Vat Output'), ('withholding', 'Withholding'), ('excise', 'Excise')])
    is_default = models.SmallIntegerField(null=True, default=0)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_at = models.DateTimeField()

    class Meta:
        unique_together = (("tenant", "tax_code"),)
        db_table = "tax_rates"

    def __str__(self):
        return f"TaxRate {self.pk}"


class TaxRule(TenantScopedModel):
    """Legacy table: tax_rules"""
    id = models.AutoField(primary_key=True)
    tax_code = models.CharField(max_length=20, null=True, blank=True)
    tax_name = models.CharField(max_length=100, null=True, blank=True)
    rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    compound_with = models.CharField(max_length=20, null=True, blank=True)
    applies_to = models.CharField(max_length=8, choices=[('products', 'Products'), ('services', 'Services'), ('both', 'Both')], null=True, blank=True)
    is_recoverable = models.SmallIntegerField(null=True, default=1)

    class Meta:
        db_table = "tax_rules"

    def __str__(self):
        return f"TaxRule {self.pk}"


class RecurringJournal(TenantScopedModel):
    """Legacy table: recurring_journals"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    frequency = models.CharField(max_length=9, choices=[('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')])
    day_of_month = models.IntegerField(null=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True)
    last_posted = models.DateField(null=True)
    next_post_date = models.DateField(null=True)
    status = models.CharField(max_length=8, choices=[('active', 'Active'), ('inactive', 'Inactive')], null=True, blank=True, default='active')
    created_by = models.IntegerField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "recurring_journals"

    def __str__(self):
        return f"RecurringJournal {self.pk}"


class RecurringJournalLine(TenantScopedModel):
    """Legacy table: recurring_journal_lines"""
    id = models.AutoField(primary_key=True)
    recurring_journal_id = models.IntegerField(db_index=True)
    account_id = models.IntegerField(db_index=True)
    entry_type = models.CharField(max_length=6, choices=[('debit', 'Debit'), ('credit', 'Credit')])
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "recurring_journal_lines"

    def __str__(self):
        return f"RecurringJournalLine {self.pk}"


class CashFlowForecast(TenantScopedModel):
    """Legacy table: cash_flow_forecasts"""
    id = models.AutoField(primary_key=True)
    forecast_date = models.DateField()
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    projected_inflows = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    projected_outflows = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    closing_balance = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    confidence_level = models.DecimalField(max_digits=5, decimal_places=2, null=True, default=Decimal('70.00'))
    created_by = models.IntegerField(null=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "cash_flow_forecasts"

    def __str__(self):
        return f"CashFlowForecast {self.pk}"


class CashFlowScenario(TenantScopedModel):
    """Legacy table: cash_flow_scenarios"""
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=100, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    adjustments = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "cash_flow_scenarios"

    def __str__(self):
        return f"CashFlowScenario {self.pk}"


class AiAnomalyDetection(TenantScopedModel):
    """Legacy table: ai_anomaly_detection"""
    id = models.AutoField(primary_key=True)
    transaction_type = models.CharField(max_length=50, null=True, blank=True)
    transaction_id = models.IntegerField(null=True)
    anomaly_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    reason = models.TextField(null=True, blank=True)
    is_reviewed = models.SmallIntegerField(null=True, default=0)
    detected_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_anomaly_detection"

    def __str__(self):
        return f"AiAnomalyDetection {self.pk}"


class AiTransactionCategory(TenantScopedModel):
    """Legacy table: ai_transaction_categories"""
    id = models.AutoField(primary_key=True)
    description_pattern = models.CharField(max_length=255, null=True, blank=True)
    predicted_account_id = models.IntegerField(null=True, db_index=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    times_used = models.IntegerField(null=True, default=0)
    created_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "ai_transaction_categories"

    def __str__(self):
        return f"AiTransactionCategory {self.pk}"


__all__ = ['ChartOfAccount', 'JournalEntry', 'JournalEntryLine', 'JournalEntryType', 'FiscalYear', 'FiscalPeriod', 'Budget', 'BudgetLine', 'FixedAsset', 'DepreciationEntry', 'BankAccount', 'BankStatement', 'BankStatementLine', 'BankRule', 'TaxJurisdiction', 'TaxRate', 'TaxRule', 'RecurringJournal', 'RecurringJournalLine', 'CashFlowForecast', 'CashFlowScenario', 'AiAnomalyDetection', 'AiTransactionCategory']
