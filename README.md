# VYRON — Digital Gaming Marketplace

VYRON sells game currency, top-ups, gift cards and digital products. It includes:
a website, a Telegram WebApp, a Telegram bot and an admin panel. Payments go through
**Hamyon API** (HUMO / UZCARD card payments that top up the balance). Orders are
fulfilled automatically through **Payerpin**.

**One command starts everything:**

```bash
pip install -r requirements.txt
python main.py
```

| Component | What it does |
|---|---|
| Website + Telegram WebApp | Soft UI in dark blue / light blue, day/night modes, UZ / EN / RU, mobile-first |
| REST API | FastAPI: auth, catalog, cart, checkout, wallet, orders, market, admin |
| Telegram bot | `/start` shows an **Open VYRON** WebApp button; order and top-up notifications |
| Fulfillment worker | Creates Payerpin orders with locking, retries, backoff and idempotency |
| Top-up watcher | Re-checks open Hamyon payments and expires stale ones |
| Catalog scheduler | Syncs the real Payerpin catalog on a schedule |
| Database | MySQL (dedicated user, utf8mb4). Migrations run automatically. SQLite fallback is for dev only |

---

## 1. Quick start (O'zbekcha qisqacha)

1. `pip install -r requirements.txt`
2. Set up MySQL with a **separate user**, not root: `mysql -u root -p < deploy/mysql_setup.sql`
   (change the password in the file first).
3. `python main.py`. On first run `.env` is created from `.env.example`. Fill in `DB_*`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS` and `PUBLIC_BASE_URL` (https).
4. Run `python main.py` again. Log in to the admin panel with your Telegram admin ID
   (inside the WebApp) or with `ADMIN_USERNAME` / `ADMIN_PASSWORD` (on the web).
5. Enter the Payerpin and Hamyon keys in **Admin → Integratsiyalar**. They are saved
   to `.env` on the server and never shown back.
6. Run **Admin → Payerpin → Katalogni sinxronlash**. Only products the supplier
   actually has are put on sale.

## 2. Configuration

Every secret lives in `.env` or in server environment variables. None are stored
in code, the database, the frontend or the logs. See [.env.example](.env.example).

| Group | Keys |
|---|---|
| App | `APP_ENV`, `HOST`, `PORT`, `PUBLIC_BASE_URL`, `WEBAPP_URL`, `SESSION_SECRET` |
| MySQL | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` (or `DATABASE_URL`) |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_IDS` (comma-separated numeric IDs) |
| Payerpin | `PAYERPIN_API_KEY`, `PAYERPIN_BASE_URL=https://api.payerpin.uz`, `PAYERPIN_WEBHOOK_SECRET` |
| Hamyon | `HAMYON_SHOP_ID`, `HAMYON_SHOP_KEY` (from @HamyonAPIBot), `HAMYON_BASE_URL` |

You can edit the integration keys from **Admin → Integrations**. The panel shows
secret keys only as *configured / not configured*.

### Hamyon (balance top-up)

1. Get `shop_id` and `shop_key` from @HamyonAPIBot. Link your card through
   @HumoCardBot (HUMO) or @CardXabarBot (UZCARD).
2. Set these callback URLs in the bot. The admin panel shows them with copy buttons:
   * `prepare_url`: `{PUBLIC_BASE_URL}/api/webhooks/hamyon/prepare`
   * `complete_url`: `{PUBLIC_BASE_URL}/api/webhooks/hamyon/complete`
3. Flow: the user enters an amount and gets a card number plus the exact amount
   (valid for 5 minutes). Once paid, the `complete` callback is checked with
   `md5(shop_id + payment_id + amount + shop_key)`. The balance is credited exactly once,
   with deduplication by `payment_id`. Purchases are paid from the balance.
4. If an open payment for the same amount already exists, the amount is raised
   by 1–2 so'm automatically, as Hamyon's rules require.

### Payerpin (supplier)

* Uses the `X-API-Key` header and the `/api/v2/{me,balance,catalog,catalog/{gameKey},order,order/{id},orders}` endpoints.
* Webhook: `POST {PUBLIC_BASE_URL}/api/webhooks/payerpin`. The signature is
  HMAC-SHA256 of `timestamp + "." + rawBody`, compared in constant time.
  Delivery IDs are deduplicated.
* Admin → Payerpin has *Test connection*, *Check balance* and *Sync catalog*.
  None of these ever creates an order or a top-up.
* If a paid order cannot be fulfilled (for example, the supplier is not configured
  or rejects the order), it becomes **FAILED** and the money goes back to the user's
  balance. Unknown supplier statuses are never treated as COMPLETED.

## 3. Order flow

```
cart / quick buy → server-side price revalidation → order VYR-YYYYMMDD-000001
 → pay from balance (atomic, locked) → PAID → FULFILLMENT_PENDING
 → SUPPLIER_PROCESSING → COMPLETED | FAILED (auto-refund to balance) | REFUNDED
```

Price = supplier cost (converted to UZS with admin rates) + payment fee + margin.
The margin can be global, per game, per product or per variant. The supplier cost
is never shown to customers.

## 4. Admin panel (`#/admin`, also inside the Telegram WebApp)

Access is granted to users with the ADMIN role or whose Telegram ID is in
`TELEGRAM_ADMIN_IDS`. Sections:

Dashboard, System health, Games, Products, Variants, Pricing, Coupons, Promotions,
Orders (refund to balance), Top-ups (re-check), Balance transactions (manual adjust),
Payments, Fulfillments, Account market moderation, Users, Broadcast,
Integrations (Payerpin / Hamyon / Telegram keys and tests), Payerpin, Branding
(logo, favicon, hero images, texts, colors), Media library (image uploads),
Top-up settings and FX rates, Telegram, Audit logs, Security.

Every admin action is written to the audit log.

## 5. Tests

```bash
pytest -q
```

Tests cover auth, catalog, cart, checkout, orders, payments and webhooks (signatures,
deduplication, idempotency), Payerpin adapter, catalog sync, pricing, coupons,
admin authorization, wallet and Hamyon callbacks (bad signature, single credit,
cancel), balance checkout, auto-refund when the supplier is unavailable, secret
masking, uploads, the account market and password reset.

## 6. Project layout

```
main.py                    single entry point (web + bot + workers)
app/api/                   FastAPI routers (auth, catalog, cart, checkout, wallet, market, admin, admin_ext, webhooks)
app/services/              orders, pricing, wallet, fulfillment, catalog_sync, media, env_store, notifications
app/providers/suppliers/   payerpin.py (PayerpinProvider)
app/providers/payments/    hamyon.py, signed-webhook provider
app/models/                SQLAlchemy models (users, games, products, orders, topups, wallet_transactions, ...)
bot/bot.py                 Telegram bot (/start → WebApp)
web/                       SPA (ES modules, no inline scripts, strict CSP)
deploy/mysql_setup.sql     database + least-privilege user
tests/                     pytest suite
```

## 7. Security

bcrypt password hashing, server-side hashed session tokens (HttpOnly cookie on the
web, Bearer token in the WebApp), Telegram `initData` HMAC verification, rate limiting,
strict CSP and security headers, upload validation (images are re-encoded with Pillow),
signed and deduplicated webhooks, and audit logs. Secrets never reach the frontend or logs.

## 8. Honest limitations

* No live Payerpin or Hamyon credentials were used during development, so neither
  integration has been tested against the real service. They follow the published
  API docs and are covered by tests with mocked responses.
* Until the catalog is synced, products show **Coming soon / NOT AVAILABLE**. Nothing is faked.
* Passwords are reset through a link the Telegram bot sends. This requires a linked
  Telegram account. There is no email sending.
