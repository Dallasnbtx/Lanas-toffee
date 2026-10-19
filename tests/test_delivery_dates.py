"""Delivery-date rule tests. Run: .venv/bin/python tests/test_delivery_dates.py"""
import os, sys, datetime, json
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import delivery

CT = ZoneInfo("America/Chicago")
cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json")))
assert cfg["order_cutoff"] == "17:00" and "Sun" not in cfg["delivery_weekdays"]


def at(y, m, d, hh, mm=0, tz=CT):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=tz)


# Week of Mon Oct 19 2026 ... Sun Oct 25 2026
CASES = [
    (at(2026, 10, 19, 9), "2026-10-20", "Mon morning -> Tue"),
    (at(2026, 10, 19, 16, 59), "2026-10-20", "Mon 4:59pm -> Tue"),
    (at(2026, 10, 19, 17, 0), "2026-10-21", "Mon 5:00pm exactly -> Wed (at cutoff counts as after)"),
    (at(2026, 10, 19, 23, 59), "2026-10-21", "Mon late night -> Wed"),
    (at(2026, 10, 20, 0, 5), "2026-10-21", "Tue just after midnight -> Wed"),
    (at(2026, 10, 23, 12), "2026-10-24", "Fri noon -> Sat"),
    (at(2026, 10, 23, 17, 30), "2026-10-26", "Fri 5:30pm -> Mon (skip Sun)"),
    (at(2026, 10, 24, 10), "2026-10-26", "Sat morning -> Mon (no Sunday delivery)"),
    (at(2026, 10, 24, 18), "2026-10-26", "Sat 6pm -> Mon"),
    (at(2026, 10, 25, 12), "2026-10-26", "Sun noon -> Mon"),
    (at(2026, 10, 25, 17), "2026-10-27", "Sun 5pm -> Tue"),
    (at(2026, 10, 19, 21, 30, datetime.timezone.utc), "2026-10-20", "UTC input converted: 21:30Z = 4:30pm CDT Mon -> Tue"),
    (at(2026, 10, 19, 22, 0, datetime.timezone.utc), "2026-10-21", "22:00Z = 5:00pm CDT Mon -> Wed"),
    (at(2026, 11, 2, 16, 30), "2026-11-03", "after DST ends (CST) Mon 4:30pm -> Tue"),
    (at(2026, 12, 31, 18), "2027-01-02", "Thu Dec 31 6pm -> Sat Jan 2 (year rollover)"),
]
fails = 0
for now, want, label in CASES:
    got = delivery.next_delivery_date(cfg, now).isoformat()
    ok = got == want
    fails += not ok
    print(("PASS" if ok else "FAIL"), label, "->", got, "" if ok else f"(expected {want})")
start = at(2026, 1, 1, 0)
for h in range(0, 24 * 366, 1):
    now = start + datetime.timedelta(hours=h)
    d = delivery.next_delivery_date(cfg, now)
    assert d.weekday() != 6, now
    assert 1 <= (d - now.astimezone(CT).date()).days <= 3, (now, d)
print("PASS every hour of 2026: never Sunday, always 1-3 days out")
assert delivery.cutoff_label(cfg) == "5pm" and delivery.days_label(cfg) == "Mon–Sat"
print("PASS labels: 'Order by 5pm', 'Mon–Sat'")
sys.exit(1 if fails else 0)
