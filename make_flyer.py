"""Make a printable half-letter flyer (5.5" x 8.5") PDF + PNG with a QR code, plus a letter sheet with 2 flyers to cut.
Usage:  python make_flyer.py --src flyer1 [--base-url https://your-shop.example.com]
BASE_URL can also come from the BASE_URL env var. Default: http://localhost:5055 (placeholder).
Use a different --src per drop area (e.g. gruene, mission-hills) to see which area converts in /admin.
"""
import argparse, json, os, io, pathlib, subprocess
import qrcode
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import labeling

HERE = pathlib.Path(__file__).parent
cfg = json.load(open(HERE / "config.json"))
ap = argparse.ArgumentParser()
ap.add_argument("--src", default="flyer1")
ap.add_argument("--base-url", default=os.environ.get("BASE_URL", f"http://localhost:{os.environ.get('PORT', '5055')}"))
args = ap.parse_args()
url = f"{args.base_url.rstrip('/')}/?src={args.src}"

qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=2)
qr.add_data(url); qr.make(fit=True)
buf = io.BytesIO(); qr.make_image(fill_color="#3B2416", back_color="white").save(buf, "PNG"); buf.seek(0)
qr_img = ImageReader(buf)
BROWN, ORANGE, CREAM, INK = HexColor("#B5541C"), HexColor("#E8A33D"), HexColor("#FFF8EF"), HexColor("#3B2416")
W, H = 5.5 * inch, 8.5 * inch


def wrap(c, text, x, y, width, font, size, color, lead=None, center=True):
    c.setFont(font, size); c.setFillColor(color)
    words, line, lines = text.split(), "", []
    for w in words:
        t = (line + " " + w).strip()
        if c.stringWidth(t, font, size) > width and line:
            lines.append(line); line = w
        else:
            line = t
    lines.append(line)
    for ln in lines:
        (c.drawCentredString(x, y, ln) if center else c.drawString(x, y, ln)); y -= (lead or size * 1.25)
    return y


def draw(c, ox=0, oy=0):
    c.saveState(); c.translate(ox, oy)
    c.setFillColor(CREAM); c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(BROWN); c.rect(0, H - 2.35 * inch, W, 2.35 * inch, fill=1, stroke=0)
    c.setFillColor(ORANGE); c.rect(0, H - 2.45 * inch, W, 0.1 * inch, fill=1, stroke=0)
    c.setFillColor(white); c.setFont("Helvetica-Bold", 13); c.drawCentredString(W / 2, H - 0.5 * inch, cfg["business_name"].upper())
    y = wrap(c, "Enjoyed your sample?", W / 2, H - 1.05 * inch, W - 0.6 * inch, "Times-Bold", 30, white)
    wrap(c, "Free next-day delivery (Mon–Sat). Order by 5pm.", W / 2, y - 2, W - 0.6 * inch, "Helvetica-Bold", 14, white)
    y = H - 2.85 * inch
    y = wrap(c, f"{cfg['tagline']}. Classic butter, Texas pecan, sea salt & more.",
             W / 2, y, W - 0.9 * inch, "Helvetica", 11.5, INK)
    qs = 2.5 * inch
    qy = y - qs - 0.1 * inch
    c.setFillColor(white); c.roundRect((W - qs) / 2 - 8, qy - 8, qs + 16, qs + 16, 10, fill=1, stroke=0)
    c.drawImage(qr_img, (W - qs) / 2, qy, qs, qs)
    y = qy - 0.32 * inch
    c.setFillColor(BROWN); c.setFont("Helvetica-Bold", 16); c.drawCentredString(W / 2, y, "SCAN TO ORDER")
    y -= 0.27 * inch
    c.setFillColor(INK); c.setFont("Helvetica", 10.5)
    c.drawCentredString(W / 2, y, "1. Scan   2. Pick your treats   3. Pay securely")
    y -= 0.2 * inch
    c.drawCentredString(W / 2, y, "Order by 5pm for next-day delivery (Mon–Sat). No delivery charge.")
    y -= 0.22 * inch
    c.setFont("Helvetica-Oblique", 8); c.setFillColor(HexColor("#8A6D58"))
    c.drawCentredString(W / 2, y, url)
    y -= 0.25 * inch
    c.setFont("Helvetica-Bold", 9); c.setFillColor(INK); c.drawCentredString(W / 2, y, cfg.get("delivery_note", ""))
    y -= 0.2 * inch
    c.setFillColor(HexColor("#8A6D58")); c.setFont("Helvetica", 9); c.drawCentredString(W / 2, y, f"Delivering to ZIP {', '.join(cfg['delivery_zips'])}   ·   {cfg['contact_phone']}   ·   {cfg['contact_email']}")
    # cottage food disclosure placeholder
    c.setFillColor(white); c.setStrokeColor(INK); c.setLineWidth(1.5)
    c.rect(0.2 * inch, 0.15 * inch, W - 0.4 * inch, 0.62 * inch, fill=1, stroke=1)
    wrap(c, cfg["cottage_food_disclosure"], W / 2, 0.58 * inch, W - 0.6 * inch, "Helvetica-Bold", 8.5, INK, lead=10.5)
    c.setFont("Helvetica", 7); c.setFillColor(INK)
    c.drawCentredString(W / 2, 0.24 * inch, f"{cfg['business_name']} · {labeling.operator_line(cfg)}")
    c.restoreState()


out = HERE / "flyers"; out.mkdir(exist_ok=True)
single = out / f"flyer_{args.src}_half-letter.pdf"
c = canvas.Canvas(str(single), pagesize=(W, H)); draw(c); c.showPage(); c.save()
two = out / f"flyer_{args.src}_letter_2up.pdf"
c = canvas.Canvas(str(two), pagesize=(letter[1], letter[0]))  # landscape letter = two half-letters side by side
draw(c, 0, 0); draw(c, W, 0)
c.setStrokeColor(HexColor("#BBBBBB")); c.setDash(4, 4); c.line(W, 0, W, H); c.showPage(); c.save()
png = out / f"flyer_{args.src}_half-letter"
subprocess.run(["pdftoppm", "-png", "-r", "200", "-singlefile", str(single), str(png)], check=False)
print("QR ->", url); print(single); print(two); print(str(png) + ".png")
