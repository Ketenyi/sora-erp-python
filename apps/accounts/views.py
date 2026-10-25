"""Accounts module: chart of accounts, journals, ledger and statements."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import (
    BankAccount,
    ChartOfAccount,
    Invoice,
    JournalEntry,
    JournalEntryLine,
    SupplierBill,
    User,
)
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_code, parse_date
from apps.core.ui import csv_response, paginate
from apps.accounts.forms import (
    AccountForm,
    BankAccountForm,
    JournalEntryForm,
    JournalLineFormSet,
    ReportPeriodForm,
)

ZERO = Decimal("0.00")
ACCOUNT_TYPES = [
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("revenue", "Revenue"),
    ("expense", "Expense"),
]
OPEN_RECEIVABLE = ["sent", "partial", "overdue"]
OPEN_PAYABLE = ["received", "approved", "partially_paid"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _date_param(request, key, default):
    return parse_date(request.GET.get(key), default) or default


def _posted_entries(start=None, end=None):
    queryset = JournalEntry.objects.filter(status="posted")
    if start:
        queryset = queryset.filter(entry_date__gte=start)
    if end:
        queryset = queryset.filter(entry_date__lte=end)
    return queryset


def _account_totals(start=None, end=None):
    """Debit/credit totals per account for posted entries in a date range."""
    entry_ids = _posted_entries(start, end).values("id")
    rows = (
        JournalEntryLine.objects.filter(journal_entry_id__in=entry_ids)
        .values("account_id", "entry_type")
        .annotate(total=Sum("amount"))
    )
    totals = defaultdict(lambda: {"debit": ZERO, "credit": ZERO})
    for row in rows:
        totals[row["account_id"]][row["entry_type"]] += row["total"] or ZERO
    return totals


def _net(totals, account_id):
    row = totals.get(account_id)
    if not row:
        return ZERO
    return (row["debit"] or ZERO) - (row["credit"] or ZERO)


def _normal_balance(account, totals):
    net = _net(totals, account.id)
    return -net if account.normal_balance == "credit" else net


def _attach_entry_totals(entries):
    ids = [entry.id for entry in entries]
    rows = (
        JournalEntryLine.objects.filter(journal_entry_id__in=ids)
        .values("journal_entry_id")
        .annotate(
            debit=Sum("amount", filter=None),
            lines=Count("id"),
        )
    )
    # Sum with a conditional is clearer done per type than with filtered sums
    # on the same annotation, so compute both maps explicitly.
    debit_map = dict(
        JournalEntryLine.objects.filter(
            journal_entry_id__in=ids, entry_type="debit"
        )
        .values("journal_entry_id")
        .annotate(total=Sum("amount"))
        .values_list("journal_entry_id", "total")
    )
    credit_map = dict(
        JournalEntryLine.objects.filter(
            journal_entry_id__in=ids, entry_type="credit"
        )
        .values("journal_entry_id")
        .annotate(total=Sum("amount"))
        .values_list("journal_entry_id", "total")
    )
    count_map = {row["journal_entry_id"]: row["lines"] for row in rows}
    for entry in entries:
        entry.total_debit = debit_map.get(entry.id, ZERO)
        entry.total_credit = credit_map.get(entry.id, ZERO)
        entry.line_count = count_map.get(entry.id, 0)


def _shift_month(value, delta):
    month = value.month - 1 + delta
    year = value.year + month // 12
    month = month % 12 + 1
    return date(year, month, 1)


def _user_names(ids):
    return {
        user.id: user.get_full_name() or user.email
        for user in User.objects.filter(id__in={i for i in ids if i})
    }


def _collect_lines(formset):
    lines = []
    for form in formset:
        if not hasattr(form, "cleaned_data") or not form.cleaned_data:
            continue
        account = form.cleaned_data.get("account")
        entry_type = form.cleaned_data.get("entry_type")
        amount = form.cleaned_data.get("amount")
        if not account and not entry_type and not amount:
            continue
        lines.append(
            {
                "account_id": account.id if account else None,
                "entry_type": entry_type,
                "amount": amount or ZERO,
                "description": (form.cleaned_data.get("description") or "").strip(),
            }
        )
    return lines


def _line_totals(lines):
    debit = sum(
        (line["amount"] for line in lines if line["entry_type"] == "debit"), ZERO
    )
    credit = sum(
        (line["amount"] for line in lines if line["entry_type"] == "credit"), ZERO
    )
    return debit.quantize(Decimal("0.01")), credit.quantize(Decimal("0.01"))


def _receivable_summary():
    invoices = Invoice.objects.filter(status__in=OPEN_RECEIVABLE)
    total = ZERO
    overdue = ZERO
    today = timezone.localdate()
    for invoice in invoices:
        balance = (invoice.total_amount or ZERO) - (invoice.paid_amount or ZERO)
        total += balance
        if invoice.due_date < today:
            overdue += balance
    return total, overdue


def _payable_summary():
    bills = SupplierBill.objects.filter(status__in=OPEN_PAYABLE)
    total = ZERO
    today = timezone.localdate()
    overdue = ZERO
    for bill in bills:
        balance = (bill.total_amount or ZERO) - (bill.paid_amount or ZERO)
        total += balance
        if bill.due_date < today:
            overdue += balance
    return total, overdue


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def dashboard(request):
    access = get_access(request.user)
    totals = _account_totals()
    accounts = list(ChartOfAccount.objects.exclude(status="deleted"))

    type_totals = defaultdict(lambda: ZERO)
    for account in accounts:
        net = _net(totals, account.id)
        if account.account_type in {"liability", "equity", "revenue"}:
            type_totals[account.account_type] += -net
        else:
            type_totals[account.account_type] += net
    revenue = type_totals["revenue"]
    expenses = type_totals["expense"]
    receivable, overdue_receivable = _receivable_summary()
    payable, _overdue_payable = _payable_summary()

    recent_entries = list(JournalEntry.objects.order_by("-created_at")[:6])
    _attach_entry_totals(recent_entries)

    today = timezone.localdate()
    monthly = []
    for offset in range(5, -1, -1):
        start = _shift_month(today.replace(day=1), -offset)
        end = _shift_month(start, 1) - timedelta(days=1)
        month_totals = _account_totals(start, end)
        month_revenue = ZERO
        month_expense = ZERO
        for account in accounts:
            net = _net(month_totals, account.id)
            if account.account_type == "revenue":
                month_revenue += -net
            elif account.account_type == "expense":
                month_expense += net
        monthly.append(
            {
                "label": start.strftime("%b %Y"),
                "revenue": month_revenue,
                "expenses": month_expense,
                "net": month_revenue - month_expense,
            }
        )
    peak = max(
        [abs(row["revenue"]) for row in monthly]
        + [abs(row["expenses"]) for row in monthly]
        + [Decimal("1")]
    )
    for row in monthly:
        row["revenue_pct"] = float(abs(row["revenue"]) / peak * 100)
        row["expense_pct"] = float(abs(row["expenses"]) / peak * 100)

    return render(
        request,
        "accounts/dashboard.html",
        {
            "page_title": "Accounts Dashboard - SORA ERP",
            "stats": {
                "assets": type_totals["asset"],
                "liabilities": type_totals["liability"],
                "equity": type_totals["equity"],
                "revenue": revenue,
                "expenses": expenses,
                "net_income": revenue - expenses,
                "receivable": receivable,
                "overdue_receivable": overdue_receivable,
                "payable": payable,
            },
            "accounts_count": len([a for a in accounts if a.status == "active"]),
            "recent_entries": recent_entries,
            "monthly": monthly,
            "access": access,
        },
    )


# ---------------------------------------------------------------------------
# Chart of accounts
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def chart_of_accounts(request):
    access = get_access(request.user)
    accounts = ChartOfAccount.objects.exclude(status="deleted")
    query = (request.GET.get("q") or "").strip()
    account_type = request.GET.get("type") or ""
    status = request.GET.get("status") or ""
    if query:
        accounts = accounts.filter(
            account_code__icontains=query
        ) | accounts.filter(account_name__icontains=query)
    if account_type:
        accounts = accounts.filter(account_type=account_type)
    if status:
        accounts = accounts.filter(status=status)
    accounts = accounts.order_by("account_code")
    totals = _account_totals()
    for account in accounts:
        row = totals.get(account.id) or {"debit": ZERO, "credit": ZERO}
        account.debit_total = row["debit"]
        account.credit_total = row["credit"]
        account.balance = _normal_balance(account, totals)
    return render(
        request,
        "accounts/chart_of_accounts.html",
        {
            "page_title": "Chart of Accounts - SORA ERP",
            "accounts": accounts,
            "search_query": query,
            "type_filter": account_type,
            "status_filter": status,
            "account_types": ACCOUNT_TYPES,
            "can_create": access.can("accounts", "create"),
            "can_edit": access.can("accounts", "edit"),
            "can_delete": access.can("accounts", "delete"),
            "access": access,
        },
    )


@login_required
@module_access_required("accounts")
def account_create(request):
    access = get_access(request.user)
    if not access.can("accounts", "create"):
        messages.error(request, "You do not have permission to create accounts.")
        return redirect("/accounts/chart-of-accounts")
    if request.method == "POST":
        form = AccountForm(request.POST)
        if form.is_valid():
            account = form.save(commit=False)
            account.created_by = request.user.id
            account.created_at = timezone.now()
            account.parent_account_id = (
                form.cleaned_data["parent"].id
                if form.cleaned_data.get("parent")
                else None
            )
            account.save()
            messages.success(request, f"Account {account.account_code} created.")
            return redirect("/accounts/chart-of-accounts")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AccountForm()
    return render(
        request,
        "accounts/account_form.html",
        {
            "page_title": "New Account - SORA ERP",
            "form": form,
            "is_edit": False,
        },
    )


@login_required
@module_access_required("accounts")
def account_edit(request, pk):
    access = get_access(request.user)
    if not access.can("accounts", "edit"):
        messages.error(request, "You do not have permission to edit accounts.")
        return redirect("/accounts/chart-of-accounts")
    account = get_object_or_404(ChartOfAccount, pk=pk)
    if request.method == "POST":
        form = AccountForm(request.POST, instance=account)
        if form.is_valid():
            account = form.save(commit=False)
            account.parent_account_id = (
                form.cleaned_data["parent"].id
                if form.cleaned_data.get("parent")
                else None
            )
            account.updated_at = timezone.now()
            account.save()
            messages.success(request, "Account updated.")
            return redirect("/accounts/chart-of-accounts")
        messages.error(request, "Please correct the errors below.")
    else:
        form = AccountForm(instance=account)
    return render(
        request,
        "accounts/account_form.html",
        {
            "page_title": f"Edit {account.account_code} - SORA ERP",
            "form": form,
            "account": account,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("accounts")
def account_delete(request, pk):
    if request.method != "POST":
        return redirect("/accounts/chart-of-accounts")
    access = get_access(request.user)
    if not access.can("accounts", "delete"):
        messages.error(request, "You do not have permission to delete accounts.")
        return redirect("/accounts/chart-of-accounts")
    account = get_object_or_404(ChartOfAccount, pk=pk)
    if JournalEntryLine.objects.filter(account_id=account.id).exists():
        messages.error(
            request, "This account has journal lines and cannot be deleted."
        )
        return redirect("/accounts/chart-of-accounts")
    account.status = "deleted"
    account.is_active = 0
    account.updated_at = timezone.now()
    account.save(update_fields=["status", "is_active", "updated_at"])
    messages.success(request, f"Account {account.account_code} deleted.")
    return redirect("/accounts/chart-of-accounts")


# ---------------------------------------------------------------------------
# Journal entries
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def journal_entries(request):
    access = get_access(request.user)
    entries = JournalEntry.objects.all()
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    date_from = _date_param(request, "from", None)
    date_to = _date_param(request, "to", None)
    if query:
        entries = entries.filter(
            entry_number__icontains=query
        ) | entries.filter(description__icontains=query) | entries.filter(
            reference__icontains=query
        )
    if status:
        entries = entries.filter(status=status)
    if date_from:
        entries = entries.filter(entry_date__gte=date_from)
    if date_to:
        entries = entries.filter(entry_date__lte=date_to)
    entries = entries.order_by("-entry_date", "-id")
    page_obj = paginate(request, entries, 20)
    _attach_entry_totals(page_obj.object_list)
    return render(
        request,
        "accounts/journal_entries.html",
        {
            "page_title": "Journal Entries - SORA ERP",
            "page_obj": page_obj,
            "entries": page_obj.object_list,
            "search_query": query,
            "status_filter": status,
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "can_create": access.can("accounts", "create"),
            "access": access,
        },
    )


def _prepare_lines(request):
    if request.method == "POST":
        return JournalLineFormSet(request.POST, prefix="lines")
    return JournalLineFormSet(prefix="lines")


@login_required
@module_access_required("accounts")
def journal_entry_create(request):
    access = get_access(request.user)
    if not access.can("accounts", "create"):
        messages.error(request, "You do not have permission to create journal entries.")
        return redirect("/accounts/journal-entries")
    if request.method == "POST":
        form = JournalEntryForm(request.POST)
        formset = _prepare_lines(request)
        if form.is_valid() and formset.is_valid():
            lines = _collect_lines(formset)
            if len(lines) < 2:
                form.add_error(None, "A journal entry needs at least two lines.")
            else:
                debit, credit = _line_totals(lines)
                if debit != credit:
                    form.add_error(
                        None,
                        f"Entry is not balanced: debits {debit:,.2f} vs credits {credit:,.2f}.",
                    )
                else:
                    now = timezone.now()
                    entry_date = form.cleaned_data["entry_date"]
                    with transaction.atomic():
                        entry = JournalEntry.objects.create(
                            entry_number=next_code(
                                f"JE-{entry_date.year}",
                                "journal_entries",
                                "entry_number",
                                width=5,
                            ),
                            entry_date=entry_date,
                            description=form.cleaned_data["description"],
                            reference=form.cleaned_data.get("reference") or "",
                            status="draft",
                            created_by=request.user.id,
                            created_at=now,
                        )
                        JournalEntryLine.objects.bulk_create(
                            [
                                JournalEntryLine(
                                    journal_entry_id=entry.id,
                                    account_id=line["account_id"],
                                    entry_type=line["entry_type"],
                                    amount=line["amount"],
                                    description=line["description"],
                                    created_at=now,
                                )
                                for line in lines
                            ]
                        )
                    messages.success(
                        request, f"Journal entry {entry.entry_number} saved as draft."
                    )
                    return redirect(f"/accounts/journal-entries/view/{entry.id}")
        messages.error(request, "Please correct the errors below.")
    else:
        form = JournalEntryForm()
        formset = _prepare_lines(request)
    return render(
        request,
        "accounts/journal_entry_form.html",
        {
            "page_title": "New Journal Entry - SORA ERP",
            "form": form,
            "formset": formset,
        },
    )


@login_required
@module_access_required("accounts")
def journal_entry_view(request, pk):
    access = get_access(request.user)
    entry = get_object_or_404(JournalEntry, pk=pk)
    lines = list(
        JournalEntryLine.objects.filter(journal_entry_id=entry.id).order_by(
            "entry_type", "id"
        )
    )
    accounts = {
        account.id: account
        for account in ChartOfAccount.objects.filter(
            id__in={line.account_id for line in lines}
        )
    }
    for line in lines:
        line.account = accounts.get(line.account_id)
    debit, credit = _line_totals(
        [
            {"entry_type": line.entry_type, "amount": line.amount}
            for line in lines
        ]
    )
    names = _user_names([entry.created_by, entry.posted_by])
    return render(
        request,
        "accounts/journal_entry_view.html",
        {
            "page_title": f"{entry.entry_number} - SORA ERP",
            "entry": entry,
            "lines": lines,
            "total_debit": debit,
            "total_credit": credit,
            "difference": debit - credit,
            "created_by_name": names.get(entry.created_by, "-"),
            "posted_by_name": names.get(entry.posted_by, "-"),
            "can_post": entry.status == "draft" and access.can("accounts", "approve"),
            "access": access,
        },
    )


@login_required
@module_access_required("accounts")
def journal_entry_post(request, pk):
    if request.method != "POST":
        return redirect(f"/accounts/journal-entries/view/{pk}")
    access = get_access(request.user)
    if not access.can("accounts", "approve"):
        messages.error(request, "You do not have permission to post journal entries.")
        return redirect(f"/accounts/journal-entries/view/{pk}")
    entry = get_object_or_404(JournalEntry, pk=pk)
    if entry.status != "draft":
        messages.error(request, "Only draft entries can be posted.")
        return redirect(f"/accounts/journal-entries/view/{entry.id}")
    lines = list(JournalEntryLine.objects.filter(journal_entry_id=entry.id))
    debit, credit = _line_totals(
        [{"entry_type": line.entry_type, "amount": line.amount} for line in lines]
    )
    if not lines or debit != credit:
        messages.error(request, "The entry is not balanced and cannot be posted.")
        return redirect(f"/accounts/journal-entries/view/{entry.id}")
    with transaction.atomic():
        entry.status = "posted"
        entry.posted_at = timezone.now()
        entry.posted_by = request.user.id
        entry.save(update_fields=["status", "posted_at", "posted_by"])
        for line in lines:
            account = ChartOfAccount.objects.get(pk=line.account_id)
            delta = line.amount if line.entry_type == "debit" else -line.amount
            account.current_balance = (account.current_balance or ZERO) + delta
            account.save(update_fields=["current_balance"])
    messages.success(request, f"Journal entry {entry.entry_number} posted.")
    return redirect(f"/accounts/journal-entries/view/{entry.id}")


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def ledger(request):
    access = get_access(request.user)
    today = timezone.localdate()
    start = _date_param(request, "from", today.replace(day=1))
    end = _date_param(request, "to", today)
    if end < start:
        start, end = end, start
    accounts = list(
        ChartOfAccount.objects.filter(status="active").order_by("account_code")
    )
    totals = _account_totals(start, end)
    for account in accounts:
        row = totals.get(account.id) or {"debit": ZERO, "credit": ZERO}
        account.debit_total = row["debit"]
        account.credit_total = row["credit"]
        account.balance = _normal_balance(account, totals)
    return render(
        request,
        "accounts/ledger.html",
        {
            "page_title": "General Ledger - SORA ERP",
            "accounts": accounts,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "access": access,
        },
    )


@login_required
@module_access_required("accounts")
def ledger_account(request, pk):
    access = get_access(request.user)
    account = get_object_or_404(ChartOfAccount, pk=pk)
    today = timezone.localdate()
    start = _date_param(request, "from", today.replace(day=1))
    end = _date_param(request, "to", today)
    if end < start:
        start, end = end, start

    before = (
        _account_totals(end=start - timedelta(days=1)).get(account.id)
        or {"debit": ZERO, "credit": ZERO}
    )
    opening = _normal_balance(account, {account.id: before})
    entry_ids = _posted_entries(start, end).values("id")
    lines = list(
        JournalEntryLine.objects.filter(
            journal_entry_id__in=entry_ids, account_id=account.id
        ).order_by("journal_entry_id", "id")
    )
    entries = {
        entry.id: entry
        for entry in JournalEntry.objects.filter(
            id__in={line.journal_entry_id for line in lines}
        )
    }
    running = opening
    period_debit = ZERO
    period_credit = ZERO
    rows = []
    for line in sorted(
        lines,
        key=lambda item: (
            entries[item.journal_entry_id].entry_date,
            item.journal_entry_id,
            item.id,
        ),
    ):
        entry = entries.get(line.journal_entry_id)
        if line.entry_type == "debit":
            period_debit += line.amount
        else:
            period_credit += line.amount
        delta = line.amount if line.entry_type == account.normal_balance else -line.amount
        running += delta
        rows.append(
            {
                "date": entry.entry_date if entry else None,
                "entry_number": entry.entry_number if entry else "-",
                "description": line.description
                or (entry.description if entry else ""),
                "reference": entry.reference if entry else "",
                "debit": line.amount if line.entry_type == "debit" else None,
                "credit": line.amount if line.entry_type == "credit" else None,
                "balance": running,
            }
        )
    return render(
        request,
        "accounts/ledger_account.html",
        {
            "page_title": f"Ledger - {account.account_name} - SORA ERP",
            "account": account,
            "rows": rows,
            "opening_balance": opening,
            "closing_balance": running,
            "period_debit": period_debit,
            "period_credit": period_credit,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "access": access,
        },
    )


# ---------------------------------------------------------------------------
# Trial balance
# ---------------------------------------------------------------------------
def _trial_balance_rows(as_at):
    accounts = ChartOfAccount.objects.filter(status="active").order_by("account_code")
    totals = _account_totals(end=as_at)
    rows = []
    total_debit = ZERO
    total_credit = ZERO
    for account in accounts:
        net = _net(totals, account.id)
        debit = net if net > 0 else ZERO
        credit = -net if net < 0 else ZERO
        total_debit += debit
        total_credit += credit
        rows.append(
            {
                "account": account,
                "debit": debit,
                "credit": credit,
                "balance": abs(net),
            }
        )
    return rows, total_debit, total_credit


@login_required
@module_access_required("accounts")
def trial_balance(request):
    access = get_access(request.user)
    as_at = _date_param(request, "as_at", timezone.localdate())
    if request.method == "POST":
        return redirect(f"/accounts/trial-balance?as_at={request.POST.get('as_at', '')}")
    rows, total_debit, total_credit = _trial_balance_rows(as_at)
    return render(
        request,
        "accounts/trial_balance.html",
        {
            "page_title": "Trial Balance - SORA ERP",
            "rows": rows,
            "total_debit": total_debit,
            "total_credit": total_credit,
            "as_at": as_at.isoformat(),
            "access": access,
        },
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def reports(request):
    return render(
        request,
        "accounts/reports.html",
        {"page_title": "Financial Reports - SORA ERP"},
    )


def _period_from_request(request):
    today = timezone.localdate()
    form = ReportPeriodForm(
        {
            "from_date": request.GET.get("from_date") or today.replace(day=1),
            "to_date": request.GET.get("to_date") or today,
        }
    )
    if form.is_valid():
        return form.cleaned_data["from_date"], form.cleaned_data["to_date"], form
    return today.replace(day=1), today, ReportPeriodForm(
        initial={"from_date": today.replace(day=1), "to_date": today}
    )


def _income_statement(start, end):
    totals = _account_totals(start, end)
    revenue = []
    expenses = []
    total_revenue = ZERO
    total_expenses = ZERO
    for account in ChartOfAccount.objects.filter(
        account_type="revenue", status="active"
    ).order_by("account_code"):
        amount = _normal_balance(account, totals)
        if amount:
            revenue.append({"account": account, "amount": amount})
            total_revenue += amount
    for account in ChartOfAccount.objects.filter(
        account_type="expense", status="active"
    ).order_by("account_code"):
        amount = _normal_balance(account, totals)
        if amount:
            expenses.append({"account": account, "amount": amount})
            total_expenses += amount
    return revenue, expenses, total_revenue, total_expenses


@login_required
@module_access_required("accounts")
def income_statement(request):
    start, end, form = _period_from_request(request)
    revenue, expenses, total_revenue, total_expenses = _income_statement(start, end)
    return render(
        request,
        "accounts/report_income_statement.html",
        {
            "page_title": "Income Statement - SORA ERP",
            "revenue": revenue,
            "expenses": expenses,
            "total_revenue": total_revenue,
            "total_expenses": total_expenses,
            "net_income": total_revenue - total_expenses,
            "form": form,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        },
    )


def _balance_sheet(as_at):
    totals = _account_totals(end=as_at)
    groups = {"asset": [], "liability": [], "equity": []}
    sums = {"asset": ZERO, "liability": ZERO, "equity": ZERO}
    for account in ChartOfAccount.objects.filter(status="active").order_by(
        "account_code"
    ):
        if account.account_type not in groups:
            continue
        amount = _normal_balance(account, totals)
        groups[account.account_type].append({"account": account, "amount": amount})
        sums[account.account_type] += amount
    revenue, expenses, total_revenue, total_expenses = _income_statement(
        None, as_at
    )
    net_income = total_revenue - total_expenses
    return groups, sums, net_income


@login_required
@module_access_required("accounts")
def balance_sheet(request):
    as_at = _date_param(request, "as_at", timezone.localdate())
    groups, sums, net_income = _balance_sheet(as_at)
    total_equity = sums["equity"] + net_income
    return render(
        request,
        "accounts/report_balance_sheet.html",
        {
            "page_title": "Balance Sheet - SORA ERP",
            "groups": groups,
            "sums": sums,
            "net_income": net_income,
            "total_equity": total_equity,
            "total_liabilities_equity": sums["liability"] + total_equity,
            "difference": sums["asset"] - (sums["liability"] + total_equity),
            "as_at": as_at.isoformat(),
        },
    )


@login_required
@module_access_required("accounts")
def general_ledger_report(request):
    today = timezone.localdate()
    start = _date_param(request, "from", today.replace(day=1))
    end = _date_param(request, "to", today)
    if end < start:
        start, end = end, start
    before = _account_totals(end=start - timedelta(days=1))
    period = _account_totals(start, end)
    rows = []
    for account in ChartOfAccount.objects.filter(status="active").order_by(
        "account_code"
    ):
        opening = _normal_balance(account, before)
        debit = (period.get(account.id) or {}).get("debit", ZERO)
        credit = (period.get(account.id) or {}).get("credit", ZERO)
        closing = _normal_balance(
            account,
            {
                account.id: {
                    "debit": (before.get(account.id) or {}).get("debit", ZERO)
                    + debit,
                    "credit": (before.get(account.id) or {}).get("credit", ZERO)
                    + credit,
                }
            },
        )
        if not any([opening, debit, credit, closing]):
            continue
        rows.append(
            {
                "account": account,
                "opening": opening,
                "debit": debit,
                "credit": credit,
                "closing": closing,
            }
        )
    return render(
        request,
        "accounts/report_general_ledger.html",
        {
            "page_title": "General Ledger Report - SORA ERP",
            "rows": rows,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        },
    )


@login_required
@module_access_required("accounts")
def export_report(request):
    report_type = request.GET.get("type") or "trial_balance"
    if report_type == "income_statement":
        start, end, _form = _period_from_request(request)
        revenue, expenses, total_revenue, total_expenses = _income_statement(
            start, end
        )
        rows = [["Revenue", ""]]
        rows += [[row["account"].account_code + " " + row["account"].account_name, row["amount"]] for row in revenue]
        rows.append(["Total revenue", total_revenue])
        rows += [["Expenses", ""]]
        rows += [[row["account"].account_code + " " + row["account"].account_name, row["amount"]] for row in expenses]
        rows.append(["Total expenses", total_expenses])
        rows.append(["Net income", total_revenue - total_expenses])
        return csv_response(
            f"income_statement_{start}_{end}.csv", ["Line", "Amount"], rows
        )
    if report_type == "balance_sheet":
        as_at = _date_param(request, "as_at", timezone.localdate())
        groups, sums, net_income = _balance_sheet(as_at)
        rows = []
        for key in ("asset", "liability", "equity"):
            rows.append([key.title(), ""])
            rows += [
                [
                    row["account"].account_code + " " + row["account"].account_name,
                    row["amount"],
                ]
                for row in groups[key]
            ]
            rows.append([f"Total {key}", sums[key]])
        rows.append(["Net income", net_income])
        return csv_response(f"balance_sheet_{as_at}.csv", ["Line", "Amount"], rows)
    as_at = _date_param(request, "as_at", timezone.localdate())
    rows, total_debit, total_credit = _trial_balance_rows(as_at)
    return csv_response(
        f"trial_balance_{as_at}.csv",
        ["Code", "Account", "Type", "Debit", "Credit"],
        [
            [
                row["account"].account_code,
                row["account"].account_name,
                row["account"].account_type,
                row["debit"],
                row["credit"],
            ]
            for row in rows
        ]
        + [["", "Total", "", total_debit, total_credit]],
    )


# ---------------------------------------------------------------------------
# Bank accounts
# ---------------------------------------------------------------------------
@login_required
@module_access_required("accounts")
def bank_accounts(request):
    access = get_access(request.user)
    accounts = BankAccount.objects.exclude(status="inactive").order_by(
        "-is_default", "bank_name"
    )
    return render(
        request,
        "accounts/bank_accounts.html",
        {
            "page_title": "Bank Accounts - SORA ERP",
            "accounts": accounts,
            "can_create": access.can("accounts", "create"),
            "can_edit": access.can("accounts", "edit"),
            "can_delete": access.can("accounts", "delete"),
            "access": access,
        },
    )


@login_required
@module_access_required("accounts")
def bank_account_create(request):
    access = get_access(request.user)
    if not access.can("accounts", "create"):
        messages.error(request, "You do not have permission to manage bank accounts.")
        return redirect("/accounts/bank-accounts")
    if request.method == "POST":
        form = BankAccountForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                account = form.save(commit=False)
                if account.is_default:
                    BankAccount.objects.update(is_default=0)
                account.save()
            messages.success(request, f"Bank account {account.bank_name} added.")
            return redirect("/accounts/bank-accounts")
        messages.error(request, "Please correct the errors below.")
    else:
        form = BankAccountForm()
    return render(
        request,
        "accounts/bank_account_form.html",
        {"page_title": "New Bank Account - SORA ERP", "form": form, "is_edit": False},
    )


@login_required
@module_access_required("accounts")
def bank_account_edit(request, pk):
    access = get_access(request.user)
    if not access.can("accounts", "edit"):
        messages.error(request, "You do not have permission to manage bank accounts.")
        return redirect("/accounts/bank-accounts")
    account = get_object_or_404(BankAccount, pk=pk)
    if request.method == "POST":
        form = BankAccountForm(request.POST, instance=account)
        if form.is_valid():
            with transaction.atomic():
                account = form.save(commit=False)
                if account.is_default:
                    BankAccount.objects.exclude(pk=account.pk).update(is_default=0)
                account.save()
            messages.success(request, "Bank account updated.")
            return redirect("/accounts/bank-accounts")
        messages.error(request, "Please correct the errors below.")
    else:
        form = BankAccountForm(instance=account)
    return render(
        request,
        "accounts/bank_account_form.html",
        {
            "page_title": "Edit Bank Account - SORA ERP",
            "form": form,
            "account": account,
            "is_edit": True,
        },
    )


@login_required
@module_access_required("accounts")
def bank_account_delete(request, pk):
    if request.method != "POST":
        return redirect("/accounts/bank-accounts")
    access = get_access(request.user)
    if not access.can("accounts", "delete"):
        messages.error(request, "You do not have permission to manage bank accounts.")
        return redirect("/accounts/bank-accounts")
    account = get_object_or_404(BankAccount, pk=pk)
    account.status = "inactive"
    account.is_default = 0
    account.save(update_fields=["status", "is_default"])
    messages.success(request, "Bank account archived.")
    return redirect("/accounts/bank-accounts")
