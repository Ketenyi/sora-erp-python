"""Seed base RBAC, company settings and demo accounts.

Usage:
    python manage.py seed_data
    python manage.py seed_data --demo
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.tenancy import default_tenant, tenant_context

from apps.core.models import (
    Attendance,
    BankAccount,
    ChartOfAccount,
    Client,
    CompanySetting,
    Employee,
    Invoice,
    JournalEntry,
    JournalEntryLine,
    Permission,
    Product,
    ProductCategory,
    Quote,
    Role,
    RolePermission,
    User,
    UserRole,
    Vendor,
    Warehouse,
)

COMPANY_SETTINGS = {
    "company_name": "SORA ERP",
    "company_logo": "/static/uploads/logo_1775239899.png",
    "company_email": "info@sora.com",
    "company_phone": "+254 700 000 000",
    "company_address": "Nairobi, Kenya",
    "currency_symbol": "KSh",
    "currency_code": "KES",
    "tax_rate": "16",
    "invoice_terms": "Payment is due within 30 days of the invoice date.",
    "quote_terms": "This quotation is valid for 30 days from the issue date.",
    "app_version": "1.0.0",
}

MODULES = ("hr", "sales", "accounts", "inventory", "purchasing", "crm", "reports", "settings")
ACTIONS = ("view", "create", "edit", "delete", "approve", "export", "manage")

ROLE_DEFINITIONS = {
    "admin": ("Administrator", "Full system access"),
    "sales_admin": ("Sales Manager", "Sales and CRM oversight"),
    "sales_rep": ("Sales Representative", "Own quotes and clients"),
    "accounts_admin": ("Accounts Administrator", "Finance and invoicing"),
    "hr_admin": ("HR Administrator", "Human resources management"),
    "hr_viewer": ("HR Viewer", "Read-only HR access"),
    "inventory_admin": ("Inventory Administrator", "Stock and warehouses"),
    "inventory_clerk": ("Inventory Clerk", "Stock movements and counting"),
    "procurement_admin": ("Procurement Administrator", "Purchasing and suppliers"),
    "crm_admin": ("CRM Administrator", "Clients and vendors"),
    "crm_rep": ("CRM Representative", "Client relationship management"),
    "viewer": ("Viewer", "Read-only access across modules"),
    "employee": ("Employee", "HR self-service"),
}

ROLE_PERMISSIONS = {
    "admin": {module: set(ACTIONS) for module in MODULES},
    "sales_admin": {
        "sales": {"view", "create", "edit", "delete", "approve", "export"},
        "crm": {"view", "create", "edit", "delete", "export"},
        "reports": {"view", "export"},
    },
    "sales_rep": {
        "sales": {"view", "create", "edit", "export"},
        "crm": {"view", "create", "edit"},
    },
    "accounts_admin": {
        "accounts": {"view", "create", "edit", "delete", "approve", "export", "manage"},
        "sales": {"view", "export"},
        "purchasing": {"view", "export"},
        "reports": {"view", "export"},
    },
    "hr_admin": {
        "hr": {"view", "create", "edit", "delete", "approve", "export", "manage"},
        "reports": {"view", "export"},
    },
    "hr_viewer": {"hr": {"view", "export"}},
    "inventory_admin": {
        "inventory": {"view", "create", "edit", "delete", "export", "manage"},
        "purchasing": {"view"},
        "reports": {"view", "export"},
    },
    "inventory_clerk": {"inventory": {"view", "create", "edit", "export"}},
    "procurement_admin": {
        "purchasing": {"view", "create", "edit", "delete", "approve", "export", "manage"},
        "inventory": {"view"},
        "accounts": {"view"},
        "reports": {"view", "export"},
    },
    "crm_admin": {
        "crm": {"view", "create", "edit", "delete", "export"},
        "sales": {"view", "create", "edit"},
    },
    "crm_rep": {"crm": {"view", "create", "edit"}},
    "viewer": {module: {"view", "export"} for module in MODULES},
    "employee": {"hr": {"view"}},
}

USERS = [
    {
        "email": "admin@sora.com",
        "password": "admin123",
        "username": "admin",
        "first_name": "System",
        "last_name": "Administrator",
        "role": "admin",
        "login_role": "admin",
    },
    {
        "email": "sales@sora.com",
        "password": "sales123",
        "username": "sales",
        "first_name": "John",
        "last_name": "Sales",
        "role": "sales",
        "login_role": "sales_rep",
    },
    {
        "email": "accounts@sora.com",
        "password": "acc123",
        "username": "accounts",
        "first_name": "Jane",
        "last_name": "Accountant",
        "role": "accountant",
        "login_role": "accounts_admin",
    },
    {
        "email": "hr@sora.com",
        "password": "hr123",
        "username": "hr",
        "first_name": "Mary",
        "last_name": "HR",
        "role": "hr_manager",
        "login_role": "hr_admin",
    },
    {
        "email": "manager@sora.com",
        "password": "manager123",
        "username": "manager",
        "first_name": "Peter",
        "last_name": "Manager",
        "role": "manager",
        "login_role": "sales_admin",
    },
    {
        "email": "brian.otieno@sora.com",
        "password": "employee123",
        "username": "brian",
        "first_name": "Brian",
        "last_name": "Otieno",
        "role": "employee",
        "login_role": "employee",
    },
]


class Command(BaseCommand):
    help = "Seed company settings, roles, permissions and demo user accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo",
            action="store_true",
            help="Also create sample clients, products, quotes, invoices and employees.",
        )

    def handle(self, *args, **options):
        self.stdout.write("Seeding company settings...")
        for key, value in COMPANY_SETTINGS.items():
            CompanySetting.objects.update_or_create(
                tenant=None, setting_key=key, defaults={"setting_value": value}
            )

        self.stdout.write("Seeding permissions...")
        permissions = {}
        for module in MODULES:
            for action in ACTIONS:
                permission, _ = Permission.objects.get_or_create(
                    module=module,
                    action=action,
                    defaults={
                        "name": f"{action.title()} {module}",
                        "description": f"Allows {action} on {module}",
                    },
                )
                permissions[(module, action)] = permission

        self.stdout.write("Seeding roles...")
        now = timezone.now()
        roles = {}
        for name, (display_name, description) in ROLE_DEFINITIONS.items():
            role, _ = Role.objects.get_or_create(
                name=name,
                defaults={
                    "display_name": display_name,
                    "description": description,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            roles[name] = role

        for role_name, module_actions in ROLE_PERMISSIONS.items():
            role = roles[role_name]
            for module, actions in module_actions.items():
                for action in actions:
                    RolePermission.objects.get_or_create(
                        role_id=role.id,
                        permission_id=permissions[(module, action)].id,
                    )

        self.stdout.write("Seeding user accounts...")
        for spec in USERS:
            user, created = User.objects.get_or_create(
                email=spec["email"],
                defaults={
                    "username": spec["username"],
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "role": spec["role"],
                    "status": "active",
                },
            )
            if created:
                user.set_password(spec["password"])
                user.save(update_fields=["password"])
                self.stdout.write(f"  created {spec['email']}")
            else:
                self.stdout.write(f"  exists  {spec['email']}")
            UserRole.objects.get_or_create(
                user_id=user.id, role_id=roles[spec["login_role"]].id
            )

        if options["demo"]:
            tenant = default_tenant()
            if tenant is None:
                raise CommandError("No workspace exists. Apply migrations before seeding.")
            with tenant_context(tenant):
                self._seed_demo()

        self.stdout.write(self.style.SUCCESS("Seed complete."))
        self.stdout.write(
            "Sign in with admin@sora.com / admin123 (admin), "
            "sales@sora.com / sales123, accounts@sora.com / acc123."
        )

    def _seed_demo(self):
        self.stdout.write("Seeding demo business data...")
        now = timezone.now()
        today = timezone.localdate()

        clients = [
            ("CLI-0001", "Acme Holdings Ltd", "Grace Wanjiku", "grace@acme.co.ke", "+254 711 000 001", "Nairobi"),
            ("CLI-0002", "Nova Traders", "David Otieno", "david@novatraders.co.ke", "+254 722 000 002", "Mombasa"),
            ("CLI-0003", "Lakeview Hotels", "Aisha Mohamed", "aisha@lakeview.co.ke", "+254 733 000 003", "Kisumu"),
        ]
        for code, company, contact, email, phone, city in clients:
            Client.objects.get_or_create(
                client_code=code,
                defaults={
                    "company_name": company,
                    "contact_person": contact,
                    "email": email,
                    "phone": phone,
                    "city": city,
                    "country": "Kenya",
                    "status": "active",
                },
            )

        bank, _ = BankAccount.objects.get_or_create(
            account_number="0123456789",
            defaults={
                "bank_name": "Equity Bank",
                "branch": "Westlands",
                "account_name": "SORA ERP Ltd",
                "swift_code": "EQBLKENA",
                "is_default": 1,
                "status": "active",
            },
        )

        admin = User.objects.filter(email="admin@sora.com").first()

        categories = {}
        for code, name, description in [
            ("CAT-0001", "Software Services", "Implementation, support and consultancy"),
            ("CAT-0002", "Hardware", "Scanners, printers and devices"),
        ]:
            category, _ = ProductCategory.objects.get_or_create(
                category_code=code,
                defaults={
                    "category_name": name,
                    "description": description,
                    "status": "active",
                    "created_by": admin.id if admin else 1,
                    "created_at": now,
                },
            )
            categories[name] = category

        for code, name, address, manager in [
            ("WH-0001", "Main Warehouse", "Industrial Area, Nairobi", "Peter Njoroge"),
            ("WH-0002", "Nairobi Branch", "Westlands, Nairobi", "Mary Achieng"),
        ]:
            Warehouse.objects.get_or_create(
                warehouse_code=code,
                defaults={
                    "warehouse_name": name,
                    "address": address,
                    "manager": manager,
                    "is_default": 1 if code == "WH-0001" else 0,
                    "status": "active",
                    "created_at": now,
                },
            )

        for code, company, contact, email, phone, city in [
            ("VEN-0001", "Barcode World Ltd", "James Kariuki", "sales@barcodeworld.co.ke", "+254 720 111 222", "Nairobi"),
            ("VEN-0002", "Office Supplies Kenya", "Lucy Mwende", "orders@officesupplies.co.ke", "+254 721 333 444", "Nairobi"),
        ]:
            Vendor.objects.get_or_create(
                vendor_code=code,
                defaults={
                    "company_name": company,
                    "contact_person": contact,
                    "email": email,
                    "phone": phone,
                    "city": city,
                    "country": "Kenya",
                    "status": "active",
                    "created_by": admin.id if admin else None,
                    "created_at": now,
                },
            )

        products = [
            ("PRD-0001", "ERP Implementation", "Implementation and configuration", "service", "75000.00", "0.00", "0"),
            ("PRD-0002", "Annual Support Plan", "12-month priority support", "service", "48000.00", "0.00", "0"),
            ("PRD-0003", "Barcode Scanner", "Wireless 2D barcode scanner", "product", "12500.00", "8500.00", "24"),
        ]
        for code, name, description, ptype, price, cost, stock in products:
            category = categories["Hardware" if ptype == "product" else "Software Services"]
            Product.objects.get_or_create(
                product_code=code,
                defaults={
                    "name": name,
                    "product_name": name,
                    "description": description,
                    "category_id": category.id,
                    "type": ptype,
                    "unit": "pcs",
                    "unit_price": Decimal(price),
                    "cost_price": Decimal(cost),
                    "current_stock": Decimal(stock),
                    "stock": int(stock),
                    "reorder_level": Decimal("5") if ptype == "product" else Decimal("0"),
                    "status": "active",
                },
            )
            Product.objects.filter(product_code=code, category_id__isnull=True).update(
                category_id=category.id
            )

        admin = User.objects.filter(email="admin@sora.com").first()
        self._seed_accounts(now, admin)
        client = Client.objects.filter(client_code="CLI-0001").first()
        if admin and client:
            quote, created = Quote.objects.get_or_create(
                quote_number="QUO-DEMO-0001",
                defaults={
                    "client_id": client.id,
                    "issue_date": today - timedelta(days=5),
                    "valid_until": today + timedelta(days=25),
                    "status": "sent",
                    "subtotal": Decimal("150000.00"),
                    "tax_amount": Decimal("24000.00"),
                    "total_amount": Decimal("174000.00"),
                    "created_by": admin.id,
                    "bank_account_id": bank.id,
                },
            )
            if created:
                Invoice.objects.get_or_create(
                    invoice_number="INV-DEMO-0001",
                    defaults={
                        "client_id": client.id,
                        "quote_id": quote.id,
                        "invoice_date": today - timedelta(days=2),
                        "due_date": today + timedelta(days=28),
                        "status": "sent",
                        "subtotal": Decimal("150000.00"),
                        "tax_amount": Decimal("24000.00"),
                        "total_amount": Decimal("174000.00"),
                        "paid_amount": Decimal("0.00"),
                        "created_by": admin.id,
                        "bank_account_id": bank.id,
                    },
                )

        for spec in USERS:
            user = User.objects.filter(email=spec["email"]).first()
            if not user or Employee.objects.filter(email=spec["email"]).exists():
                continue
            employee = Employee.objects.create(
                employee_code=f"EMP-{user.id:04d}",
                first_name=spec["first_name"],
                last_name=spec["last_name"],
                email=spec["email"],
                hire_date=today - timedelta(days=400),
                employment_status="active",
                employee_type="full_time",
                job_title=spec["login_role"].replace("_", " ").title(),
                country="Kenya",
                status="active",
                created_by=admin.id if admin else user.id,
                created_at=now,
                updated_at=now,
            )
            user.employee_id = employee.id
            user.save(update_fields=["employee_id"])
            for offset in range(3):
                Attendance.objects.get_or_create(
                    employee_id=employee.id,
                    date=today - timedelta(days=offset + 1),
                    defaults={
                        "clock_in": "08:55:00",
                        "clock_out": "17:30:00",
                        "total_hours": Decimal("8.25"),
                        "status": "present",
                        "created_at": now,
                    },
                )

    def _seed_accounts(self, now, admin):
        today = timezone.localdate()
        accounts = [
            ("1000", "Cash and Bank", "asset", "debit"),
            ("1100", "Accounts Receivable", "asset", "debit"),
            ("1200", "Inventory", "asset", "debit"),
            ("1500", "Fixed Assets", "asset", "debit"),
            ("2000", "Accounts Payable", "liability", "credit"),
            ("2100", "VAT Payable", "liability", "credit"),
            ("3000", "Owner's Equity", "equity", "credit"),
            ("3100", "Retained Earnings", "equity", "credit"),
            ("4000", "Sales Revenue", "revenue", "credit"),
            ("4100", "Service Revenue", "revenue", "credit"),
            ("5000", "Cost of Sales", "expense", "debit"),
            ("6000", "Operating Expenses", "expense", "debit"),
            ("6100", "Payroll Expenses", "expense", "debit"),
            ("6200", "Rent Expense", "expense", "debit"),
        ]
        by_code = {}
        for code, name, account_type, normal in accounts:
            account, _ = ChartOfAccount.objects.get_or_create(
                account_code=code,
                defaults={
                    "account_name": name,
                    "account_type": account_type,
                    "normal_balance": normal,
                    "status": "active",
                    "created_by": admin.id if admin else 1,
                    "created_at": now,
                },
            )
            by_code[code] = account

        entries = [
            (
                "JE-DEMO-00001",
                today - timedelta(days=45),
                "Opening balances",
                [
                    ("1000", "debit", "500000.00", "Opening cash position"),
                    ("1100", "debit", "250000.00", "Opening receivables"),
                    ("1500", "debit", "350000.00", "Opening fixed assets"),
                    ("3000", "credit", "900000.00", "Owner capital"),
                    ("3100", "credit", "200000.00", "Retained earnings"),
                ],
            ),
            (
                "JE-DEMO-00002",
                today - timedelta(days=10),
                "Demo invoice INV-DEMO-0001",
                [
                    ("1100", "debit", "174000.00", "Invoice to Acme Holdings"),
                    ("4000", "credit", "150000.00", "Implementation services"),
                    ("2100", "credit", "24000.00", "VAT on sales"),
                ],
            ),
            (
                "JE-DEMO-00003",
                today - timedelta(days=5),
                "Part payment from Acme Holdings",
                [
                    ("1000", "debit", "100000.00", "Payment received"),
                    ("1100", "credit", "100000.00", "Receivable settled"),
                ],
            ),
            (
                "JE-DEMO-00004",
                today - timedelta(days=3),
                "Monthly operating expenses",
                [
                    ("6000", "debit", "62000.00", "Office running costs"),
                    ("1000", "credit", "62000.00", "Paid from bank"),
                ],
            ),
        ]
        for number, entry_date, description, lines in entries:
            entry, created = JournalEntry.objects.get_or_create(
                entry_number=number,
                defaults={
                    "entry_date": entry_date,
                    "description": description,
                    "status": "posted",
                    "created_by": admin.id if admin else 1,
                    "created_at": now,
                    "posted_at": now,
                    "posted_by": admin.id if admin else 1,
                },
            )
            if not created:
                continue
            JournalEntryLine.objects.bulk_create(
                [
                    JournalEntryLine(
                        journal_entry_id=entry.id,
                        account_id=by_code[code].id,
                        entry_type=entry_type,
                        amount=Decimal(amount),
                        description=line_description,
                        created_at=now,
                    )
                    for code, entry_type, amount, line_description in lines
                ]
            )
            for code, entry_type, amount, _line_description in lines:
                account = by_code[code]
                delta = Decimal(amount)
                if entry_type == "credit":
                    delta = -delta
                account.current_balance = (account.current_balance or Decimal("0.00")) + delta
                account.save(update_fields=["current_balance"])
        self.stdout.write(f"  chart of accounts: {len(by_code)} accounts")
