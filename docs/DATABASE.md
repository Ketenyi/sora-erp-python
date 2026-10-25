# Database guide and advice

## Current state

| Item | Value |
| --- | --- |
| Engine | PostgreSQL 18.6 (64-bit, Windows) |
| Cluster | dedicated data directory, port **5433** |
| Database | `sora_erp` |
| Size | ~20 MB with demo data |
| Tables | 163 (155 legacy SORA tables + 8 Django framework tables) |
| Indexes | 585 (copied from the legacy schema metadata) |
| Foreign keys | **5**, all inside Django framework tables (`auth_*`, `django_admin_log`) |
| DB views | none created yet |

The schema was generated one-to-one from the legacy PHP/MySQL database:
original table names, plain integer IDs, no database-level relationships. All
155 models live in `apps.core.models`.

PostgreSQL 18 is backward-compatible with the 16 version originally selected;
no downgrade is needed.

## Table groups

| Domain | Examples |
| --- | --- |
| Identity & access | `users`, `roles`, `permissions`, `role_permissions`, `user_roles`, `user_module_roles`, `user_permissions` |
| CRM | `clients`, `vendors`, `contacts`, `client_activities` |
| Sales | `quotes`, `quote_items`, `quote_followups`, `rfqs` |
| Invoicing | `invoices`, `invoice_items`, `invoice_payments`, `credit_notes` |
| Accounting | `chart_of_accounts`, `journal_entries`, `journal_entry_lines`, `fiscal_years`, `bank_accounts`, `expenses` |
| Inventory | `products`, `product_categories`, `stock_movements`, `warehouses` |
| Purchasing | `purchase_orders`, `purchase_order_items`, `goods_receipts`, `supplier_bills` |
| HR | `employees`, `departments`, `positions`, `attendance`, `leave_requests`, `leave_balances`, `payroll_runs`, `payslips` |
| System | `company_settings`, `email_templates`, `sso_settings`, `audit_log`, `user_activity_log`, `api_keys`, `webhooks` |

## Integrity strategy

The missing foreign keys are the biggest structural weakness. They were omitted
to make the initial port and data import safe, not because they are undesirable.
Orphaned rows currently go unnoticed and reporting joins can silently drop data.

### 1. Audit for orphans

Run these before adding constraints:

```sql
-- Quotes pointing at missing clients
SELECT count(*) FROM quotes q
LEFT JOIN clients c ON c.id = q.client_id
WHERE q.client_id IS NOT NULL AND c.id IS NULL;

-- Quote items without a quote
SELECT count(*) FROM quote_items i
LEFT JOIN quotes q ON q.id = i.quote_id
WHERE q.id IS NULL;

-- Invoices pointing at missing clients
SELECT count(*) FROM invoices i
LEFT JOIN clients c ON c.id = i.client_id
WHERE c.id IS NULL;

-- Invoice items without an invoice
SELECT count(*) FROM invoice_items i
LEFT JOIN invoices v ON v.id = i.invoice_id
WHERE v.id IS NULL;

-- Employees pointing at missing departments
SELECT count(*) FROM employees e
LEFT JOIN departments d ON d.id = e.department_id
WHERE e.department_id IS NOT NULL AND d.id IS NULL;

-- Journal lines without a journal entry
SELECT count(*) FROM journal_entry_lines l
LEFT JOIN journal_entries j ON j.id = l.journal_entry_id
WHERE j.id IS NULL;
```

Decide per table: delete orphans, set the column to `NULL`, or create the missing
parent row. Do this with a tested script and a backup.

### 2. Add constraints without downtime

Add each FK as `NOT VALID`, clean data, then validate. Example:

```sql
ALTER TABLE quotes
  ADD CONSTRAINT quotes_client_fk
  FOREIGN KEY (client_id) REFERENCES clients(id)
  ON DELETE RESTRICT
  NOT VALID;

-- after cleaning:
ALTER TABLE quotes VALIDATE CONSTRAINT quotes_client_fk;
```

For optional relationships use `ON DELETE SET NULL`; for document lines use
`ON DELETE CASCADE`; for master data (clients, employees) prefer `RESTRICT` and
soft deletes.

### 3. Reflect constraints in Django

Once the data is clean, replace integer fields with real foreign keys:

```python
client = models.ForeignKey(
    "core.Client",
    db_column="client_id",
    on_delete=models.RESTRICT,
    related_name="quotes",
)
```

Keep `db_column` so the physical column name stays `client_id`, and keep
`db_constraint=True` so Django does not drop the database constraint. Update
views/forms to use `quote.client` / `quote.client_id` deliberately. This is a
larger refactor; schedule it module by module, starting with documents and lines.

## Document numbering (fix before production)

`apps.core.services.next_sequence()` runs:

```sql
SELECT quote_number FROM quotes WHERE quote_number LIKE 'QUO-YYYYMMDD-%'
ORDER BY quote_number DESC LIMIT 1
```

Two concurrent requests can read the same maximum and generate the same number.
Today the unique index turns this into a `500` error rather than duplicate data.
Recommended replacement — a per-prefix/day counter table:

```sql
CREATE TABLE document_sequences (
    prefix      text    NOT NULL,
    period      date    NOT NULL,
    last_value  integer NOT NULL DEFAULT 0,
    PRIMARY KEY (prefix, period)
);
```

```sql
INSERT INTO document_sequences (prefix, period, last_value)
VALUES ('QUO', CURRENT_DATE, 1)
ON CONFLICT (prefix, period)
DO UPDATE SET last_value = document_sequences.last_value + 1
RETURNING last_value;
```

Run that inside the same transaction as the insert; the row lock serialises the
counter. `next_code()` (e.g. `CLI-0007`) can use a PostgreSQL sequence, or a row
in the same table keyed by `('CLI', '0001-01-01')`.

An alternative for short transactions is
`SELECT pg_advisory_xact_lock(hashtext('quote-number'))` before the `MAX()` query.

## Money and currency

- Amounts are `numeric(12,2)` — maximum about 9.99 billion. Increase to
  `numeric(18,2)` (or `18,4` for unit prices/rates) before high-value or
  multi-currency use.
- `DecimalField` in Django maps to `numeric`, so no floating-point rounding.
  Always keep `Decimal` in Python; never cast money to `float`.
- The system is single-currency today (`currency_symbol` / `currency_code` in
  `company_settings`). For multi-currency, add per-document `currency_code`,
  `exchange_rate`, and store both transaction and base amounts; report in base
  currency only.
- `invoices.paid_amount` is denormalised. Reconcile it against
  `invoice_payments` / payment allocations and consider a periodic consistency
  check or a database view.

## Statuses, dates and time zones

- Status columns are `varchar` with Django `choices`, but the database does not
  enforce them. Add `CHECK` constraints for critical workflows:

  ```sql
  ALTER TABLE invoices
    ADD CONSTRAINT invoices_status_check
    CHECK (status IN ('draft','sent','partial','paid','overdue','cancelled'));
  ```

- Business dates are `date`; event timestamps are `timestamptz` (Django uses
  `USE_TZ=True`). Keep the application time zone `Africa/Nairobi` and store UTC
  in the database.
- There is no explicit `paid_at` column. "Revenue this month" currently uses
  `invoices.updated_at`, which changes on any edit. Add `paid_at` (or derive from
  payments) before relying on monthly revenue reports.
- The legacy MySQL database may contain zero dates (`0000-00-00`). Convert them
  to `NULL` during import; PostgreSQL rejects them.

## Indexing advice

585 indexes already exist, carried over from MySQL. More is not always better:
every write maintains them.

1. Review unused indexes:

   ```sql
   SELECT relname, indexrelname, idx_scan
   FROM pg_stat_user_indexes
   WHERE idx_scan = 0
   ORDER BY pg_relation_size(indexrelid) DESC;
   ```

2. Add composite indexes for the actual query patterns, for example:
   - `quotes (status, client_id)`, `quotes (assigned_to, status)`
   - `invoices (client_id, status)`, existent `invoices (status, due_date)`
   - `quote_items (quote_id)`, `invoice_items (invoice_id)`
   - `attendance (employee_id, date)` — should also be unique
   - `leave_requests (employee_id, status, start_date)`
   - `journal_entry_lines (journal_entry_id)`, `(account_id, created_at)`
   - `stock_movements (product_id, created_at)`
3. Use `EXPLAIN (ANALYZE, BUFFERS)` on slow list/report pages and install
   `pg_stat_statements` before tuning.
4. Keep list pages paginated (they already use `Paginator` with 15 rows).

## Soft deletes

Only some tables have `deleted_at` (for example `clients`). Pick one policy:

- soft delete master data (`deleted_at`) and filter everywhere, or
- hard delete master data and rely on FKs/audit.

Document-numbered records (quotes, invoices, journals) should never be
hard-deleted; use `cancelled`/`void` statuses and keep the audit trail.

## Auditing

`audit_log` records `user_id`, `action`, `table_name`, `record_id`, `old_data`,
`new_data`, IP and user agent. The Admin Console and Settings > System page read
it. Today writes are application-driven, so any code path that bypasses them is
invisible.

Options:

- centralize writes in a service/signals for all create/update/delete operations;
- add PostgreSQL triggers on sensitive tables (`users`, `roles`, `invoices`,
  `journal_entries`, `payroll_runs`);
- retain rows (e.g. 12–24 months) and partition `audit_log` by month when it grows.

## Multi-tenancy

The legacy schema hints at one database per company (`companies.database_name`).
Avoid that model in Django: it requires dynamic connection routing and makes
migrations painful.

For this size, prefer **one database with a `tenant_id` / `company_id` column**
on business tables, plus a default manager that filters by the current tenant.
For strict isolation (separate schemas), `django-tenants` is the established
option. Choose one strategy before onboarding a second company; retrofitting is
expensive.

## Migrating the legacy MySQL data

1. Take a consistent MySQL dump and record row counts per table.
2. Use `pgloader` for a first pass:

   ```lisp
   LOAD DATABASE
        FROM mysql://user:pass@127.0.0.1/sora
        INTO postgresql://sora:pass@127.0.0.1:5433/sora_erp
   WITH include drop, create tables, create indexes, reset sequences,
        workers = 4, concurrency = 2
   SET maintenance_work_mem to '256MB'
   CAST type datetime to timestamptz drop default drop not null using zero-dates-to-null,
        type date to date using zero-dates-to-null;
   ```

   Because Django already created the schema, either import into a staging
   database and copy with `INSERT … SELECT`, or use pgloader with `data only`.
3. Validate: row counts per table, sums of invoice/quote totals, distinct status
   values, min/max dates, and spot-check documents and payslips.
4. Recompute sequences (`setval`) for legacy auto-increment columns.
5. Import users' password hashes. PHP `password_hash()` uses bcrypt; Django stores
   a different format. Either force password resets, or add a custom hasher that
   verifies `$2y$` hashes and re-hashes on next login.
6. Re-point uploaded files (`company_logo`, employee documents, invoice PDFs) to
   the new `uploads/` layout and normalise `/assets/uploads/` → `/uploads/`.

## Backups and recovery

Development:

```bat
"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -h 127.0.0.1 -p 5433 -U sora -Fc -d sora_erp -f sora_erp.dump
```

Production:

- nightly `pg_dump -Fc` plus `uploads/` and `.env`, copied off-machine;
- or continuous WAL archiving for point-in-time recovery;
- test a restore quarterly (`pg_restore --clean --if-exists` into a scratch DB);
- retention: 7 daily, 4 weekly, 12 monthly is a reasonable start.

## Security

- Use a dedicated application role (`sora`) without `SUPERUSER`/`CREATEDB`.
- Keep a separate migration/owner role; the runtime role needs only DML rights.
- Rotate the password in `.env` and the database together.
- Enable TLS (`sslmode=require`) when the database is not on localhost.
- Put PgBouncer in front of PostgreSQL for production concurrency.
- Do not store SMTP/API secrets in plain text long-term; encrypt them with a key
  from the environment or a secrets manager, and restrict access to Settings to
  administrators (already enforced).

## Priorities

If this moves beyond a local demo, do these in order:

1. Replace `MAX()`-based numbering with the `document_sequences` table.
2. Clean orphans and add foreign keys for document headers/lines and employees.
3. Add `paid_at` / payment allocations for correct revenue reporting.
4. Add the composite indexes listed above and review unused ones.
5. Set up automated backups and a restore test.
6. Migrate real data with pgloader, validate, and force password resets.
7. Decide the multi-tenancy model before the second company is onboarded.
