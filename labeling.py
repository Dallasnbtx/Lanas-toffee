"""Cottage-food labeling helpers: allergen text, operator line, sales tax, and printable package labels."""
import io
from decimal import Decimal, ROUND_HALF_UP
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

ALLERGEN_ORDER = ["milk", "eggs", "wheat", "soy", "peanuts", "tree nuts", "fish", "shellfish", "sesame"]


def contains_text(allergens):
    """['milk','tree nuts (pecan)','soy'] -> 'Contains: Milk, Tree Nuts (Pecan), Soy'. Empty -> 'Contains: none of the 9 major allergens'."""
    if not allergens:
        return "Contains: none of the 9 major food allergens"

    def nice(a):
        base, _, rest = a.partition(" (")
        return base.title() + (f" ({rest.rstrip(')').title()})" if rest else "")
    return "Contains: " + ", ".join(nice(a) for a in allergens)


def operator_line(cfg):
    """What to print for the operator address / DSHS ID. Falls back to the placeholder text until filled in."""
    parts = []
    if (cfg.get("operator_address") or "").strip():
        parts.append(cfg["operator_address"].strip())
    if (cfg.get("dshs_id") or "").strip():
        parts.append(f"DSHS ID: {cfg['dshs_id'].strip()}")
    return " · ".join(parts) if parts else cfg.get("operator_placeholder", "Address/DSHS ID: provided on package label")


def operator_label_line(cfg):
    """On the package label itself the placeholder would be circular, so print a fill-in blank until it's set."""
    if operator_is_set(cfg):
        return operator_line(cfg)
    return "ADDRESS / DSHS ID: ______________________  [set operator_address or dshs_id in config.json]"


def operator_is_set(cfg):
    return bool((cfg.get("operator_address") or "").strip() or (cfg.get("dshs_id") or "").strip())


def tax_rate(cfg):
    t = cfg.get("sales_tax") or {}
    return Decimal(str(t.get("rate_percent", 0))) if t.get("enabled", True) else Decimal(0)


def compute_tax(cfg, subtotal_cents):
    """Sales tax on the subtotal, rounded half-up to the cent."""
    return int((Decimal(subtotal_cents) * tax_rate(cfg) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def tax_label(cfg):
    t = cfg.get("sales_tax") or {}
    return f"{t.get('label', 'Sales tax')} ({tax_rate(cfg).normalize():f}%)"


# ---------- package labels ----------
LABEL_W, LABEL_H = 3 * inch, 2 * inch


def _wrap(c, text, font, size, width):
    words, lines, line = text.split(), [], ""
    for w in words:
        t = (line + " " + w).strip()
        if c.stringWidth(t, font, size) > width and line:
            lines.append(line)
            line = w
        else:
            line = t
    if line:
        lines.append(line)
    return lines


def draw_label(c, x, y, cfg, product, order_id=None):
    pad = 0.12 * inch
    w = LABEL_W - 2 * pad
    c.saveState()
    c.setStrokeColor(HexColor("#BBBBBB"))
    c.setDash(2, 2)
    c.rect(x, y, LABEL_W, LABEL_H, stroke=1, fill=0)
    c.setDash()
    c.setFillColor(black)
    ty = y + LABEL_H - pad - 9
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x + pad, ty, cfg["business_name"])
    ty -= 10
    c.setFont("Helvetica", 6.5)
    for ln in _wrap(c, operator_label_line(cfg), "Helvetica", 6.5, w):
        c.drawString(x + pad, ty, ln)
        ty -= 8
    ty -= 3
    c.setFont("Helvetica-Bold", 9.5)
    for ln in _wrap(c, product["common_name"], "Helvetica-Bold", 9.5, w):
        c.drawString(x + pad, ty, ln)
        ty -= 11
    c.setFont("Helvetica", 7)
    c.drawString(x + pad, ty, f"{product['name']} · {product['unit']}")
    ty -= 10
    c.setFont("Helvetica-Bold", 7.5)
    for ln in _wrap(c, contains_text(product.get("allergens", [])).upper(), "Helvetica-Bold", 7.5, w):
        c.drawString(x + pad, ty, ln)
        ty -= 9
    # required statement pinned to the bottom
    st = _wrap(c, cfg["cottage_food_disclosure"], "Helvetica-Bold", 6.6, w)
    sy = y + pad + (len(st) - 1) * 7.6
    c.setFont("Helvetica-Bold", 6.6)
    for ln in st:
        c.drawString(x + pad, sy, ln)
        sy -= 7.6
    if order_id:
        c.setFont("Helvetica", 5.5)
        c.setFillColor(HexColor("#777777"))
        c.drawRightString(x + LABEL_W - pad, y + LABEL_H - pad - 6, f"Order #{order_id}")
    c.restoreState()


def labels_pdf(cfg, entries, sheet=True, order_id=None):
    """entries: list of product dicts (one per package). sheet=True -> letter page grid of 3x2in labels
    (2 across x 5 down, 1.25in side / 0.5in top margins — adjust to your label stock); False -> one 3x2in label per page."""
    buf = io.BytesIO()
    if sheet:
        c = canvas.Canvas(buf, pagesize=letter)
        cols, rows, mx, my = 2, 5, 1.25 * inch, 0.5 * inch
        for i, p in enumerate(entries):
            k = i % (cols * rows)
            if i and k == 0:
                c.showPage()
            col, row = k % cols, k // cols
            draw_label(c, mx + col * LABEL_W, letter[1] - my - (row + 1) * LABEL_H, cfg, p, order_id)
    else:
        c = canvas.Canvas(buf, pagesize=(LABEL_W, LABEL_H))
        for i, p in enumerate(entries):
            if i:
                c.showPage()
            draw_label(c, 0, 0, cfg, p, order_id)
    c.showPage()
    c.save()
    return buf.getvalue()
