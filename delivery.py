"""Delivery-date rules.

* Orders placed before the cutoff (default 5:00 PM America/Chicago) get the NEXT delivery day.
* Orders at/after the cutoff count as the next day's orders, so they get the delivery day after that.
* Delivery days are configurable (default Mon–Sat; no Sunday delivery).

Examples (cutoff 17:00, Mon–Sat):
  Mon 10:00 -> Tue      Mon 17:00 -> Wed
  Fri 16:59 -> Sat      Fri 17:30 -> Mon (skip Sun)
  Sat 12:00 -> Mon      Sat 18:00 -> Mon (counted as a Sunday order -> next delivery day Mon)
  Sun 12:00 -> Mon      Sun 18:00 -> Tue
"""
import datetime
from zoneinfo import ZoneInfo

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _rules(cfg):
    tz = ZoneInfo(cfg.get("timezone", "America/Chicago"))
    hh, mm = (int(x) for x in cfg.get("order_cutoff", "17:00").split(":"))
    days = {DAY_NAMES.index(d[:3].title()) for d in cfg.get("delivery_weekdays", DAY_NAMES[:6])}
    if not days:
        raise ValueError("delivery_weekdays must list at least one day")
    return tz, datetime.time(hh, mm), days


def next_delivery_date(cfg, now=None):
    """Return the delivery date (datetime.date) for an order placed at `now` (aware datetime; default: current time)."""
    tz, cutoff, days = _rules(cfg)
    now = (now or datetime.datetime.now(tz)).astimezone(tz)
    order_day = now.date()
    if now.time() >= cutoff:
        order_day += datetime.timedelta(days=1)  # after cutoff = treated as tomorrow's order
    d = order_day + datetime.timedelta(days=1)
    while d.weekday() not in days:
        d += datetime.timedelta(days=1)
    return d


def pretty(d):
    return d.strftime("%A, %B ") + str(d.day)


def cutoff_label(cfg):
    _, t, _ = _rules(cfg)
    return t.strftime("%I:%M %p").lstrip("0").replace(":00", "").lower().replace(" ", "")  # e.g. "5pm"


def days_label(cfg):
    days = sorted(DAY_NAMES.index(d[:3].title()) for d in cfg.get("delivery_weekdays", DAY_NAMES[:6]))
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
        return f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
    return ", ".join(DAY_NAMES[i] for i in days)
