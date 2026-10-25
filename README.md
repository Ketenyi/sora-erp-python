# SORA ERP (Django + PostgreSQL)

Python/Django port of the legacy PHP SORA ERP in `C:\xampp1\htdocs\SORA`. The port keeps the legacy data model (155 models, original table names) and rebuilds the UI, access control and workflows in Django.

## Stack

| Layer | Choice |
| --- | --- |
| Framework | Django 6.1 |
| Runtime | Python 3.14 |
| Database | PostgreSQL 18 (dedicated cluster on port **5433**) |
| DB driver | psycopg 3 |
| Frontend | Django templates, vanilla JS, Font Awesome |

## Run the app

Double-click `start-sora.cmd` in this folder. It starts PostgreSQL (if needed) and the Django server, then opens the login page.

Manual start:

```bat
cd sora-erp-python
..\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```

Open <http://127.0.0.1:8000/login>.

### Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Administrator | `admin@sora.com` | `admin123` |
| Sales representative | `sales@sora.com` | `sales123` |
| Accounts administrator | `accounts@sora.com` | `acc123` |
| HR administrator | `hr@sora.com` | `hr123` |
| Manager | `manager@sora.com` | `manager123` |
| Employee (self-service) | `brian.otieno@sora.com` | `employee123` |

Change these before exposing the app outside your machine.

## Modules

| Module | URL prefix | Highlights |
| --- | --- | --- |
| Core | `/` | Login, dashboard, presence/attendance API, RBAC |
| Sales | `/sales/` | Quotes, pipeline, reports |
| CRM | `/crm/` | Clients, vendors |
| Invoices | `/invoices/` | Invoice list, view, payments |
| Accounts | `/accounts/` | Chart of accounts, journals, ledger, trial balance |
| Inventory | `/inventory/` | Products, categories, stock movements |
| Purchasing | `/purchasing/` | Purchase orders, goods receipts, supplier bills |
| HR | `/hr/` | Employees, leave, attendance, payroll, performance, recruitment |
| Settings | `/settings/` | Company, users, roles, multi-role manager, email, integrations, system info |
| Admin console | `/admin/` | System-wide overview, audit trail, module counts |

## Common commands

Run from `sora-erp-python` with the virtualenv Python:

```bat
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py seed_data --demo
..\.venv\Scripts\python.exe manage.py seed_settings
..\.venv\Scripts\python.exe manage.py seed_hr
..\.venv\Scripts\python.exe manage.py check
..\.venv\Scripts\python.exe manage.py createsuperuser
..\.venv\Scripts\python.exe manage.py purge_security_logs --dry-run
```

`purge_security_logs` removes login attempts and request audits older than the
`security_audit_retention_days` platform setting. Pass `--days N` to override the
retention window for a one-off run. Schedule it daily from cron or Task Scheduler.

Smoke tests (database must be running):

```bat
set PYTHONPATH=%CD%
..\.venv\Scripts\python.exe scripts\smoke_pages.py
..\.venv\Scripts\python.exe scripts\smoke_settings.py
..\.venv\Scripts\python.exe scripts\smoke_hr_actions.py
```

## Project layout

```
sora-erp-python/
├─ config/                 # settings, root URLs, WSGI/ASGI
├─ apps/
│  ├─ core/                # models (155), auth, RBAC, dashboard, services
│  ├─ sales/ crm/ invoices/
│  ├─ accounts/ inventory/ purchasing/
│  ├─ hr/ settings_center/ admin_console/
├─ templates/              # base layout + per-module templates
├─ static/                 # CSS, JS, logos
├─ uploads/                # user uploads (served at /uploads/)
├─ scripts/                # smoke tests and codegen helpers
├─ docs/
├─ start-sora.cmd
└─ manage.py
```

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — environment, database, seeding, troubleshooting
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request flow, RBAC, module conventions
- [docs/DATABASE.md](docs/DATABASE.md) — schema strategy, integrity, indexing and migration advice

## Known limitations

- Legacy schema is one-to-one: integer IDs, **no database foreign keys**, no DB-level referential integrity.
- Document numbers (`QUO-…`, `INV-…`) are generated with a `MAX()` scan. Concurrent requests can collide; see `docs/DATABASE.md` for the PostgreSQL sequence fix.
- eTIMS, OpenAI, Zoho and SSO pages store and validate configuration but do not make live external API calls in this build.
- Email supports SMTP save + test; transactional sending depends on configured templates and SMTP.
- Development server only. For production use a WSGI server, `DEBUG=False`, a real secret, HTTPS, and collected static files.

## Production checklist

- Set `DJANGO_DEBUG=False`, a strong `DJANGO_SECRET_KEY`, `SORA_SECRET_ENCRYPTION_KEY`,
  and explicit `DJANGO_ALLOWED_HOSTS` in `.env`. Startup refuses to boot in
  production without the first two.
- Run `manage.py collectstatic` and serve static files through nginx or WhiteNoise.
- Run the app under Waitress/gunicorn with a reverse proxy. Set
  `TRUST_PROXY_HEADERS=1` only when the proxy overwrites `X-Forwarded-For` and
  `X-Forwarded-Proto`.
- Use a dedicated PostgreSQL role with least privilege; enable TLS and scheduled backups.
- Schedule `manage.py purge_security_logs` to enforce the audit retention policy.
- Require MFA for platform administrators under Cloud → Security, and review the
  request audit trail regularly.
- Change all demo passwords and remove demo data.
