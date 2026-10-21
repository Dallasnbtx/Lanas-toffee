"""Partner dashboard tests (offline, temp DB).
Run:  .venv/bin/python tests/test_partner.py
"""
import os, sys, tempfile, datetime, json
KEY = "k_" + "x" * 40
os.environ.update(DB_PATH=os.path.join(tempfile.mkdtemp(), "p.db"), ADMIN_PASSWORD="pw", STRIPE_SECRET_KEY="",
                  PARTNERS=f"gristmill:gristmill10:10:{KEY}:The Gristmill;short:short1:10:tooshort")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decimal import Decimal
import app as shop

def ok(msg): print("PASS", msg)

# ---- pure helpers ----
P = shop.load_partners()
assert set(P) == {"gristmill"}, P            # short key rejected
assert P["gristmill"]["pct"] == Decimal("10") and P["gristmill"]["name"] == "The Gristmill"
sm = shop.src_matches
assert sm("gristmill10", "gristmill10") and sm("gristmill10-s07", "gristmill10") and sm("gristmill10_x", "gristmill10")
assert not sm("gristmill100", "gristmill10") and not sm("gristmill1", "gristmill10") and not sm("xgristmill10", "gristmill10")
assert not sm("", "gristmill10") and not sm(None, "gristmill10") and not sm("glossy2", "gristmill10")
assert shop.server_of("gristmill10-s07", "gristmill10") == "07" and shop.server_of("gristmill10-s15", "gristmill10") == "15"
assert shop.server_of("gristmill10", "gristmill10") == "house" and shop.server_of("gristmill10-bar", "gristmill10") == "house"
ok("prefix matching + server parsing")
cc = shop.commission_cents
assert cc(3800, 10) == 380 and cc(1245, 10) == 125 and cc(1244, 10) == 124 and cc(0, 10) == 0
assert cc(2600, Decimal("12.5")) == 325 and cc(1001, Decimal("7.5")) == 75
o = {"subtotal_cents": 3800, "tax_cents": 314, "total_cents": 4114, "refunded_cents": 0, "status": "new"}
assert shop.net_item_cents(o) == 3800
assert shop.net_item_cents(dict(o, refunded_cents=4114)) == 0
assert shop.net_item_cents(dict(o, status="cancelled")) == 0
assert shop.net_item_cents(dict(o, refunded_cents=2057)) == 1900   # half refunded -> half the items
ok("commission math (10% of item subtotal, half-up rounding, refunds, cancellations)")

# ---- seed DB ----
now = datetime.datetime.now(shop.TZ)
cur = now.strftime("%Y-%m-%d %H:%M")
prev = (now.replace(day=1) - datetime.timedelta(days=3)).strftime("%Y-%m-%d 18:30")
PII = dict(customer_name="Zelda Quixote", email="zelda.secret@example.com", phone="+1 830 555 0199",
           address="999 Hiddenvalley Rd, New Braunfels, TX 78130", zip="78130", notes="GATECODE-4321 back porch")
items = json.dumps([{"id": "classic", "name": "Classic Butter Toffee", "qty": 2, "price_cents": 1200}])
with shop.app.app_context():
    d = shop.db()
    def add(src, sub, ts, paid=True, refunded=0, status="new"):
        tax = round(sub * 0.0825)
        d.execute("INSERT INTO orders (created_at, mode, payment_status, status, src, items_json, subtotal_cents, tax_cents, "
                  "total_cents, refunded_cents, customer_name, email, phone, address, zip, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, "stripe", "paid" if paid else "unpaid", status, src, items, sub, tax, sub + tax,
                   (sub + tax) if refunded else 0, *PII.values()))
    add("gristmill10-s07", 3800, cur)
    add("gristmill10-s07", 2400, cur)
    add("gristmill10", 1200, cur)
    add("gristmill10-s03", 2600, cur, refunded=1)
    add("gristmill10-s03", 5000, prev)
    add("gristmill100", 9900, cur)          # different tag, must not count
    add("glossy2", 7700, cur)               # flyer, must not count
    add("gristmill10-s01", 8800, cur, paid=False)  # unpaid, must not count
    d.execute("INSERT INTO partner_payouts (partner_slug, paid_on, period, amount_cents, method) VALUES ('gristmill','2026-10-09','2026-09',500,'Zelle')")
    d.commit()

c = shop.app.test_client()
for url in ["/partner/gristmill", "/partner/gristmill?key=", "/partner/gristmill?key=wrong",
            f"/partner/gristmill?key={KEY}x", f"/partner/nope?key={KEY}", "/partner/short?key=tooshort"]:
    assert c.get(url).status_code == 404, url
ok("wrong / missing key and unknown partner -> 404")

r = c.get(f"/partner/gristmill?key={KEY}")
assert r.status_code == 200
assert "noindex" in r.headers.get("X-Robots-Tag", "") and r.headers.get("Referrer-Policy") == "no-referrer"
h = r.get_data(as_text=True)
assert 'name="robots" content="noindex' in h and 'class="bezel"' not in h and "bezel.js" not in h
for bad in ["Zelda", "Quixote", "zelda.secret", "example.com", "555 0199", "555", "Hiddenvalley", "GATECODE", "back porch"]:
    assert bad not in h, f"PII leaked: {bad}"
assert KEY not in h
ok("no PII (names, email, phone, address, notes) or key in rendered HTML; noindex, no bezel")

with shop.app.app_context():
    orders = shop.partner_orders("gristmill10", Decimal(10))
assert len(orders) == 5, orders
assert sum(o["commission_cents"] for o in orders) == 380 + 240 + 120 + 0 + 500
assert [o for o in orders if o["refunded"]][0]["commission_cents"] == 0
assert "$74.00" in h and "$7.40" in h          # current month: 38+24+12 (refund excluded) and 10%
assert "No. 07" in h and "No. 03" in h and "House" in h and "Refunded" in h
assert "$5.00" in h and "Zelle" in h           # payout row
assert "#6" not in h and "#7" not in h and "#8" not in h   # other tags / unpaid not listed
ok("lists only paid orders whose src starts with the prefix; totals, per-server, payouts")

with shop.app.app_context():
    rows = [shop.row_to_order(x) for x in shop.db().execute("SELECT * FROM orders").fetchall()]
roll = shop.by_partner_rollup(rows)
assert roll["gristmill10"]["commission_cents"] == 1240 and roll["gristmill10"]["orders"] == 6
assert "gristmill100" not in roll or roll["gristmill100"]["slug"] == ""
ar = c.get("/admin", headers={"Authorization": "Basic " + __import__("base64").b64encode(b"a:pw").decode()})
assert ar.status_code == 200 and "By partner" in ar.get_data(as_text=True) and "owed $12.40" in ar.get_data(as_text=True)
ok("admin 'by partner' rollup groups gristmill10* tags")
# ---- PARTNERS unset: feature fully off ----
import importlib
os.environ["PARTNERS"] = ""
importlib.reload(shop)
assert shop.load_partners() == {}
c0 = shop.app.test_client()
assert c0.get("/partner/gristmill?key=anything").status_code == 404
assert c0.get("/partner/anything").status_code == 404
with shop.app.app_context():
    assert shop.by_partner_rollup([]) == {}
ah = c0.get("/admin", headers={"Authorization": "Basic " + __import__("base64").b64encode(b"a:pw").decode()})
assert ah.status_code == 200 and "By partner" not in ah.get_data(as_text=True)
assert c0.get("/healthz").status_code == 200
ok("PARTNERS empty: /partner/* 404, admin by-partner panel hidden, shop healthy")
print("ALL PARTNER TESTS PASSED")
