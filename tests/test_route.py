"""Delivery route: Maps link splitting, order filtering, geocode cache, OSRM ordering and fallbacks (network mocked)."""
import os, sys, json, base64, sqlite3, tempfile, urllib.parse
os.environ.update(DB_PATH=os.path.join(tempfile.mkdtemp(), "route.db"), ADMIN_PASSWORD="pw", STRIPE_SECRET_KEY="",
                  PARTNERS="", DELIVERY_START_ADDRESS="1 Fake Start Ln, Testville, TX 00000")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import app as shop, route

START = os.environ["DELIVERY_START_ADDRESS"]
AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:pw").decode()}


def q(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def wps(url):
    w = q(url).get("waypoints")
    return w.split("|") if w else []


# ---------- link splitting ----------
@pytest.mark.parametrize("n,expect_links", [(0, 0), (1, 1), (3, 1), (9, 1), (10, 2), (12, 2), (19, 2), (20, 3), (29, 3)])
def test_link_counts_and_continuity(n, expect_links):
    stops = [f"{i} Stop St" for i in range(1, n + 1)]
    links = route.maps_links(START, stops)
    assert len(links) == expect_links
    if not n:
        return
    visited = []
    for k, l in enumerate(links):
        p = q(l["url"])
        assert l["url"].startswith("https://www.google.com/maps/dir/?api=1&")
        assert p["travelmode"] == "driving" and len(wps(l["url"])) <= 9
        assert p["origin"] == (START if k == 0 else q(links[k - 1]["url"])["destination"])
        visited += wps(l["url"]) + [p["destination"]]
    assert visited == stops + [START]  # every stop once, in order, then home
    assert links[-1]["ends_home"] and links[0]["starts_home"]


def test_twelve_stops_split_exactly():
    stops = [f"{i} Stop St" for i in range(1, 13)]
    a, b = route.maps_links(START, stops)
    assert wps(a["url"]) == stops[:9] and q(a["url"])["destination"] == stops[9]
    assert q(b["url"])["origin"] == stops[9] and wps(b["url"]) == stops[10:] and q(b["url"])["destination"] == START
    assert (a["label"], a["first"], a["last"], b["first"], b["last"]) == ("Route 1", 1, 10, 11, 12)


def test_no_start_uses_current_location():
    links = route.maps_links("", ["A", "B"])
    assert len(links) == 1 and "origin" not in q(links[0]["url"]) and q(links[0]["url"])["destination"] == "B"


# ---------- filtering ----------
def o(id, **kw):
    base = dict(id=id, created_at=f"2026-10-25 10:{id:02d}", payment_status="paid", status="new", total_cents=1000,
                refunded_cents=0, in_area=1, mode="stripe", address=f"{id} Main St", items=[])
    base.update(kw)
    return base


def test_classify_filters():
    rows = [o(1), o(2, payment_status="unpaid"), o(3, status="refunded"), o(4, refunded_cents=1000),
            o(5, refunded_cents=300), o(6, status="cancelled"), o(7, status="delivered"), o(8, in_area=0),
            o(9, payment_status="TEST — demo mode, no payment", mode="demo"), o(10, status="out for delivery")]
    stops, outside, delivered = route.classify(rows)
    assert [x["id"] for x in stops] == [1, 5, 10] and [x["id"] for x in outside] == [8] and [x["id"] for x in delivered] == [7]
    assert 9 in [x["id"] for x in route.classify(rows, include_demo=True)[0]]


# ---------- geocoding, ordering, fallback ----------
class FakeNet:
    def __init__(self, osrm=None, geo_fail=False):
        self.calls, self.osrm, self.geo_fail = [], osrm, geo_fail

    def __call__(self, url, timeout=10):
        self.calls.append(url)
        if "nominatim" in url:
            if self.geo_fail:
                raise OSError("geocoder down")
            addr = q(url)["q"]
            n = int(addr.split()[0]) if addr[0].isdigit() else 0
            return [{"lat": str(29.70 + n / 1000), "lon": str(-98.12 - n / 1000), "display_name": addr,
                     "address": {"postcode": "78130"}}]
        if self.osrm is None:
            raise OSError("osrm down")
        return self.osrm(url)


def osrm_reverse(url):
    """Pretend the best loop visits the stops in reverse input order."""
    n = url.split("/driving/")[1].split("?")[0].count(";")
    return {"code": "Ok", "trips": [{"distance": 16093.44, "duration": 1800}],
            "waypoints": [{"waypoint_index": 0}] + [{"waypoint_index": n - i} for i in range(n)]}


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr(route.time, "sleep", lambda s: None)
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


STOPS = [o(i, address=f"{i}0 Main St, New Braunfels, TX 78130") for i in (1, 2, 3)]


def test_osrm_optimizes(conn, monkeypatch):
    monkeypatch.setattr(route, "http_get_json", FakeNet(osrm=osrm_reverse))
    p = route.plan(conn, START, STOPS)
    assert p["optimized"] and [s["id"] for s in p["stops"]] == [3, 2, 1] and not p["notices"]
    assert p["miles"] == 10.0 and p["minutes"] == 30 and len(p["links"]) == 1


def test_osrm_down_falls_back_to_entered_order(conn, monkeypatch):
    monkeypatch.setattr(route, "http_get_json", FakeNet(osrm=None))
    p = route.plan(conn, START, list(reversed(STOPS)))
    assert not p["optimized"] and [s["id"] for s in p["stops"]] == [3, 2, 1]
    assert any("optimizer unavailable" in n for n in p["notices"]) and len(p["links"]) == 1


def test_osrm_bad_reply_falls_back(conn, monkeypatch):
    monkeypatch.setattr(route, "http_get_json", FakeNet(osrm=lambda u: {"code": "NoTrips"}))
    p = route.plan(conn, START, STOPS)
    assert not p["optimized"] and [s["id"] for s in p["stops"]] == [1, 2, 3] and p["notices"]


def test_geocoder_down_falls_back(conn, monkeypatch):
    net = FakeNet(osrm=osrm_reverse, geo_fail=True)
    monkeypatch.setattr(route, "http_get_json", net)
    p = route.plan(conn, START, STOPS)
    assert not p["optimized"] and [s["id"] for s in p["stops"]] == [1, 2, 3]
    assert any("unavailable" in n for n in p["notices"]) and not any("osrm" in u for u in net.calls)


def test_unfound_address_goes_last(conn, monkeypatch):
    net = FakeNet(osrm=osrm_reverse)
    real = net.__call__
    monkeypatch.setattr(route, "http_get_json", lambda u, timeout=10: [] if "Nowhere" in u else real(u))
    stops = STOPS + [o(4, address="Nowhere Rd")]
    p = route.plan(conn, START, [stops[3]] + stops[:3])
    assert [s["id"] for s in p["stops"]] == [3, 2, 1, 4] and any("#4" in n for n in p["notices"])


def test_geocode_cache_and_rate_limit(conn, monkeypatch):
    net, sleeps = FakeNet(osrm=osrm_reverse), []
    monkeypatch.setattr(route, "http_get_json", net)
    assert route.geocode(conn, "10 Main St", sleep=sleeps.append)
    assert route.geocode(conn, "10  main st ", sleep=sleeps.append)  # cache hit (normalized)
    assert len(net.calls) == 1
    route.geocode(conn, "20 Main St", sleep=sleeps.append)
    assert len(net.calls) == 2 and sleeps and 0 < sleeps[-1] <= 1.05
    assert "LanasToffee" in route.USER_AGENT and "@" in route.USER_AGENT


# ---------- page ----------
def seed(c, n, day, **kw):
    with shop.app.app_context():
        shop.notify.send_alert = lambda o: None
        items, sub = shop.build_items({"classic": 1})
        for i in range(n):
            d = dict(mode="stripe", payment_status="paid", customer_name=f"Cust {day} {i}", email="x@example.com",
                     phone="830-555-0000", address=f"{i + 1}0 Main St, New Braunfels, TX 78130", zip="78130", notes="porch",
                     src="", items=items, subtotal_cents=sub, tax_cents=0, total_cents=sub, delivery_date=day,
                     delivery_date_iso=day, in_area=1)
            d.update(kw)
            shop.save_order(**d)


def test_route_page(monkeypatch):
    monkeypatch.setattr(route, "http_get_json", FakeNet(osrm=osrm_reverse))
    monkeypatch.setattr(route.time, "sleep", lambda s: None)
    c = shop.app.test_client()
    assert c.get("/admin/route").status_code == 401
    seed(c, 12, "2026-11-02")
    seed(c, 1, "2026-11-02", customer_name="Refunded Person", refunded_cents=10**6, status="refunded")
    seed(c, 1, "2026-11-02", customer_name="Unpaid Person", payment_status="unpaid")
    seed(c, 1, "2026-11-03", customer_name="Other Day Person")
    r = c.get("/admin/route?date=2026-11-02", headers=AUTH)
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and html.count('class="stop"') == 12
    assert "Refunded Person" not in html and "Unpaid Person" not in html and "Other Day Person" not in html
    assert html.count("google.com/maps/dir/") == 2 and "Route 2" in html and "Shortest-drive order" in html
    assert START in html and "window.print()" in html
    assert START not in c.get("/").get_data(as_text=True)  # start address only on the admin page
    d = c.get("/admin/route", headers=AUTH).get_data(as_text=True)  # default = earliest upcoming day with orders
    assert "November 2" in d or "No paid orders" in d
    assert c.get("/admin/route?date=bogus", headers=AUTH).status_code == 200


# ---------- admin test stops ----------
import csv, io, re, sqlite3 as _sq
import route_test_stops as rts

HOST = "http://localhost"
SAME = {**AUTH, "Origin": HOST}
PKEY = "k" * 30


def _rows(where="1=1", args=()):
    c = _sq.connect(shop.DB_PATH)
    c.row_factory = _sq.Row
    try:
        return c.execute(f"SELECT * FROM orders WHERE {where}", args).fetchall()
    finally:
        c.close()


def _ntest(day=None):
    return len(_rows("is_test=1 AND src='test-route'" + (" AND delivery_date_iso=?" if day else ""), (day,) if day else ()))


@pytest.fixture
def tc(monkeypatch):
    monkeypatch.setattr(route, "http_get_json", FakeNet(osrm=osrm_reverse))
    monkeypatch.setattr(route.time, "sleep", lambda s: None)
    monkeypatch.setenv("PARTNERS", f"gristmill:gristmill10:10:{PKEY}:The Gristmill")
    sent = []
    monkeypatch.setattr(shop.notify, "send_alert", lambda o: sent.append(o))
    c = shop.app.test_client()
    x = _sq.connect(shop.DB_PATH); x.execute("DELETE FROM orders WHERE is_test=1"); x.commit(); x.close()
    c.sent = sent
    return c


def test_test_stops_auth_and_get_do_nothing(tc):
    before = len(_rows())
    assert tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}).status_code == 401
    assert tc.post("/admin/route/test-stops/remove").status_code == 401
    assert tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"},
                   headers={"Authorization": "Basic " + base64.b64encode(b"x:wrong").decode(), "Origin": HOST}).status_code == 401
    assert tc.get("/admin/route/test-stops/load?date=2026-10-23", headers=AUTH).status_code == 405
    assert tc.get("/admin/route/test-stops/remove", headers=AUTH).status_code == 405
    # cross-site / no-origin POSTs refused even with valid auth
    assert tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}, headers=AUTH).status_code == 403
    assert tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"},
                   headers={**AUTH, "Origin": "https://evil.example"}).status_code == 403
    assert tc.post("/admin/route/test-stops/remove", headers={**AUTH, "Referer": "https://evil.example/x"}).status_code == 403
    assert tc.post("/admin/route/test-stops/load", data={"date": "nope"}, headers=SAME).status_code == 400
    tc.get("/admin/route?date=2026-10-23", headers=AUTH)
    assert len(_rows()) == before and _ntest() == 0


def test_load_no_duplicate_and_route_shows(tc):
    r = tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}, headers=SAME)
    assert r.status_code == 302 and "date=2026-10-23" in r.headers["Location"] and "loaded=12" in r.headers["Location"]
    assert _ntest("2026-10-23") == 12
    tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}, headers={**AUTH, "Referer": HOST + "/admin/route"})
    assert _ntest("2026-10-23") == 12  # replaced, not duplicated
    tc.post("/admin/route/test-stops/load", data={"date": "2026-10-24"}, headers=SAME)
    assert _ntest("2026-10-24") == 12 and _ntest() == 24
    assert tc.sent == []  # no order alert emails
    for row in _rows("is_test=1"):
        assert row["src"] == "test-route" and row["payment_status"] == "paid" and row["mode"] == "test"
        assert row["stripe_session_id"] is None and row["payment_intent"] is None and row["zip"] == "78130"
        assert row["customer_name"].startswith("Test Stop ") and row["phone"].startswith("830-555-01")
        assert "TEST STOP — not a real order" in row["notes"] and 900 <= row["total_cents"] <= 6000
    html = tc.get("/admin/route?date=2026-10-23&loaded=12", headers=AUTH).get_data(as_text=True)
    assert html.count('class="stop test"') == 12 and html.count(">TEST</span>") == 12
    assert "google.com/maps/dir/" in html and "Loaded 12 test stops" in html and "12 TEST stops" in html
    assert "Load test stops for this date" in html and "confirm(" in html
    a = tc.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "Test stops loaded" in a and a.count(">TEST</span>") == 24


def test_test_stops_excluded_everywhere(tc):
    seed(tc, 1, "2026-10-23", customer_name="Real Gristmill Buyer", src="gristmill10-s03", subtotal_cents=1200, total_cents=1299)
    real_id = _rows("customer_name='Real Gristmill Buyer'")[-1]["id"]
    base_admin = tc.get("/admin", headers=AUTH).get_data(as_text=True)
    base_csv = tc.get("/admin/orders.csv", headers=AUTH).get_data(as_text=True)
    base_partner = tc.get(f"/partner/gristmill?key={PKEY}").get_data(as_text=True)
    # make one test stop look like a partner order too: must still be excluded
    tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}, headers=SAME)
    c = _sq.connect(shop.DB_PATH); c.execute("UPDATE orders SET src='gristmill10' WHERE is_test=1 AND id=(SELECT MIN(id) FROM orders WHERE is_test=1)")
    c.commit(); c.close()
    assert _ntest() == 11  # src no longer test-route on that one -> also not removable by the button
    csv_txt = tc.get("/admin/orders.csv", headers=AUTH).get_data(as_text=True)
    assert csv_txt == base_csv and "Test Stop" not in csv_txt
    ids = [r["id"] for r in csv.DictReader(io.StringIO(csv_txt))]
    assert str(real_id) in ids
    p = tc.get(f"/partner/gristmill?key={PKEY}").get_data(as_text=True)
    strip = lambda h: re.sub(r"\d{1,2}:\d\d [AP]M", "", h)
    assert strip(p) == strip(base_partner) and "Test Stop" not in p
    with shop.app.app_context():
        po = {o["id"] for o in shop.partner_orders("gristmill10", 10)}
    assert real_id in po and not po & {r["id"] for r in _rows("is_test=1")}
    a = tc.get("/admin", headers=AUTH).get_data(as_text=True)
    sect = lambda h, start, end: h[h.index(start):h.index(end, h.index(start))]
    for start, end in [("Open deliveries by day", "</div>\n  </div>"), ("Conversions by flyer source", "</div>\n  </div>"),
                       ("By partner", "</div>\n  </div>")]:
        assert sect(a, start, end) == sect(base_admin, start, end)
    # refunds: endpoint refuses test rows; refund webhook lookup ignores them
    tid = _rows("is_test=1")[0]["id"]
    assert tc.post(f"/admin/orders/{tid}/refunded", data={"on": "1"}, headers=AUTH).status_code == 404
    assert tc.get(f"/success?order={tid}").get_data(as_text=True).count("Test Stop") == 0
    # cleanup the hand-edited row so other tests start clean
    c = _sq.connect(shop.DB_PATH); c.execute("UPDATE orders SET src='test-route' WHERE is_test=1"); c.commit(); c.close()


def test_remove_deletes_only_test_rows(tc):
    seed(tc, 2, "2026-10-23", customer_name="Keep Me")
    # a real order that happens to use src=test-route but is not flagged must survive
    seed(tc, 1, "2026-10-23", customer_name="Keep Me Too", src="test-route")
    real_before = len(_rows("COALESCE(is_test,0)=0"))
    tc.post("/admin/route/test-stops/load", data={"date": "2026-10-23"}, headers=SAME)
    tc.post("/admin/route/test-stops/load", data={"date": "2026-10-24"}, headers=SAME)
    # a flagged row with another src must also survive (both marks must match)
    c = _sq.connect(shop.DB_PATH)
    c.execute("INSERT INTO orders (customer_name, src, is_test, payment_status, items_json, total_cents) VALUES ('Odd', 'other', 1, 'paid', '[]', 0)")
    c.commit(); c.close()
    r = tc.post("/admin/route/test-stops/remove", data={"date": "2026-10-23"}, headers=SAME)
    assert r.status_code == 302 and "removed=24" in r.headers["Location"]
    assert _ntest() == 0 and len(_rows("COALESCE(is_test,0)=0")) == real_before
    assert len(_rows("customer_name LIKE 'Keep Me%'")) >= 3 and len(_rows("customer_name='Odd'")) == 1
    html = tc.get("/admin/route?date=2026-10-23&removed=24", headers=AUTH).get_data(as_text=True)
    assert "Removed 24 test stops" in html and 'class="stop test"' not in html
    c = _sq.connect(shop.DB_PATH); c.execute("DELETE FROM orders WHERE customer_name='Odd'"); c.commit(); c.close()
