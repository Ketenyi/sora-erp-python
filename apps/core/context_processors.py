"""Template context shared by every page (menu, branding, permissions)."""

from __future__ import annotations

from django.utils import timezone

from apps.core.permissions import get_access
from apps.core.services import get_company_settings, get_currency_symbol


def _normalise_logo(value: str) -> str:
    if not value:
        return ""
    value = value.replace("/assets/uploads/", "/uploads/")
    if value.startswith(("http://", "https://", "/")):
        return value
    return "/" + value


def _normalise_color(value: str, fallback: str = "#FFD700") -> str:
    candidate = (value or "").strip()
    if len(candidate) == 7 and candidate.startswith("#"):
        try:
            int(candidate[1:], 16)
            return candidate.upper()
        except ValueError:
            pass
    return fallback


def _item(name, icon, route, submodule=None, module=None, action="view"):
    row = {"name": name, "icon": icon, "route": route}
    if submodule:
        row["submodule"] = submodule
    if module:
        row["module"] = module
    if action != "view":
        row["action"] = action
    return row


def _hr_menu(access):
    if access.is_hr_admin or access.has_grant("hr"):
        items = [
            _item("Dashboard", "tachometer-alt", "/hr/dashboard", "dashboard"),
            _item("Employees", "user-friends", "/hr/employees", "employees"),
            _item("Departments", "building", "/hr/departments", "departments"),
            _item("Positions", "briefcase", "/hr/positions", "positions"),
            _item("Leave Management", "calendar-alt", "/hr/leave", "leave"),
            _item("Attendance", "clock", "/hr/attendance", "attendance"),
            _item("Performance", "chart-line", "/hr/performance", "performance"),
            _item("Recruitment", "user-plus", "/hr/recruitment/jobs", "recruitment"),
            _item("Payroll", "money-bill-wave", "/hr/payroll", "payroll"),
            _item("Training", "graduation-cap", "/hr/training", "training"),
            _item("Recognition", "award", "/hr/recognition", "recognition"),
            _item("Succession", "sitemap", "/hr/succession", "succession"),
            _item("Wellness", "heart-pulse", "/hr/wellness", "wellness"),
            _item("HR Analytics", "chart-pie", "/hr/analytics", "analytics"),
            _item("Reports", "file-alt", "/hr/reports", "reports"),
        ]
        if access.has_grant("hr"):
            items.extend(
                [
                    _item("My Dashboard", "id-badge", "/hr/employee/dashboard", "self_service"),
                    _item("Request Leave", "calendar-plus", "/hr/leave/request", "leave"),
                    _item("My Leave", "list", "/hr/leave/my-requests", "leave"),
                    _item("Leave Balance", "chart-line", "/hr/leave/balance", "leave"),
                    _item("My Attendance", "clock", "/hr/attendance/my-time", "attendance"),
                    _item("Overtime", "clock", "/hr/leave/overtime", "leave"),
                ]
            )
        return items
    if access.is_hr_viewer:
        return [
            _item("Dashboard", "tachometer-alt", "/hr/dashboard", "dashboard"),
            _item("View Employees", "user-friends", "/hr/employees", "employees"),
            _item("Departments", "building", "/hr/departments", "departments"),
            _item("Positions", "briefcase", "/hr/positions", "positions"),
            _item("Leave Overview", "calendar-alt", "/hr/leave", "leave"),
            _item("Reports", "file-alt", "/hr/reports", "reports"),
        ]
    if access.is_employee:
        return [
            _item("My Dashboard", "tachometer-alt", "/hr/employee/dashboard", "self_service"),
            _item("Request Leave", "calendar-plus", "/hr/leave/request", "leave"),
            _item("My Leave", "list", "/hr/leave/my-requests", "leave"),
            _item("Leave Balance", "chart-line", "/hr/leave/balance", "leave"),
            _item("My Attendance", "clock", "/hr/attendance/my-time", "attendance"),
            _item("Overtime", "clock", "/hr/leave/overtime", "leave"),
        ]
    return []


def _sales_menu(access):
    has_sales = (
        access.is_sales_admin or access.is_sales_rep or access.has_grant("sales")
    )
    has_crm = access.is_crm_admin or access.is_crm_rep or access.has_grant("crm")
    shared_module = "crm" if access.has_grant("crm") else "sales"
    shared = [
        _item("Clients", "user-friends", "/crm/clients", "clients", shared_module),
        _item("Vendors", "truck", "/crm/vendors", "vendors", shared_module),
    ]
    if has_crm and not has_sales:
        return [
            _item("CRM Dashboard", "tachometer-alt", "/crm/dashboard", "dashboard", "crm"),
            *shared,
            _item("CRM Reports", "chart-bar", "/crm/reports", "reports", "crm"),
        ]
    if access.is_sales_admin or access.has_grant("sales"):
        return [
            _item("Dashboard", "tachometer-alt", "/sales/dashboard", "dashboard"),
            _item("All Quotes", "file-alt", "/sales/quotes", "quotes"),
            _item("Pipeline", "chart-line", "/sales/pipeline", "pipeline"),
            *shared,
            _item("Sales Reports", "chart-bar", "/sales/reports", "reports"),
        ]
    if access.is_sales_rep:
        return [
            _item("My Dashboard", "tachometer-alt", "/sales/dashboard", "dashboard"),
            _item("My Quotes", "file-alt", "/sales/quotes", "quotes"),
            _item("My Pipeline", "chart-line", "/sales/pipeline", "pipeline"),
            *shared,
            _item("Sales Reports", "chart-bar", "/sales/reports", "reports"),
        ]
    return shared


def _projects_menu(access):
    if access.is_projects_admin or access.has_grant("projects"):
        items = [
            _item("PM Dashboard", "tachometer-alt", "/projects/dashboard", "dashboard"),
            _item("Portfolio", "folder-open", "/projects/portfolio", "portfolio"),
            _item("Tenders & Gantt", "chart-gantt", "/projects/tender", "tender"),
            _item("MOM & Approvals", "file-signature", "/projects/approvals", "approvals"),
            _item("Field Expenses", "receipt", "/projects/expenses", "expenses"),
            _item("Scorecards", "medal", "/projects/scorecards", "scorecards"),
            _item("Handover", "people-arrows", "/projects/handover", "handover"),
            _item("SLA Hub", "headset", "/projects/sla", "sla"),
            _item("Analytics", "chart-pie", "/projects/analytics", "analytics"),
            _item("My Dashboards", "table-columns", "/projects/dashboards", "dashboards"),
        ]
        if access.has_grant("projects"):
            items.insert(0, _item("My Work", "tasks", "/projects/my-tasks", "my_tasks"))
        if access.is_admin:
            items.append(
                _item("License & Features", "id-card", "/projects/license", "license")
            )
        return items
    if access.is_projects_viewer:
        return [
            _item("PM Dashboard", "tachometer-alt", "/projects/dashboard", "dashboard"),
            _item("Portfolio", "folder-open", "/projects/portfolio", "portfolio"),
            _item("Tenders & Gantt", "chart-gantt", "/projects/tender", "tender"),
            _item("MOM & Approvals", "file-signature", "/projects/approvals", "approvals"),
            _item("Field Expenses", "receipt", "/projects/expenses", "expenses"),
            _item("Scorecards", "medal", "/projects/scorecards", "scorecards"),
            _item("SLA Hub", "headset", "/projects/sla", "sla"),
            _item("Analytics", "chart-pie", "/projects/analytics", "analytics"),
            _item("My Dashboards", "table-columns", "/projects/dashboards", "dashboards"),
        ]
    return [
        _item("My Work", "tasks", "/projects/my-tasks", "my_tasks"),
        _item("PM Dashboard", "tachometer-alt", "/projects/dashboard", "dashboard"),
        _item("Portfolio", "folder-open", "/projects/portfolio", "portfolio"),
        _item("Tenders & Gantt", "chart-gantt", "/projects/tender", "tender"),
        _item("MOM & Approvals", "file-signature", "/projects/approvals", "approvals"),
        _item("Field Expenses", "receipt", "/projects/expenses", "expenses"),
        _item("Scorecards", "medal", "/projects/scorecards", "scorecards"),
        _item("Handover", "people-arrows", "/projects/handover", "handover"),
        _item("SLA Hub", "headset", "/projects/sla", "sla"),
        _item("Analytics", "chart-pie", "/projects/analytics", "analytics"),
        _item("My Dashboards", "table-columns", "/projects/dashboards", "dashboards"),
    ]


def _finance_menu(access):
    if access.is_accounts_admin or access.has_grant("accounts"):
        return [
            _item("Dashboard", "tachometer-alt", "/accounts/dashboard", "dashboard"),
            _item("Chart of Accounts", "list", "/accounts/chart-of-accounts", "chart_of_accounts"),
            _item("Journal Entries", "book", "/accounts/journal-entries", "journal_entries"),
            _item("Ledger", "table", "/accounts/ledger", "ledger"),
            _item("Trial Balance", "balance-scale", "/accounts/trial-balance", "trial_balance"),
            _item("Invoices", "file-invoice-dollar", "/invoices/list", "invoices"),
            _item("Accounts Reports", "chart-bar", "/accounts/reports", "reports"),
        ]
    return [
        _item("Dashboard", "tachometer-alt", "/accounts/dashboard", "dashboard"),
        _item("Invoices", "file-invoice-dollar", "/invoices/list", "invoices"),
        _item("Accounts Reports", "chart-bar", "/accounts/reports", "reports"),
    ]


def _inventory_menu(access):
    if access.is_inventory_admin or access.has_grant("inventory"):
        return [
            _item("Dashboard", "tachometer-alt", "/inventory/dashboard", "dashboard"),
            _item("Products", "box", "/inventory/products", "products"),
            _item("Categories", "tags", "/inventory/categories", "categories"),
            _item("Stock Movements", "exchange-alt", "/inventory/stock-movements", "stock_movements"),
            _item("Stock Transfers", "truck-arrow-right", "/inventory/stock-transfers", "stock_transfers"),
            _item("Stock Adjustments", "sliders", "/inventory/stock-adjustments", "stock_adjustments"),
            _item("Inventory Reports", "chart-bar", "/inventory/reports", "reports"),
        ]
    return [
        _item("Dashboard", "tachometer-alt", "/inventory/dashboard", "dashboard"),
        _item("Products", "box", "/inventory/products", "products"),
    ]


def _purchasing_menu(access):
    if access.is_purchasing_admin or access.has_grant("purchasing"):
        return [
            _item("Dashboard", "tachometer-alt", "/purchasing/dashboard", "dashboard"),
            _item("Purchase Orders", "shopping-cart", "/purchasing/purchase-orders", "purchase_orders"),
            _item("Goods Receipts", "check-double", "/purchasing/goods-receipts", "goods_receipts"),
            _item("Supplier Bills", "file-invoice", "/purchasing/supplier-bills", "supplier_bills"),
            _item("Supplier Payments", "money-check", "/purchasing/supplier-payments", "supplier_payments"),
            _item("Requests for Quotation", "file-signature", "/purchasing/rfq", "rfq"),
            _item("Purchasing Reports", "chart-bar", "/purchasing/reports", "reports"),
        ]
    return [
        _item("Dashboard", "tachometer-alt", "/purchasing/dashboard", "dashboard"),
        _item("Purchase Orders", "shopping-cart", "/purchasing/purchase-orders", "purchase_orders"),
    ]


def _helpdesk_menu(access):
    items = [
        _item("Dashboard", "tachometer-alt", "/helpdesk/dashboard", "dashboard"),
        _item("Ticket Queue", "ticket", "/helpdesk/tickets", "tickets"),
        _item("New Ticket", "plus-circle", "/helpdesk/tickets/new", "tickets", action="create"),
        _item("SLA Contracts", "file-contract", "/helpdesk/contracts", "contracts"),
        _item("Health Checks", "calendar-check", "/helpdesk/health-checks", "health_checks"),
    ]
    if access.can("helpdesk", "manage"):
        items.append(
            _item("Automation & CSAT", "gears", "/helpdesk/settings", "settings")
        )
    return items


def _assets_menu(access):
    return [
        _item("Dashboard", "tachometer-alt", "/assets/dashboard", "dashboard"),
        _item("Asset Register", "barcode", "/assets/register", "register"),
        _item("Categories", "tags", "/assets/categories", "categories"),
        _item("Maintenance", "screwdriver-wrench", "/assets/maintenance", "maintenance"),
        _item("Depreciation", "chart-line", "/assets/depreciation", "depreciation"),
        _item("Endpoint Agents", "laptop-code", "/assets/agents", "agents"),
    ]


def _pos_menu(access):
    return [
        _item("Terminal", "cash-register", "/pos/", "terminal"),
        _item("POS Dashboard", "chart-line", "/pos/dashboard", "dashboard"),
        _item("Sales History", "receipt", "/pos/sales", "sales"),
        _item("Sessions & Cash-up", "cash-register", "/pos/sessions", "sessions"),
    ]


def _visible_items(access, default_module, items):
    visible = []
    for item in items:
        module = item.get("module", default_module)
        if access.has_grant(module) and not access.can(
            module, item.get("action", "view"), item.get("submodule")
        ):
            continue
        visible.append(item)
    return visible


def build_menu(access, enabled_codes=None, is_tenant_admin=False, is_platform_admin=False):
    """Build the sidebar, optionally restricted to the tenant's licences."""

    nav_modules = {
        "hr": "SORA_HR",
        "sales": "SORA_SALES_CRM",
        "crm": "SORA_SALES_CRM",
        "projects": "SORA_PROJECTS",
        "finance": "SORA_FINANCE",
        "inventory": "SORA_INVENTORY",
        "purchasing": "SORA_PROCUREMENT",
        "helpdesk": "SORA_HELPDESK_SLA",
        "assets": "SORA_ASSET_MANAGEMENT",
        "pos": "SORA_POS",
    }

    def licensed(key):
        if enabled_codes is None:
            return True
        code = nav_modules.get(key)
        return code is None or code in enabled_codes

    menu = {
        "dashboard": {
            "name": "Dashboard",
            "icon": "tachometer-alt",
            "route": "/dashboard",
            "type": "single",
        }
    }

    if access.can_view_hr and licensed("hr"):
        items = _visible_items(access, "hr", _hr_menu(access))
        if items:
            menu["hr"] = {
                "name": "Human Resources",
                "icon": "users",
                "type": "dropdown",
                "items": items,
            }

    if (access.can_view_sales or access.can_view_crm) and licensed("sales"):
        items = _visible_items(access, "sales", _sales_menu(access))
        if items:
            menu["sales"] = {
                "name": "Sales & CRM",
                "icon": "chart-line",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_projects and licensed("projects"):
        items = _visible_items(access, "projects", _projects_menu(access))
        if items:
            menu["projects"] = {
                "name": "Projects",
                "icon": "diagram-project",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_accounts and licensed("finance"):
        items = _visible_items(access, "accounts", _finance_menu(access))
        if items:
            menu["finance"] = {
                "name": "Finance & Accounts",
                "icon": "book",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_inventory and licensed("inventory"):
        items = _visible_items(access, "inventory", _inventory_menu(access))
        if items:
            menu["inventory"] = {
                "name": "Inventory",
                "icon": "boxes",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_purchasing and licensed("purchasing"):
        items = _visible_items(access, "purchasing", _purchasing_menu(access))
        if items:
            menu["purchasing"] = {
                "name": "Procurement",
                "icon": "truck",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_helpdesk and licensed("helpdesk"):
        items = _visible_items(access, "helpdesk", _helpdesk_menu(access))
        if items:
            menu["helpdesk"] = {
                "name": "Helpdesk & SLA",
                "icon": "headset",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_assets and licensed("assets"):
        items = _visible_items(access, "assets", _assets_menu(access))
        if items:
            menu["assets"] = {
                "name": "Asset Management",
                "icon": "barcode",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_pos and licensed("pos"):
        items = _visible_items(access, "pos", _pos_menu(access))
        if items:
            menu["pos"] = {
                "name": "Point of Sale",
                "icon": "cash-register",
                "type": "dropdown",
                "items": items,
            }

    if access.can_view_settings:
        menu["settings"] = {
            "name": "Settings",
            "icon": "cog",
            "route": "/settings",
            "type": "single",
        }

    if is_tenant_admin or is_platform_admin:
        menu["billing"] = {
            "name": "Billing & Plan",
            "icon": "credit-card",
            "route": "/billing/",
            "type": "single",
        }

    if access.is_admin:
        admin_items = [
            {"name": "Admin Console", "icon": "gauge-high", "route": "/admin/dashboard"},
            {"name": "User Management", "icon": "users", "route": "/settings/users"},
            {"name": "Roles & Permissions", "icon": "user-shield", "route": "/settings/roles"},
            {"name": "System Information", "icon": "database", "route": "/settings/system"},
        ]
        if is_platform_admin:
            admin_items.append(
                {"name": "SORA Cloud Control", "icon": "cloud", "route": "/cloud/"}
            )
        menu["admin"] = {
            "name": "Administration",
            "icon": "shield-halved",
            "type": "dropdown",
            "items": admin_items,
        }
    elif is_platform_admin:
        menu["cloud"] = {
            "name": "SORA Cloud Control",
            "icon": "cloud",
            "route": "/cloud/",
            "type": "single",
        }
    return menu


def _search_items(menu):
    items = []
    for entry in menu.values():
        if entry["type"] == "dropdown":
            for sub in entry["items"]:
                items.append(
                    {
                        "name": sub["name"],
                        "route": sub["route"],
                        "icon": sub["icon"],
                        "category": entry["name"],
                    }
                )
        else:
            items.append(
                {
                    "name": entry["name"],
                    "route": entry["route"],
                    "icon": entry["icon"],
                    "category": "Main",
                }
            )
    return items


def sora(request):
    access = get_access(getattr(request, "user", None))
    company = get_company_settings()
    user = getattr(request, "user", None)
    tenant = getattr(request, "tenant", None)
    membership = getattr(request, "membership", None)
    is_platform_admin = bool(getattr(user, "is_platform_admin", False))
    is_tenant_admin = bool(membership and membership.is_tenant_admin)
    enabled_codes = getattr(request, "enabled_module_codes", None) if tenant else None

    menu = build_menu(
        access,
        enabled_codes=enabled_codes,
        is_tenant_admin=is_tenant_admin,
        is_platform_admin=is_platform_admin,
    )

    saas = {
        "tenant": tenant,
        "membership": membership,
        "trial": None,
        "locked": False,
        "lock_reason": "",
        "status": "",
        "status_label": "",
        "announcements": [],
        "impersonating": bool(getattr(request, "impersonating", False)),
        "impersonator_name": "",
        "is_platform_admin": is_platform_admin,
        "is_tenant_admin": is_tenant_admin,
        "platform_name": "SORA ERP",
        "billing_url": "/billing/",
        "cloud_url": "/cloud/",
        "brand_color": "#FFD700",
    }

    if tenant is not None:
        from apps.saas.services import (
            announcements_for,
            enabled_module_map,
            platform_settings,
            trial_banner,
        )

        licence_map = enabled_module_map(tenant)
        if len(licence_map) == 1:
            only_module = next(iter(licence_map.values())).module
            menu["dashboard"]["route"] = only_module.landing_route or "/dashboard"
            menu["dashboard"]["name"] = only_module.brand_name or only_module.name
        settings_data = platform_settings()
        saas.update(
            {
                "trial": trial_banner(tenant),
                "locked": tenant.is_locked,
                "lock_reason": tenant.lock_reason,
                "status": tenant.effective_status,
                "status_label": tenant.effective_status_label,
                "announcements": announcements_for(tenant, user),
                "platform_name": settings_data.get("platform_name") or "SORA ERP",
                "trial_days_left": tenant.trial_days_left,
                "enabled_modules": list(licence_map.values()),
            }
        )
        platform_color = _normalise_color(settings_data.get("brand_primary_color"))
        saas["brand_color"] = _normalise_color(tenant.primary_color, platform_color)

    impersonator = getattr(request, "impersonator", None)
    if impersonator is not None:
        saas["impersonator_name"] = impersonator.get_full_name()

    company_name = company.get("company_name") or "SORA ERP"
    if tenant is not None:
        company_name = tenant.display_name
    logo = ""
    if tenant is not None and tenant.logo_url:
        logo = _normalise_logo(tenant.logo_url)
    company_logo = logo or _normalise_logo(company.get("company_logo", "")) or "/static/uploads/logo_1775239899.png"

    user_name = ""
    if user is not None and getattr(user, "is_authenticated", False):
        user_name = user.get_full_name()

    return {
        "access": access,
        "company": company,
        "company_name": company_name,
        "company_logo": company_logo,
        "currency_symbol": get_currency_symbol(company),
        "menu_items": menu,
        "search_items": _search_items(menu),
        "current_url": request.path.strip("/"),
        "theme": request.COOKIES.get("sora_theme", "light"),
        "sidebar_collapsed": request.COOKIES.get("sora_sidebar") == "collapsed",
        "user_name": user_name or "User",
        "user_role": (getattr(user, "role", "") or "guest").replace("_", " ").title(),
        "today": timezone.localdate(),
        "saas": saas,
    }
