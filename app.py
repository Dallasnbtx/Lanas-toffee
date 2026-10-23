import os, re, hmac, json, sqlite3, secrets, csv, io, logging, datetime, threading
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from zoneinfo import ZoneInfo
from flask import (Flask, render_template, request, jsonify, redirect, url_for,
                   abort, Response, g)
import notify, delivery, labeling, route, route_test_stops
from urllib.parse import urlsplit
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR0 = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR0, ".env"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "orders.db"))
PORT = int(os.environ.get("PORT", "5055"))
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{PORT}").rstrip("/")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_TAX_RATE_ID = os.environ.get("STRIPE_TAX_RATE_ID", "").strip()  # optional txr_... from Stripe Dashboard
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
TZ = ZoneInfo(os.environ.get("TZ_NAME", "America/Chicago"))
MODE = "stripe" if STRIPE_SECRET_KEY else "demo"

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("candy")

if MODE == "stripe":
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if ADMIN_PASSWORD == "changeme":
    log.warning("ADMIN_PASSWORD is the default 'changeme' - set it in .env")
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))
STATUSES = ["new", "out for delivery", "delivered", "cancelled", "refunded"]
PENDING = {}  # demo-mode carts awaiting the mock checkout form
_lock = threading.Lock()


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def products_by_id(cfg):
    return {p["id"]: p for p in cfg["products"]}


# ---------- storage ----------
def db():
    if "db" not in g:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT, mode TEXT, stripe_session_id TEXT UNIQUE,
        payment_status TEXT, status TEXT DEFAULT 'new',
        customer_name TEXT, email TEXT, phone TEXT, address TEXT, zip TEXT,
        notes TEXT, src TEXT, items_json TEXT, total_cents INTEGER,
        delivery_date TEXT, in_area INTEGER DEFAULT 1)""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(orders)")}
    for col, typ in [("delivery_date_iso", "TEXT"), ("subtotal_cents", "INTEGER"), ("tax_cents", "INTEGER"),
                     ("refunded_cents", "INTEGER DEFAULT 0"), ("payment_intent", "TEXT"),
                     ("is_test", "INTEGER DEFAULT 0")]:  # is_test: admin route test stops
        if col not in cols:  # migration for older DBs
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
    c.execute("""CREATE TABLE IF NOT EXISTS partner_payouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, partner_slug TEXT, paid_on TEXT, period TEXT,
        amount_cents INTEGER, method TEXT, note TEXT)""")
    c.commit()
    c.close()


def row_to_order(r):
    o = dict(r)
    o["items"] = json.loads(o.pop("items_json") or "[]")
    return o


def save_order(**o):
    items = o.pop("items")
    o["items_json"] = json.dumps(items)
    o.setdefault("created_at", datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"))
    o.setdefault("status", "new")
    with _lock:
        d = db()
        if o.get("stripe_session_id"):
            ex = d.execute("SELECT id FROM orders WHERE stripe_session_id=?", (o["stripe_session_id"],)).fetchone()
            if ex:  # idempotent: webhook + success page may both record
                return ex["id"], False
        cols = ",".join(o.keys())
        cur = d.execute(f"INSERT INTO orders ({cols}) VALUES ({','.join('?'*len(o))})", tuple(o.values()))
        d.commit()
        oid = cur.lastrowid
    order = row_to_order(db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone())
    if not order.get("is_test"):  # test stops never email
        notify.send_alert(order)
    return oid, True


# ---------- helpers ----------
def delivery_info(now=None):
    """Delivery date for an order placed now: (pretty string, ISO date)."""
    d = delivery.next_delivery_date(load_config(), now)
    return delivery.pretty(d), d.isoformat()


def delivery_date():
    return delivery_info()[0]


@app.context_processor
def _delivery_ctx():
    cfg = load_config()
    return {"cutoff_label": delivery.cutoff_label(cfg), "days_label": delivery.days_label(cfg),
            "contains": labeling.contains_text, "operator_line": labeling.operator_line(cfg),
            "operator_set": labeling.operator_is_set(cfg), "tax_label": labeling.tax_label(cfg),
            "tax_rate": float(labeling.tax_rate(cfg))}


def zip_ok(z):
    z = (z or "").strip()[:5]
    return z in load_config()["delivery_zips"]


def build_items(raw):
    """Price everything server-side from config; never trust client prices."""
    cfg = load_config()
    pmap = products_by_id(cfg)
    maxq = cfg.get("max_qty_per_item", 20)
    items = []
    for pid, qty in (raw or {}).items():
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if pid in pmap and 0 < qty <= maxq:
            p = pmap[pid]
            items.append({"id": pid, "name": p["name"], "common_name": p.get("common_name", p["name"]),
                          "allergens": p.get("allergens", []), "unit": p["unit"], "qty": qty,
                          "price_cents": p["price_cents"]})
    return items, sum(i["qty"] * i["price_cents"] for i in items)


def clean_src(s):
    return "".join(ch for ch in (s or "") if ch.isalnum() or ch in "-_")[:40]


# ---------- restaurant partners ----------
def load_partners(raw=None):
    """PARTNERS env var: 'slug:tagprefix:percent:key[:Display Name]' entries separated by ';' or newlines.
    Example: PARTNERS='gristmill:gristmill10:10:<long random key>:The Gristmill'. Bad entries are skipped."""
    raw = os.environ.get("PARTNERS", "") if raw is None else raw
    out = {}
    for ent in re.split(r"[;\n]+", raw):
        parts = [x.strip() for x in ent.strip().split(":")]
        if len(parts) not in (4, 5) or not all(parts[:4]):
            continue
        slug, prefix, pct, key = parts[:4]
        name = parts[4] if len(parts) == 5 and parts[4] else slug.replace("-", " ").title()
        try:
            pct = Decimal(pct)
        except Exception:
            continue
        if len(key) < 24 or clean_src(prefix) != prefix:  # refuse short keys / odd prefixes
            log.warning("partner %s skipped: key too short or bad prefix", slug)
            continue
        out[slug] = {"slug": slug, "prefix": prefix, "pct": pct, "key": key, "name": name}
    return out


def src_matches(src, prefix):
    """Tag belongs to a partner if it IS the prefix or continues with '-' or '_' (gristmill10, gristmill10-s07).
    'gristmill100' does not match 'gristmill10'."""
    src = src or ""
    return bool(prefix) and (src == prefix or (src.startswith(prefix) and src[len(prefix)] in "-_"))


def server_of(src, prefix):
    m = re.fullmatch(r"[-_]s(\d{1,3})", (src or "")[len(prefix):])
    return f"{int(m.group(1)):02d}" if m else "house"


def commission_cents(net_cents, pct):
    return int((Decimal(int(net_cents or 0)) * Decimal(pct) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def net_item_cents(o):
    """Item subtotal (no tax, no delivery) less any refund. Refunds are pro-rated onto the subtotal."""
    sub = int(o.get("subtotal_cents") or 0)
    total = int(o.get("total_cents") or 0)
    ref = int(o.get("refunded_cents") or 0)
    if o.get("status") in ("cancelled", "refunded") or (total and ref >= total):
        return 0
    if ref and total:
        sub -= min(sub, int((Decimal(ref) * sub / total).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    return max(sub, 0)


PARTNER_COLS = "id, created_at, src, subtotal_cents, total_cents, refunded_cents, status, payment_status"


def partner_orders(prefix, pct):
    """Paid orders for a partner, reduced to non-PII fields only."""
    rows = db().execute(f"SELECT {PARTNER_COLS} FROM orders WHERE payment_status='paid' AND COALESCE(is_test,0)=0 ORDER BY created_at DESC, id DESC").fetchall()
    out = []
    for r in rows:
        o = dict(r)
        if not src_matches(o["src"], prefix):
            continue
        net = net_item_cents(o)
        try:
            dt = datetime.datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M")
            day, tm = dt.strftime("%b %-d, %Y"), dt.strftime("%-I:%M %p")
        except (TypeError, ValueError):
            day, tm = o["created_at"] or "", ""
        out.append({"id": o["id"], "date": o["created_at"], "day": day, "time": tm, "month": (o["created_at"] or "")[:7],
                    "server": server_of(o["src"], prefix), "item_cents": int(o["subtotal_cents"] or 0),
                    "net_cents": net, "commission_cents": commission_cents(net, pct),
                    "refunded": bool(o["refunded_cents"]) or o["status"] in ("cancelled", "refunded"),
                    "partial": bool(o["refunded_cents"]) and net > 0})
    return out


def month_label(ym):
    try:
        return datetime.date.fromisoformat(ym + "-01").strftime("%B %Y")
    except ValueError:
        return ym


def due_date_label(ym):
    y, m = map(int, ym.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return datetime.date(y, m, 10).strftime("%b 10, %Y")


def summarize(orders, key):
    acc = {}
    for o in orders:
        a = acc.setdefault(o[key], {"orders": 0, "net_cents": 0, "commission_cents": 0})
        a["orders"] += 1; a["net_cents"] += o["net_cents"]; a["commission_cents"] += o["commission_cents"]
    return acc


@app.route("/partner/<slug>")
def partner_page(slug):
    partners = load_partners()
    p = partners.get(slug)
    key = request.args.get("key", "")
    expected = p["key"] if p else secrets.token_urlsafe(32)
    if not hmac.compare_digest(key.encode(), expected.encode()) or not p:
        abort(404)
    orders = partner_orders(p["prefix"], p["pct"])
    cur = datetime.datetime.now(TZ).strftime("%Y-%m")
    by_month = summarize(orders, "month")
    by_server = summarize(orders, "server")
    payouts = [dict(r) for r in db().execute(
        "SELECT paid_on, period, amount_cents, method, note FROM partner_payouts WHERE partner_slug=? ORDER BY paid_on DESC",
        (slug,)).fetchall()]
    earned = sum(o["commission_cents"] for o in orders)
    paid = sum(x["amount_cents"] or 0 for x in payouts)
    resp = Response(render_template(
        "partner.html", cfg=load_config(), p=p, orders=orders, cur=cur, cur_label=month_label(cur),
        cur_sum=by_month.get(cur, {"orders": 0, "net_cents": 0, "commission_cents": 0}),
        prior=[(m, month_label(m), due_date_label(m), v) for m, v in sorted(by_month.items(), reverse=True) if m < cur],
        servers=sorted(by_server.items(), key=lambda kv: (kv[0] == "house", kv[0])),
        payouts=payouts, earned=earned, paid=paid, pct_label=f"{p['pct'].normalize():f}%",
        now_label=datetime.datetime.now(TZ).strftime("%b %-d, %Y %-I:%M %p")))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def by_partner_rollup(rows, partners=None):
    """Admin rollup: configured partners by prefix; other tags ending in -sNN grouped by their base."""
    partners = load_partners() if partners is None else partners
    out = {}
    if not partners:  # feature off: nothing to show
        return out
    for o in rows:
        src = o.get("src") or ""
        if not src or o.get("is_test"):
            continue
        p = next((p for p in partners.values() if src_matches(src, p["prefix"])), None)
        if p:
            k = p["prefix"]
        else:
            m = re.fullmatch(r"(.+?)[-_]s\d{1,3}", src)
            if not m:
                continue
            k = m.group(1)
        a = out.setdefault(k, {"slug": p["slug"] if p else "", "pct": p["pct"] if p else None, "orders": 0,
                               "tags": set(), "total_cents": 0, "net_cents": 0, "commission_cents": 0})
        a["orders"] += 1; a["tags"].add(src); a["total_cents"] += o.get("total_cents") or 0
        if o.get("payment_status") == "paid":
            net = net_item_cents(o); a["net_cents"] += net
            if p:
                a["commission_cents"] += commission_cents(net, p["pct"])
    return out


# ---------- public pages ----------
@app.route("/")
def index():
    cfg = load_config()
    return render_template("index.html", cfg=cfg, mode=MODE, src=clean_src(request.args.get("src")),
                           delivery_date=delivery_date())


def zip_sorry():
    z = load_config()["delivery_zips"]
    return (f"Sorry — right now we only deliver to ZIP{'s' if len(z) > 1 else ''} {', '.join(z)} "
            "(New Braunfels). We're hoping to expand soon!")


@app.post("/api/check-zip")
def api_check_zip():
    z = (request.get_json(silent=True) or {}).get("zip", "")
    ok = zip_ok(z)
    return jsonify(ok=ok, message=None if ok else
                   zip_sorry())


@app.post("/api/checkout")
def api_checkout():
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    items, subtotal = build_items(data.get("items"))
    tax = labeling.compute_tax(cfg, subtotal)
    total = subtotal + tax
    if not items:
        return jsonify(error="Your cart is empty."), 400
    if subtotal < cfg.get("min_order_cents", 0):
        return jsonify(error=f"Minimum order is ${cfg['min_order_cents']/100:.2f}."), 400
    z = (data.get("zip") or "").strip()[:5]
    if not zip_ok(z):
        return jsonify(error="That ZIP is outside our delivery area."), 400
    src = clean_src(data.get("src"))

    if MODE == "demo":
        token = secrets.token_urlsafe(10)
        PENDING[token] = {"items": items, "subtotal": subtotal, "tax": tax, "total": total, "zip": z, "src": src}
        return jsonify(url=url_for("demo_checkout", token=token), delivery_date=delivery_date())

    ddate, diso = delivery_info()

    line_items = []
    for i in items:
        li = {"quantity": i["qty"], "price_data": {
            "currency": cfg["currency"], "unit_amount": i["price_cents"],
            "product_data": {"name": f"{i['name']} ({i['unit']})",
                             "description": f"{i['common_name']}. {labeling.contains_text(i['allergens'])}"}}}
        if STRIPE_TAX_RATE_ID:
            li["tax_rates"] = [STRIPE_TAX_RATE_ID]  # Stripe computes the tax per line
        line_items.append(li)
    if not STRIPE_TAX_RATE_ID and tax:
        line_items.append({"quantity": 1, "price_data": {"currency": cfg["currency"], "unit_amount": tax,
                                                         "product_data": {"name": labeling.tax_label(cfg)}}})
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=line_items,
        shipping_address_collection={"allowed_countries": ["US"]},
        shipping_options=[{"shipping_rate_data": {
            "type": "fixed_amount", "display_name": f"{cfg['delivery_label']} - {ddate}",
            "fixed_amount": {"amount": 0, "currency": cfg["currency"]},
            "delivery_estimate": {"minimum": {"unit": "business_day", "value": 1},
                                  "maximum": {"unit": "business_day", "value": 1}}}}],
        phone_number_collection={"enabled": True},
        custom_text={"submit": {"message": cfg["cottage_food_disclosure"] + " " + cfg.get("delivery_note", "")}},
        custom_fields=[{"key": "delivery_notes", "label": {"type": "custom", "custom": "Delivery notes (gate code, etc.)"},
                        "type": "text", "optional": True}],
        metadata={"src": src, "zip_checked": z, "delivery_date_iso": diso, "items": json.dumps({i["id"]: i["qty"] for i in items})},
        success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/?src={src}",
    )
    return jsonify(url=session.url)


@app.route("/demo-checkout/<token>", methods=["GET", "POST"])
def demo_checkout(token):
    if MODE != "demo":
        abort(404)
    pend = PENDING.get(token)
    if not pend:
        return redirect(url_for("index"))
    cfg = load_config()
    errors, form = [], {}
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in
                ["name", "email", "phone", "street", "city", "state", "zip", "notes"]}
        for k, label in [("name", "Name"), ("email", "Email"), ("phone", "Phone"),
                         ("street", "Street address"), ("city", "City"), ("zip", "ZIP")]:
            if not form[k]:
                errors.append(f"{label} is required.")
        if form["email"] and "@" not in form["email"]:
            errors.append("Please enter a valid email.")
        if form["phone"] and sum(ch.isdigit() for ch in form["phone"]) < 10:
            errors.append("Please enter a 10-digit phone number.")
        if form["zip"] and not zip_ok(form["zip"]):
            errors.append(zip_sorry())
        if not errors:
            ddate, diso = delivery_info()  # computed at the moment the order is placed
            addr = f"{form['street']}, {form['city']}, {form['state'] or 'TX'} {form['zip'][:5]}"
            oid, _ = save_order(mode="demo", payment_status="TEST — demo mode, no payment", customer_name=form["name"],
                                email=form["email"], phone=form["phone"], address=addr, zip=form["zip"][:5],
                                notes=form["notes"], src=pend["src"], items=pend["items"],
                                subtotal_cents=pend["subtotal"], tax_cents=pend["tax"],
                                total_cents=pend["total"], delivery_date=ddate, delivery_date_iso=diso)
            PENDING.pop(token, None)
            return redirect(url_for("success", order=oid))
    form.setdefault("zip", pend["zip"])
    form.setdefault("city", "New Braunfels")
    form.setdefault("state", "TX")
    return render_template("demo_checkout.html", cfg=cfg, pend=pend, errors=errors, form=form,
                           delivery_date=delivery_date())


def parse_stripe_session(s):
    """Turn a Checkout Session (plain dict) into our order fields."""
    cd = s.get("customer_details") or {}
    ship = (s.get("collected_information") or {}).get("shipping_details") or s.get("shipping_details") or {}
    a = ship.get("address") or cd.get("address") or {}
    addr = ", ".join(x for x in [a.get("line1"), a.get("line2"), a.get("city"),
                                 f"{a.get('state') or ''} {a.get('postal_code') or ''}".strip()] if x)
    md = s.get("metadata") or {}
    items, subtotal = build_items(json.loads(md.get("items") or "{}"))
    total = s.get("amount_total") or 0
    tax = ((s.get("total_details") or {}).get("amount_tax") or 0) or max(total - subtotal, 0)
    notes = ""
    for f in (s.get("custom_fields") or []):
        if f.get("key") == "delivery_notes":
            notes = (f.get("text") or {}).get("value") or ""
    z = (a.get("postal_code") or "")[:5]
    if md.get("delivery_date_iso"):  # the date the customer was shown at checkout
        d = datetime.date.fromisoformat(md["delivery_date_iso"])
        ddate, diso = delivery.pretty(d), d.isoformat()
    else:
        created = datetime.datetime.fromtimestamp(s.get("created") or datetime.datetime.now().timestamp(), TZ)
        ddate, diso = delivery_info(created)
    pi = s.get("payment_intent")
    pi = pi.get("id") if isinstance(pi, dict) else pi
    return dict(mode="stripe", stripe_session_id=s["id"], payment_status="paid", payment_intent=pi or None,
                customer_name=ship.get("name") or cd.get("name") or "", email=cd.get("email") or "",
                phone=cd.get("phone") or "", address=addr, zip=z, notes=notes, src=md.get("src", ""),
                items=items, subtotal_cents=subtotal, tax_cents=tax, total_cents=total, delivery_date=ddate, delivery_date_iso=diso,
                in_area=1 if zip_ok(z) else 0)


def record_stripe_session(session_id):
    s = stripe.checkout.Session.retrieve(session_id).to_dict()
    if s.get("payment_status") != "paid":
        return None
    oid, _ = save_order(**parse_stripe_session(s))
    return oid


@app.post("/webhook/stripe")
def stripe_webhook():
    if MODE != "stripe" or not STRIPE_WEBHOOK_SECRET:
        return jsonify(error="webhook not configured"), 400
    try:
        event = stripe.Webhook.construct_event(request.get_data(), request.headers.get("Stripe-Signature", ""),
                                               STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        log.warning("Bad webhook signature: %s", e)
        return jsonify(error="invalid signature"), 400
    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        record_stripe_session(event["data"]["object"]["id"])
    elif event["type"] in ("charge.refunded", "charge.refund.updated"):
        try:
            record_refund(event["type"], event["data"]["object"])
        except Exception as e:  # never make Stripe retry forever over a lookup problem; log it
            log.warning("Refund event %s not applied: %s", event.get("id"), e)
    return jsonify(received=True)


def _obj(x):
    return x.to_dict() if hasattr(x, "to_dict") else dict(x or {})


def order_for_payment_intent(pi):
    """Find our order for a PaymentIntent; backfill orders recorded before payment_intent was stored."""
    if not pi:
        return None
    r = db().execute("SELECT id, total_cents, status FROM orders WHERE payment_intent=? AND COALESCE(is_test,0)=0", (pi,)).fetchone()
    if r:
        return r
    lst = _obj(stripe.checkout.Session.list(payment_intent=pi, limit=1))
    for sess in lst.get("data") or []:
        sid = _obj(sess).get("id")
        r = db().execute("SELECT id, total_cents, status FROM orders WHERE stripe_session_id=? AND COALESCE(is_test,0)=0", (sid,)).fetchone()
        if r:
            db().execute("UPDATE orders SET payment_intent=? WHERE id=?", (pi, r["id"]))
            db().commit()
            return r
    return None


def record_refund(etype, obj):
    """charge.refunded (Charge) / charge.refund.updated (Refund): set refunded_cents from the charge's
    current amount_refunded. Absolute value, so repeated or re-delivered events are idempotent."""
    obj = _obj(obj)
    charge_id = obj.get("id") if etype == "charge.refunded" else obj.get("charge")
    charge = obj if etype == "charge.refunded" else {}
    if charge_id:
        try:
            charge = _obj(stripe.Charge.retrieve(charge_id))  # fresh copy: safe if events arrive out of order
        except Exception as e:
            log.warning("Could not fetch charge %s (%s); using event data", charge_id, e)
    pi = charge.get("payment_intent") or obj.get("payment_intent")
    pi = pi.get("id") if isinstance(pi, dict) else pi
    if "amount_refunded" not in charge:
        log.warning("Refund event without charge totals for %s; ignored", pi); return None
    r = order_for_payment_intent(pi)
    if not r:
        log.info("Refund for unknown payment %s ignored", pi); return None
    refunded = int(charge.get("amount_refunded") or 0)
    amount = int(charge.get("amount") or r["total_cents"] or 0)
    d = db()
    d.execute("UPDATE orders SET refunded_cents=? WHERE id=?", (refunded, r["id"]))
    if amount and refunded >= amount:
        d.execute("UPDATE orders SET status='refunded' WHERE id=?", (r["id"],))
    elif r["status"] == "refunded":  # a refund was reversed/failed: reopen
        d.execute("UPDATE orders SET status='new' WHERE id=?", (r["id"],))
    d.commit()
    log.info("Order #%s refunded_cents=%s of %s", r["id"], refunded, amount)
    return r["id"]


@app.route("/success")
def success():
    cfg = load_config()
    order = None
    if request.args.get("order"):
        r = db().execute("SELECT * FROM orders WHERE id=? AND mode='demo' AND COALESCE(is_test,0)=0", (request.args["order"],)).fetchone()
        order = row_to_order(r) if r else None
    elif request.args.get("session_id") and MODE == "stripe":
        sid = request.args["session_id"]
        r = db().execute("SELECT * FROM orders WHERE stripe_session_id=?", (sid,)).fetchone()
        if not r:
            try:
                record_stripe_session(sid)  # webhook may not have arrived yet
            except Exception as e:
                log.warning("Could not fetch session: %s", e)
            r = db().execute("SELECT * FROM orders WHERE stripe_session_id=?", (sid,)).fetchone()
        order = row_to_order(r) if r else None
    return render_template("success.html", cfg=cfg, order=order)


# ---------- admin ----------
def require_admin(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        auth = request.authorization
        if not auth or not secrets.compare_digest(auth.password or "", ADMIN_PASSWORD):
            return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Orders"'})
        return fn(*a, **kw)
    return wrapper


@app.route("/admin")
@require_admin
def admin():
    rows = [row_to_order(r) for r in db().execute("SELECT * FROM orders ORDER BY id DESC").fetchall()]
    real = [o for o in rows if not o.get("is_test")]  # test stops: listed with a TEST badge, never counted
    by_day = {}
    for o in real:
        if o["status"] not in ("delivered", "cancelled", "refunded"):
            by_day.setdefault((o.get("delivery_date_iso") or "", o["delivery_date"]), []).append(o["id"])
    by_src = {}
    for o in real:
        k = o["src"] or "(none)"
        by_src.setdefault(k, [0, 0])
        by_src[k][0] += 1
        by_src[k][1] += o["total_cents"] or 0
    return render_template("admin.html", cfg=load_config(), orders=rows, statuses=STATUSES, mode=MODE,
                           by_src=by_src, by_day=sorted(by_day.items()), by_partner=by_partner_rollup(real),
                           partners_on=bool(load_partners()), test_counts=sorted(route_test_stops.counts(db()).items()))


@app.route("/admin/route")
@require_admin
def admin_route():
    """Delivery route for one day: stops in driving order, Google Maps links, printable stop list."""
    cfg = load_config()
    now = datetime.datetime.now(TZ)
    day = request.args.get("date", "")
    try:
        day = datetime.date.fromisoformat(day).isoformat()
    except ValueError:
        day = route.default_day(db(), cfg, now, delivery.next_delivery_date(cfg, now))
    d = datetime.date.fromisoformat(day)
    rows = [row_to_order(r) for r in db().execute("SELECT * FROM orders WHERE delivery_date_iso=?", (day,)).fetchall()]
    stops, outside, delivered = route.classify(rows, include_demo=(MODE == "demo"))
    start_addr = os.environ.get("DELIVERY_START_ADDRESS", "").strip()
    p = route.plan(db(), start_addr, stops)
    if not start_addr:
        p["notices"].insert(0, "DELIVERY_START_ADDRESS isn't set, so routes start from your phone's location and end at the last stop.")
    for n, o in enumerate(p["stops"], 1):
        o["stop_no"] = n
    return render_template("route.html", cfg=cfg, day=day, day_label=delivery.pretty(d), plan=p,
                           start_addr=start_addr, outside=outside, delivered=delivered, mode=MODE,
                           prev_day=(d - datetime.timedelta(days=1)).isoformat(),
                           next_day=(d + datetime.timedelta(days=1)).isoformat(),
                           n_items=sum(i["qty"] for o in p["stops"] for i in o["items"]),
                           n_test=sum(1 for o in p["stops"] if o.get("is_test")),
                           test_counts=sorted(route_test_stops.counts(db()).items()),
                           loaded=request.args.get("loaded", type=int), removed=request.args.get("removed", type=int))


def same_origin():
    """Basic-auth browsers resend credentials on cross-site POSTs, so state-changing admin POSTs must come from
    our own pages: Origin (or, failing that, Referer) must match this host."""
    src = request.headers.get("Origin") or request.headers.get("Referer") or ""
    u = urlsplit(src)
    return bool(u.netloc) and u.netloc == request.host and u.scheme == request.scheme


@app.post("/admin/route/test-stops/load")
@require_admin
def admin_test_stops_load():
    """Load ~12 fake paid TEST orders at public 78130 places for any picked date (replaces that date's set)."""
    if not same_origin():
        abort(403)
    try:
        day = datetime.date.fromisoformat(request.form.get("date", ""))
    except ValueError:
        abort(400)
    cfg = load_config()
    n = route_test_stops.load(db(), day, lambda raw: build_items(raw), lambda sub: labeling.compute_tax(cfg, sub),
                              delivery.pretty, datetime.datetime.now(TZ))
    return redirect(url_for("admin_route", date=day.isoformat(), loaded=n))


@app.post("/admin/route/test-stops/remove")
@require_admin
def admin_test_stops_remove():
    """Delete every test stop (is_test=1 AND src='test-route'); real orders are never touched."""
    if not same_origin():
        abort(403)
    n = route_test_stops.remove_all(db())
    day = request.form.get("date", "")
    try:
        day = datetime.date.fromisoformat(day).isoformat()
    except ValueError:
        day = None
    return redirect(url_for("admin_route", date=day, removed=n) if day else url_for("admin_route", removed=n))


@app.post("/admin/orders/<int:oid>/refunded")
@require_admin
def admin_refunded(oid):
    """Mark an order fully refunded (after refunding in Stripe) or clear the flag. Drives partner commissions."""
    d = db()
    r = d.execute("SELECT total_cents FROM orders WHERE id=? AND COALESCE(is_test,0)=0", (oid,)).fetchone()
    if not r:  # missing, or a test stop (no refunds on test rows)
        abort(404)
    d.execute("UPDATE orders SET refunded_cents=? WHERE id=?", (r["total_cents"] if request.form.get("on") == "1" else 0, oid))
    d.commit()
    return redirect(url_for("admin"))


@app.post("/admin/orders/<int:oid>/status")
@require_admin
def admin_status(oid):
    st = request.form.get("status")
    if st in STATUSES:
        d = db()
        d.execute("UPDATE orders SET status=? WHERE id=?", (st, oid))
        d.commit()
    return redirect(url_for("admin"))


@app.route("/admin/orders.csv")
@require_admin
def admin_csv():
    rows = [row_to_order(r) for r in db().execute("SELECT * FROM orders WHERE COALESCE(is_test,0)=0 ORDER BY id").fetchall()]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "created_at", "status", "payment_status", "mode", "name", "phone", "email", "address",
                "zip", "in_area", "notes", "items", "allergens", "subtotal", "sales_tax", "total", "delivery_date", "delivery_date_iso", "src",
                "stripe_session_id"])
    for o in rows:
        w.writerow([o["id"], o["created_at"], o["status"], o["payment_status"], o["mode"], o["customer_name"],
                    o["phone"], o["email"], o["address"], o["zip"], o["in_area"], o["notes"],
                    "; ".join(f"{i['qty']}x {i['name']}" for i in o["items"]),
                    "; ".join(f"{i['name']}: {', '.join(i.get('allergens', [])) or 'none'}" for i in o["items"]),
                    f"{(o['subtotal_cents'] or 0)/100:.2f}", f"{(o['tax_cents'] or 0)/100:.2f}",
                    f"{(o['total_cents'] or 0)/100:.2f}", o["delivery_date"], o["delivery_date_iso"], o["src"], o["stripe_session_id"]])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=orders.csv"})


@app.route("/admin/orders/<int:oid>/labels.pdf")
@require_admin
def admin_labels(oid):
    """Printable package labels for an order: one label per package. ?format=single for one 3x2in label per page."""
    r = db().execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        abort(404)
    cfg = load_config()
    pmap = products_by_id(cfg)
    entries = []
    for i in row_to_order(r)["items"]:
        p = dict(pmap.get(i["id"], {}), **{k: i[k] for k in ("name", "unit") if k in i})
        p.setdefault("common_name", i.get("common_name", i["name"]))
        p.setdefault("allergens", i.get("allergens", []))
        entries += [p] * i["qty"]
    pdf = labeling.labels_pdf(cfg, entries, sheet=request.args.get("format") != "single", order_id=oid)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=order-{oid}-labels.pdf"})


@app.route("/food-safety")
def food_safety():
    return render_template("food_safety.html", cfg=load_config())


@app.route("/healthz")
def healthz():
    return jsonify(ok=True, mode=MODE, next_delivery=delivery_date())


@app.template_filter("money")
def money(c):
    return f"${(c or 0)/100:.2f}"


init_db()

if __name__ == "__main__":
    log.info("Starting %s in %s mode on port %s", load_config()["business_name"], MODE.upper(), PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
