# Architecture

## Request flow

```
Browser
  └─ config/urls.py
       ├─ apps.core.urls          /, /login, /logout, /dashboard, /api/*
       ├─ apps.<module>.urls      /sales/, /crm/, /invoices/, /accounts/,
       │                          /inventory/, /purchasing/, /hr/
       ├─ apps.settings_center    /settings/
       └─ apps.admin_console      /admin/
            └─ views.py → forms.py → apps.core.models → PostgreSQL
                 └─ templates/<module>/*.html → templates/base.html
```

Every authenticated page is rendered through `templates/base.html`. The
`apps.core.context_processors.sora` processor supplies:

- `access` — the resolved `Access` object (RBAC)
- `company`, `company_name`, `company_logo`, `currency_symbol`
- `menu_items` / `search_items` — sidebar and global search
- `user_name`, `user_role`, `theme`, `today`

## Application layout

| App | Responsibility |
| --- | --- |
| `apps.core` | 155 models (all legacy tables), custom `User`, RBAC, authentication, dashboard, presence/attendance API, shared services and UI helpers |
| `apps.sales` | Quotes, quote items, pipeline, reports |
| `apps.crm` | Clients, vendors, contacts, activities |
| `apps.invoices` | Invoice register, invoice detail, payments, printing |
| `apps.accounts` | Chart of accounts, journals, ledger, trial balance, bank accounts, expenses |
| `apps.inventory` | Products, categories, stock movements, warehouses |
| `apps.purchasing` | Purchase orders, goods receipts, supplier bills |
| `apps.hr` | Employees, departments, positions, leave, attendance, payroll, performance, recruitment |
| `apps.settings_center` | Company profile, users, roles/permissions, multi-role assignment, email/SMTP, email templates, integrations, system info |
| `apps.admin_console` | Administrator dashboard, system-wide counts, audit trail, redirects to management screens |

All business models live in `apps.core.models` (split by domain: `sales.py`,
`crm.py`, `accounting.py`, `inventory.py`, `purchasing.py`, `hr.py`,
`documents.py`, `projects.py`, `licensing.py`, `system.py`, `user.py`).
Module apps contain only forms, views, URLs and tests. This keeps the legacy
schema in one place and avoids circular imports.

## Authentication

- `AUTH_USER_MODEL = "core.User"` maps to the legacy `users` table.
- Login uses **email** as the username field (`USERNAME_FIELD = "email"`).
- `login_view` redirects:
  - administrators → `/admin/dashboard`
  - users with sales access → `/sales/dashboard`
  - everyone else → `/dashboard`
- `next` parameters are validated with `url_has_allowed_host_and_scheme`.
- Sessions use the database backend; presence heartbeats write to
  `user_activity_log` and attendance clock in/out writes to `attendance`.

## RBAC

`apps.core.permissions.get_access(user)` resolves rights once per request and
caches them on the user object (`_sora_access`).

Sources, in precedence order:

1. **Admin roles** — `admin`, `super_admin`, `system_admin` get every module as `admin`.
2. **`user_roles`** (multi-role assignments from Settings) mapped through `ROLE_MODULE_MAP`,
   e.g. `hr_admin → hr:admin`, `sales_rep → sales:rep`, `inventory_clerk → inventory:clerk`.
3. **Primary role fallbacks** (`PRIMARY_ROLE_FALLBACKS`) for the legacy `users.role` column,
   e.g. `manager → sales:admin, crm:admin, accounts:viewer …`.
4. **`user_module_roles`** explicit assignment from the Multi-Role Manager — wins over derived roles.
5. **Role permissions** (`role_permissions` → `permissions`) and per-user grants
   (`user_permissions`) add fine-grained actions on top.

Module roles and implied actions:

| Role | Implied actions |
| --- | --- |
| `admin` | view, create, edit, delete, approve, export, manage |
| `rep` / `clerk` | view, create, edit, export |
| `viewer` | view, export |
| `employee` | view |

Decorators:

```python
@login_required
@module_access_required("hr", "sales")     # any one module is enough
@permission_required("accounts", "approve") # specific action
@admin_required                             # administrators only
```

`Access` also exposes template-friendly flags: `can_view_hr`, `is_sales_admin`,
`is_accounts_viewer`, `is_employee`, etc. Use these in templates instead of
embedding role strings.

## Services and helpers

- `apps.core.services` — company settings cache-free lookup, currency symbol,
  decimal/date parsing, document number generation (`next_sequence`, `next_code`),
  date ranges.
- `apps.core.ui` — pagination (`paginate`) and CSV responses (`csv_response`).
- `apps.core.context_processors` — the sidebar/search map. Add new module entries
  here so every page picks them up.

## Templates

- `templates/base.html` — header, sidebar, presence widget, theme toggle, toasts,
  global search.
- `templates/core/dashboard.html` — landing page after non-admin login.
- `templates/<module>/*.html` — list/detail/form pages. Each extends `base.html`
  and adds `css/module.css` via `extra_css`.
- `templates/settings/_nav.html` — Settings tab strip.
- `templates/partials/` — shared fragments (pagination, empty states).
- CSS is in `static/css`: `layout.css` (shell), `dashboard.css`, `module.css`
  (tables, forms, kanban, print) and `login.css`.

Conventions:

- Page header markup uses `.page-head` with actions on the right.
- Tables use `.data-table` inside `.table-responsive`.
- Forms use `.form-grid` / `.form-group`; filter bars use `.filter-bar`.
- Money goes through `|floatformat:2|intcomma` with `currency_symbol`.
- Destructive actions are `POST` forms with `{% csrf_token %}`.

## Admin console

`apps.admin_console.views.dashboard` is restricted to `access.is_admin` and shows:

- counts for users, clients, employees, quotes, invoices, products and journals
- revenue collected, month revenue, outstanding receivables, low-stock products
- recent audit entries (`audit_log`) and newest users
- quick links into Settings and each module

Non-admin users are redirected to `/dashboard`; direct management URLs
(`/admin/users`, `/admin/roles`, `/admin/settings`, `/admin/system`) redirect to
their canonical Settings pages.

## Adding a module or screen

1. Add the model(s) in `apps.core.models` with the legacy `db_table`.
2. Add a `forms.py` with a `ModelForm`/`Form` and explicit field labels.
3. Add views with `@login_required` plus the narrowest RBAC decorator.
4. Register URLs in `apps/<module>/urls.py`; mount the app in `config/urls.py`.
5. Add templates under `templates/<module>/`, extending `base.html`.
6. Add sidebar/search entries in `build_menu` (`apps/core/context_processors.py`).
7. Add seed data (idempotent `update_or_create`/`get_or_create`).
8. Add the route to a smoke script and run `manage.py check`.

## Testing strategy

There is no formal unit-test suite yet. The port is verified with:

- `manage.py check` — configuration and model checks.
- `scripts/smoke_pages.py` — signs in as admin and GETs every parameterless route
  (183 URLs at the time of writing) with expected HTTP < 400.
- `scripts/smoke_settings.py` — 53 assertions across Settings forms, secret
  preservation, CRUD and admin access control.
- `scripts/smoke_hr_actions.py` — leave, overtime, performance and recruitment workflows.
- `scripts/smoke_inventory.py`, `scripts/smoke_purchasing.py` — stock and procurement flows.

For future work, add `django.test.TestCase` coverage per module and run it against
PostgreSQL (`manage.py test`) so database-specific behaviour is exercised.

## Design decisions and trade-offs

- **Single core app for models** — mirrors the legacy database, simplifies the port
  and cross-module reporting, but means a large `apps/core`.
- **No foreign keys** — intentional for the first port (data import safety and
  compatibility). See `docs/DATABASE.md` for the phased integrity plan.
- **Auto-generated models** — `documents.py`, `projects.py` and `licensing.py` keep
  legacy tables that no screen uses yet, so the schema stays complete.
- **Settings secrets** — password/secret inputs are blank on render; blank submissions
  preserve the stored value (SMTP, eTIMS, OpenAI, Zoho, SSO).
- **Admin console** — kept separate from Settings to provide a system-wide read-only
  overview without duplicating management forms.
