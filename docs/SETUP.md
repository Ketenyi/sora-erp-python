# Setup and operations

This project runs on Django 6.1.2 / Python 3.14.7 with PostgreSQL 18 and psycopg 3.3.6.

## Current local environment

The machine already has everything provisioned:

| Item | Value |
| --- | --- |
| Project | `sora-erp-python` |
| Virtualenv | `..\.venv` (sibling of the project folder) |
| PostgreSQL binaries | `C:\Program Files\PostgreSQL\18\bin` |
| PostgreSQL data directory | `%LOCALAPPDATA%\SoraERP\pgdata` |
| PostgreSQL port | `5433` |
| Database | `sora_erp` |
| App DB role | `sora` — password stored in `.env` |
| Cluster superuser password | `%LOCALAPPDATA%\SoraERP\.superpw` |
| Server log | `%LOCALAPPDATA%\SoraERP\postgres.log` |
| App URL | <http://127.0.0.1:8000> |

A separate cluster on **5433** is used because the pre-existing PostgreSQL service on
**5432** has an unknown superuser password. The 5432 installation was left untouched.

## Start and stop

Start everything:

```bat
start-sora.cmd
```

Start only the database:

```bat
"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" ^
  -D "%LOCALAPPDATA%\SoraERP\pgdata" ^
  -l "%LOCALAPPDATA%\SoraERP\postgres.log" ^
  -o "-p 5433" start
```

Stop the database:

```bat
"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" ^
  -D "%LOCALAPPDATA%\SoraERP\pgdata" stop
```

Check readiness:

```bat
"C:\Program Files\PostgreSQL\18\bin\pg_isready.exe" -h 127.0.0.1 -p 5433
```

The cluster is not registered as a Windows service, so it does **not** start automatically
after a reboot. Either use `start-sora.cmd` or register a service with
`pg_ctl register` (run as Administrator) if you want it always on.

## Environment file

Settings load `.env` from the project root:

```env
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=...
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,[::1]
DATABASE_URL=postgresql://sora:<password>@127.0.0.1:5433/sora_erp
TIME_ZONE=Africa/Nairobi
```

Optional variables:

- `DB_CONN_MAX_AGE` (default 60), `DB_CONNECT_TIMEOUT` (default 5)
- `SESSION_COOKIE_AGE` (default 28800 seconds)
- `CSRF_TRUSTED_ORIGINS`
- `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
  `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL` — used as SMTP fallbacks. Values saved from
  Settings > Email take precedence for the test-email action.
- `LOG_LEVEL` (default `INFO`)

Never commit `.env` (it is already in `.gitignore`).

## First-time setup from scratch

Only needed if the project is moved to another machine.

1. Create/activate a virtualenv (it lives one level above the project):

   ```bat
   cd ..
   py -m venv .venv
   .venv\Scripts\python.exe -m pip install -r sora-erp-python\requirements.txt
   ```

2. Create a PostgreSQL cluster on port 5433:

   ```bat
   set "PGBIN=C:\Program Files\PostgreSQL\18\bin"
   "%PGBIN%\initdb.exe" -D "%LOCALAPPDATA%\SoraERP\pgdata" -U postgres --auth=scram-sha-256 --pwfile=%LOCALAPPDATA%\SoraERP\.superpw
   "%PGBIN%\pg_ctl.exe" -D "%LOCALAPPDATA%\SoraERP\pgdata" -l "%LOCALAPPDATA%\SoraERP\postgres.log" -o "-p 5433" start
   ```

   `.superpw` must exist and contain the superuser password before `initdb` runs.

3. Create the application role and database:

   ```bat
   "%PGBIN%\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -c "CREATE ROLE sora LOGIN PASSWORD '<password>';"
   "%PGBIN%\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -c "CREATE DATABASE sora_erp OWNER sora ENCODING 'UTF8';"
   ```

4. Write `.env` with the matching `DATABASE_URL`.

5. Apply schema and seed data:

   ```bat
   cd sora-erp-python
   ..\.venv\Scripts\python.exe manage.py migrate
   ..\.venv\Scripts\python.exe manage.py seed_data --demo
   ..\.venv\Scripts\python.exe manage.py seed_settings
   ..\.venv\Scripts\python.exe manage.py seed_hr
   ```

6. Start the server and sign in at <http://127.0.0.1:8000/login>.

## Seeding

| Command | Purpose | Idempotent |
| --- | --- | --- |
| `seed_data` | Company settings, permissions, roles, users; `--demo` adds clients, products, quotes, invoices, journals and demo employees | yes |
| `seed_settings` | Email templates, SSO provider rows, integration defaults; `--force` refreshes template content | yes |
| `seed_hr` | Departments, positions, leave types, attendance, payroll and recruitment demo data | yes |

## Smoke tests

Run with `PYTHONPATH` set to the project root:

```bat
set "PYTHONPATH=%CD%"
..\.venv\Scripts\python.exe scripts\smoke_pages.py     :: all parameterless GET routes
..\.venv\Scripts\python.exe scripts\smoke_settings.py :: settings forms + admin console
..\.venv\Scripts\python.exe scripts\smoke_hr_actions.py :: HR workflows
..\.venv\Scripts\python.exe scripts\smoke_inventory.py
..\.venv\Scripts\python.exe scripts\smoke_purchasing.py
```

`smoke_pages.py` signs in as `admin@sora.com` and expects the seeded demo data.

## Troubleshooting

**`connection refused` on port 5433**
Start the cluster with the `pg_ctl start` command above and check
`%LOCALAPPDATA%\SoraERP\postgres.log`.

**`password authentication failed for user "sora"`**
The password in `.env` no longer matches the role. Reset it:

```bat
"%PGBIN%\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -c "ALTER ROLE sora PASSWORD '<new>';"
```

Then update `DATABASE_URL` in `.env`.

**Template/CSS change not visible**
The development server caches compiled templates while running without the reloader.
Restart `manage.py runserver`; static files are served fresh by Django in DEBUG mode.

**Static or uploaded files missing**
`/static/` maps to `static/`; `/uploads/` maps to `uploads/` and is only served by Django
when `DEBUG=True`. In production collect static files and serve `/uploads/` from nginx.

**Scripts fail with `ModuleNotFoundError: config`**
Run them through the virtualenv Python from the project root and set
`PYTHONPATH` to the project root, as shown above.

**Port 8000 already in use**

```bat
netstat -ano | findstr :8000
taskkill /PID <pid> /F
```

**Reset the database (destructive)**
Stop the server, then:

```bat
"%PGBIN%\dropdb.exe" -h 127.0.0.1 -p 5433 -U postgres sora_erp
"%PGBIN%\createdb.exe" -h 127.0.0.1 -p 5433 -U postgres -O sora sora_erp
..\.venv\Scripts\python.exe manage.py migrate
..\.venv\Scripts\python.exe manage.py seed_data --demo
..\.venv\Scripts\python.exe manage.py seed_settings
```

## Backups

```bat
"%PGBIN%\pg_dump.exe" -h 127.0.0.1 -p 5433 -U sora -Fc -d sora_erp -f sora_erp_backup.dump
"%PGBIN%\pg_restore.exe" -h 127.0.0.1 -p 5433 -U sora -d sora_erp --clean --if-exists sora_erp_backup.dump
```

Schedule daily dumps and keep copies off-machine. Also back up `uploads/` and `.env`
(the secret key invalidates sessions and signed data if lost).
