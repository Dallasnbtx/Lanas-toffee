"""Offline test of the Stripe code path (no network): mocks Stripe API calls and signs a fake webhook.
Run:  .venv/bin/python tests/test_stripe_flow.py
"""
import os, sys, json, time, hmac, hashlib, tempfile
os.environ.update(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_test123",
                  DB_PATH=os.path.join(tempfile.mkdtemp(), "t.db"), ADMIN_PASSWORD="pw")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stripe, app as shop

captured = {}
class FakeObj(dict):
    def to_dict(self): return dict(self)
def fake_create(**kw):
    captured.update(kw); return type("S", (), {"url": "https://checkout.stripe.com/c/pay/cs_test_1"})()
SESSION = FakeObj(id="cs_test_1", payment_status="paid", amount_total=4114, total_details={"amount_tax": 0},
    customer_details={"email": "a@b.com", "name": "Pat Buyer", "phone": "+18305550000"},
    collected_information={"shipping_details": {"name": "Pat Buyer", "address": {"line1": "1 Oak St", "city": "New Braunfels", "state": "TX", "postal_code": "78132"}}},
    metadata={"src": "flyerB", "delivery_date_iso": "2026-10-21", "items": json.dumps({"classic": 2, "pecan": 1})},
    custom_fields=[{"key": "delivery_notes", "text": {"value": "Back porch"}}])
stripe.checkout.Session.create = staticmethod(fake_create)
stripe.checkout.Session.retrieve = staticmethod(lambda sid, **kw: SESSION)

c = shop.app.test_client()
r = c.post("/api/checkout", json={"items": {"classic": 2, "pecan": 1}, "zip": "78130", "src": "flyerB"})
assert r.json["url"].startswith("https://checkout.stripe.com"), r.json
assert captured["shipping_address_collection"] == {"allowed_countries": ["US"]}
assert captured["phone_number_collection"] == {"enabled": True}
assert captured["shipping_options"][0]["shipping_rate_data"]["fixed_amount"]["amount"] == 0
assert captured["metadata"]["src"] == "flyerB" and captured["metadata"]["delivery_date_iso"]
assert "Mon–Sat" in captured["shipping_options"][0]["shipping_rate_data"]["display_name"]
tax_li = captured["line_items"][-1]["price_data"]
assert tax_li["product_data"]["name"] == "Texas sales tax (8.25%)" and tax_li["unit_amount"] == 314, tax_li
assert "Contains: Milk, Soy" in captured["line_items"][0]["price_data"]["product_data"]["description"]
assert "PRIVATE RESIDENCE" in captured["custom_text"]["submit"]["message"]
shop.STRIPE_TAX_RATE_ID = "txr_test_123"
c.post("/api/checkout", json={"items": {"classic": 2, "pecan": 1}, "zip": "78130", "src": "flyerB"})
assert len(captured["line_items"]) == 2 and all(li["tax_rates"] == ["txr_test_123"] for li in captured["line_items"])
shop.STRIPE_TAX_RATE_ID = ""
print("tax line item / Stripe tax_rates / allergen description / disclosure OK")
print("checkout session params OK")

payload = json.dumps({"id": "evt_1", "object": "event", "type": "checkout.session.completed",
                      "data": {"object": {"id": "cs_test_1", "object": "checkout.session"}}})
assert c.post("/webhook/stripe", data=payload, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
ts = int(time.time())
sig = hmac.new(b"whsec_test123", f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
r = c.post("/webhook/stripe", data=payload, headers={"Stripe-Signature": f"t={ts},v1={sig}"})
assert r.status_code == 200, r.data
c.post("/webhook/stripe", data=payload, headers={"Stripe-Signature": f"t={ts},v1={sig}"})  # duplicate delivery
csv_ = c.get("/admin/orders.csv", headers={"Authorization": "Basic YWRtaW46cHc="}).data.decode()
rows = csv_.strip().splitlines()
assert len(rows) == 2, rows  # header + exactly one order (idempotent)
assert "Pat Buyer" in rows[1] and "flyerB" in rows[1] and "Back porch" in rows[1] and ",38.00,3.14,41.14," in rows[1] and "Classic Butter Toffee: milk, soy" in rows[1] and "2026-10-21" in rows[1] and "Wednesday, October 21" in rows[1]
assert "Pat Buyer" in c.get("/success?session_id=cs_test_1").data.decode()
print("webhook signature check, idempotency, admin CSV, success page OK")
print(rows[1])
