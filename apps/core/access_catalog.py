"""Module and submodule catalogue shared by access control and routing.

The sidebar, the module-access screen and the request guard all read from this
single map so a grant means the same thing everywhere.
"""

from __future__ import annotations

MODULE_CATALOG = {
    "hr": {
        "label": "Human Resources",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("employees", "Employees"),
            ("departments", "Departments"),
            ("positions", "Positions"),
            ("leave", "Leave"),
            ("attendance", "Attendance"),
            ("performance", "Performance"),
            ("recruitment", "Recruitment"),
            ("payroll", "Payroll"),
            ("training", "Training"),
            ("recognition", "Recognition"),
            ("succession", "Succession"),
            ("wellness", "Wellness"),
            ("analytics", "HR analytics"),
            ("reports", "Reports"),
            ("self_service", "Employee self-service"),
        ],
    },
    "sales": {
        "label": "Sales",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("quotes", "Quotes"),
            ("pipeline", "Pipeline"),
            ("reports", "Reports"),
        ],
    },
    "crm": {
        "label": "CRM",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("clients", "Clients"),
            ("vendors", "Vendors"),
            ("reports", "Reports"),
        ],
    },
    "accounts": {
        "label": "Finance & Accounts",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("chart_of_accounts", "Chart of accounts"),
            ("journal_entries", "Journal entries"),
            ("ledger", "Ledger"),
            ("trial_balance", "Trial balance"),
            ("invoices", "Invoices"),
            ("receivables", "Receivables"),
            ("reports", "Reports"),
        ],
    },
    "inventory": {
        "label": "Inventory",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("products", "Products"),
            ("categories", "Categories"),
            ("stock_movements", "Stock movements"),
            ("stock_transfers", "Stock transfers"),
            ("stock_adjustments", "Stock adjustments"),
            ("reports", "Reports"),
        ],
    },
    "purchasing": {
        "label": "Procurement",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("purchase_orders", "Purchase orders"),
            ("goods_receipts", "Goods receipts"),
            ("supplier_bills", "Supplier bills"),
            ("supplier_payments", "Supplier payments"),
            ("rfq", "Requests for quotation"),
            ("reports", "Reports"),
        ],
    },
    "projects": {
        "label": "Projects",
        "submodules": [
            ("dashboard", "PM dashboard"),
            ("portfolio", "Portfolio"),
            ("tender", "Tenders & Gantt"),
            ("approvals", "MOM & approvals"),
            ("expenses", "Field expenses"),
            ("scorecards", "Scorecards"),
            ("handover", "Handover"),
            ("sla", "SLA hub"),
            ("analytics", "Analytics"),
            ("dashboards", "Saved dashboards"),
            ("my_tasks", "My work"),
            ("license", "Licence & features"),
        ],
    },
    "helpdesk": {
        "label": "Helpdesk & SLA",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("tickets", "Tickets"),
            ("contracts", "SLA contracts"),
            ("health_checks", "Health checks"),
            ("settings", "Automation & CSAT"),
        ],
    },
    "assets": {
        "label": "Asset Management",
        "submodules": [
            ("dashboard", "Dashboard"),
            ("register", "Asset register"),
            ("categories", "Categories"),
            ("maintenance", "Maintenance"),
            ("depreciation", "Depreciation"),
            ("agents", "Endpoint agents"),
        ],
    },
    "pos": {
        "label": "Point of Sale",
        "submodules": [
            ("terminal", "Terminal"),
            ("dashboard", "Dashboard"),
            ("sales", "Sales history"),
            ("sessions", "Sessions & cash-up"),
        ],
    },
}

MODULE_LABELS = {key: row["label"] for key, row in MODULE_CATALOG.items()}


def submodule_choices(module: str) -> list[tuple[str, str]]:
    return list(MODULE_CATALOG.get(module, {}).get("submodules", []))


def submodule_label(module: str, submodule: str) -> str:
    for code, label in submodule_choices(module):
        if code == submodule:
            return label
    return submodule.replace("_", " ").title()


ROUTE_TARGETS: list[tuple[str, tuple[tuple[str, str], ...]]] = [
    ("/hr/employee", (("hr", "self_service"),)),
    ("/hr/leave/approvers", (("hr", "leave"),)),
    ("/hr/leave", (("hr", "leave"),)),
    ("/hr/attendance", (("hr", "attendance"),)),
    ("/hr/dashboard", (("hr", "dashboard"),)),
    ("/hr/employees", (("hr", "employees"),)),
    ("/hr/departments", (("hr", "departments"),)),
    ("/hr/positions", (("hr", "positions"),)),
    ("/hr/performance", (("hr", "performance"),)),
    ("/hr/recruitment", (("hr", "recruitment"),)),
    ("/hr/payroll", (("hr", "payroll"),)),
    ("/hr/training", (("hr", "training"),)),
    ("/hr/recognition", (("hr", "recognition"),)),
    ("/hr/succession", (("hr", "succession"),)),
    ("/hr/wellness", (("hr", "wellness"),)),
    ("/hr/analytics", (("hr", "analytics"),)),
    ("/hr/reports", (("hr", "reports"),)),
    ("/sales/quotes", (("sales", "quotes"),)),
    ("/sales/pipeline", (("sales", "pipeline"),)),
    ("/sales/reports", (("sales", "reports"),)),
    ("/sales/dashboard", (("sales", "dashboard"),)),
    ("/crm/clients", (("crm", "clients"), ("sales", "clients"))),
    ("/crm/vendors", (("crm", "vendors"), ("purchasing", "vendors"))),
    ("/crm/dashboard", (("crm", "dashboard"),)),
    ("/crm/reports", (("crm", "reports"),)),
    ("/accounts/chart-of-accounts", (("accounts", "chart_of_accounts"),)),
    ("/accounts/journal-entries", (("accounts", "journal_entries"),)),
    ("/accounts/ledger", (("accounts", "ledger"),)),
    ("/accounts/trial-balance", (("accounts", "trial_balance"),)),
    ("/accounts/reports", (("accounts", "reports"),)),
    ("/accounts/dashboard", (("accounts", "dashboard"),)),
    ("/invoices/receivables", (("accounts", "receivables"), ("sales", "receivables"))),
    ("/invoices/reports", (("accounts", "reports"), ("sales", "reports"))),
    ("/invoices", (("accounts", "invoices"), ("sales", "invoices"))),
    ("/inventory/products", (("inventory", "products"),)),
    ("/inventory/categories", (("inventory", "categories"),)),
    ("/inventory/stock-movements", (("inventory", "stock_movements"),)),
    ("/inventory/stock-transfers", (("inventory", "stock_transfers"),)),
    ("/inventory/stock-adjustments", (("inventory", "stock_adjustments"),)),
    ("/inventory/reports", (("inventory", "reports"),)),
    ("/inventory/dashboard", (("inventory", "dashboard"),)),
    ("/purchasing/purchase-orders", (("purchasing", "purchase_orders"),)),
    ("/purchasing/goods-receipts", (("purchasing", "goods_receipts"),)),
    ("/purchasing/supplier-bills", (("purchasing", "supplier_bills"),)),
    ("/purchasing/supplier-payments", (("purchasing", "supplier_payments"),)),
    ("/purchasing/rfq", (("purchasing", "rfq"),)),
    ("/purchasing/reports", (("purchasing", "reports"),)),
    ("/purchasing/dashboard", (("purchasing", "dashboard"),)),
    ("/projects/license", (("projects", "license"),)),
    ("/projects/my-tasks", (("projects", "my_tasks"),)),
    ("/projects/dashboard", (("projects", "dashboard"),)),
    ("/projects/portfolio", (("projects", "portfolio"),)),
    ("/projects/tender", (("projects", "tender"),)),
    ("/projects/approvals", (("projects", "approvals"),)),
    ("/projects/expenses", (("projects", "expenses"),)),
    ("/projects/scorecards", (("projects", "scorecards"),)),
    ("/projects/handover", (("projects", "handover"),)),
    ("/projects/sla", (("projects", "sla"),)),
    ("/projects/analytics", (("projects", "analytics"),)),
    ("/projects/dashboards", (("projects", "dashboards"),)),
    ("/projects", (("projects", "portfolio"),)),
    ("/helpdesk/settings", (("helpdesk", "settings"),)),
    ("/helpdesk/contracts", (("helpdesk", "contracts"),)),
    ("/helpdesk/health-checks", (("helpdesk", "health_checks"),)),
    ("/helpdesk/tickets", (("helpdesk", "tickets"),)),
    ("/helpdesk/dashboard", (("helpdesk", "dashboard"),)),
    ("/assets/categories", (("assets", "categories"),)),
    ("/assets/maintenance", (("assets", "maintenance"),)),
    ("/assets/depreciation", (("assets", "depreciation"),)),
    ("/assets/agents", (("assets", "agents"),)),
    ("/assets/register", (("assets", "register"),)),
    ("/assets/dashboard", (("assets", "dashboard"),)),
    ("/assets", (("assets", "register"),)),
    ("/pos/dashboard", (("pos", "dashboard"),)),
    ("/pos/sales", (("pos", "sales"),)),
    ("/pos/sessions", (("pos", "sessions"),)),
    ("/pos", (("pos", "terminal"),)),
]


def resolve_targets(path: str) -> tuple[tuple[str, str], ...]:
    """Return the permission targets that can open ``path``."""

    for prefix, targets in ROUTE_TARGETS:
        if path == prefix or path.startswith(prefix + "/"):
            return targets
    return ()
