"""Sales tax, allergen display, required statement, operator address/DSHS ID, and package labels (DEMO mode).
Run: .venv/bin/python tests/test_tax_allergens.py
"""
import os, sys, json, tempfile, subprocess, re
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp()
cfg = json.load(open(os.path.join(HERE, "config.json")))
CFG = os.path.join(tmp, "config.json")
json.dump(cfg, open(CFG, "w"))
os.environ.update(STRIPE_SECRET_KEY="", CONFIG_PATH=CFG, DB_PATH=os.path.join(tmp, "t.db"), ADMIN_PASSWORD="pw")
sys.path.insert(0, HERE)
import labeling, app as shop

STMT = "THIS PRODUCT WAS PRODUCED IN A PRIVATE RESIDENCE THAT IS NOT SUBJECT TO GOVERNMENTAL LICENSING OR INSPECTION."
AUTH = {"Authorization": "Basic YWRtaW46cHc="}  # admin:pw
assert cfg["cottage_food_disclosure"] == STMT

# --- tax math ---
assert labeling.compute_tax(cfg, 1200) == 99      # 99.0
assert labeling.compute_tax(cfg, 1000) == 83      # 82.5 -> half-up 83
assert labeling.compute_tax(cfg, 3800) == 314     # 313.5 -> 314
assert labeling.compute_tax(cfg, 0) == 0
assert labeling.compute_tax(dict(cfg, sales_tax={"enabled": False, "rate_percent": 8.25}), 1000) == 0
assert labeling.compute_tax(dict(cfg, sales_tax={"rate_percent": 6.25, "label": "x"}), 1000) == 63
assert labeling.tax_label(cfg) == "Texas sales tax (8.25%)"
print("PASS tax math")

# --- allergen text ---
assert labeling.contains_text(["milk", "soy"]) == "Contains: Milk, Soy"
assert labeling.contains_text(["milk", "tree nuts (pecan)", "soy"]) == "Contains: Milk, Tree Nuts (Pecan), Soy"
assert labeling.contains_text([]).startswith("Contains: none")
for p in cfg["products"]:
    assert p.get("common_name") and isinstance(p.get("allergens"), list), p["id"]
    assert all(a.split(" (")[0] in cfg["major_allergens"] for a in p["allergens"]), p
print("PASS allergen text + every product has common_name and allergens")

c = shop.app.test_client()
home = c.get("/?src=flyerT").data.decode()
assert STMT in home and home.index(STMT) < home.index('class="grid"'), "statement must be near/above products"
assert "Contains: Milk, Tree Nuts (Pecan), Soy" in home and "Pecan Butter Toffee" in home
assert "Address/DSHS ID: provided on package label" in home
assert "Delivered personally by Lana&#39;s Toffee Treats — New Braunfels 78130 only." in home
assert "Texas sales tax (8.25%)" in home and "/food-safety" in home
print("PASS home page: statement before products, common names, allergens, operator placeholder, delivery note, tax row")

fs = c.get("/food-safety")
assert fs.status_code == 200 and STMT in fs.data.decode() and "Contains: Milk, Tree Nuts (Almond), Soy" in fs.data.decode()
print("PASS food safety page")

# --- checkout: tax applied, statement shown before Pay button ---
r = c.post("/api/checkout", json={"items": {"classic": 2, "pecan": 1}, "zip": "78130", "src": "flyerT"}).json
page = c.get(r["url"]).data.decode()
assert page.index(STMT) < page.index("Place order"), "statement must appear before the pay button"
assert "$38.00" in page and "$3.14" in page and "$41.14" in page and "Contains: Milk, Soy" in page
r2 = c.post(r["url"], data={"name": "T Cust", "email": "t@example.com", "phone": "8305550100", "street": "1 A St",
                            "city": "New Braunfels", "state": "TX", "zip": "78130", "notes": "x"})
oid = int(re.search(r"order=(\d+)", r2.headers["Location"]).group(1))
succ = c.get(f"/success?order={oid}").data.decode()
assert all(x in succ for x in ["$38.00", "$3.14", "$41.14", "Contains: Milk, Tree Nuts (Pecan), Soy", STMT,
                               "Address/DSHS ID: provided on package label"])
print("PASS checkout + success: subtotal $38.00, tax $3.14, total $41.14, allergens, statement")

adm = c.get("/admin", headers=AUTH).data.decode()
assert "$3.14" in adm and "Contains: Milk, Soy" in adm and f"/admin/orders/{oid}/labels.pdf" in adm
csv_ = c.get("/admin/orders.csv", headers=AUTH).data.decode().splitlines()
assert "subtotal,sales_tax,total" in csv_[0] and ",38.00,3.14,41.14," in csv_[1] and "Texas Pecan Toffee: milk, tree nuts (pecan), soy" in csv_[1]
print("PASS admin + CSV show tax and allergens")

# --- labels PDF (placeholder, then with operator info set) ---
def label_text(q=""):
    resp = c.get(f"/admin/orders/{oid}/labels.pdf{q}", headers=AUTH)
    assert resp.status_code == 200 and resp.mimetype == "application/pdf"
    f = os.path.join(tmp, "l.pdf"); open(f, "wb").write(resp.data)
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", f], capture_output=True, text=True).stdout).group(1))
    return subprocess.run(["pdftotext", "-layout", f, "-"], capture_output=True, text=True).stdout, pages
assert c.get(f"/admin/orders/{oid}/labels.pdf").status_code == 401
txt, pages = label_text()
flat = " ".join(txt.split())
assert pages == 1 and flat.count("Lana's Toffee Treats") == 3, (pages, flat)  # 3 packages on one sheet
assert "CONTAINS: MILK, TREE NUTS (PECAN), SOY" in flat and "Butter Toffee with Milk Chocolate" in flat
assert "PRODUCED IN A PRIVATE RESIDENCE" in flat and "ADDRESS / DSHS ID: ____" in flat
txt, pages = label_text("?format=single")
assert pages == 3
json.dump(dict(cfg, operator_address="123 Example Rd, New Braunfels, TX 78130", dshs_id="CF-TEST-0001"), open(CFG, "w"))
flat = " ".join(label_text()[0].split())
assert "123 Example Rd, New Braunfels, TX 78130" in flat and "DSHS ID: CF-TEST-0001" in flat and "____" not in flat
succ = c.get(f"/success?order={oid}").data.decode()
assert "123 Example Rd" in succ and "DSHS ID: CF-TEST-0001" in succ
print("PASS package labels (sheet + single) and operator address/DSHS ID on labels + receipt once set")
