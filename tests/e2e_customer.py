"""Customer end-to-end run against the RUNNING app (DEMO mode):
decode the flyer QR -> open it on a phone-size browser -> add 3 products -> demo checkout -> verify in admin
-> save package labels + the new-order notification email (NOT sent). Screenshots in screenshots/e2e/.
Run: .venv/bin/python tests/e2e_customer.py
"""
import os, sys, re, json, sqlite3, subprocess, base64, urllib.request
import cv2
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
OUT = "screenshots/e2e"
FLYER = "flyers/flyer_flyer1_half-letter.png"
env = dict(l.strip().split("=", 1) for l in open(".env") if "=" in l and not l.startswith("#"))
PW = env.get("ADMIN_PASSWORD", "changeme")
os.environ.setdefault("BASE_URL", env.get("BASE_URL", "http://localhost:5055"))
import notify

# 1. scan the QR code on the flyer
img = cv2.imread(FLYER)
url, pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
assert url, "QR not decoded"
print("decoded QR URL:", url)
assert url.endswith("?src=flyer1")

with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    phone = dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    # scan-result screenshot: the flyer with the detected QR outlined + decoded text (like a phone camera popup)
    q = pts.astype(int).reshape(-1, 2)
    cv2.polylines(img, [q], True, (0, 200, 0), 6)
    b64 = base64.b64encode(cv2.imencode(".png", img)[1]).decode()
    pg = br.new_page(**phone)
    pg.set_content(f"""<body style="margin:0;font-family:-apple-system,Roboto,Arial;background:#111;color:#fff">
      <div style="padding:14px 16px;font-size:15px;opacity:.8">📷 Camera — QR code detected</div>
      <img src="data:image/png;base64,{b64}" style="width:100%;display:block">
      <div style="position:fixed;left:12px;right:12px;bottom:24px;background:#fff;color:#111;border-radius:16px;padding:14px 16px;box-shadow:0 8px 30px rgba(0,0,0,.5)">
        <div style="font-size:12px;color:#666">Open in browser</div><div style="font-weight:700;font-size:16px;word-break:break-all">{url}</div></div></body>""")
    pg.screenshot(path=f"{OUT}/01_scan_result.png")
    # 2. open the decoded URL
    pg = br.new_page(**phone)
    pg.goto(url); pg.evaluate("localStorage.clear()"); pg.reload()
    pg.screenshot(path=f"{OUT}/02_landing.png")
    # 3. add 3 products
    for pid in ["classic", "pecan", "darkalmond"]:
        pg.click(f".card[data-id={pid}] .p")
    pg.click(".card[data-id=classic] .p")  # 2 x classic
    pg.fill("#zip", "78130"); pg.wait_for_selector(".msg.ok")
    pg.evaluate("window.scrollTo(0, document.querySelector('#checkout').getBoundingClientRect().bottom + window.scrollY - 780)")
    pg.screenshot(path=f"{OUT}/03_cart.png")
    pg.click("#checkout"); pg.wait_for_url("**/demo-checkout/**")
    # 4. delivery details
    pg.fill("input[name=name]", "Test Customer"); pg.fill("input[name=email]", "test@example.com")
    pg.fill("input[name=phone]", "830-555-0100"); pg.fill("input[name=street]", "123 Test St")
    pg.fill("input[name=city]", "New Braunfels"); pg.fill("input[name=state]", "TX"); pg.fill("input[name=zip]", "78130")
    pg.fill("textarea[name=notes]", "TEST ORDER")
    pg.screenshot(path=f"{OUT}/04_form_filled.png", full_page=True)
    pg.click("button[type=submit]"); pg.wait_for_url("**/success**")
    oid = int(re.search(r"order=(\d+)", pg.url).group(1))
    pg.screenshot(path=f"{OUT}/05_success.png", full_page=True)
    succ = pg.content()
    # 5. admin
    actx = br.new_context(**phone, http_credentials={"username": "admin", "password": PW})
    ap = actx.new_page(); ap.goto(url.split("/?")[0] + "/admin")
    card = ap.locator(".panel", has_text=f"#{oid} · Test Customer")
    card.scroll_into_view_if_needed()
    ap.screenshot(path=f"{OUT}/06_admin.png", full_page=True)
    atext = card.inner_text()
    lab = actx.request.get(url.split("/?")[0] + f"/admin/orders/{oid}/labels.pdf")
    open(f"{OUT}/order_{oid}_labels.pdf", "wb").write(lab.body())
    br.close()
subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", "-singlefile",
                f"{OUT}/order_{oid}_labels.pdf", f"{OUT}/07_package_labels"], check=True)

# verify the order
o = notify  # noqa
con = sqlite3.connect(os.path.join(HERE, "data", "orders.db")); con.row_factory = sqlite3.Row
row = dict(con.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()); row["items"] = json.loads(row.pop("items_json"))
checks = {
    "flyer source flyer1": row["src"] == "flyer1" and "src: flyer1" in atext,
    "delivery date stored + in admin": bool(row["delivery_date_iso"]) and f"Deliver {row['delivery_date']}" in atext,
    "tax $4.29 (8.25% of $52.00), total $56.29": row["subtotal_cents"] == 5200 and row["tax_cents"] == 429 and row["total_cents"] == 5629,
    "tax shown in admin": "$4.29" in atext and "$56.29" in atext,
    "allergens in admin": "Contains: Milk, Tree Nuts (Almond), Soy" in atext and "Contains: Milk, Tree Nuts (Pecan), Soy" in atext,
    "statement on success page": "PRIVATE RESIDENCE THAT IS NOT SUBJECT" in succ,
    "customer details": row["phone"] == "830-555-0100" and row["notes"] == "TEST ORDER"
                        and row["address"] == "123 Test St, New Braunfels, TX 78130",
    "payment status": row["payment_status"] == "TEST — demo mode, no payment",
}
for k, v in checks.items():
    print("PASS" if v else "FAIL", k)
# 6. notification email (written to a file; NOT sent)
open("test_order_email.txt", "w").write(notify.render_email(row))
print("order", oid, "| email ->", os.path.join(HERE, "test_order_email.txt"))
sys.exit(0 if all(checks.values()) else 1)
