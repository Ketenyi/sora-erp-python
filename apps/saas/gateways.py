"""Payment gateway hooks: Stripe Checkout and M-Pesa Daraja STK push.

Manual/bank verification remains the default. Online gateways activate only
when their settings are enabled and every required credential is present.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from apps.saas.models import TenantAuditLog, TenantPayment
from apps.saas.services import apply_subscription, platform_settings

ZERO = Decimal("0.00")
STRIPE_API = "https://api.stripe.com/v1"
MPESA_BASES = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}


class GatewayError(Exception):
    """A gateway rejected the request or could not be reached."""


class GatewayNotConfigured(GatewayError):
    """The gateway is disabled or missing required credentials."""


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _config() -> dict:
    return platform_settings()


def _stripe_missing(config: dict) -> list[str]:
    required = [
        ("stripe_publishable_key", "publishable key"),
        ("stripe_secret_key", "secret key"),
    ]
    return [label for key, label in required if not config.get(key)]


def _mpesa_missing(config: dict) -> list[str]:
    required = [
        ("mpesa_consumer_key", "consumer key"),
        ("mpesa_consumer_secret", "consumer secret"),
        ("mpesa_shortcode", "shortcode"),
        ("mpesa_passkey", "passkey"),
    ]
    return [label for key, label in required if not config.get(key)]


def gateway_status() -> dict:
    """Readiness for each gateway, without exposing credentials."""
    config = _config()
    stripe_missing = _stripe_missing(config)
    mpesa_missing = _mpesa_missing(config)
    return {
        "stripe": {
            "enabled": _truthy(config.get("stripe_enabled")),
            "ready": _truthy(config.get("stripe_enabled")) and not stripe_missing,
            "missing": stripe_missing,
        },
        "mpesa": {
            "enabled": _truthy(config.get("mpesa_enabled")),
            "ready": _truthy(config.get("mpesa_enabled")) and not mpesa_missing,
            "missing": mpesa_missing,
        },
    }


def stripe_ready() -> bool:
    return gateway_status()["stripe"]["ready"]


def mpesa_ready() -> bool:
    return gateway_status()["mpesa"]["ready"]


def _require_stripe() -> dict:
    config = _config()
    if not _truthy(config.get("stripe_enabled")):
        raise GatewayNotConfigured("Stripe checkout is not enabled.")
    missing = _stripe_missing(config)
    if missing:
        raise GatewayNotConfigured(f"Stripe is missing its {', '.join(missing)}.")
    return config


def _require_mpesa() -> dict:
    config = _config()
    if not _truthy(config.get("mpesa_enabled")):
        raise GatewayNotConfigured("M-Pesa STK push is not enabled.")
    missing = _mpesa_missing(config)
    if missing:
        raise GatewayNotConfigured(f"M-Pesa is missing its {', '.join(missing)}.")
    return config


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict | None = None,
    timeout: int = 20,
) -> dict:
    request = Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", "SORA-ERP/1.0")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8") or "{}"
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        message = raw
        try:
            payload = json.loads(raw)
            message = (
                payload.get("error", {}).get("message")
                or payload.get("errorMessage")
                or payload.get("ResponseDescription")
                or raw
            )
        except (json.JSONDecodeError, AttributeError):
            pass
        raise GatewayError(f"Gateway error: {message[:300]}")
    except (URLError, TimeoutError, OSError) as error:
        raise GatewayError(f"Could not reach the payment gateway: {error}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise GatewayError("The payment gateway returned an unreadable response.")


def _amount_cents(amount) -> int:
    cents = int((Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if cents <= 0:
        raise GatewayError("The payment amount must be greater than zero.")
    return cents


# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------
def start_stripe_checkout(payment: TenantPayment, *, success_url: str, cancel_url: str) -> dict:
    config = _require_stripe()
    plan_name = payment.plan.name if payment.plan else "SORA subscription"
    fields = [
        ("mode", "payment"),
        ("client_reference_id", str(payment.id)),
        ("customer_email", payment.tenant.admin_email or ""),
        ("success_url", success_url),
        ("cancel_url", cancel_url),
        ("line_items[0][quantity]", "1"),
        ("line_items[0][price_data][currency]", (payment.currency or "KES").lower()),
        ("line_items[0][price_data][unit_amount]", str(_amount_cents(payment.amount))),
        ("line_items[0][price_data][product_data][name]", f"{payment.tenant.display_name} · {plan_name}"),
        ("line_items[0][price_data][product_data][description]", f"{payment.billing_cycle.title()} subscription payment"),
        ("metadata[payment_id]", str(payment.id)),
        ("metadata[tenant_slug]", payment.tenant.slug),
    ]
    body = urlencode(fields).replace("%7BCHECKOUT_SESSION_ID%7D", "{CHECKOUT_SESSION_ID}")
    session = _request_json(
        f"{STRIPE_API}/checkout/sessions",
        method="POST",
        body=body.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['stripe_secret_key']}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    checkout_url = session.get("url")
    checkout_id = session.get("id")
    if not checkout_url or not checkout_id:
        raise GatewayError("Stripe did not return a checkout URL.")
    payment.gateway_checkout_id = checkout_id
    payment.gateway_payload = {"checkout_session": session}
    payment.save(update_fields=["gateway_checkout_id", "gateway_payload"])
    return {"id": checkout_id, "url": checkout_url}


def retrieve_stripe_session(session_id: str) -> dict:
    config = _require_stripe()
    return _request_json(
        f"{STRIPE_API}/checkout/sessions/{session_id}",
        headers={"Authorization": f"Bearer {config['stripe_secret_key']}"},
    )


def stripe_payment_matches(payment: TenantPayment, session: dict) -> bool:
    amount = session.get("amount_total")
    currency = (session.get("currency") or "").lower()
    if amount is not None and int(amount) != _amount_cents(payment.amount):
        return False
    if currency and currency != (payment.currency or "").lower():
        return False
    return True


def verify_stripe_signature(payload: bytes, signature_header: str, secret: str, tolerance: int = 300) -> bool:
    if not secret or not signature_header:
        return False
    values = {}
    for part in signature_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "v1":
            values.setdefault("v1", []).append(value)
        elif key:
            values[key] = value
    timestamp = values.get("t")
    signatures = values.get("v1") or []
    if not timestamp or not signatures:
        return False
    try:
        if abs(time.time() - int(timestamp)) > tolerance:
            return False
    except (TypeError, ValueError):
        return False
    signed = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in signatures)


# ---------------------------------------------------------------------------
# M-Pesa
# ---------------------------------------------------------------------------
def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10 and digits.startswith("0"):
        return "254" + digits[1:]
    if len(digits) == 9 and digits[0] in {"1", "7"}:
        return "254" + digits
    if len(digits) == 12 and digits.startswith("254"):
        return digits
    raise GatewayError("Enter a valid Kenyan M-Pesa number, e.g. 0712 345 678.")


def _mpesa_base(config: dict) -> str:
    environment = (config.get("mpesa_environment") or "sandbox").strip().lower()
    return MPESA_BASES.get(environment, MPESA_BASES["sandbox"])


def _mpesa_token(config: dict) -> str:
    credentials = base64.b64encode(
        f"{config['mpesa_consumer_key']}:{config['mpesa_consumer_secret']}".encode("utf-8")
    ).decode("utf-8")
    payload = _request_json(
        f"{_mpesa_base(config)}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}"},
    )
    token = payload.get("access_token")
    if not token:
        raise GatewayError("M-Pesa did not return an access token.")
    return token


def _mpesa_password(shortcode: str, passkey: str, timestamp: str) -> str:
    return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode("utf-8")).decode("utf-8")


def start_mpesa_stk(payment: TenantPayment, *, phone: str, callback_url: str) -> dict:
    config = _require_mpesa()
    if (payment.currency or "").upper() != "KES":
        raise GatewayError("M-Pesa STK push only supports KES payments.")
    phone_number = normalize_phone(phone)
    amount = int(Decimal(payment.amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if amount < 1:
        raise GatewayError("M-Pesa payments must be at least KES 1.")
    timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
    shortcode = str(config["mpesa_shortcode"]).strip()
    payload = {
        "BusinessShortCode": shortcode,
        "Password": _mpesa_password(shortcode, config["mpesa_passkey"], timestamp),
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone_number,
        "PartyB": shortcode,
        "PhoneNumber": phone_number,
        "CallBackURL": callback_url,
        "AccountReference": f"SORA-{payment.id}",
        "TransactionDesc": f"SORA subscription payment #{payment.id}",
    }
    response = _request_json(
        f"{_mpesa_base(config)}/mpesa/stkpush/v1/processrequest",
        method="POST",
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_mpesa_token(config)}",
            "Content-Type": "application/json",
        },
    )
    if str(response.get("ResponseCode")) != "0":
        raise GatewayError(
            response.get("ResponseDescription") or response.get("errorMessage") or "M-Pesa rejected the STK push."
        )
    checkout_id = response.get("CheckoutRequestID")
    if not checkout_id:
        raise GatewayError("M-Pesa did not return a checkout request id.")
    payment.gateway_checkout_id = checkout_id
    payment.gateway_payload = {
        "stk_request": {
            "phone": phone_number,
            "amount": amount,
            "merchant_request_id": response.get("MerchantRequestID"),
        },
        "stk_response": response,
    }
    payment.save(update_fields=["gateway_checkout_id", "gateway_payload"])
    return {
        "checkout_request_id": checkout_id,
        "message": response.get("CustomerMessage") or "Check your phone for the M-Pesa prompt.",
    }


def query_mpesa_status(payment: TenantPayment) -> dict:
    config = _require_mpesa()
    if not payment.gateway_checkout_id:
        raise GatewayError("This payment has no M-Pesa checkout request to query.")
    timestamp = timezone.localtime().strftime("%Y%m%d%H%M%S")
    shortcode = str(config["mpesa_shortcode"]).strip()
    payload = {
        "BusinessShortCode": shortcode,
        "Password": _mpesa_password(shortcode, config["mpesa_passkey"], timestamp),
        "Timestamp": timestamp,
        "CheckoutRequestID": payment.gateway_checkout_id,
    }
    return _request_json(
        f"{_mpesa_base(config)}/mpesa/stkpushquery/v1/query",
        method="POST",
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {_mpesa_token(config)}",
            "Content-Type": "application/json",
        },
    )


def apply_mpesa_result(
    payment: TenantPayment,
    *,
    result_code,
    result_desc: str = "",
    receipt: str = "",
    payload: dict | None = None,
) -> TenantPayment:
    payload = payload or {}
    if str(result_code) == "0":
        return complete_payment(
            payment,
            reference=receipt or payment.gateway_checkout_id,
            payload={"mpesa_callback": payload},
        )
    fail_payment(payment, result_desc or f"M-Pesa result {result_code}.", payload={"mpesa_callback": payload})
    return payment


def mpesa_callback_url(request) -> str:
    config = _config()
    base = (config.get("mpesa_callback_url") or "").strip()
    if not base:
        base = request.build_absolute_uri("/billing/gateway/mpesa/callback/")
    token = (config.get("mpesa_callback_token") or "").strip()
    if not token:
        return base
    parts = urlparse(base)
    query = dict(parse_qsl(parts.query))
    query.setdefault("token", token)
    return urlunparse(parts._replace(query=urlencode(query)))


def mpesa_callback_token_valid(request) -> bool:
    expected = (_config().get("mpesa_callback_token") or "").strip()
    supplied = (request.GET.get("token") or "").strip()
    if not expected or not supplied:
        return False
    return secrets.compare_digest(expected, supplied)


def callback_receipt(callback: dict) -> str:
    for item in (callback.get("CallbackMetadata") or {}).get("Item") or []:
        if item.get("Name") == "MpesaReceiptNumber":
            return str(item.get("Value") or "")
    return ""


def stripe_event_payment(event: dict) -> TenantPayment | None:
    obj = event.get("data", {}).get("object") or {}
    payment_id = (obj.get("metadata") or {}).get("payment_id")
    if payment_id:
        payment = TenantPayment.objects.filter(pk=payment_id).first()
        if payment:
            return payment
    session_id = obj.get("id")
    if session_id:
        return TenantPayment.objects.filter(gateway_checkout_id=session_id).first()
    return None


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------
@transaction.atomic
def complete_payment(
    payment: TenantPayment,
    *,
    reference: str = "",
    payload: dict | None = None,
    actor=None,
) -> TenantPayment:
    payment = (
        TenantPayment.objects.select_for_update()
        .select_related("tenant")
        .get(pk=payment.pk)
    )
    if payment.status == "completed":
        return payment
    tenant = payment.tenant
    plan = payment.plan or tenant.plan
    if plan is None:
        raise GatewayError("No plan is attached to this payment.")
    payment.status = "completed"
    payment.paid_at = timezone.now()
    if actor is not None:
        payment.recorded_by = actor
    if reference:
        payment.reference = reference[:120]
    if payload is not None:
        payment.gateway_payload = {**(payment.gateway_payload or {}), **payload}
    payment.save(
        update_fields=[
            "status",
            "paid_at",
            "recorded_by",
            "reference",
            "gateway_payload",
        ]
    )
    apply_subscription(tenant, plan=plan, billing_cycle=payment.billing_cycle, actor=actor)
    payment.period_start = tenant.subscription_starts_at.date() if tenant.subscription_starts_at else None
    payment.period_end = tenant.subscription_ends_at.date() if tenant.subscription_ends_at else None
    payment.save(update_fields=["period_start", "period_end"])
    TenantAuditLog.objects.create(
        tenant=tenant,
        actor=actor,
        action="payment.gateway_completed",
        details=(
            f"{payment.amount} {payment.currency} confirmed via {payment.get_method_display()} "
            f"(ref: {payment.reference or payment.gateway_checkout_id or 'n/a'})."
        ),
    )
    return payment


@transaction.atomic
def fail_payment(payment: TenantPayment, reason: str, *, payload: dict | None = None) -> TenantPayment:
    payment = TenantPayment.objects.select_for_update().select_related("tenant").get(pk=payment.pk)
    if payment.status == "completed":
        return payment
    payment.status = "failed"
    note = (reason or "Gateway payment failed.").strip()
    payment.notes = f"{payment.notes}\nGateway: {note}".strip() if payment.notes else f"Gateway: {note}"
    if payload is not None:
        payment.gateway_payload = {**(payment.gateway_payload or {}), **payload}
    payment.save(update_fields=["status", "notes", "gateway_payload"])
    TenantAuditLog.objects.create(
        tenant=payment.tenant,
        action="payment.gateway_failed",
        details=f"{payment.amount} {payment.currency} via {payment.get_method_display()}: {note}",
    )
    return payment
