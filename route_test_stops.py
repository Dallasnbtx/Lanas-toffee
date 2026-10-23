"""Admin "test stops" for trying the delivery route page on the live site.

Fake PAID orders at PUBLIC places in 78130 (parks, churches, stores, museums, government buildings; no homes),
the same addresses as seed_route_preview.py (each verified as 78130 with Nominatim).
Every row is flagged twice: is_test=1 AND src='test-route'. Both must match for a row to be removed, and
test rows are left out of alerts, totals, CSV, partner pages/statements and refund handling (see app.py).
"""
import datetime, json

SRC = "test-route"
NOTE = "TEST STOP — not a real order"
# (place, address, items)
PLACES = [
    ("New Braunfels Public Library", "700 E Common St, New Braunfels, TX 78130", {"classic": 2}),
    ("Landa Park", "164 Landa Park Dr, New Braunfels, TX 78130", {"pecan": 1}),
    ("Gruene Hall", "1281 Gruene Rd, New Braunfels, TX 78130", {"classic": 1, "pecan": 1}),
    ("Comal County Courthouse Annex", "150 N Seguin Ave, New Braunfels, TX 78130", {"darkalmond": 1}),
    ("New Braunfels City Hall", "550 Landa St, New Braunfels, TX 78130", {"classic": 3}),
    ("Sts. Peter & Paul Catholic Church", "386 N Castell Ave, New Braunfels, TX 78130", {"pecan": 2}),
    ("First Protestant Church", "172 W Coll St, New Braunfels, TX 78130", {"classic": 1}),
    ("Sophienburg Museum & Archives", "401 W Coll St, New Braunfels, TX 78130", {"darkalmond": 2}),
    ("Naegelin's Bakery", "129 S Seguin Ave, New Braunfels, TX 78130", {"classic": 1, "darkalmond": 1}),
    ("McKenna Children's Museum", "801 W San Antonio St, New Braunfels, TX 78130", {"pecan": 1}),
    ("Prince Solms Park", "100 Liebscher Dr, New Braunfels, TX 78130", {"classic": 2, "pecan": 1}),
    ("Walmart Supercenter", "1209 Interstate 35 S, New Braunfels, TX 78130", {"classic": 1}),
]


def load(conn, day, build_items, compute_tax, pretty, now):
    """Replace the test stops for `day` (a datetime.date) with a fresh set. Returns how many were added.
    Inserts directly (not via save_order), so no order alert email is sent."""
    iso = day.isoformat()
    conn.execute("DELETE FROM orders WHERE is_test=1 AND src=? AND delivery_date_iso=?", (SRC, iso))
    created = now.strftime("%Y-%m-%d %H:%M")
    n = 0
    for n, (place, addr, raw) in enumerate(PLACES, 1):
        items, sub = build_items(raw)
        tax = compute_tax(sub)
        conn.execute(
            """INSERT INTO orders (created_at, mode, stripe_session_id, payment_status, status, customer_name, email,
               phone, address, zip, notes, src, items_json, subtotal_cents, tax_cents, total_cents, delivery_date,
               delivery_date_iso, in_area, refunded_cents, payment_intent, is_test)
               VALUES (?, 'test', NULL, 'paid', 'new', ?, '', ?, ?, '78130', ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL, 1)""",
            (created, f"Test Stop {n}", f"830-555-01{n:02d}", addr, f"{NOTE} ({place})", SRC, json.dumps(items),
             sub, tax, sub + tax, pretty(day), iso))
    conn.commit()
    return n


def remove_all(conn):
    """Delete every test stop (both flags must match). Real orders are never touched. Returns the count."""
    cur = conn.execute("DELETE FROM orders WHERE is_test=1 AND src=?", (SRC,))
    conn.commit()
    return cur.rowcount


def counts(conn):
    """{delivery_date_iso: n} of loaded test stops."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT delivery_date_iso, COUNT(*) FROM orders WHERE is_test=1 AND src=? GROUP BY delivery_date_iso", (SRC,))}
