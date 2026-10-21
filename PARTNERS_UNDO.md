# Partner program — how to turn it off or roll it back

The partner dashboard is controlled by the `PARTNERS` environment variable. Everything else (shop, Stripe checkout, flyers, admin orders) works the same with or without it. Database changes are additive only: a new `refunded_cents` and `payment_intent` column on `orders`, and a new `partner_payouts` table. Leaving them in place is harmless.

## Turn off one restaurant (or all of them)

1. In the Render dashboard → your service → **Environment**, open the `PARTNERS` variable.
2. Remove that restaurant's entry (or clear the whole value to turn every partner page off).
3. **Save / Redeploy.**

Effects after redeploy:

- `/partner/<slug>?key=…` returns **404** for every removed slug.
- The admin "By partner" panel is hidden when `PARTNERS` is empty.
- Existing flyers and server cards keep working. Their QR codes still open the normal shop (`/?src=gristmill10-s07` etc.); the tag is saved on the order but no partner page reads it.
- Past orders, refunds and payouts stay in the database. Turning a partner back on later shows them again.

## Fully roll back the partner code

If you want the code itself gone (not just the env var):

1. On the machine that has the git repo, revert the partner commit(s), e.g. `git revert <partner-commit>` (or restore the files from before the partner change).
2. Redeploy that commit to Render.
3. You can leave or drop `PARTNERS` in Render; with the code gone it is ignored.
4. Optional cleanup (not required): in a sqlite shell, `ALTER TABLE` cannot drop columns easily; leaving `refunded_cents`, `payment_intent` and `partner_payouts` alone is fine.

## Flyers and QR codes

Old and new flyers are unaffected by turning partners off. A QR that points at `https://lanas-toffee.onrender.com/?src=…` always lands on the public shop. The `src` tag is only used for reporting when a matching `PARTNERS` entry exists.

## Refunds stay even with PARTNERS off

Automatic refund tracking (`charge.refunded` / `charge.refund.updated` on the Stripe webhook) is independent of the partner feature. It updates `refunded_cents` / status on orders so the admin page and any future partner page stay accurate. The manual **Mark refunded** button remains as a fallback.
