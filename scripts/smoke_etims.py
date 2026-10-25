"""eTIMS/KRA OSCU smoke test with a fake transport (rolled back at the end)."""

import json
import os
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient
from django.utils import timezone

from apps.core.crypto import decrypt_secret
from apps.core.etims import (
    AUTH_PATH,
    DEVICE_INIT_PATH,
    MAX_ATTEMPTS,
    SALES_PATH,
    EtimsNotConfigured,
    EtimsTransportError,
    _validate_endpoint,
    enqueue_sale,
    get_config,
    process_due,
    reset_token_cache,
    retry_submission,
    submit_submission,
    test_connection,
)
from apps.core.models import CompanySetting, EtimsSubmission, Product
from apps.core.tenancy import default_tenant, tenant_context
from apps.pos.models import PosSale, PosSession, PosRefund

ZERO = Decimal("0.00")
SALES_OK = {
    "resultCd": "000",
    "resultMsg": "Success",
    "rcptNo": 987654321,
    "intrlData": "internal-data",
    "rcptSign": "signature-value",
    "qrCodeUrl": "https://kra.example/verify/987654321",
}


class Rollback(Exception):
    pass


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")
tenant = default_tenant()
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


class FakeKra:
    def __init__(self, *, sales_response=None, sales_error=None):
        self.calls = []
        self.sales_response = sales_response or dict(SALES_OK)
        self.sales_error = sales_error

    def __call__(self, url, *, body=None, headers=None, basic_auth=None, timeout=8):
        self.calls.append(url)
        if url.endswith(AUTH_PATH + "?grant_type=client_credentials"):
            return 200, {"access_token": "fake-token", "expires_in": 3600}
        if url.endswith(DEVICE_INIT_PATH):
            return 200, {"resultCd": "000", "resultMsg": "OK", "cmcKey": "device-key-123"}
        if url.endswith(SALES_PATH):
            if self.sales_error:
                raise self.sales_error
            return 200, dict(self.sales_response)
        return 404, {"error": "unknown path"}

    def count(self, suffix):
        return len([url for url in self.calls if url.endswith(suffix)])


def checkout(session_id, *, items, payments, discount="0.00"):
    response = client.post(
        "/pos/checkout",
        data=json.dumps(
            {
                "session_id": session_id,
                "items": items,
                "payments": payments,
                "discount": discount,
            }
        ),
        content_type="application/json",
    )
    payload = response.json()
    assert response.status_code == 200 and payload.get("ok"), payload
    return payload


def sale_submission(sale_id):
    return EtimsSubmission.objects.get(
        document_type="pos_sale", pos_sale_id=sale_id
    )


try:
    with transaction.atomic():
        with tenant_context(tenant):
            PosSession.objects.filter(status="open").update(
                status="closed", closed_at=timezone.now(), closed_by=1
            )
            EtimsSubmission.objects.exclude(status="submitted").update(
                status="cancelled", next_attempt_at=None
            )

        # --- Settings UI saves credentials encrypted ---------------------
        response = client.get("/settings/etims")
        check("GET settings etims", response.status_code == 200, response.status_code)
        response = client.post(
            "/settings/etims",
            data={
                "etims_enabled": "on",
                "etims_api_url": "https://etims.test.example",
                "etims_tin": "P051234567X",
                "etims_branch_code": "00",
                "etims_device_serial": "SORA-TEST-01",
                "etims_api_key": "kra-api-user",
                "etims_api_secret": "super-secret-pass",
                "etims_timeout": "8",
            },
        )
        check("POST settings etims", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            config = get_config()
            check("config enabled", config.enabled)
            check("config complete", config.missing_fields == [], config.missing_fields)
            check("auto submit off for test", config.auto_submit is False)
            check("password decrypts", config.password == "super-secret-pass")
            secret_row = CompanySetting.objects.get(tenant=tenant, setting_key="etims_api_secret")
            check("secret encrypted at rest", secret_row.setting_value.startswith("enc:v1:"))
            check(
                "plaintext secret absent",
                "super-secret-pass" not in secret_row.setting_value,
            )

        # --- Session and first sale --------------------------------------
        response = client.post(
            "/pos/sessions/open",
            data={
                "terminal_name": "eTIMS Till",
                "opening_float": "1000.00",
                "notes": "",
                "next": "/pos/",
            },
        )
        check("POST session open", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            session = PosSession.objects.filter(status="open", terminal_name="eTIMS Till").first()
            water = Product.objects.get(product_code="POS-1001")
            service = Product.objects.get(product_code="POS-1007")
            session_id = session.id

        sale_a = checkout(
            session_id,
            items=[
                {"product_id": water.id, "quantity": 2},
                {"product_id": service.id, "quantity": 1},
            ],
            payments=[{"method": "cash", "amount": "700.00"}],
            discount="62.00",
        )
        check("checkout queues receipt", sale_a["total"] == "647.28", sale_a)
        with tenant_context(tenant):
            sub_a = sale_submission(sale_a["sale_id"])
            payload = sub_a.payload
            check("submission pending", sub_a.status == "pending")
            check("eTIMS invoice number allocated", bool(sub_a.etims_invoice_no))
            check("payload tin", payload.get("tin") == "P051234567X")
            check("payload branch", payload.get("bhfId") == "00")
            check("payload receipt type", payload.get("rcptTyCd") == "S", payload.get("rcptTyCd"))
            check("payload payment code", payload.get("pmtTyCd") == "01", payload.get("pmtTyCd"))
            check("payload taxable", payload.get("totTaxblAmt") == 558.0, payload.get("totTaxblAmt"))
            check("payload tax", payload.get("totTaxAmt") == 89.28, payload.get("totTaxAmt"))
            check("payload discount", payload.get("totDcAmt") == 62.0, payload.get("totDcAmt"))
            check("payload total", payload.get("totAmt") == 647.28, payload.get("totAmt"))
            check("payload item count", payload.get("totItemCnt") == 2)
            check(
                "payload item tax code",
                all(row["taxTyCd"] == "A" for row in payload["salesTrnsItems"]),
            )
            check(
                "payload supply sums",
                round(sum(row["splyAmt"] for row in payload["salesTrnsItems"]), 2) == 558.0,
            )
            check("payload has no secrets", "super-secret-pass" not in json.dumps(payload))
            sale_a_obj = PosSale.objects.get(pk=sale_a["sale_id"])
            again = enqueue_sale(sale_a_obj)
            check("queue idempotent", again.id == sub_a.id)
            check(
                "one submission per sale",
                EtimsSubmission.objects.filter(pos_sale_id=sale_a["sale_id"]).count() == 1,
            )

        reset_token_cache()
        kra = FakeKra()
        with patch("apps.core.etims._post_json", kra):
            with tenant_context(tenant):
                status = submit_submission(sub_a.id)
        check("submit success", status == "submitted", status)
        check("auth called once", kra.count(AUTH_PATH + "?grant_type=client_credentials") == 1)
        check("device initialized once", kra.count(DEVICE_INIT_PATH) == 1)
        check("sales transmitted once", kra.count(SALES_PATH) == 1)
        with tenant_context(tenant):
            sub_a.refresh_from_db()
            check("kra receipt stored", sub_a.kra_receipt_number == "987654321", sub_a.kra_receipt_number)
            check("kra signature stored", sub_a.kra_receipt_signature == "signature-value")
            check("kra qr stored", sub_a.kra_qr_url == SALES_OK["qrCodeUrl"])
            check("attempts counted", sub_a.attempts == 1, sub_a.attempts)
            check("no retry scheduled", sub_a.next_attempt_at is None)
            device_row = CompanySetting.objects.get(tenant=tenant, setting_key="etims_cmc_key")
            check("device key encrypted", device_row.setting_value.startswith("enc:v1:"))
            check("device key decrypts", decrypt_secret(device_row.setting_value) == "device-key-123")
            check("config picks device key", get_config().cmc_key == "device-key-123")

        response = client.get(f"/pos/sales/{sale_a['sale_id']}")
        html = response.content.decode()
        check("receipt shows kra block", "KRA eTIMS Receipt" in html)
        check("receipt shows kra number", "987654321" in html)
        check("receipt renders qr", "data:image/png;base64," in html)

        # --- Refund issues a credit note tied to the original -------------
        with tenant_context(tenant):
            water_item = PosSale.objects.get(pk=sale_a["sale_id"]).items.get(product_id=water.id)
            water_item_id = water_item.id
        response = client.post(
            f"/pos/sales/{sale_a['sale_id']}/refund",
            data={
                "method": "cash",
                f"qty_{water_item_id}": "1",
                f"restock_{water_item_id}": "on",
                "reason": "Cracked bottle",
            },
        )
        check("POST refund", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            refund_a = PosRefund.objects.filter(sale_id=sale_a["sale_id"]).first()
            note = EtimsSubmission.objects.get(
                document_type="credit_note", document_number=refund_a.refund_number
            )
            check("credit note queued", note.status == "pending")
            check("credit note type", note.payload.get("rcptTyCd") == "R")
            check(
                "credit note origin invoice",
                note.payload.get("orgInvcNo") == sub_a.etims_invoice_no,
                note.payload.get("orgInvcNo"),
            )
            check("credit note total", note.payload.get("totAmt") == 62.64, note.payload.get("totAmt"))
            check("credit note uses new number", note.etims_invoice_no != sub_a.etims_invoice_no)
            note_id = note.id

        kra_note = FakeKra()
        with patch("apps.core.etims._post_json", kra_note):
            with tenant_context(tenant):
                status = submit_submission(note_id)
        check("credit note submitted", status == "submitted", status)
        check("token cached across sends", kra_note.count(AUTH_PATH + "?grant_type=client_credentials") == 0)
        check("device init not repeated", kra_note.count(DEVICE_INIT_PATH) == 0)
        check("credit note transmitted", kra_note.count(SALES_PATH) == 1)
        response = client.get(f"/pos/refunds/{refund_a.id}")
        check("GET credit note receipt", response.status_code == 200, response.status_code)

        # --- Transient failure retries and manual recovery ----------------
        sale_b = checkout(
            session_id,
            items=[{"product_id": water.id, "quantity": 1}],
            payments=[{"method": "cash", "amount": "69.60"}],
        )
        with tenant_context(tenant):
            sub_b = sale_submission(sale_b["sale_id"])
            sub_b_id = sub_b.id
        kra_fail = FakeKra(sales_error=EtimsTransportError("KRA unavailable"))
        with patch("apps.core.etims._post_json", kra_fail):
            with tenant_context(tenant):
                status = submit_submission(sub_b_id)
                sub_b.refresh_from_db()
        check("transport failure marks failed", status == "failed", status)
        check("retry scheduled", sub_b.next_attempt_at is not None)
        check("attempts increment", sub_b.attempts == 1, sub_b.attempts)
        check("failure recorded", "unavailable" in sub_b.last_error, sub_b.last_error)

        kra_early = FakeKra()
        with patch("apps.core.etims._post_json", kra_early):
            with tenant_context(tenant):
                counts = process_due()
        check("future retry not due", kra_early.count(SALES_PATH) == 0, kra_early.calls)
        check("due run empty", counts == {"submitted": 0, "failed": 0, "pending": 0, "cancelled": 0}, counts)

        kra_retry = FakeKra()
        with patch("apps.core.etims._post_json", kra_retry):
            with tenant_context(tenant):
                retry_submission(sub_b)
                status = submit_submission(sub_b_id)
        check("manual retry succeeds", status == "submitted", status)

        # --- Permanent rejection stops automatic retries ------------------
        sale_c = checkout(
            session_id,
            items=[{"product_id": water.id, "quantity": 1}],
            payments=[{"method": "cash", "amount": "69.60"}],
        )
        with tenant_context(tenant):
            sub_c_id = sale_submission(sale_c["sale_id"]).id
        kra_reject = FakeKra(
            sales_response={"resultCd": "902", "resultMsg": "Invalid item code"}
        )
        with patch("apps.core.etims._post_json", kra_reject):
            with tenant_context(tenant):
                status = submit_submission(sub_c_id)
                sub_c = EtimsSubmission.objects.get(pk=sub_c_id)
        check("rejection marks rejected", status == "rejected", status)
        check("rejected status stored", sub_c.status == "rejected")
        check("rejected has no retry time", sub_c.next_attempt_at is None)
        check("rejected error stored", "Invalid item code" in sub_c.last_error, sub_c.last_error)
        check("rejected code stored", sub_c.response_code == "902", sub_c.response_code)

        kra_skip = FakeKra()
        with patch("apps.core.etims._post_json", kra_skip):
            with tenant_context(tenant):
                process_due()
                sub_c.refresh_from_db()
        check("rejected not retried by worker", kra_skip.count(SALES_PATH) == 0, kra_skip.calls)
        check("rejected remains rejected", sub_c.status == "rejected" and sub_c.attempts == 1)
        response = client.get("/settings/etims")
        check(
            "settings shows rejected row",
            "Rejected by KRA" in response.content.decode(),
        )

        kra_manual = FakeKra()
        with patch("apps.core.etims._post_json", kra_manual):
            with tenant_context(tenant):
                retry_submission(sub_c)
                status = submit_submission(sub_c_id)
        check("manual retry of rejected", status == "submitted", status)

        # --- Exhausted transient failures stay final ----------------------
        sale_d = checkout(
            session_id,
            items=[{"product_id": water.id, "quantity": 1}],
            payments=[{"method": "cash", "amount": "69.60"}],
        )
        with tenant_context(tenant):
            sub_d = sale_submission(sale_d["sale_id"])
            sub_d_id = sub_d.id
            EtimsSubmission.objects.filter(pk=sub_d_id).update(attempts=MAX_ATTEMPTS - 1)
        kra_last = FakeKra(sales_error=EtimsTransportError("still down"))
        with patch("apps.core.etims._post_json", kra_last):
            with tenant_context(tenant):
                status = submit_submission(sub_d_id)
                sub_d.refresh_from_db()
        check("exhausted attempt fails", status == "failed", status)
        check("exhausted attempt count", sub_d.attempts == MAX_ATTEMPTS, sub_d.attempts)
        check("exhausted has no retry time", sub_d.next_attempt_at is None)
        kra_after = FakeKra()
        with patch("apps.core.etims._post_json", kra_after):
            with tenant_context(tenant):
                process_due()
                sub_d.refresh_from_db()
        check("exhausted not retried by worker", kra_after.count(SALES_PATH) == 0, kra_after.calls)
        check("exhausted remains failed", sub_d.status == "failed")

        # --- Credit note waits for the original receipt -------------------
        sale_e = checkout(
            session_id,
            items=[{"product_id": water.id, "quantity": 1}],
            payments=[{"method": "cash", "amount": "69.60"}],
        )
        with tenant_context(tenant):
            water_item_e = PosSale.objects.get(pk=sale_e["sale_id"]).items.get(product_id=water.id)
            sub_e = sale_submission(sale_e["sale_id"])
            sub_e_id = sub_e.id
        response = client.post(
            f"/pos/sales/{sale_e['sale_id']}/refund",
            data={
                "method": "cash",
                f"qty_{water_item_e.id}": "1",
                f"restock_{water_item_e.id}": "on",
                "reason": "Wrong flavour",
            },
        )
        check("POST refund before submit", response.status_code == 302, response.status_code)
        with tenant_context(tenant):
            note_e = EtimsSubmission.objects.get(
                document_type="credit_note", pos_sale_id=sale_e["sale_id"]
            )
            note_e_id = note_e.id
        kra_wait = FakeKra()
        with patch("apps.core.etims._post_json", kra_wait):
            with tenant_context(tenant):
                status = submit_submission(note_e_id)
                note_e.refresh_from_db()
        check("credit note waits for original", status == "pending", status)
        check("credit note not transmitted early", kra_wait.count(SALES_PATH) == 0)
        check("credit note waiting message", "Waiting for the original" in note_e.last_error)
        check("credit note retry scheduled", note_e.next_attempt_at is not None)

        kra_orig = FakeKra()
        with patch("apps.core.etims._post_json", kra_orig):
            with tenant_context(tenant):
                status = submit_submission(sub_e_id)
        check("original submitted after wait", status == "submitted", status)
        kra_note2 = FakeKra()
        with patch("apps.core.etims._post_json", kra_note2):
            with tenant_context(tenant):
                retry_submission(note_e)
                status = submit_submission(note_e_id)
        check("credit note submitted after original", status == "submitted", status)

        # --- Transport hardening ------------------------------------------
        try:
            _validate_endpoint("https://10.0.0.5/etims")
            private_blocked = False
        except EtimsNotConfigured:
            private_blocked = True
        check("private endpoint blocked", private_blocked)
        try:
            _validate_endpoint("http://etims.example/")
            insecure_blocked = False
        except EtimsNotConfigured:
            insecure_blocked = True
        check("plain http endpoint blocked", insecure_blocked)
        try:
            _validate_endpoint("https://user:pass@etims.example/")
            creds_blocked = False
        except EtimsNotConfigured:
            creds_blocked = True
        check("credentials in url blocked", creds_blocked)

        reset_token_cache()
        kra_test = FakeKra()
        with patch("apps.core.etims._post_json", kra_test):
            with tenant_context(tenant):
                ok, message = test_connection()
        check("connection test succeeds", ok is True, message)

        response = client.get("/settings/etims")
        check("settings page renders queue", response.status_code == 200)

        raise Rollback
except Rollback:
    pass

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
