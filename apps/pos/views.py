"""Point-of-sale terminal, sales history, receipts, refunds and till sessions."""

from __future__ import annotations

import base64
import io
import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.core.models import Client, EtimsSubmission, Product, ProductCategory, User
from apps.core.permissions import get_access, module_access_required
from apps.core.services import next_sequence
from apps.core.ui import paginate
from apps.pos.forms import RefundForm, SessionCloseForm, SessionOpenForm, VoidSaleForm
from apps.pos.models import PosPayment, PosRefund, PosSale, PosSaleItem, PosSession
from apps.pos.services import (
    PosError,
    create_refund,
    create_sale,
    method_labels,
    method_map,
    money,
    payment_methods,
    pos_policy,
    refundable_items,
    session_summary,
    submitted_etims_for_sale,
    void_sale,
)

ZERO = Decimal("0.00")
PAGE_SIZE = 15
SESSION_PAGE_SIZE = 20


def _user_names(ids):
    ids = {pk for pk in ids if pk}
    if not ids:
        return {}
    return {
        row.id: row.get_full_name() or row.email
        for row in User.objects.filter(id__in=ids)
    }


def _method_rows(*, enabled_only=False):
    return [
        {
            "code": row.code,
            "label": row.label,
            "is_cash": row.is_cash,
            "is_credit": row.is_credit,
            "requires_reference": row.requires_reference,
            "enabled": row.enabled,
        }
        for row in payment_methods(enabled_only=enabled_only)
    ]


def _decorate_sales(rows):
    rows = list(rows)
    names = _user_names({row.cashier for row in rows})
    labels = method_labels()
    for sale in rows:
        sale.cashier_name = names.get(sale.cashier, f"User #{sale.cashier}")
        methods = []
        for payment in sale.payments.all():
            label = labels.get(payment.method, payment.method)
            if label not in methods:
                methods.append(label)
        sale.payment_label = " + ".join(methods) if methods else "Unpaid"
        sale.voidable = (
            sale.status == "completed"
            and sale.session.status == "open"
            and not sale.refunded_amount
            and not sale.invoice_id
        )
    return rows


def _today_stats():
    today = timezone.localdate()
    sales = PosSale.objects.filter(status="completed", created_at__date=today)
    revenue = sales.aggregate(total=Sum("total_amount"))["total"] or ZERO
    count = sales.count()
    items = (
        PosSaleItem.objects.filter(sale__status="completed", sale__created_at__date=today).aggregate(
            total=Sum("quantity")
        )["total"]
        or ZERO
    )
    refunds_today = PosRefund.objects.filter(created_at__date=today)
    refund_total = refunds_today.aggregate(total=Sum("total_amount"))["total"] or ZERO
    mix = (
        PosPayment.objects.filter(sale__status="completed", sale__created_at__date=today)
        .values("method")
        .annotate(total=Sum("amount"))
    )
    mix_map = {row["method"]: row["total"] for row in mix}
    change_total = sales.aggregate(total=Sum("change_amount"))["total"] or ZERO
    for tender in payment_methods(enabled_only=False):
        if tender.is_cash and change_total:
            mix_map[tender.code] = mix_map.get(tender.code, ZERO) - change_total
    refund_mix = (
        PosRefund.objects.filter(created_at__date=today)
        .values("method")
        .annotate(total=Sum("total_amount"))
    )
    for row in refund_mix:
        mix_map[row["method"]] = mix_map.get(row["method"], ZERO) - row["total"]
    method_rows = []
    seen = set()
    for row in payment_methods(enabled_only=False):
        seen.add(row.code)
        method_rows.append(
            {
                "code": row.code,
                "label": row.label,
                "amount": money(mix_map.get(row.code, ZERO)),
                "zero": row.code not in mix_map,
            }
        )
    for code, amount in mix_map.items():
        if code not in seen:
            method_rows.append(
                {"code": code, "label": code, "amount": money(amount), "zero": False}
            )
    open_sessions = list(PosSession.objects.filter(status="open"))
    expected_cash = sum((session_summary(row)["expected_cash"] for row in open_sessions), ZERO)
    return {
        "sales": count,
        "revenue": money(revenue),
        "refunds": money(refund_total),
        "net_revenue": money(revenue - refund_total),
        "refund_count": refunds_today.count(),
        "items": items,
        "average": money(revenue / count) if count else ZERO,
        "payment_rows": method_rows,
        "open_sessions": len(open_sessions),
        "expected_cash": money(expected_cash),
    }


def _open_session_for(user):
    return (
        PosSession.objects.filter(status="open", opened_by=user.id)
        .order_by("-opened_at", "-id")
        .first()
    )


def _qr_data_uri(value: str) -> str:
    if not value:
        return ""
    try:
        import segno

        buffer = io.BytesIO()
        segno.make(value, error="m").save(buffer, kind="png", scale=4, border=2)
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return ""


def _parse_decimal(value):
    try:
        return Decimal(str(value).replace(",", "").strip() or "0")
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


# ---------------------------------------------------------------------------
# Terminal
# ---------------------------------------------------------------------------
@login_required
@module_access_required("pos")
def terminal(request):
    access = get_access(request.user)
    open_session = _open_session_for(request.user)
    category_names = {
        row.id: row.category_name for row in ProductCategory.objects.all()
    }
    products = list(Product.objects.filter(status="active").order_by("name"))
    used_categories = set()
    for product in products:
        product.category_name = category_names.get(product.category_id, "Uncategorised")
        used_categories.add(product.category_id)
        if product.type == "service":
            product.stock_state = "service"
            product.display_stock = "Service"
        else:
            stock = product.current_stock or ZERO
            product.display_stock = f"{stock:.2f} {product.unit or 'units'}"
            if stock <= 0:
                product.stock_state = "out"
            elif product.reorder_level and stock <= product.reorder_level:
                product.stock_state = "low"
            else:
                product.stock_state = "ok"
    categories = sorted(
        ({"id": key, "name": category_names.get(key, "Uncategorised")} for key in used_categories),
        key=lambda row: row["name"],
    )
    policy = pos_policy()

    context = {
        "page_title": "POS Terminal - SORA ERP",
        "open_session": open_session,
        "products": products,
        "categories": categories,
        "clients": Client.objects.filter(status="active").order_by("company_name")[:200],
        "open_form": SessionOpenForm(),
        "can_sell": access.can("pos", "create"),
        "can_void": access.can("pos", "delete"),
        "today": _today_stats(),
        "recent_sales": [],
        "payment_methods_json": json.dumps(_method_rows(enabled_only=True)).replace("<", "\\u003c"),
        "pos_policy": policy,
    }
    if open_session is not None:
        context["session_stats"] = session_summary(open_session)
        context["recent_sales"] = _decorate_sales(
            PosSale.objects.filter(session=open_session)
            .select_related("session")
            .prefetch_related("payments", "items")
            .order_by("-created_at", "-id")[:5]
        )
        context["open_form"] = None
    return render(request, "pos/terminal.html", context)


@login_required
@module_access_required("pos")
def checkout(request):
    if request.method != "POST":
        return redirect("/pos/")
    access = get_access(request.user)
    if not access.can("pos", "create"):
        return JsonResponse({"ok": False, "error": "You do not have permission to sell."}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "The checkout request was malformed."}, status=400)

    session_id = payload.get("session_id")
    if session_id:
        session = PosSession.objects.filter(
            pk=session_id, status="open", opened_by=request.user.id
        ).first()
    else:
        session = _open_session_for(request.user)
    if session is None:
        return JsonResponse(
            {"ok": False, "error": "Open a till session before taking a sale."}, status=400
        )

    client = None
    client_id = payload.get("client_id")
    if client_id:
        client = Client.objects.filter(pk=client_id, status="active").first()
        if client is None:
            return JsonResponse({"ok": False, "error": "That customer could not be found."}, status=400)

    try:
        sale = create_sale(
            session=session,
            cashier_id=request.user.id,
            items=payload.get("items") or [],
            payments=payload.get("payments") or [],
            discount=payload.get("discount") or ZERO,
            client=client,
            customer_name=payload.get("customer_name") or "",
            customer_phone=payload.get("customer_phone") or "",
            notes=payload.get("notes") or "",
            client_reference=payload.get("client_reference") or "",
        )
    except PosError as error:
        return JsonResponse({"ok": False, "error": str(error)}, status=400)

    return JsonResponse(
        {
            "ok": True,
            "sale_id": sale.id,
            "sale_number": sale.sale_number,
            "total": str(sale.total_amount),
            "change": str(sale.change_amount),
            "invoice_id": sale.invoice_id,
            "redirect": f"/pos/sales/{sale.id}",
        }
    )


# ---------------------------------------------------------------------------
# Sales and receipts
# ---------------------------------------------------------------------------
@login_required
@module_access_required("pos")
def sales(request):
    access = get_access(request.user)
    queryset = (
        PosSale.objects.select_related("session")
        .prefetch_related("items", "payments")
        .order_by("-created_at", "-id")
    )
    query = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or ""
    method = request.GET.get("method") or ""
    date_from = request.GET.get("from") or ""
    date_to = request.GET.get("to") or ""
    session_id = request.GET.get("session") or ""
    if query:
        queryset = queryset.filter(
            Q(sale_number__icontains=query)
            | Q(customer_name__icontains=query)
            | Q(customer_phone__icontains=query)
        )
    if status:
        queryset = queryset.filter(status=status)
    if method:
        queryset = queryset.filter(payments__method=method).distinct()
    if session_id:
        queryset = queryset.filter(session_id=session_id)
    parsed_from = _parse_day(date_from)
    parsed_to = _parse_day(date_to)
    if parsed_from:
        queryset = queryset.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        queryset = queryset.filter(created_at__date__lte=parsed_to)

    page_obj = paginate(request, queryset, PAGE_SIZE)
    rows = _decorate_sales(page_obj.object_list)
    return render(
        request,
        "pos/sales.html",
        {
            "page_title": "POS Sales - SORA ERP",
            "page_obj": page_obj,
            "sales": rows,
            "today": _today_stats(),
            "filters": {
                "q": query,
                "status": status,
                "method": method,
                "from": date_from,
                "to": date_to,
                "session": session_id,
            },
            "payment_methods": [(row["code"], row["label"]) for row in _method_rows()],
            "can_void": access.can("pos", "delete"),
        },
    )


def _parse_day(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@login_required
@module_access_required("pos")
def receipt(request, sale_id):
    access = get_access(request.user)
    sale = get_object_or_404(
        PosSale.objects.select_related("session", "client"), pk=sale_id
    )
    items = list(sale.items.select_related("product"))
    payments = list(sale.payments.all())
    names = _user_names({sale.cashier})
    cashier_name = names.get(sale.cashier, f"User #{sale.cashier}")
    submission = (
        EtimsSubmission.objects.filter(pos_sale=sale).order_by("-id").first()
    )
    refunds = list(
        sale.refunds.prefetch_related("items").order_by("-created_at", "-id")
    )
    remaining = refundable_items(sale)
    has_remaining = any(row["refundable"] for row in remaining)
    filed = submission is not None and submission.status == "submitted"
    can_void = (
        access.can("pos", "delete")
        and sale.status == "completed"
        and sale.session.status == "open"
        and not refunds
        and not sale.invoice_id
        and not filed
    )
    can_refund = (
        access.can("pos", "edit")
        and sale.status == "completed"
        and not sale.invoice_id
        and has_remaining
    )
    return render(
        request,
        "pos/receipt.html",
        {
            "page_title": f"Receipt {sale.sale_number} - SORA ERP",
            "sale": sale,
            "items": items,
            "payments": payments,
            "cashier_name": cashier_name,
            "can_void": can_void,
            "void_form": VoidSaleForm(),
            "method_labels": method_labels(),
            "submission": submission,
            "kra_qr": _qr_data_uri(submission.kra_qr_url) if filed else "",
            "refunds": refunds,
            "can_refund": can_refund,
            "refund_session": _open_session_for(request.user),
            "receipt_footer": pos_policy()["receipt_footer"],
        },
    )


@login_required
@module_access_required("pos")
def sale_void(request, sale_id):
    access = get_access(request.user)
    sale = get_object_or_404(PosSale.objects.select_related("session"), pk=sale_id)
    if not access.can("pos", "delete"):
        messages.error(request, "You do not have permission to void sales.")
        return redirect(f"/pos/sales/{sale.id}")
    if request.method != "POST":
        return redirect(f"/pos/sales/{sale.id}")
    form = VoidSaleForm(request.POST)
    reason = form.cleaned_data["reason"] if form.is_valid() else ""
    try:
        void_sale(sale, request.user.id, reason)
    except PosError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f"Sale {sale.sale_number} voided and stock restored.")
    return redirect(f"/pos/sales/{sale.id}")


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------
@login_required
@module_access_required("pos")
def sale_refund(request, sale_id):
    access = get_access(request.user)
    sale = get_object_or_404(
        PosSale.objects.select_related("session", "client"), pk=sale_id
    )
    if not access.can("pos", "edit"):
        messages.error(request, "You do not have permission to process refunds.")
        return redirect(f"/pos/sales/{sale.id}")
    if sale.invoice_id:
        messages.error(
            request,
            "This sale was invoiced on account. Issue a credit note from the invoice instead.",
        )
        return redirect(f"/pos/sales/{sale.id}")
    methods = [
        row for row in _method_rows(enabled_only=True) if not row["is_credit"]
    ]
    session = _open_session_for(request.user)
    rows = [row for row in refundable_items(sale) if row["refundable"]]
    if not rows:
        messages.info(request, "There is nothing left to refund on this sale.")
        return redirect(f"/pos/sales/{sale.id}")

    if request.method == "POST":
        form = RefundForm(request.POST, methods=methods)
        if not form.is_valid():
            messages.error(request, "Choose a refund method and record the reason.")
        elif session is None:
            messages.error(request, "Open a till session before processing a refund.")
        else:
            items = []
            for row in rows:
                quantity = _parse_decimal(request.POST.get(f"qty_{row['item'].id}"))
                if quantity <= ZERO:
                    continue
                items.append(
                    {
                        "sale_item_id": row["item"].id,
                        "quantity": quantity,
                        "restock": bool(request.POST.get(f"restock_{row['item'].id}")),
                    }
                )
            try:
                refund = create_refund(
                    sale=sale,
                    session=session,
                    items=items,
                    method=form.cleaned_data["method"],
                    reference=form.cleaned_data.get("reference") or "",
                    reason=form.cleaned_data["reason"],
                    user_id=request.user.id,
                )
            except PosError as error:
                messages.error(request, str(error))
            else:
                messages.success(
                    request,
                    f"Refund {refund.refund_number} recorded for {refund.total_amount:.2f}.",
                )
                return redirect(f"/pos/refunds/{refund.id}")
    else:
        form = RefundForm(methods=methods)

    return render(
        request,
        "pos/refund.html",
        {
            "page_title": f"Refund {sale.sale_number} - SORA ERP",
            "sale": sale,
            "rows": rows,
            "form": form,
            "session": session,
            "policy": pos_policy(),
        },
    )


@login_required
@module_access_required("pos")
def refund_receipt(request, refund_id):
    refund = get_object_or_404(
        PosRefund.objects.select_related("sale", "session"), pk=refund_id
    )
    names = _user_names({refund.refunded_by})
    submission = (
        EtimsSubmission.objects.filter(pos_refund=refund).order_by("-id").first()
    )
    return render(
        request,
        "pos/refund_receipt.html",
        {
            "page_title": f"Refund {refund.refund_number} - SORA ERP",
            "refund": refund,
            "items": list(refund.items.select_related("sale_item__product")),
            "user_name": names.get(refund.refunded_by, f"User #{refund.refunded_by}"),
            "method_labels": method_labels(),
            "submission": submission,
            "kra_qr": _qr_data_uri(submission.kra_qr_url) if submission else "",
            "receipt_footer": pos_policy()["receipt_footer"],
        },
    )


# ---------------------------------------------------------------------------
# Sessions and cash-up
# ---------------------------------------------------------------------------
def _decorate_sessions(rows):
    rows = list(rows)
    names = _user_names({row.opened_by for row in rows} | {row.closed_by for row in rows})
    for session in rows:
        summary = session_summary(session)
        session.summary = summary
        session.opener_name = names.get(session.opened_by, f"User #{session.opened_by}")
        session.closer_name = names.get(session.closed_by, "") if session.closed_by else ""
    return rows


@login_required
@module_access_required("pos")
def session_list(request):
    access = get_access(request.user)
    rows = _decorate_sessions(
        PosSession.objects.order_by("-opened_at", "-id")[:SESSION_PAGE_SIZE]
    )
    open_rows = [row for row in rows if row.status == "open"]
    return render(
        request,
        "pos/sessions.html",
        {
            "page_title": "Till Sessions - SORA ERP",
            "sessions": rows,
            "open_sessions": open_rows,
            "open_form": SessionOpenForm(),
            "today": _today_stats(),
            "can_open": access.can("pos", "create"),
            "can_close": access.can("pos", "edit"),
        },
    )


@login_required
@module_access_required("pos")
def session_open(request):
    access = get_access(request.user)
    if not access.can("pos", "create"):
        messages.error(request, "You do not have permission to open a till.")
        return redirect("/pos/sessions")
    if request.method != "POST":
        return redirect("/pos/sessions")
    form = SessionOpenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please correct the till details.")
        return redirect("/pos/sessions")
    terminal_name = form.cleaned_data["terminal_name"].strip()
    if _open_session_for(request.user):
        messages.error(request, "You already have an open session. Close it first.")
        return redirect("/pos/")
    if PosSession.objects.filter(status="open", terminal_name__iexact=terminal_name).exists():
        messages.error(request, f"{terminal_name} already has an open session.")
        return redirect("/pos/sessions")
    session = PosSession.objects.create(
        session_number=next_sequence("SES", "pos_sessions", "session_number"),
        terminal_name=terminal_name,
        opened_by=request.user.id,
        opened_at=timezone.now(),
        opening_float=form.cleaned_data["opening_float"] or ZERO,
        notes=form.cleaned_data.get("notes") or "",
        status="open",
    )
    messages.success(request, f"Till {session.session_number} opened on {terminal_name}.")
    target = request.POST.get("next") or "/pos/"
    if not target.startswith("/pos"):
        target = "/pos/"
    return redirect(target)


@login_required
@module_access_required("pos")
def session_detail(request, session_id):
    access = get_access(request.user)
    session = get_object_or_404(PosSession, pk=session_id)
    summary = session_summary(session)
    names = _user_names({session.opened_by, session.closed_by})
    session.opener_name = names.get(session.opened_by, f"User #{session.opened_by}")
    session.closer_name = names.get(session.closed_by, "") if session.closed_by else ""
    rows = _decorate_sales(
        PosSale.objects.filter(session=session)
        .select_related("session")
        .prefetch_related("items", "payments")
        .order_by("-created_at", "-id")
    )
    labels = method_labels()
    method_rows = [
        {"code": code, "label": labels.get(code, code), "amount": amount}
        for code, amount in summary["by_method"].items()
    ]
    refunds = list(
        PosRefund.objects.filter(session=session)
        .select_related("sale")
        .prefetch_related("items")
        .order_by("-created_at", "-id")
    )
    close_form = SessionCloseForm(
        initial={"closing_float": summary["expected_cash"]}
    ) if session.status == "open" else None
    return render(
        request,
        "pos/session_detail.html",
        {
            "page_title": f"Till {session.session_number} - SORA ERP",
            "session": session,
            "summary": summary,
            "method_rows": method_rows,
            "sales": rows,
            "refunds": refunds,
            "close_form": close_form,
            "can_close": access.can("pos", "edit") and session.status == "open",
            "can_void": access.can("pos", "delete"),
        },
    )


@login_required
@module_access_required("pos")
def session_close(request, session_id):
    access = get_access(request.user)
    session = get_object_or_404(PosSession, pk=session_id)
    if not access.can("pos", "edit"):
        messages.error(request, "You do not have permission to close tills.")
        return redirect(f"/pos/sessions/{session.id}")
    if request.method != "POST":
        return redirect(f"/pos/sessions/{session.id}")
    if session.status == "closed":
        messages.error(request, "This session is already closed.")
        return redirect(f"/pos/sessions/{session.id}")
    form = SessionCloseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter the counted cash amount.")
        return redirect(f"/pos/sessions/{session.id}")
    summary = session_summary(session)
    closing = form.cleaned_data["closing_float"] or ZERO
    session.closed_by = request.user.id
    session.closed_at = timezone.now()
    session.closing_float = closing
    session.expected_cash = summary["expected_cash"]
    session.cash_variance = money(closing - summary["expected_cash"])
    session.status = "closed"
    note = (form.cleaned_data.get("notes") or "").strip()
    if note:
        session.notes = f"{session.notes}\nClose: {note}".strip() if session.notes else note
    session.save()
    messages.success(
        request,
        f"Till {session.session_number} closed with a variance of {session.cash_variance:.2f}.",
    )
    return redirect(f"/pos/sessions/{session.id}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@login_required
@module_access_required("pos")
def dashboard(request):
    today = timezone.localdate()
    stats = _today_stats()
    month_start = today.replace(day=1)
    top_products = list(
        PosSaleItem.objects.filter(
            sale__status="completed", sale__created_at__date__gte=month_start
        )
        .values("description")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-revenue")[:6]
    )
    low_stock = list(
        Product.objects.filter(
            status="active", type="product", current_stock__lte=F("reorder_level")
        ).order_by("current_stock")[:6]
    )
    open_rows = _decorate_sessions(PosSession.objects.filter(status="open")[:5])
    recent_sales = _decorate_sales(
        PosSale.objects.select_related("session")
        .prefetch_related("items", "payments")
        .order_by("-created_at", "-id")[:8]
    )

    trend = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        day_sales = PosSale.objects.filter(status="completed", created_at__date=day)
        day_refunds = PosRefund.objects.filter(created_at__date=day)
        gross = day_sales.aggregate(total=Sum("total_amount"))["total"] or ZERO
        refunded = day_refunds.aggregate(total=Sum("total_amount"))["total"] or ZERO
        trend.append(
            {
                "date": day,
                "label": day.strftime("%a"),
                "revenue": money(gross - refunded),
                "gross": money(gross),
                "refunds": money(refunded),
                "count": day_sales.count(),
            }
        )
    peak = max((row["revenue"] for row in trend), default=ZERO) or Decimal("1")
    for row in trend:
        row["percent"] = round(float(row["revenue"] / peak * 100), 1)

    return render(
        request,
        "pos/dashboard.html",
        {
            "page_title": "POS Dashboard - SORA ERP",
            "today": stats,
            "top_products": top_products,
            "low_stock": low_stock,
            "open_sessions": open_rows,
            "recent_sales": recent_sales,
            "trend": trend,
            "method_rows": stats["payment_rows"],
        },
    )
