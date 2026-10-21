"""Webhook refund tracking (offline, mocked Stripe). Run: .venv/bin/python tests/test_refunds.py"""
import os, sys, json, time, hmac, hashlib, tempfile
os.environ.update(STRIPE_SECRET_KEY="sk_test_fake", STRIPE_WEBHOOK_SECRET="whsec_test123",
                  DB_PATH=os.path.join(tempfile.mkdtemp(), "r.db"), ADMIN_PASSWORD="pw", PARTNERS="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stripe, app as shop

class Fake(dict):
    def to_dict(self): return dict(self)

SESSIONS = {
    "cs_with_pi": Fake(id="cs_with_pi", payment_status="paid", amount_total=4114, total_details={"amount_tax": 314},
        customer_details={"email": "a@b.com", "name": "A", "phone": "+1"},
        collected_information={"shipping_details": {"name": "A", "address": {"line1": "1 Oak", "city": "New Braunfels", "state": "TX", "postal_code": "78130"}}},
        metadata={"src": "flyerB", "delivery_date_iso": "2026-10-21", "items": json.dumps({"classic": 2, "pecan": 1})},
        custom_fields=[], payment_intent="pi_full"),
    "cs_legacy": Fake(id="cs_legacy", payment_status="paid", amount_total=2600, total_details={"amount_tax": 0},
        customer_details={"email": "b@b.com", "name": "B", "phone": "+1"},
        collected_information={"shipping_details": {"name": "B", "address": {"line1": "1 Oak", "city": "New Braunfels", "state": "TX", "postal_code": "78130"}}},
        metadata={"src": "flyerB", "delivery_date_iso": "2026-10-21", "items": json.dumps({"classic": 1})},
        custom_fields=[], payment_intent="pi_legacy"),
}
CHARGES = {
    "ch_full": Fake(id="ch_full", payment_intent="pi_full", amount=4114, amount_refunded=4114),
    "ch_part": Fake(id="ch_part", payment_intent="pi_full", amount=4114, amount_refunded=2057),
    "ch_legacy": Fake(id="ch_legacy", payment_intent="pi_legacy", amount=2600, amount_refunded=2600),
    "ch_unknown": Fake(id="ch_unknown", payment_intent="pi_nope", amount=1000, amount_refunded=1000),
}
LIST_CALLS = []
stripe.checkout.Session.retrieve = staticmethod(lambda sid, **kw: SESSIONS[sid])
stripe.checkout.Session.list = staticmethod(lambda **kw: (LIST_CALLS.append(kw) or Fake(data=[SESSIONS["cs_legacy"]] if kw.get("payment_intent") == "pi_legacy" else [])))
stripe.Charge.retrieve = staticmethod(lambda cid, **kw: CHARGES[cid])

def sign(payload):
    ts = int(time.time())
    return f"t={ts},v1=" + hmac.new(b"whsec_test123", f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()

def post(etype, obj):
    payload = json.dumps({"id": "evt_" + etype + "_" + obj["id"], "object": "event", "type": etype, "data": {"object": obj}})
    return c.post("/webhook/stripe", data=payload, headers={"Stripe-Signature": sign(payload)})

c = shop.app.test_client()

# Seed two paid orders: one with payment_intent stored, one without (legacy)
with shop.app.app_context():
    shop.record_stripe_session("cs_with_pi")
    shop.record_stripe_session("cs_legacy")
    shop.db().execute("UPDATE orders SET payment_intent=NULL WHERE stripe_session_id='cs_legacy'")
    shop.db().commit()
    a = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_with_pi'").fetchone())
    b = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_legacy'").fetchone())
assert a["payment_intent"] == "pi_full" and b["payment_intent"] is None
print("PASS payment_intent stored on new orders")

# Full refund
r = post("charge.refunded", CHARGES["ch_full"]); assert r.status_code == 200, r.data
with shop.app.app_context():
    o = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_with_pi'").fetchone())
assert o["refunded_cents"] == 4114 and o["status"] == "refunded", o
print("PASS full refund -> refunded_cents=amount, status=refunded")

# Idempotent re-delivery
post("charge.refunded", CHARGES["ch_full"]); post("charge.refund.updated", Fake(id="re_1", charge="ch_full", payment_intent="pi_full", amount=4114))
with shop.app.app_context():
    o2 = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_with_pi'").fetchone())
    n = shop.db().execute("SELECT count(*) AS n FROM orders").fetchone()["n"]
assert o2["refunded_cents"] == 4114 and o2["status"] == "refunded" and n == 2
print("PASS repeated charge.refunded / charge.refund.updated is idempotent")

# Partial refund (re-open from full)
post("charge.refunded", CHARGES["ch_part"])
with shop.app.app_context():
    o3 = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_with_pi'").fetchone())
assert o3["refunded_cents"] == 2057 and o3["status"] == "new", o3   # reopened after partial reverse
assert shop.net_item_cents(o3) == 1900  # half of 3800
print("PASS partial refund: refunded_cents=amount_refunded, commission reduced, status reopens")

# Legacy order: no payment_intent stored; webhook looks up Session.list
LIST_CALLS.clear()
r = post("charge.refunded", CHARGES["ch_legacy"]); assert r.status_code == 200
with shop.app.app_context():
    o4 = dict(shop.db().execute("SELECT * FROM orders WHERE stripe_session_id='cs_legacy'").fetchone())
assert o4["refunded_cents"] == 2600 and o4["status"] == "refunded" and o4["payment_intent"] == "pi_legacy"
assert LIST_CALLS and LIST_CALLS[0]["payment_intent"] == "pi_legacy"
print("PASS legacy order: Session.list(payment_intent=...) backfill, then refund applied")

# Unknown payment ignored (no crash, no new rows)
n_before = n
r = post("charge.refunded", CHARGES["ch_unknown"]); assert r.status_code == 200
with shop.app.app_context():
    assert shop.db().execute("SELECT count(*) AS n FROM orders").fetchone()["n"] == 2
print("PASS unknown payment_intent ignored")

# Manual admin fallback still works
with shop.app.app_context():
    oid = shop.db().execute("SELECT id FROM orders WHERE stripe_session_id='cs_with_pi'").fetchone()["id"]
    shop.db().execute("UPDATE orders SET refunded_cents=0, status='new' WHERE id=?", (oid,)); shop.db().commit()
c.post(f"/admin/orders/{oid}/refunded", data={"on": "1"}, headers={"Authorization": "Basic YWRtaW46cHc="})
with shop.app.app_context():
    o5 = dict(shop.db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
assert o5["refunded_cents"] == 4114
print("PASS manual Mark refunded admin button still works")
print("ALL REFUND TESTS PASSED")
