"""Delivery route planning: pick the day's stops, geocode them (Nominatim, cached in the DB),
order them for the shortest loop (OSRM trip service) and build Google Maps multi-stop links.

Free services, no API keys. Both are public, rate-limited demo services:
  * Nominatim: max 1 request/second, must send an identifying User-Agent, results cached.
  * OSRM demo server (router.project-osrm.org): "reasonable use" only.
If either fails we fall back to the entered order (order number) and say so on the page.
"""
import json, os, re, time, datetime, threading, urllib.parse, urllib.request

NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
USER_AGENT = os.environ.get("GEOCODE_USER_AGENT", "LanasToffeeTreats-DeliveryRoute/1.0 (Lananbtx@gmail.com)")
MAX_WAYPOINTS = 9            # Google Maps URL limit (plus origin + destination)
GEOCODE_BUDGET_S = 20        # stay well under the gunicorn 30 s worker timeout
FAIL_RETRY_S = 24 * 3600     # retry a "not found" address after a day

_rate_lock = threading.Lock()
_last_call = [0.0]


def http_get_json(url, timeout=10):
    """Single network entry point (tests replace this)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ---------- which orders go on the route ----------
def classify(orders, include_demo=False):
    """Split one day's orders into (route stops, out-of-area, already delivered). Skips unpaid, cancelled, refunded."""
    stops, outside, delivered = [], [], []
    for o in sorted(orders, key=lambda o: (o.get("created_at") or "", o["id"])):
        paid = o.get("payment_status") == "paid" or (include_demo and o.get("mode") == "demo")
        total, ref = int(o.get("total_cents") or 0), int(o.get("refunded_cents") or 0)
        if not paid or o.get("status") in ("cancelled", "refunded") or (ref and ref >= total):
            continue
        if o.get("status") == "delivered":
            delivered.append(o)
        elif not o.get("in_area", 1):
            outside.append(o)
        else:
            stops.append(o)
    return stops, outside, delivered


# ---------- geocoding ----------
def _key(addr):
    return re.sub(r"\s+", " ", (addr or "").strip().lower())


def ensure_cache(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS geocache (q TEXT PRIMARY KEY, lat REAL, lon REAL,
                    display TEXT, postcode TEXT, ok INTEGER, fetched_at REAL)""")
    conn.commit()


def geocode(conn, addr, deadline=None, sleep=time.sleep):
    """(lat, lon) for an address, or None. Cached in table geocache; network calls spaced >= 1 s apart."""
    ensure_cache(conn)
    k = _key(addr)
    if not k:
        return None
    r = conn.execute("SELECT lat, lon, ok, fetched_at FROM geocache WHERE q=?", (k,)).fetchone()
    if r and (r[2] or time.time() - (r[3] or 0) < FAIL_RETRY_S):
        return (r[0], r[1]) if r[2] else None
    if deadline and time.time() > deadline:
        raise TimeoutError("geocode budget used up")
    with _rate_lock:
        wait = 1.05 - (time.time() - _last_call[0])
        if wait > 0:
            sleep(wait)
        try:
            res = http_get_json(NOMINATIM_URL + "?" + urllib.parse.urlencode(
                {"q": addr, "format": "jsonv2", "limit": 1, "countrycodes": "us", "addressdetails": 1}))
        finally:
            _last_call[0] = time.time()
    hit = res[0] if res else None
    lat, lon = (float(hit["lat"]), float(hit["lon"])) if hit else (None, None)
    conn.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?,?,?,?,?)",
                 (k, lat, lon, hit.get("display_name") if hit else None,
                  (hit.get("address") or {}).get("postcode") if hit else None, 1 if hit else 0, time.time()))
    conn.commit()
    return (lat, lon) if hit else None


# ---------- ordering ----------
def osrm_trip_order(start, points):
    """Indexes of `points` in visiting order for a loop start -> ... -> start. Returns (order, meters, seconds)."""
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in [start] + list(points))
    res = http_get_json(f"{OSRM_URL}/trip/v1/driving/{coords}?roundtrip=true&source=first&overview=false", timeout=15)
    if res.get("code") != "Ok" or len(res.get("waypoints") or []) != len(points) + 1:
        raise ValueError(f"OSRM: {res.get('code')} {res.get('message', '')}".strip())
    pos = [w["waypoint_index"] for w in res["waypoints"]]
    order = sorted(range(len(points)), key=lambda i: pos[i + 1])
    trip = (res.get("trips") or [{}])[0]
    return order, trip.get("distance"), trip.get("duration")


# ---------- Google Maps links ----------
def maps_url(origin, destination, waypoints=()):
    q = {"api": "1", "origin": origin, "destination": destination, "travelmode": "driving"}
    if not origin:
        q.pop("origin")  # Maps then starts from the current location
    if waypoints:
        q["waypoints"] = "|".join(waypoints)
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote, safe=",")


def maps_links(start, stop_addrs, max_waypoints=MAX_WAYPOINTS):
    """Split start -> stops -> start into Google Maps links of <= max_waypoints waypoints each.
    Each link begins where the previous one ended. Returns dicts with label, url, first/last stop numbers."""
    if not stop_addrs:
        return []
    # No start address set: begin at the phone's current location (Maps default) and finish at the last stop.
    pts = [start] + list(stop_addrs) + ([start] if start else [])
    links, i = [], 0
    while i < len(pts) - 1:
        j = min(i + max_waypoints + 1, len(pts) - 1)
        nums = [n for n in range(i + 1, j + 1) if n <= len(stop_addrs)]  # stop numbers covered (1-based)
        links.append({"url": maps_url(pts[i], pts[j], pts[i + 1:j]), "first": nums[0] if nums else None,
                      "last": nums[-1] if nums else None, "ends_home": bool(start) and j == len(pts) - 1,
                      "starts_home": i == 0, "n_stops": len(nums)})
        i = j
    for n, l in enumerate(links, 1):
        l["label"] = f"Route {n}" if len(links) > 1 else "Route"
    return links


# ---------- whole plan ----------
def plan(conn, start_addr, stops, budget_s=GEOCODE_BUDGET_S):
    """Order `stops` (order dicts with 'address') for the drive. Never raises: falls back to entered order."""
    notices, deadline = [], time.time() + budget_s
    coords, missing = {}, []
    start = None
    try:
        start = geocode(conn, start_addr, deadline) if start_addr else None
        if start_addr and not start:
            notices.append("Couldn't find the start address on the map, so stops are in order-number order.")
        for o in stops:
            c = geocode(conn, o["address"], deadline)
            if c:
                coords[o["id"]] = c
            else:
                missing.append(o)
    except TimeoutError:
        missing = [o for o in stops if o["id"] not in coords and o not in missing]
        notices.append("Map lookups are still running for some stops (free service, 1 per second). "
                       "Reload in a minute for the best order.")
    except Exception as e:  # network down, service error
        notices.append(f"Map lookup service unavailable ({type(e).__name__}), so stops are in order-number order.")
        start, coords, missing = None, {}, list(stops)
    if missing and start:
        notices.append("Couldn't place on the map: " + ", ".join(f"#{o['id']}" for o in missing)
                       + ". Check those addresses; they're added at the end.")
    located = [o for o in stops if o["id"] in coords]
    optimized, meters, seconds = False, None, None
    if start and len(located) >= 1:
        try:
            order, meters, seconds = osrm_trip_order(start, [coords[o["id"]] for o in located])
            located = [located[i] for i in order]
            optimized = True
        except Exception as e:
            notices.append(f"Route optimizer unavailable ({type(e).__name__}), so stops are in order-number order.")
    ordered = located + [o for o in stops if o not in located]
    return {"stops": ordered, "links": maps_links(start_addr, [o["address"] for o in ordered]),
            "optimized": optimized, "notices": notices,
            "miles": round(meters / 1609.344, 1) if optimized and meters else None,
            "minutes": round(seconds / 60) if optimized and seconds else None}


def default_day(conn, cfg, now, next_delivery):
    """Earliest delivery day from today on that has open paid orders; otherwise the next delivery day."""
    today = now.date().isoformat()
    r = conn.execute("""SELECT MIN(delivery_date_iso) FROM orders WHERE delivery_date_iso >= ? AND payment_status='paid'
                        AND status NOT IN ('delivered','cancelled','refunded')
                        AND (COALESCE(refunded_cents,0)=0 OR refunded_cents < total_cents)""", (today,)).fetchone()
    return (r[0] if r and r[0] else None) or next_delivery.isoformat()
