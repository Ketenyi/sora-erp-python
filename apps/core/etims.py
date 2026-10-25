"""KRA eTIMS (OSCU) transmission queue.

Credentials and the device key live in ``CompanySetting`` rows; the password
and device key are encrypted at rest. Nothing here logs payloads or secrets.
Transmission is idempotent per document because a unique submission row is
created before the first network call.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

from django.conf import settings
from django.utils import timezone

from apps.core.crypto import decrypt_secret, encrypt_secret
from apps.core.services import get_company_settings, next_number, set_company_setting

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
RESPONSE_LIMIT = 512 * 1024
USER_AGENT = "SORA-ERP/eTIMS"
STALE_PROCESSING_MINUTES = 10

AUTH_PATH = "/oauth2/v1/generate"
DEVICE_INIT_PATH = "/etims-oscu/v1/selectInitOsdcInfo"
SALES_PATH = "/etims-oscu/v1/sendSalesTrns"

TOKEN_PATH_CACHE: dict[str, tuple[str, float]] = {}
TOKEN_LOCK = threading.Lock()


class EtimsError(RuntimeError):
    """Base class for eTIMS failures."""


class EtimsNotConfigured(EtimsError):
    """Credentials or device settings are incomplete."""


class EtimsTransportError(EtimsError):
    """The KRA endpoint could not be reached or returned an unusable reply."""


class EtimsRejected(EtimsError):
    """KRA answered but refused the transaction."""

    def __init__(self, message: str, *, code: str = "", response=None):
        super().__init__(message)
        self.code = code
        self.response = response or {}


@dataclass(frozen=True)
class EtimsConfig:
    enabled: bool
    base_url: str
    tin: str
    branch_code: str
    device_serial: str
    username: str
    password: str
    cmc_key: str
    auto_submit: bool
    timeout: int

    @property
    def missing_fields(self) -> list[str]:
        checks = (
            (self.base_url, "API URL"),
            (self.tin, "TIN"),
            (self.branch_code, "Branch code"),
            (self.device_serial, "Device serial"),
            (self.username, "API username"),
            (self.password, "API password"),
        )
        return [label for value, label in checks if not value]

    @property
    def is_ready(self) -> bool:
        return self.enabled and not self.missing_fields


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clamp_timeout(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 8
    return max(2, min(parsed, 30))


def get_config() -> EtimsConfig:
    values = get_company_settings()

    def text(key: str) -> str:
        return str(values.get(key) or "").strip()

    return EtimsConfig(
        enabled=_truthy(values.get("etims_enabled")),
        base_url=text("etims_api_url").rstrip("/"),
        tin=text("etims_tin"),
        branch_code=text("etims_branch_code") or "00",
        device_serial=text("etims_device_serial"),
        username=text("etims_api_key"),
        password=decrypt_secret(text("etims_api_secret")),
        cmc_key=decrypt_secret(text("etims_cmc_key")),
        auto_submit=_truthy(values.get("etims_auto_submit", "true")),
        timeout=_clamp_timeout(values.get("etims_timeout", 8)),
    )


def _store_device_key(value: str) -> None:
    set_company_setting("etims_cmc_key", encrypt_secret(value))


def reset_token_cache() -> None:
    with TOKEN_LOCK:
        TOKEN_PATH_CACHE.clear()


def _token_cache_key(config: EtimsConfig) -> str:
    from apps.core.tenancy import current_tenant

    tenant = current_tenant()
    tenant_id = getattr(tenant, "pk", "global") or "global"
    return f"{tenant_id}|{config.base_url}|{config.username}"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_endpoint(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    local_debug = settings.DEBUG and host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (settings.DEBUG and parsed.scheme == "http" and local_debug):
        raise EtimsNotConfigured("The eTIMS API URL must use HTTPS.")
    if not host or parsed.username or parsed.password:
        raise EtimsNotConfigured("The eTIMS API URL is invalid.")
    try:
        addrinfo = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise EtimsTransportError("The eTIMS API host could not be resolved.")
    if local_debug:
        return
    for info in addrinfo:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise EtimsNotConfigured("The eTIMS API host resolves to a private address.")


def _post_json(
    url: str,
    *,
    body=None,
    headers: dict | None = None,
    basic_auth: tuple[str, str] | None = None,
    timeout: int = 8,
) -> tuple[int, object]:
    _validate_endpoint(url)
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, method="POST")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if basic_auth:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")
    for key, value in (headers or {}).items():
        if value:
            request.add_header(key, str(value))
    opener = build_opener(_NoRedirect(), HTTPSHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
            status = response.status
    except HTTPError as error:
        raw = error.read(RESPONSE_LIMIT + 1)
        status = error.code
    except (URLError, TimeoutError, OSError) as error:
        raise EtimsTransportError("The eTIMS endpoint is unreachable.") from error
    if len(raw) > RESPONSE_LIMIT:
        raise EtimsTransportError("The eTIMS response was too large.")
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return status, {}
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        if status >= 400:
            return status, {"error": text[:500]}
        raise EtimsTransportError("The eTIMS endpoint returned a non-JSON response.")


def _find_value(data, *names, depth=0):
    if depth > 5 or not isinstance(data, (dict, list)):
        return None
    if isinstance(data, list):
        for row in data:
            found = _find_value(row, *names, depth=depth + 1)
            if found not in (None, ""):
                return found
        return None
    lowered = {str(key).lower(): value for key, value in data.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    for value in data.values():
        found = _find_value(value, *names, depth=depth + 1)
        if found not in (None, ""):
            return found
    return None


def _result_code(data) -> str:
    return str(_find_value(data, "resultCd", "resultCode", "code") or "").strip()


def _result_message(data) -> str:
    return str(_find_value(data, "resultMsg", "resultMessage", "message", "error") or "").strip()


def _result_ok(data) -> bool:
    return _result_code(data) in {"000", "0", "00", "200"} or bool(
        _find_value(data, "rcptNo", "rcptNo".lower())
    )


def _auth_headers(config: EtimsConfig, token: str, *, include_device: bool = False) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "tin": config.tin,
        "bhfId": config.branch_code,
    }
    if include_device and config.cmc_key:
        headers["cmcKey"] = config.cmc_key
    return headers


def get_access_token(config: EtimsConfig, *, force: bool = False) -> str:
    key = _token_cache_key(config)
    with TOKEN_LOCK:
        cached = TOKEN_PATH_CACHE.get(key)
        if cached and not force and cached[1] > time.time() + 60:
            return cached[0]
    status, data = _post_json(
        f"{config.base_url}{AUTH_PATH}?grant_type=client_credentials",
        basic_auth=(config.username, config.password),
        timeout=config.timeout,
    )
    token = _find_value(data, "access_token", "accessToken", "token")
    if status >= 400 or not token:
        raise EtimsTransportError("KRA rejected the eTIMS API credentials.")
    try:
        expires_in = int(_find_value(data, "expires_in", "expiresIn") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    with TOKEN_LOCK:
        TOKEN_PATH_CACHE[key] = (str(token), time.time() + max(120, expires_in - 60))
    return str(token)


def _api_call(config: EtimsConfig, path: str, payload, *, include_device=False, retried=False):
    token = get_access_token(config)
    status, data = _post_json(
        f"{config.base_url}{path}",
        body=payload,
        headers=_auth_headers(config, token, include_device=include_device),
        timeout=config.timeout,
    )
    if status == 401 and not retried:
        get_access_token(config, force=True)
        return _api_call(config, path, payload, include_device=include_device, retried=True)
    return status, data


def initialize_device(config: EtimsConfig) -> EtimsConfig:
    status, data = _api_call(
        config,
        DEVICE_INIT_PATH,
        {
            "tin": config.tin,
            "bhfId": config.branch_code,
            "dvcSrlNo": config.device_serial,
        },
    )
    if status >= 400:
        raise EtimsTransportError("KRA could not initialize this eTIMS device.")
    if not _result_ok(data):
        message = _result_message(data) or "KRA refused the device initialization."
        raise EtimsRejected(f"eTIMS device initialization failed: {message}", code=_result_code(data), response=data)
    key = _find_value(data, "cmcKey", "cmc_key", "communicationKey")
    if not key:
        raise EtimsTransportError("KRA did not return a device key.")
    _store_device_key(str(key))
    return replace(config, cmc_key=str(key))


def submit_sales(config: EtimsConfig, payload: dict) -> dict:
    if not config.cmc_key:
        config = initialize_device(config)
    status, data = _api_call(config, SALES_PATH, payload, include_device=True)
    if status >= 400:
        raise EtimsTransportError(f"KRA returned HTTP {status} for the receipt.")
    if not _result_ok(data):
        message = _result_message(data) or f"KRA rejected receipt {payload.get('invcNo')}."
        raise EtimsRejected(message[:500], code=_result_code(data), response=data)
    return {
        "receipt_number": str(_find_value(data, "rcptNo", "receiptNo") or ""),
        "internal_data": str(_find_value(data, "intrlData", "internalData") or ""),
        "receipt_signature": str(_find_value(data, "rcptSign", "receiptSignature") or ""),
        "qr_url": str(_find_value(data, "qrCodeUrl", "qrUrl", "qrCode") or ""),
        "code": _result_code(data),
        "raw": data,
    }


def test_connection(config: EtimsConfig | None = None) -> tuple[bool, str]:
    config = config or get_config()
    if not config.enabled:
        return False, "Enable eTIMS before running the connection test."
    if config.missing_fields:
        return False, "Missing: " + ", ".join(config.missing_fields) + "."
    try:
        get_access_token(config, force=True)
        initialize_device(config)
    except EtimsError as error:
        return False, str(error)
    return True, "KRA authentication and device initialization succeeded."


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------
def _tax_code(rate) -> str:
    value = Decimal(str(rate or 0))
    if value == Decimal("16.00"):
        return "A"
    if value == Decimal("8.00"):
        return "B"
    if value == 0:
        return "D"
    return "A"


def _money(value) -> float:
    return float((value or Decimal("0.00")).quantize(Decimal("0.01")))


def _client_name(sale) -> str:
    if sale.client_id:
        try:
            return sale.client.company_name
        except Exception:
            pass
    return sale.customer_name or "Walk-in customer"


def _method_code(code: str) -> str:
    from apps.pos.models import PosPaymentMethod

    method = PosPaymentMethod.objects.filter(code=code).first()
    return (method.etims_code if method else "") or "01"


def _payment_code(sale) -> str:
    codes = []
    for payment in sale.payments.all():
        code = _method_code(payment.method)
        if code and code not in codes:
            codes.append(code)
    if not codes:
        return "01"
    if len(codes) == 1:
        return codes[0]
    return "03"


def build_pos_payload(sale, config: EtimsConfig, invoice_no: int | None = None) -> dict:
    items = list(sale.items.select_related("product"))
    discount = Decimal(sale.discount_amount or 0)
    gross = (Decimal(sale.subtotal or 0) + Decimal(sale.tax_amount or 0)) or Decimal("1.00")
    ratio = discount / gross if discount else Decimal("0")
    rows = []
    discount_left = discount
    for index, item in enumerate(items, start=1):
        line_gross = Decimal(item.line_total or 0) + (
            Decimal(item.line_total or 0) * Decimal(item.tax_rate or 0) / Decimal("100")
        )
        if ratio and index == len(items):
            line_discount = max(discount_left, Decimal("0"))
        else:
            line_discount = (line_gross * ratio).quantize(Decimal("0.01"))
            discount_left -= line_discount
        supply = (Decimal(item.line_total or 0) - line_discount).quantize(Decimal("0.01"))
        product = item.product
        rows.append(
            {
                "itemSeq": index,
                "itemCd": (getattr(product, "product_code", "") or f"MISC-{item.id}")[:20],
                "itemNm": item.description[:200],
                "qty": float(item.quantity),
                "prc": _money(item.unit_price),
                "splyAmt": _money(supply),
                "dcAmt": _money(line_discount),
                "taxTyCd": _tax_code(item.tax_rate),
                "taxAmt": _money(supply * (item.tax_rate or 0) / Decimal("100")),
            }
        )
    tax_total = Decimal(sale.tax_amount or 0)
    taxable = max(Decimal(sale.subtotal or 0) - discount, Decimal("0"))
    customer_tin = ""
    if sale.client_id:
        try:
            customer_tin = (sale.client.tax_id or "").strip()
        except Exception:
            customer_tin = ""
    return {
        "tin": config.tin,
        "bhfId": config.branch_code,
        "invcNo": int(invoice_no or sale.id),
        "orgInvcNo": 0,
        "custTin": customer_tin,
        "custNm": _client_name(sale)[:100],
        "salesTyCd": "N",
        "rcptTyCd": "S",
        "pmtTyCd": _payment_code(sale),
        "salesSttsCd": "02",
        "salesDt": timezone.localtime(sale.created_at).strftime("%Y%m%d%H%M%S"),
        "stockRlsDt": timezone.localtime(sale.created_at).strftime("%Y%m%d%H%M%S"),
        "totItemCnt": len(rows),
        "totTaxblAmt": _money(taxable),
        "totTaxAmt": _money(tax_total),
        "totDcAmt": _money(discount),
        "totAmt": _money(sale.total_amount),
        "prchrAcptcYn": "N",
        "remark": f"POS {sale.sale_number}"[:200],
        "salesTrnsItems": rows,
    }


def queue_pos_sale(sale, *, actor_id=None):
    """Create the immutable submission row. Never performs a network call."""
    from apps.core.models import EtimsSubmission

    config = get_config()
    if not config.enabled:
        return None
    existing = EtimsSubmission.objects.filter(
        document_type="pos_sale", document_number=sale.sale_number
    ).first()
    if existing is not None:
        return existing
    invoice_no = next_number("etims")
    submission, _ = EtimsSubmission.objects.get_or_create(
        document_type="pos_sale",
        document_number=sale.sale_number,
        defaults={
            "tenant": sale.tenant,
            "pos_sale": sale,
            "etims_invoice_no": invoice_no,
            "status": "pending",
            "next_attempt_at": timezone.now(),
            "payload": build_pos_payload(sale, config, invoice_no),
            "created_by": actor_id,
            "created_at": timezone.now(),
            "updated_at": timezone.now(),
        },
    )
    return submission


def build_refund_payload(refund, config: EtimsConfig, invoice_no: int, original_invoice_no: int) -> dict:
    sale = refund.sale
    discount = Decimal(refund.discount_amount or 0)
    items = list(refund.items.select_related("sale_item__product"))
    gross_total = (Decimal(refund.subtotal or 0) + Decimal(refund.tax_amount or 0)) or Decimal("1.00")
    ratio = discount / gross_total if discount else Decimal("0")
    discount_left = discount
    rows = []
    for index, item in enumerate(items, start=1):
        product = item.sale_item.product if item.sale_item_id else None
        line_gross = Decimal(item.line_total) + (
            Decimal(item.line_total) * Decimal(item.tax_rate or 0) / Decimal("100")
        )
        if ratio and index == len(items):
            line_discount = max(discount_left, Decimal("0"))
        else:
            line_discount = (line_gross * ratio).quantize(Decimal("0.01"))
            discount_left -= line_discount
        supply = (Decimal(item.line_total) - line_discount).quantize(Decimal("0.01"))
        rows.append(
            {
                "itemSeq": index,
                "itemCd": (getattr(product, "product_code", "") or f"MISC-{item.id}")[:20],
                "itemNm": item.description[:200],
                "qty": float(item.quantity),
                "prc": _money(item.unit_price),
                "splyAmt": _money(supply),
                "dcAmt": _money(line_discount),
                "taxTyCd": _tax_code(item.tax_rate),
                "taxAmt": _money(supply * (item.tax_rate or 0) / Decimal("100")),
            }
        )
    customer_tin = ""
    if sale.client_id:
        try:
            customer_tin = (sale.client.tax_id or "").strip()
        except Exception:
            customer_tin = ""
    local = timezone.localtime(refund.created_at)
    return {
        "tin": config.tin,
        "bhfId": config.branch_code,
        "invcNo": int(invoice_no),
        "orgInvcNo": int(original_invoice_no),
        "custTin": customer_tin,
        "custNm": _client_name(sale)[:100],
        "salesTyCd": "N",
        "rcptTyCd": "R",
        "pmtTyCd": _method_code(refund.method),
        "salesSttsCd": "02",
        "salesDt": local.strftime("%Y%m%d%H%M%S"),
        "stockRlsDt": local.strftime("%Y%m%d%H%M%S"),
        "totItemCnt": len(rows),
        "totTaxblAmt": _money(max(Decimal(refund.subtotal or 0) - discount, Decimal("0"))),
        "totTaxAmt": _money(refund.tax_amount),
        "totDcAmt": _money(discount),
        "totAmt": _money(refund.total_amount),
        "prchrAcptcYn": "N",
        "remark": f"Refund {refund.refund_number} of {sale.sale_number}: {refund.reason}"[:200],
        "salesTrnsItems": rows,
    }


def queue_refund(refund, *, actor_id=None):
    """Queue a KRA credit note tied to the original POS receipt."""
    from apps.core.models import EtimsSubmission

    config = get_config()
    if not config.enabled:
        return None
    original = (
        EtimsSubmission.objects.filter(
            pos_sale=refund.sale, document_type="pos_sale"
        )
        .exclude(status="cancelled")
        .order_by("id")
        .first()
    )
    if original is None or not original.etims_invoice_no:
        return None
    existing = EtimsSubmission.objects.filter(
        document_type="credit_note", document_number=refund.refund_number
    ).first()
    if existing is not None:
        return existing
    invoice_no = next_number("etims")
    submission, _ = EtimsSubmission.objects.get_or_create(
        document_type="credit_note",
        document_number=refund.refund_number,
        defaults={
            "tenant": refund.tenant,
            "pos_sale": refund.sale,
            "pos_refund": refund,
            "etims_invoice_no": invoice_no,
            "status": "pending",
            "next_attempt_at": timezone.now(),
            "payload": build_refund_payload(
                refund, config, invoice_no, original.etims_invoice_no
            ),
            "created_by": actor_id,
            "created_at": timezone.now(),
            "updated_at": timezone.now(),
        },
    )
    return submission


def cancel_pending_for_sale(sale, reason: str = "Sale voided") -> int:
    from apps.core.models import EtimsSubmission

    return EtimsSubmission.objects.filter(
        pos_sale=sale, status__in=["pending", "processing", "failed"]
    ).update(
        status="cancelled",
        last_error=reason[:1000],
        next_attempt_at=None,
        updated_at=timezone.now(),
    )


def _dispatch(submission_id: int, tenant_id: int) -> None:
    from django.db import connections

    def run():
        try:
            submit_after_commit(submission_id, tenant_id)
        finally:
            connections.close_all()

    threading.Thread(
        target=run, name=f"etims-submit-{submission_id}", daemon=True
    ).start()


def _schedule(submission) -> None:
    if submission is None or not get_config().auto_submit:
        return
    submission_id = submission.pk
    tenant_id = submission.tenant_id

    def dispatch():
        _dispatch(submission_id, tenant_id)

    from django.db import transaction

    transaction.on_commit(dispatch)


def enqueue_sale(sale, *, actor_id=None):
    """Queue the receipt and, when enabled, transmit it without blocking checkout."""
    submission = queue_pos_sale(sale, actor_id=actor_id)
    _schedule(submission)
    return submission


def enqueue_refund(refund, *, actor_id=None):
    submission = queue_refund(refund, actor_id=actor_id)
    _schedule(submission)
    return submission


def _backoff(attempts: int) -> timedelta:
    seconds = min(60 * (2 ** max(attempts - 1, 0)), 3600)
    return timedelta(seconds=seconds)


def _mark_retry(submission, message: str, *, code: str = "", response=None, permanent=False):
    from apps.core.models import EtimsSubmission

    attempts = submission.attempts or 0
    exhausted = permanent or attempts >= MAX_ATTEMPTS
    EtimsSubmission.objects.filter(pk=submission.pk).update(
        status="rejected" if permanent else "failed",
        last_error=(message or "eTIMS transmission failed.")[:1000],
        response_code=(code or "")[:20],
        response=response or {},
        next_attempt_at=None if exhausted else timezone.now() + _backoff(attempts),
        updated_at=timezone.now(),
    )


def submit_submission(submission_id: int) -> str:
    """Claim and transmit one queued submission. Returns the final status."""
    from apps.core.models import EtimsSubmission

    config = get_config()
    submission = EtimsSubmission.objects.filter(pk=submission_id).first()
    if submission is None or submission.status in {"submitted", "cancelled"}:
        return submission.status if submission else "missing"
    if not config.enabled:
        EtimsSubmission.objects.filter(pk=submission.pk).update(
            status="cancelled",
            last_error="eTIMS is disabled for this workspace.",
            next_attempt_at=None,
            updated_at=timezone.now(),
        )
        return "cancelled"
    if config.missing_fields:
        message = "Awaiting eTIMS credentials: " + ", ".join(config.missing_fields) + "."
        EtimsSubmission.objects.filter(pk=submission.pk).update(
            status="pending",
            last_error=message[:1000],
            next_attempt_at=timezone.now() + timedelta(minutes=10),
            updated_at=timezone.now(),
        )
        return "pending"

    if submission.document_type == "credit_note" and submission.pos_sale_id:
        original = (
            EtimsSubmission.objects.filter(
                pos_sale_id=submission.pos_sale_id, document_type="pos_sale"
            )
            .exclude(pk=submission.pk)
            .order_by("id")
            .first()
        )
        if original is None or original.status != "submitted":
            EtimsSubmission.objects.filter(pk=submission.pk).update(
                status="pending",
                last_error="Waiting for the original receipt to be accepted by KRA.",
                next_attempt_at=timezone.now() + timedelta(minutes=2),
                updated_at=timezone.now(),
            )
            return "pending"

    now = timezone.now()
    claimed = EtimsSubmission.objects.filter(
        pk=submission.pk, status__in=["pending", "failed"]
    ).update(
        status="processing",
        attempts=(submission.attempts or 0) + 1,
        updated_at=now,
    )
    if not claimed:
        return "processing"
    submission.refresh_from_db()
    try:
        result = submit_sales(config, submission.payload or {})
    except EtimsRejected as error:
        _mark_retry(
            submission, str(error), code=error.code, response=error.response, permanent=True
        )
        logger.warning("eTIMS rejected %s: %s", submission.document_number, error)
        return "rejected"
    except EtimsError as error:
        _mark_retry(submission, str(error))
        logger.warning("eTIMS transport failed for %s: %s", submission.document_number, error)
        return "failed"
    except Exception:
        _mark_retry(submission, "Unexpected eTIMS transmission error.")
        logger.exception("Unexpected eTIMS failure for %s", submission.document_number)
        return "failed"

    EtimsSubmission.objects.filter(pk=submission.pk).update(
        status="submitted",
        submitted_at=timezone.now(),
        updated_at=timezone.now(),
        kra_receipt_number=result["receipt_number"][:50],
        kra_internal_data=result["internal_data"][:2000],
        kra_receipt_signature=result["receipt_signature"][:4000],
        kra_qr_url=result["qr_url"][:2000],
        response_code=result["code"][:20],
        response=result["raw"] if isinstance(result["raw"], (dict, list)) else {},
        last_error="",
        next_attempt_at=None,
    )
    return "submitted"


def submit_after_commit(submission_id: int, tenant_id: int) -> None:
    """Best-effort synchronous send after a checkout commits."""
    try:
        from apps.saas.models import Tenant
        from apps.core.tenancy import tenant_context

        tenant = Tenant.objects.filter(pk=tenant_id).first()
        if tenant is None:
            return
        with tenant_context(tenant):
            submit_submission(submission_id)
    except Exception:
        logger.exception("eTIMS post-commit submission failed for #%s", submission_id)


def recover_stale(now=None) -> int:
    from apps.core.models import EtimsSubmission

    now = now or timezone.now()
    cutoff = now - timedelta(minutes=STALE_PROCESSING_MINUTES)
    return EtimsSubmission.objects.filter(
        status="processing", updated_at__lt=cutoff
    ).update(
        status="failed",
        last_error="The worker stopped before KRA replied; queued for retry.",
        next_attempt_at=now,
        updated_at=now,
    )


def process_due(limit: int = 20) -> dict:
    """Submit queued receipts for the active workspace."""
    from django.db.models import Q

    from apps.core.models import EtimsSubmission

    recover_stale()
    now = timezone.now()
    due_ids = list(
        EtimsSubmission.objects.filter(
            Q(status="pending", next_attempt_at__isnull=True)
            | Q(status="pending", next_attempt_at__lte=now)
            | Q(status="failed", next_attempt_at__lte=now),
            document_type__in=["pos_sale", "credit_note"],
        )
        .order_by("created_at", "id")
        .values_list("id", flat=True)[: max(1, min(limit, 200))]
    )
    counts = {"submitted": 0, "failed": 0, "pending": 0, "cancelled": 0}
    for submission_id in due_ids:
        status = submit_submission(submission_id)
        if status in counts:
            counts[status] += 1
    return counts


def retry_submission(submission) -> None:
    from apps.core.models import EtimsSubmission

    EtimsSubmission.objects.filter(pk=submission.pk).update(
        status="pending",
        next_attempt_at=timezone.now(),
        last_error="",
        updated_at=timezone.now(),
    )


def pending_for_sale(sale):
    from apps.core.models import EtimsSubmission

    return EtimsSubmission.objects.filter(pos_sale=sale).order_by("-id").first()


__all__ = [
    "EtimsConfig",
    "EtimsError",
    "EtimsNotConfigured",
    "EtimsRejected",
    "EtimsTransportError",
    "build_pos_payload",
    "cancel_pending_for_sale",
    "enqueue_refund",
    "enqueue_sale",
    "get_config",
    "process_due",
    "queue_pos_sale",
    "reset_token_cache",
    "retry_submission",
    "submit_after_commit",
    "submit_submission",
    "test_connection",
]
