# Lana's Toffee Treats — sample-drop order page (prototype)

Leave a free candy sample + flyer → prospect scans the QR → mobile order page → pays with Stripe →
enters delivery info → free next-day delivery (Mon–Sat, order by 5pm). Every order lands in `/admin` (and optionally your email).

> Business: **Lana's Toffee Treats**, 830-832-8140, Lananbtx@gmail.com, delivering to ZIP **78130**.
> Still **placeholder**: the toffee products, prices, images, and the cottage-food disclosure line. Edit `config.json`.

## Quick start (DEMO mode — no Stripe needed)
```bash
cd candy-shop
./run.sh                      # creates .venv, installs deps, starts on http://localhost:5055
```
* Shop: http://localhost:5055/?src=flyer1
* Admin: http://localhost:5055/admin — browser login: any username + the `ADMIN_PASSWORD` in `.env`
  (`.env` is created per install and never zipped; copy `.env.example` → `.env` and set a long random password)
* CSV export: http://localhost:5055/admin/orders.csv

DEMO mode shows a mock checkout page (name, email, phone, address, delivery notes, ZIP check) and
records the order as "demo (unpaid)". No money moves.

## Files
| File | What it does |
|---|---|
| `config.json` | Business name, tagline, contact, **products & prices (cents)**, **delivery ZIP allowlist**, delivery label, max qty, min order, disclosure text |
| `app.py` | Flask app: shop page, ZIP check, Stripe Checkout, demo checkout, webhook, success page, admin, CSV |
| `notify.py` | Order alert hook (email via SMTP; swap in SMS/Slack if you like) |
| `make_flyer.py` | Printable flyer (half-letter PDF + PNG, and a letter sheet with 2 per page) with QR code |
| `make_images.py` | Regenerates the placeholder SVG product images |
| `screenshots.py` | Mobile screenshots of the running app (needs `pip install playwright`) |
| `tests/test_stripe_flow.py` | Offline test of the Stripe path (mocked API + signed webhook) |
| `data/orders.db` | SQLite order store (created automatically) |

## Cottage-food labeling, allergens & sales tax (shown BEFORE payment)
* **Required statement** (`cottage_food_disclosure` in `config.json`), shown in all caps in a boxed notice above the products,
  on the checkout page right before the Pay button (and in Stripe Checkout's submit message), on the success/receipt page,
  the flyer, the food-safety page (`/food-safety`), and every package label:
  "THIS PRODUCT WAS PRODUCED IN A PRIVATE RESIDENCE THAT IS NOT SUBJECT TO GOVERNMENTAL LICENSING OR INSPECTION."
* **Operator address / DSHS ID:** fill in `operator_address` and/or `dshs_id` in `config.json`. Until one is set, the site
  says "Address/DSHS ID: provided on package label", the package label prints a fill-in blank, and admin shows a warning.
  Once set, it appears on the receipt, the food-safety page and the labels.
* **Allergens:** each product has `common_name` and `allergens` (from the 9 major allergens: milk, eggs, wheat, soy, peanuts,
  tree nuts, fish, shellfish, sesame; name the nut, e.g. `"tree nuts (pecan)"`). They show as "Contains: ..." on product cards,
  cart, checkout, success page, admin, CSV, notification email and labels. **The current values are SAMPLES.** Lana must
  confirm them against her real recipes and ingredient labels (chocolate often contains soy and milk), then set
  `"allergens_confirmed": true` to remove the warning.
* **Package labels:** in `/admin` each order has "letter sheet (3×2 in)", a grid of 10 labels per letter page, one per package
  (adjust margins in `labeling.py` to match your label stock), and "single 3×2 in" for a label printer. Each label carries the
  operation name, address or DSHS ID, the product's common name, its allergens and the required statement.
* **Sales tax:** `sales_tax` in `config.json` (default 8.25%, the New Braunfels combined rate, labeled "Texas sales tax"), applied
  to the subtotal (rounded half-up) and shown in the cart, checkout, success page, admin, CSV and email. With Stripe, either
  set `STRIPE_TAX_RATE_ID=txr_...` (create a tax rate in the Stripe Dashboard; Stripe applies it to each line) or leave it
  empty and the app adds the computed tax as a separate line item. **Confirm with the Texas Comptroller** whether these
  items are taxable, the correct local rate for the delivery address, and that you hold a sales tax permit before charging tax.
* These features follow the rules Dallas researched. **Verify the current requirements with Texas DSHS before selling**; this
  app doesn't give legal advice.

## Delivery-date rules (`delivery.py`, settings in `config.json`)
* `order_cutoff` (default `"17:00"`, America/Chicago) and `delivery_weekdays` (default Mon–Sat, no Sunday).
* Before 5pm → the next delivery day. At/after 5pm → counted as the next day's order, so the delivery day after that.
  Sundays are skipped. Examples: Mon 4:59pm → Tue · Mon 5:00pm → Wed · Fri 5:30pm → Mon · Sat 10am or 6pm → Mon · Sun 6pm → Tue.
* The date is shown on the shop page, cart, checkout and success page, and stored on each order
  (`delivery_date` + `delivery_date_iso`). Admin groups open orders by delivery day. In Stripe mode the date shown
  at checkout is passed in the session metadata and used for the order.
* Tests: `.venv/bin/python tests/test_delivery_dates.py`

## Keep it running (`start-all.sh` / `supervise.sh` / `stop-all.sh`)
`./start-all.sh` starts a background supervisor that runs the app under gunicorn and restarts it within about 15 s if it
dies (log: `data/supervise.log`). With `TUNNEL=1 ./start-all.sh` it also runs a Cloudflare quick tunnel. If the tunnel
restarts, it updates `BASE_URL` in `.env`, regenerates the flyer and logs a warning, because the public URL changes.
There's no systemd or cron on the Grok box, so after the box restarts, run `./start-all.sh` again.

## Customize
* **Products / prices:** edit `config.json` → `products` (`price_cents`: 1200 = $12.00). Prices are always
  computed on the server from this file, so a customer can't change them.
* **Real photos:** drop `static/img/<product id>.jpg` and change the `src` in `templates/index.html`
  (`'img/' ~ p.id ~ '.svg'` → `.jpg`), or just overwrite the `.svg` files.
* **Delivery area:** `delivery_zips` in `config.json`. Checked on the cart page, again on the server before
  payment, and again on the address Stripe collects (out-of-area paid orders are flagged in admin so you can refund).
* **Track drop areas:** give each batch of flyers its own source: `python make_flyer.py --src gruene`.
  The QR opens `/?src=gruene`, the source is saved on the order, and `/admin` shows orders and revenue by source.

## Flyer
```bash
.venv/bin/python make_flyer.py --src flyer1 --base-url https://YOUR-PUBLIC-URL
```
Writes `flyers/flyer_<src>_half-letter.pdf/.png` and `flyers/flyer_<src>_letter_2up.pdf` (cut down the middle).
**Re-generate after deploying.** The bundled flyer points at `http://localhost:5055`, which only works on this computer.

**Cottage food line:** the disclosure on the flyer and site is a **placeholder**. Texas cottage food rules
generally require a label saying the food was made in a home kitchen that isn't inspected by the health
department, plus other label items (name/address, allergens, etc.). Rules on delivery and online sales
have changed in recent years. **Confirm the exact current wording and requirements with Texas DSHS
(Cottage Food Production) before you sell**, then update `cottage_food_disclosure` in `config.json`.

## Stripe (test first, then live)
1. Create a free account at https://dashboard.stripe.com and stay in **Test mode**.
2. Developers → API keys → copy the **Secret key** (`sk_test_...`).
3. Run: `STRIPE_SECRET_KEY=sk_test_... BASE_URL=http://localhost:5055 ./run.sh`
   The Checkout button now opens real Stripe Checkout. It collects card, email, phone and a US shipping
   address, and shows one free shipping option, "Next-day local delivery — Free". Pay with test card
   `4242 4242 4242 4242`, any future date, any CVC.
4. **Webhook** (records paid orders even if the customer closes the tab before the success page):
   * Local: install the Stripe CLI, then `stripe listen --forward-to localhost:5055/webhook/stripe`.
     It prints a `whsec_...`; restart with `STRIPE_WEBHOOK_SECRET=whsec_...`.
   * Deployed: Dashboard → Developers → Webhooks → Add endpoint `https://YOUR-URL/webhook/stripe`,
     event `checkout.session.completed` (optionally `checkout.session.async_payment_succeeded`),
     and copy its signing secret into `STRIPE_WEBHOOK_SECRET`.
   Signatures are verified, and repeat deliveries are ignored (one order per session).
5. **Go live:** finish Stripe account activation (bank account, business info), switch to Live mode, and
   swap in `sk_live_...` plus a live-mode webhook secret. Stripe charges its standard per-transaction fee.

## Order alerts (optional)
Set `NOTIFY_EMAIL_TO` and `SMTP_HOST/PORT/USER/PASSWORD` (see `.env.example` and `notify.py`). Without them,
alerts are just written to the server log. Stripe's mobile app can also notify you of every payment.

## Deploy (needs a public HTTPS URL so the QR works on phones)
**Printed QR codes need a URL that never changes.** Cloudflare *quick* tunnels (`cloudflared tunnel --url ...`) hand out
a random `*.trycloudflare.com` address that changes every time the tunnel or machine restarts. They have no uptime
guarantee and are meant for testing, so don't print flyers with one. (On the Grok box they don't connect at all:
outbound port 7844 is blocked.) Stable options:
* **Named Cloudflare Tunnel + your own domain** (domain is about $10/yr, e.g. lanastoffee.com; Cloudflare account free):
  `cloudflared tunnel login`, `cloudflared tunnel create toffee`, `cloudflared tunnel route dns toffee shop.lanastoffee.com`,
  `cloudflared tunnel run --url http://localhost:5055 toffee`. This runs on a computer that's always on (home PC or Raspberry Pi)
  with normal internet access.
* **Render / Railway** (instructions below). Use the host's own `*.onrender.com` / `*.up.railway.app` URL or your own domain.
* **Render** (free web service): New → Web Service from a GitHub repo, build `pip install -r requirements.txt`,
  start `gunicorn -b 0.0.0.0:$PORT app:app`, then add env vars. Free instances sleep when idle,
  so the first scan after a quiet spell can take 30–60 s to load.
* **Railway / Fly.io:** both detect the `Procfile`. Small apps are cheap or free-tier.
* **SQLite note:** free hosts often wipe local disk on redeploy. Add a persistent disk/volume and set
  `DB_PATH` to it, or treat the Stripe Dashboard as the source of truth and export the CSV often.
* Set `BASE_URL` to the public URL, change `ADMIN_PASSWORD`, then regenerate the flyer.

### No-server alternative: Stripe Payment Links
Create one Payment Link per product (or a "Sampler" link) in the Stripe Dashboard and turn on
"Collect customers' addresses" (shipping, US), "Require phone number", and a free shipping rate. Point the QR code
at the link (`make_flyer.py --base-url` accepts any URL; add `?client_reference_id=flyer1` to tag sources).
Orders appear in the Stripe Dashboard and app. You lose the cart, the ZIP pre-check, and this admin page.

## Tests
```bash
.venv/bin/python tests/test_delivery_dates.py   # cutoff / Mon–Sat rules
.venv/bin/python tests/test_tax_allergens.py    # tax math, allergens, statement, operator ID, labels
.venv/bin/python tests/test_stripe_flow.py      # Stripe path, offline (mocked API + signed webhook)
.venv/bin/python tests/e2e_customer.py          # against the RUNNING app: decode flyer QR -> phone browser -> order -> admin
```
`e2e_customer.py` writes screenshots to `screenshots/e2e/` and writes the new-order alert email (not sent) to `test_order_email.txt`.
