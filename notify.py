"""Order notification hook.

Disabled by default (just logs the email it WOULD send). To enable email alerts for every new order, set:
  NOTIFY_EMAIL_TO   - where alerts go (e.g. Lananbtx@gmail.com)
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD, SMTP_FROM (defaults to SMTP_USER)
Any SMTP provider works (Gmail app password, SendGrid, Mailgun, Postmark SMTP, etc.).
You can also replace send_alert() with an SMS (Twilio) or Slack/Discord webhook call.
"""
import os, json, smtplib, logging
from email.message import EmailMessage

log = logging.getLogger("notify")


def _cfg():
    with open(os.environ.get("CONFIG_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))) as f:
        return json.load(f)


def _money(c):
    return f"${(c or 0)/100:.2f}"


def _contains(allergens):
    import labeling
    return labeling.contains_text(allergens or [])


def subject(o):
    test = "[TEST] " if o.get("mode") == "demo" else ""
    return (f"{test}New order #{o['id']} — {_money(o['total_cents'])} — deliver {o['delivery_date']} "
            f"— {o['customer_name']}")


def format_order(o, cfg=None):
    import labeling
    cfg = cfg or _cfg()
    base = os.environ.get("BASE_URL", "http://localhost:5055").rstrip("/")
    w = max([len(f"{i['qty']} x {i['name']} ({i['unit']})") for i in o["items"]] + [30])
    lines = [
        f"{cfg['business_name']} — new order",
        "=" * 44,
        f"Order #:        {o['id']}",
        f"Placed:         {o['created_at']} CT",
        f"DELIVER ON:     {o['delivery_date']}",
        f"Payment:        {o['payment_status']}",
        f"Flyer source:   {o.get('src') or '(none)'}",
        "",
        "CUSTOMER",
        f"  Name:    {o['customer_name']}",
        f"  Phone:   {o['phone']}",
        f"  Email:   {o['email']}",
        f"  Address: {o['address']}",
        f"  Notes:   {o.get('notes') or '-'}",
    ]
    if not o.get("in_area", 1):
        lines.append("  !! ADDRESS IS OUTSIDE THE DELIVERY AREA — contact customer / refund")
    lines += ["", "ITEMS"]
    for i in o["items"]:
        left = f"{i['qty']} x {i['name']} ({i['unit']})"
        lines.append(f"  {left.ljust(w)}  {_money(i['price_cents'])} ea   {_money(i['qty'] * i['price_cents']).rjust(8)}")
        lines.append(f"      {i.get('common_name', i['name'])} — {_contains(i.get('allergens'))}")
    lab = labeling.tax_label(cfg)
    lw = w + 20
    lines += ["", f"  {'Subtotal'.ljust(lw)}{_money(o.get('subtotal_cents')).rjust(8)}",
              f"  {lab.ljust(lw)}{_money(o.get('tax_cents')).rjust(8)}",
              f"  {'Delivery'.ljust(lw)}{'FREE'.rjust(8)}",
              f"  {'TOTAL'.ljust(lw)}{_money(o['total_cents']).rjust(8)}",
              "",
              f"Admin:          {base}/admin",
              f"Package labels: {base}/admin/orders/{o['id']}/labels.pdf",
              "",
              f"Label statement: {cfg['cottage_food_disclosure']}"]
    if not cfg.get("allergens_confirmed"):
        lines.append("Note: allergen info is SAMPLE data until confirmed in config.json.")
    return "\n".join(lines) + "\n"


def render_email(o):
    """Full message text as it would be sent: 'Subject: ...' line, blank line, plain-text body."""
    return f"Subject: {subject(o)}\n\n{format_order(o)}"


def send_alert(order):
    to = os.environ.get("NOTIFY_EMAIL_TO")
    host = os.environ.get("SMTP_HOST")
    if not (to and host):
        log.info("Notification hook disabled (set NOTIFY_EMAIL_TO + SMTP_HOST to enable). Would send:\n%s",
                 render_email(order))
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject(order)
        msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")
        msg["To"] = to
        msg.set_content(format_order(order))
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception as e:  # never break checkout because an alert failed
        log.exception("Order alert failed: %s", e)
        return False
