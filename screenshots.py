"""Capture mobile screenshots (390x844) of the running app in DEMO mode. Usage: python screenshots.py [base_url]"""
import sys, os
PW = next((l.split("=",1)[1].strip() for l in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) if l.startswith("ADMIN_PASSWORD=")), "changeme")
from playwright.sync_api import sync_playwright
B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5055"
OUT = "screenshots"
with sync_playwright() as p:
    br = p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    pg = ctx.new_page()
    pg.goto(f"{B}/?src=flyer1"); pg.evaluate("localStorage.clear()"); pg.reload()
    pg.screenshot(path=f"{OUT}/1_home.png")
    pg.screenshot(path=f"{OUT}/1b_home_full.png", full_page=True)
    for pid, n in [("classic", 2), ("pecan", 1), ("sampler", 1)]:
        for _ in range(n): pg.click(f".card[data-id={pid}] .p")
    pg.fill("#zip", "78201"); pg.wait_for_selector(".msg.bad")
    pg.locator("#cart").scroll_into_view_if_needed(); pg.screenshot(path=f"{OUT}/2b_cart_outside_area.png")
    pg.fill("#zip", "78130"); pg.wait_for_selector(".msg.ok")
    pg.evaluate("window.scrollTo(0, document.querySelector('#checkout').getBoundingClientRect().bottom + window.scrollY - 700)"); pg.screenshot(path=f"{OUT}/2_cart.png")
    pg.click("#checkout"); pg.wait_for_url("**/demo-checkout/**")
    pg.screenshot(path=f"{OUT}/3_checkout.png")
    pg.fill("input[name=name]", "Jane Sample"); pg.fill("input[name=email]", "jane@example.com")
    pg.fill("input[name=phone]", "830-555-0142"); pg.fill("input[name=street]", "456 Landa St")
    pg.fill("textarea[name=notes]", "Please leave on the porch swing")
    pg.screenshot(path=f"{OUT}/3b_checkout_filled_full.png", full_page=True)
    pg.click("button[type=submit]"); pg.wait_for_url("**/success**")
    pg.screenshot(path=f"{OUT}/4_success.png", full_page=True)
    actx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                          http_credentials={"username": "admin", "password": PW})
    ap = actx.new_page(); ap.goto(f"{B}/admin"); ap.screenshot(path=f"{OUT}/5_admin.png", full_page=True)
    br.close()
print("done")
