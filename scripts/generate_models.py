"""Generate Django models from the reverse-engineered MySQL schema.

Input: scripts/schema/columns.tsv and indexes.tsv (information_schema extracts).
Output: apps/core/models/<group>.py

The legacy database has no foreign key constraints, so id-style columns are
generated as indexed integers. Composite primary-key tables get a surrogate
``id`` column (a documented, deliberate schema improvement).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "scripts" / "schema"
OUT_DIR = ROOT / "apps" / "core" / "models"

MODEL_GROUPS: dict[str, list[str]] = {
    "system": [
        "settings",
        "company_settings",
        "companies",
        "company_modules",
        "modules",
        "module_permissions",
        "permissions",
        "roles",
        "role_permissions",
        "user_roles",
        "user_permissions",
        "user_module_roles",
        "notifications",
        "email_templates",
        "email_report_logs",
        "sso_settings",
        "api_keys",
        "webhooks",
        "webhook_logs",
        "saved_reports",
        "report_schedules",
        "audit_log",
        "audit_details",
        "user_activity_log",
        "user_sessions",
        "user_companies",
        "attachments",
        "currencies",
        "exchange_rates",
        "approval_workflows",
        "approval_rules",
        "approval_tracking",
    ],
    "documents": [
        "document_ai_categories",
        "document_approvals",
        "document_audit_log",
        "document_categories",
        "document_shares",
        "document_tags",
        "document_templates",
        "document_versions",
        "document_workflows",
        "document_workflow_instances",
        "generated_documents",
    ],
    "projects": [
        "projects",
        "project_budgets",
        "project_comments",
        "project_expenses",
        "project_invoices",
        "project_tasks",
        "project_team",
        "project_time_entries",
    ],
    "licensing": [
        "admin_users",
        "master_activity_log",
        "master_companies",
        "master_invitations",
        "master_users",
        "master_user_companies",
        "custom_licenses",
        "license_audit_log",
        "license_keys",
        "license_violations",
        "subscription_plans",
        "subscription_transactions",
        "usage_tracking",
        "mobile_sessions",
        "offline_sync_queue",
    ],
    "sales": [
        "quotes",
        "quote_items",
        "quote_followups",
        "quote_messages",
        "invoices",
        "invoice_items",
        "payments",
    ],
    "crm": [
        "clients",
        "vendors",
        "vendor_quotations",
        "vendor_quotation_items",
    ],
    "accounting": [
        "chart_of_accounts",
        "journal_entries",
        "journal_entry_lines",
        "journal_entry_types",
        "fiscal_years",
        "fiscal_periods",
        "budgets",
        "budget_lines",
        "fixed_assets",
        "depreciation_entries",
        "bank_accounts",
        "bank_statements",
        "bank_statement_lines",
        "bank_rules",
        "tax_jurisdictions",
        "tax_rates",
        "tax_rules",
        "recurring_journals",
        "recurring_journal_lines",
        "cash_flow_forecasts",
        "cash_flow_scenarios",
        "ai_anomaly_detection",
        "ai_transaction_categories",
    ],
    "inventory": [
        "products",
        "product_categories",
        "warehouses",
        "stock_adjustments",
        "stock_adjustment_items",
        "stock_layers",
        "stock_movements",
        "stock_transfers",
        "stock_transfer_items",
    ],
    "purchasing": [
        "purchase_orders",
        "purchase_order_items",
        "goods_receipts",
        "goods_receipt_items",
        "supplier_bills",
        "supplier_bill_items",
        "supplier_payments",
        "request_for_quotations",
        "rfqs",
        "rfq_items",
    ],
    "hr": [
        "departments",
        "positions",
        "employees",
        "employee_documents",
        "employee_feedback",
        "employee_kpi_contract",
        "employee_movements",
        "employee_performance",
        "employee_recognition",
        "employee_skills",
        "employee_training",
        "attendance",
        "leave_balances",
        "leave_requests",
        "leave_transactions",
        "leave_types",
        "overtime_requests",
        "payroll_items",
        "payroll_periods",
        "performance_notifications",
        "performance_reviews",
        "performance_tasks",
        "goals",
        "kpi_performance",
        "job_postings",
        "candidates",
        "interviews",
        "onboarding",
        "training_programs",
        "succession_plans",
        "recognition_badges",
        "wellness_challenges",
        "wellness_challenge_progress",
        "wellness_checkins",
        "skills",
        "approvers",
    ],
}

# Tables that are views, not base tables.
VIEW_TABLES = {
    "v_account_balances",
    "v_ar_aging",
    "v_cash_flow",
    "v_employee_summary",
    "v_leave_calendar",
    "v_low_stock_alerts",
    "v_stock_levels",
    "v_stock_movements",
    "v_trial_balance",
}

# Hand-written names for irregular plurals / ambiguous tables.
CLASS_NAME_OVERRIDES = {
    "chart_of_accounts": "ChartOfAccount",
    "request_for_quotations": "RequestForQuotation",
    "rfqs": "Rfq",
    "rfq_items": "RfqItem",
    "kpi_performance": "KpiPerformance",
    "employee_kpi_contract": "EmployeeKpiContract",
    "sso_settings": "SsoSetting",
    "api_keys": "ApiKey",
    "webhook_logs": "WebhookLog",
    "email_templates": "EmailTemplate",
    "email_report_logs": "EmailReportLog",
    "master_users": "MasterUser",
    "master_companies": "MasterCompany",
    "master_invitations": "MasterInvitation",
    "master_user_companies": "MasterUserCompany",
    "master_activity_log": "MasterActivityLog",
    "usage_tracking": "UsageTracking",
    "onboarding": "Onboarding",
    "attendance": "Attendance",
    "leave_balances": "LeaveBalance",
    "leave_requests": "LeaveRequest",
    "leave_transactions": "LeaveTransaction",
    "leave_types": "LeaveType",
    "overtime_requests": "OvertimeRequest",
    "payroll_items": "PayrollItem",
    "payroll_periods": "PayrollPeriod",
    "performance_reviews": "PerformanceReview",
    "performance_tasks": "PerformanceTask",
    "performance_notifications": "PerformanceNotification",
    "recognition_badges": "RecognitionBadge",
    "wellness_challenges": "WellnessChallenge",
    "wellness_challenge_progress": "WellnessChallengeProgress",
    "wellness_checkins": "WellnessCheckin",
    "training_programs": "TrainingProgram",
    "succession_plans": "SuccessionPlan",
    "job_postings": "JobPosting",
    "employee_documents": "EmployeeDocument",
    "employee_feedback": "EmployeeFeedback",
    "employee_kpi_contract": "EmployeeKpiContract",
    "employee_movements": "EmployeeMovement",
    "employee_performance": "EmployeePerformance",
    "employee_recognition": "EmployeeRecognition",
    "employee_skills": "EmployeeSkill",
    "employee_training": "EmployeeTraining",
    "document_ai_categories": "DocumentAiCategory",
    "document_approvals": "DocumentApproval",
    "document_audit_log": "DocumentAuditLog",
    "document_categories": "DocumentCategory",
    "document_shares": "DocumentShare",
    "document_tags": "DocumentTag",
    "document_templates": "DocumentTemplate",
    "document_versions": "DocumentVersion",
    "document_workflows": "DocumentWorkflow",
    "document_workflow_instances": "DocumentWorkflowInstance",
    "generated_documents": "GeneratedDocument",
    "audit_log": "AuditLog",
    "audit_details": "AuditDetail",
    "user_activity_log": "UserActivityLog",
    "user_sessions": "UserSession",
    "user_companies": "UserCompany",
    "mobile_sessions": "MobileSession",
    "offline_sync_queue": "OfflineSyncQueue",
    "license_audit_log": "LicenseAuditLog",
    "license_keys": "LicenseKey",
    "license_violations": "LicenseViolation",
    "custom_licenses": "CustomLicense",
    "subscription_plans": "SubscriptionPlan",
    "subscription_transactions": "SubscriptionTransaction",
    "company_settings": "CompanySetting",
    "company_modules": "CompanyModule",
    "module_permissions": "ModulePermission",
    "user_module_roles": "UserModuleRole",
    "saved_reports": "SavedReport",
    "report_schedules": "ReportSchedule",
    "approval_rules": "ApprovalRule",
    "approval_tracking": "ApprovalTracking",
    "approval_workflows": "ApprovalWorkflow",
    "cash_flow_forecasts": "CashFlowForecast",
    "cash_flow_scenarios": "CashFlowScenario",
    "recurring_journals": "RecurringJournal",
    "recurring_journal_lines": "RecurringJournalLine",
    "fiscal_years": "FiscalYear",
    "fiscal_periods": "FiscalPeriod",
    "fixed_assets": "FixedAsset",
    "depreciation_entries": "DepreciationEntry",
    "bank_statements": "BankStatement",
    "bank_statement_lines": "BankStatementLine",
    "tax_jurisdictions": "TaxJurisdiction",
    "chart_of_accounts": "ChartOfAccount",
    "stock_adjustments": "StockAdjustment",
    "stock_adjustment_items": "StockAdjustmentItem",
    "stock_layers": "StockLayer",
    "stock_movements": "StockMovement",
    "stock_transfers": "StockTransfer",
    "stock_transfer_items": "StockTransferItem",
    "product_categories": "ProductCategory",
    "purchase_orders": "PurchaseOrder",
    "purchase_order_items": "PurchaseOrderItem",
    "goods_receipts": "GoodsReceipt",
    "goods_receipt_items": "GoodsReceiptItem",
    "supplier_bills": "SupplierBill",
    "supplier_bill_items": "SupplierBillItem",
    "supplier_payments": "SupplierPayment",
    "vendor_quotations": "VendorQuotation",
    "vendor_quotation_items": "VendorQuotationItem",
    "quote_items": "QuoteItem",
    "quote_followups": "QuoteFollowup",
    "quote_messages": "QuoteMessage",
    "invoice_items": "InvoiceItem",
    "project_budgets": "ProjectBudget",
    "project_comments": "ProjectComment",
    "project_expenses": "ProjectExpense",
    "project_invoices": "ProjectInvoice",
    "project_tasks": "ProjectTask",
    "project_team": "ProjectTeam",
    "project_time_entries": "ProjectTimeEntry",
    "ai_anomaly_detection": "AiAnomalyDetection",
    "ai_transaction_categories": "AiTransactionCategory",
    "budget_lines": "BudgetLine",
    "journal_entries": "JournalEntry",
    "journal_entry_lines": "JournalEntryLine",
    "journal_entry_types": "JournalEntryType",
    "exchange_rates": "ExchangeRate",
    "tax_rates": "TaxRate",
    "tax_rules": "TaxRule",
    "bank_rules": "BankRule",
    "api_keys": "ApiKey",
    "webhooks": "Webhook",
    "user_roles": "UserRole",
    "user_permissions": "UserPermission",
    "role_permissions": "RolePermission",
    "modules": "Module",
    "permissions": "Permission",
    "roles": "Role",
    "settings": "Setting",
    "notifications": "Notification",
    "attachments": "Attachment",
    "currencies": "Currency",
    "clients": "Client",
    "vendors": "Vendor",
    "invoices": "Invoice",
    "quotes": "Quote",
    "payments": "Payment",
    "products": "Product",
    "warehouses": "Warehouse",
    "employees": "Employee",
    "departments": "Department",
    "positions": "Position",
    "skills": "Skill",
    "approvers": "Approver",
    "goals": "Goal",
    "candidates": "Candidate",
    "interviews": "Interview",
    "companies": "Company",
    "projects": "Project",
}

PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
}

RESERVED_MODEL_FIELDS = {
    "objects", "pk", "save", "delete", "clean", "validate_unique", "full_clean",
}


def singularize(table: str) -> str:
    if table in CLASS_NAME_OVERRIDES:
        return CLASS_NAME_OVERRIDES[table]
    word = table
    if word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith(("sses", "shes", "ches", "xes", "zes")):
        word = word[:-2]
    elif word.endswith("ses") and not word.endswith("oses"):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return "".join(part.capitalize() for part in word.split("_"))


def load_columns() -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = defaultdict(list)
    with (SCHEMA_DIR / "columns.tsv").open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            table, column, coltype, nullable, default, key, extra = parts[:7]
            tables[table].append(
                {
                    "table": table,
                    "column": column,
                    "type": coltype,
                    "nullable": nullable == "YES",
                    "default": None if default == "NULL" else default,
                    "key": key,
                    "extra": extra,
                }
            )
    return tables


def load_indexes() -> dict[str, list[tuple[str, bool, list[str]]]]:
    indexes: dict[str, list[tuple[str, bool, list[str]]]] = defaultdict(list)
    with (SCHEMA_DIR / "indexes.tsv").open(encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                continue
            table, name, non_unique, columns = parts
            indexes[table].append((name, non_unique == "0", columns.split(",")))
    return indexes


def field_name(column: str) -> str:
    if column in PY_KEYWORDS or column in RESERVED_MODEL_FIELDS:
        return f"{column}_field"
    return column


def parse_enum_values(coltype: str) -> list[str]:
    body = coltype[coltype.index("(") + 1 : coltype.rindex(")")]
    return [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", body)]


def literal_default(value: str | None):
    if value is None:
        return None
    if value.upper() == "NULL":
        return None
    if value.upper() == "CURRENT_TIMESTAMP()" or value.upper().startswith("CURRENT_TIMESTAMP"):
        return "timezone.now"
    if value.lower() in {"true", "false"}:
        return value.capitalize()
    if re.fullmatch(r"-?\d+", value):
        return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        return f"Decimal('{value}')"
    if value.startswith("'") and value.endswith("'"):
        return repr(value[1:-1].replace("''", "'"))
    return repr(value)


def field_spec(col: dict) -> dict:
    """Return a field specification: name, class, kwargs."""
    name = field_name(col["column"])
    coltype = col["type"]
    base = coltype.split("(")[0].lower()
    nullable = col["nullable"]
    default = literal_default(col["default"])
    kwargs: list[str] = []

    if col["key"] == "PRI" and "auto_increment" in col["extra"]:
        return {"name": name, "cls": "models.AutoField", "kwargs": ["primary_key=True"]}

    if base in {"enum"}:
        values = parse_enum_values(coltype)
        max_len = max((len(v) for v in values), default=1)
        kwargs.append(f"max_length={max_len}")
        choices = ", ".join(f"({v!r}, {v.replace('_', ' ').title()!r})" for v in values)
        kwargs.append(f"choices=[{choices}]")
        cls = "models.CharField"
    elif base in {"varchar", "char"}:
        size = re.search(r"\((\d+)\)", coltype)
        kwargs.append(f"max_length={int(size.group(1)) if size else 255}")
        cls = "models.CharField"
    elif base in {"int", "integer", "mediumint"}:
        cls = "models.IntegerField"
    elif base == "bigint":
        cls = "models.BigIntegerField"
    elif base == "smallint":
        cls = "models.SmallIntegerField"
    elif base == "tinyint":
        size = re.search(r"\((\d+)\)", coltype)
        width = int(size.group(1)) if size else 4
        cls = "models.BooleanField" if width == 1 else "models.SmallIntegerField"
    elif base in {"decimal", "numeric"}:
        size = re.search(r"\((\d+),(\d+)\)", coltype)
        digits, places = (int(size.group(1)), int(size.group(2))) if size else (12, 2)
        kwargs.extend([f"max_digits={digits}", f"decimal_places={places}"])
        cls = "models.DecimalField"
    elif base in {"double", "float", "real"}:
        cls = "models.FloatField"
    elif base in {"text", "tinytext", "mediumtext", "longtext"}:
        cls = "models.TextField"
    elif base in {"datetime", "timestamp"}:
        if (
            col["column"] == "created_at"
            and not nullable
            and "current_timestamp" in (col["default"] or "").lower()
        ):
            return {"name": name, "cls": "models.DateTimeField", "kwargs": ["auto_now_add=True"]}
        if col["column"] == "updated_at" and "on update" in (col["extra"] or "").lower():
            return {"name": name, "cls": "models.DateTimeField", "kwargs": ["auto_now=True"]}
        cls = "models.DateTimeField"
    elif base == "date":
        cls = "models.DateField"
    elif base == "time":
        cls = "models.TimeField"
    elif base == "year":
        cls = "models.IntegerField"
    elif base in {"blob", "binary", "varbinary"}:
        cls = "models.BinaryField"
    elif base in {"json"}:
        cls = "models.JSONField"
    elif base in {"point", "geometry", "polygon"}:
        cls = "models.TextField"
    else:
        raise ValueError(f"Unsupported type {coltype} on {col['table']}.{col['column']}")

    if nullable:
        kwargs.append("null=True")
        if cls in {"models.CharField", "models.TextField"}:
            kwargs.append("blank=True")
    if col["key"] == "UNI":
        kwargs.append("unique=True")
    if default is not None and col["key"] != "UNI":
        kwargs.append(f"default={default}")

    return {"name": name, "cls": cls, "kwargs": kwargs}


def render_field(spec: dict) -> str:
    args = ", ".join(spec["kwargs"])
    return f"{spec['name']} = {spec['cls']}({args})"


def build_model(table: str, cols: list[dict], idxs: list[tuple[str, bool, list[str]]]) -> str:
    class_name = singularize(table)
    pk_fields = [c for c in cols if c["key"] == "PRI"]
    composite = len(pk_fields) > 1

    specs = [field_spec(col) for col in cols]
    spec_by_column = {col["column"]: spec for col, spec in zip(cols, specs)}

    unique_single: set[str] = {
        col["column"] for col in cols if col["key"] == "UNI"
    }
    db_index_single: set[str] = set()
    unique_together: list[tuple[str, ...]] = []
    multi_indexes: list[tuple[str, ...]] = []

    if composite:
        specs.insert(
            0,
            {"name": "id", "cls": "models.AutoField", "kwargs": ["primary_key=True"]},
        )
    elif len(pk_fields) == 1:
        pk_col = pk_fields[0]
        spec = spec_by_column[pk_col["column"]]
        if pk_col["column"] == "id":
            spec["cls"] = "models.AutoField"
            if "primary_key=True" not in spec["kwargs"]:
                spec["kwargs"] = ["primary_key=True"]
        elif "primary_key=True" not in spec["kwargs"]:
            spec["kwargs"].append("primary_key=True")

    seen: set[tuple[str, ...]] = set()
    for _name, unique, columns in idxs:
        cols_tuple = tuple(columns)
        if cols_tuple in seen:
            continue
        seen.add(cols_tuple)
        if len(columns) == 1:
            column = columns[0]
            if column in unique_single:
                continue
            spec = spec_by_column.get(column)
            if spec is None:
                continue
            if unique:
                unique_single.add(column)
                if "unique=True" not in spec["kwargs"]:
                    spec["kwargs"].append("unique=True")
            else:
                db_index_single.add(column)
        else:
            if unique:
                unique_together.append(cols_tuple)
            else:
                multi_indexes.append(cols_tuple)

    if composite and pk_fields:
        pk_cols = tuple(c["column"] for c in pk_fields)
        if pk_cols not in unique_together:
            unique_together.append(pk_cols)

    for column in db_index_single:
        spec = spec_by_column.get(column)
        if spec and "db_index=True" not in spec["kwargs"] and "unique=True" not in spec["kwargs"]:
            spec["kwargs"].append("db_index=True")

    body = [f"    {render_field(spec)}" for spec in specs]

    meta_lines = [f'        db_table = "{table}"']
    if unique_together:
        tuples = ", ".join(repr(t) for t in unique_together)
        meta_lines.append(f"        unique_together = ({tuples},)")
    if multi_indexes:
        index_lines = ",\n".join(
            "            models.Index(fields=[%s])" % ", ".join(f'"{c}"' for c in cols_tuple)
            for cols_tuple in multi_indexes
        )
        meta_lines.append(f"        indexes = [\n{index_lines},\n        ]")

    cls = [f"class {class_name}(models.Model):"]
    cls.append(f'    """Legacy table: {table}"""')
    cls.extend(body)
    cls.append("")
    cls.append("    class Meta:")
    cls.extend(meta_lines)
    cls.append("")
    cls.append("    def __str__(self):")
    cls.append(f'        return f"{class_name} {{self.pk}}"')
    cls.append("")
    cls.append("")

    return "\n".join(cls)


def main() -> None:
    tables = load_columns()
    indexes = load_indexes()

    base_tables = set(tables) - VIEW_TABLES
    assigned: list[str] = [t for group in MODEL_GROUPS.values() for t in group]
    missing = base_tables - set(assigned) - {"users"}
    duplicated = {t for t in assigned if assigned.count(t) > 1}
    if missing:
        raise SystemExit(f"Unmapped tables: {sorted(missing)}")
    if duplicated:
        raise SystemExit(f"Duplicate assignments: {sorted(duplicated)}")
    unknown = set(assigned) - base_tables
    if unknown:
        raise SystemExit(f"Assigned tables missing from schema: {sorted(unknown)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        '"""Auto-generated from information_schema metadata. Do not edit by hand."""\n\n'
        "from decimal import Decimal\n\n"
        "from django.db import models\n"
        "from django.utils import timezone\n\n"
    )

    printed: list[str] = []
    for group, group_tables in MODEL_GROUPS.items():
        chunks = [header]
        exported = []
        for table in group_tables:
            chunks.append(build_model(table, tables[table], indexes.get(table, [])))
            exported.append(singularize(table))
        chunks.append(f"__all__ = {exported!r}\n")
        (OUT_DIR / f"{group}.py").write_text("\n".join(chunks), encoding="utf-8")
        printed.extend(exported)

    init_lines = [
        '"""Model package: custom user plus all legacy schema models."""',
        "",
        "from .user import User",
    ]
    for group in MODEL_GROUPS:
        init_lines.append(f"from .{group} import *  # noqa: F401,F403")
    init_lines.append("")
    (OUT_DIR / "__init__.py").write_text("\n".join(init_lines), encoding="utf-8")

    print(f"Generated {len(printed)} models across {len(MODEL_GROUPS)} files")
    print(f"Custom user + {len(base_tables) - 1} legacy tables")


if __name__ == "__main__":
    main()
