"""Smoke-test billing gateways: manual flow, Stripe checkout and M-Pesa callbacks."""

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal
from unittest.mock import patch

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction
from django.test import Client as TestClient

from apps.saas.models import PlatformSetting, SaasPlan, TenantPayment
from apps.core.tenancy import default_tenant

failures = []


class Rollback(Exception):
    pass


def check(label, condition, detail=""):
    if condition:
        print(f"PASS {label}")
    else:
        failures.append(label)
        print(f"FAIL {label} {detail}")


def set_setting(key, value):
    PlatformSetting.objects.filter(key=key).update(value=value)


client = TestClient(headers={"host": "localhost"})
assert client.login(email="admin@sora.com", password="admin123")
tenant = default_tenant()
plan = SaasPlan.objects.get(code="business")

try:
    with transaction.atomic():
        response = client.get("/billing/")
        body = response.content.decode()
        check("GET billing", response.status_code == 200)
        check("gateways hidden by default", "Card via Stripe (pay now)" not in body)
        check("manual flow offered", "M-Pesa (submit reference for verification)" in body)

        response = client.post(
            "/billing/",
            data={
                "action": "renewal",
                "plan": plan.id,
                "billing_cycle": "monthly",
                "method": "mpesa",
                "reference": "MANUAL-REF-1",
                "phone_number": "",
                "notes": "Paid at the counter.",
            },
        )
        check("manual renewal redirects", response.status_code == 302, response.status_code)
        manual = TenantPayment.objects.order_by("-id").first()
        check(
            "manual payment pending",
            manual.status == "pending" and manual.method == "mpesa" and manual.reference == "MANUAL-REF-1",
            f"{manual.status} {manual.method}",
        )

        set_setting("stripe_enabled", "true")
        set_setting("stripe_publishable_key", "pk_test_123")
        set_setting("stripe_secret_key", "sk_test_123")
        set_setting("stripe_webhook_secret", "whsec_test_123")
        set_setting("mpesa_enabled", "true")
        set_setting("mpesa_consumer_key", "consumer-key")
        set_setting("mpesa_consumer_secret", "consumer-secret")
        set_setting("mpesa_shortcode", "174379")
        set_setting("mpesa_passkey", "passkey")
        set_setting("mpesa_callback_token", "test-callback-token")

        response = client.get("/billing/")
        body = response.content.decode()
        check("stripe option visible", "Card via Stripe (pay now)" in body)
        check("mpesa instant option visible", "M-Pesa STK push (pay now)" in body)
        check("instant payment badge", "Instant payment" in body)

        captured = {}

        def fake_mpesa(payment, *, phone, callback_url):
            captured["phone"] = phone
            captured["callback_url"] = callback_url
            payment.gateway_checkout_id = "ws_CO_TEST_7001"
            payment.save(update_fields=["gateway_checkout_id"])
            return {"checkout_request_id": "ws_CO_TEST_7001", "message": "Check your phone."}

        with patch("apps.saas.views.start_mpesa_stk", side_effect=fake_mpesa):
            response = client.post(
                "/billing/",
                data={
                    "action": "renewal",
                    "plan": plan.id,
                    "billing_cycle": "monthly",
                    "method": "mpesa_instant",
                    "phone_number": "0712 345 678",
                    "reference": "",
                    "notes": "",
                },
            )
        check("instant mpesa redirects", response.status_code == 302, response.status_code)
        instant = TenantPayment.objects.order_by("-id").first()
        check("instant mpesa pending", instant.status == "pending" and instant.method == "mpesa")
        check("phone normalized", captured.get("phone") == "254712345678", captured.get("phone"))
        check(
            "callback url tokenized",
            "/billing/gateway/mpesa/callback/" in captured.get("callback_url", "")
            and "token=test-callback-token" in captured.get("callback_url", ""),
            captured.get("callback_url"),
        )
        response = client.get("/billing/")
        check(
            "mpesa status action rendered",
            f"/billing/gateway/mpesa/status/{instant.id}/" in response.content.decode(),
        )

        callback_payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "MR-1",
                    "CheckoutRequestID": "ws_CO_TEST_7001",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": int(instant.amount)},
                            {"Name": "MpesaReceiptNumber", "Value": "QK98765432"},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]
                    },
                }
            }
        }
        response = client.post(
            "/billing/gateway/mpesa/callback/?token=wrong",
            data=json.dumps(callback_payload),
            content_type="application/json",
        )
        check("mpesa callback rejects bad token", response.status_code == 403, response.status_code)
        response = client.post(
            "/billing/gateway/mpesa/callback/?token=test-callback-token",
            data=json.dumps(callback_payload),
            content_type="application/json",
        )
        check("mpesa callback accepted", response.status_code == 200, response.status_code)
        instant.refresh_from_db()
        check(
            "mpesa completed",
            instant.status == "completed" and instant.reference == "QK98765432",
            f"{instant.status} {instant.reference}",
        )

        stripe_redirect = {}

        def fake_stripe(payment, *, success_url, cancel_url):
            stripe_redirect["success_url"] = success_url
            stripe_redirect["cancel_url"] = cancel_url
            payment.gateway_checkout_id = "cs_test_return"
            payment.save(update_fields=["gateway_checkout_id"])
            return {"id": "cs_test_return", "url": "https://checkout.stripe.com/c/pay/test"}

        with patch("apps.saas.views.start_stripe_checkout", side_effect=fake_stripe):
            response = client.post(
                "/billing/",
                data={
                    "action": "renewal",
                    "plan": plan.id,
                    "billing_cycle": "monthly",
                    "method": "stripe",
                    "phone_number": "",
                    "reference": "",
                    "notes": "",
                },
            )
        check(
            "stripe redirect",
            response.status_code == 302
            and response["Location"] == "https://checkout.stripe.com/c/pay/test",
            response.get("Location"),
        )
        stripe_payment = TenantPayment.objects.order_by("-id").first()
        check("stripe payment pending", stripe_payment.status == "pending" and stripe_payment.method == "stripe")
        response = client.get("/billing/")
        check(
            "stripe pay action rendered",
            f"/billing/gateway/stripe/start/{stripe_payment.id}/" in response.content.decode(),
        )
        check(
            "stripe success url",
            f"payment={stripe_payment.id}" in stripe_redirect.get("success_url", "")
            and "{CHECKOUT_SESSION_ID}" in stripe_redirect.get("success_url", ""),
            stripe_redirect.get("success_url"),
        )

        session = {
            "id": "cs_test_return",
            "status": "complete",
            "payment_status": "paid",
            "amount_total": int(stripe_payment.amount * 100),
            "currency": stripe_payment.currency.lower(),
            "metadata": {"payment_id": str(stripe_payment.id)},
            "payment_intent": "pi_test_123",
        }
        with patch("apps.saas.views.retrieve_stripe_session", return_value=session):
            response = client.get(
                f"/billing/gateway/stripe/return/?payment={stripe_payment.id}&session_id=cs_test_return"
            )
        check("stripe return redirects", response.status_code == 302, response.status_code)
        stripe_payment.refresh_from_db()
        check(
            "stripe completed",
            stripe_payment.status == "completed" and stripe_payment.reference == "pi_test_123",
            f"{stripe_payment.status} {stripe_payment.reference}",
        )

        webhook_payment = TenantPayment.objects.create(
            tenant=tenant,
            plan=plan,
            amount=Decimal("12000.00"),
            currency="KES",
            method="stripe",
            status="pending",
            billing_cycle="monthly",
            gateway_checkout_id="cs_test_webhook",
        )
        event = {
            "id": "evt_test",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_webhook",
                    "status": "complete",
                    "payment_status": "paid",
                    "amount_total": 1200000,
                    "currency": "kes",
                    "metadata": {"payment_id": str(webhook_payment.id)},
                    "payment_intent": "pi_webhook_1",
                }
            },
        }
        payload = json.dumps(event).encode()
        timestamp = int(time.time())
        signature = hmac.new(
            b"whsec_test_123",
            f"{timestamp}.".encode() + payload,
            hashlib.sha256,
        ).hexdigest()
        response = client.post(
            "/billing/gateway/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}",
        )
        check("stripe webhook accepted", response.status_code == 200, response.status_code)
        webhook_payment.refresh_from_db()
        check(
            "stripe webhook completed",
            webhook_payment.status == "completed" and webhook_payment.reference == "pi_webhook_1",
            webhook_payment.status,
        )
        response = client.post(
            "/billing/gateway/stripe/webhook/",
            data=payload,
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )
        check("stripe webhook rejects bad signature", response.status_code == 400, response.status_code)

        response = client.get("/billing/")
        check("billing page still renders", response.status_code == 200)

        raise Rollback
except Rollback:
    pass

print(f"{len(failures)} failure(s)")
for failure in failures:
    print(f"  - {failure}")
raise SystemExit(1 if failures else 0)
