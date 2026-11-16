# EasyCart Proof of Concept

AODB Group 6 · MSIT 620 · Designing a Modern Data Architecture for a Hypothetical Software System

EasyCart is a hypothetical Philippine marketplace for costumes and party supplies. This small project tests the core of our TO BE data architecture: **checkout that stays correct when many buyers want the last item**. All data is synthetic. No real payments are made.

## What it proves

| Demo | What you see | Requirement in the guidelines | Pain point fixed |
|---|---|---|---|
| 1. Flash sale | 50 buyers try to buy 10 units at the same moment. Exactly 10 succeed and nothing is oversold. The same sale with the old row-lock method is much slower | Polyglot persistence (PostgreSQL + Redis) | P1, P2 |
| 2. Payment | A signed (fake) PayMongo webhook saves the payment, marks the order PAID, lowers the official stock and writes an outbox event in one transaction. A repeated webhook is ignored. A wrongly signed one is rejected | API integration | P8 |
| 3. Unpaid order | A buyer who does not pay loses the hold after 30 seconds. The unit goes back on sale and the order is cancelled | Polyglot persistence | P2 |
| 4. Governance | A buyer is blocked from the analyst page. The analyst sees masked personal data. Every view and every blocked try is saved in the audit log | Data governance | P11 |

## What is inside

| Container | Job |
|---|---|
| `postgres` (PostgreSQL 17) | The official record: orders, payments, `stock_record`, `outbox_event`, `audit_log` |
| `redis` (Redis 7) | The fast stock counter (`stock:{sku_id}`) and the checkout hold (`hold:{order_id}:{sku_id}`) |
| `api` (Python, FastAPI) | The REST API, with a clickable test page at `/docs` |

Not built in this PoC (designed in Phase 4 only): Kafka, Elasticsearch, pgvector, Neo4j, Snowflake and the Kong gateway. The outbox events are still saved in PostgreSQL, ready for Kafka. A simple role check inside the API stands in for the gateway.

The checkout hold is 30 seconds here so the demo is quick. The real design uses 10 minutes (600 seconds).

## How to run it in GitHub Codespaces (no install needed)

1. Open this repository on github.com. Click the green **Code** button, then the **Codespaces** tab, then **Create codespace on main**. Wait until the editor opens in your browser.
2. In the **Terminal** at the bottom, type this and press Enter. The first time takes about 2 to 4 minutes.
   ```bash
   docker compose up -d --build
   ```
3. Check that all three containers are running:
   ```bash
   docker compose ps
   ```
4. A pop-up says the application on port 8000 is available. Click **Open in Browser**. If you miss it, open the **Ports** tab beside the Terminal, find port **8000**, and click the globe icon. The API test page (`/docs`) opens.

When you are done for the day, stop the codespace so it does not use your free hours: go to github.com/codespaces, click the three dots beside the codespace, then **Stop codespace**. The next time, start it again and run `docker compose up -d`.

## How to run it on your own computer

Install Docker Desktop, open a terminal in this folder, run `docker compose up -d --build`, then open http://localhost:8000/docs.

## The four demos

In the `/docs` page, click an endpoint, click **Try it out**, fill in the boxes, then click **Execute**. The answer appears under **Response body**.

### Demo 1: Flash sale

In the Terminal:

```bash
docker compose exec api python scripts/flash_sale.py
```

It prints a table. What to read:

- **bought** should equal the stock (10) and **oversold** should be 0 in both rows.
- **median** and **p95** show how long buyers waited. The TO BE row should be much faster than the AS IS row.
- "Redis matches Postgres: True" means the fast copy and the official record agree.

Try a bigger sale: `docker compose exec api python scripts/flash_sale.py --buyers 100 --stock 10`

### Demo 2: Payment

1. **POST /v1/demo/reset** → Execute (starts clean, stock of SKU 102 = 10).
2. **POST /v1/checkout** with `buyer_id` = 7 and `sku_id` = 102 → the answer shows `order_id` 1, status PENDING.
3. **POST /v1/demo/pay-fake/{order_id}** with 1 → 401, "Invalid signature". Nothing is saved.
4. **POST /v1/demo/pay/{order_id}** with 1 → "payment confirmed".
5. Click **Execute** again → "duplicate ignored". There is still only one payment.
6. **GET /v1/demo/state** → `postgres_on_hand` for SKU 102 is now 9, `payments` is 1, and there is a `payment.confirmed` outbox event.

### Demo 3: Unpaid order

1. **POST /v1/checkout** with `buyer_id` = 8 and `sku_id` = 102 → note the new `order_id`.
2. **GET /v1/orders/{order_id}** → status PENDING, and `hold_seconds_left` counts down.
3. Wait about 35 seconds. **GET /v1/orders/{order_id}** again → status CANCELLED.
4. **GET /v1/demo/state** → `redis_free` went back up, and there is an `order.cancelled` outbox event.

### Demo 4: Governance

1. **GET /v1/admin/orders** with `x-user` = juan and `x-role` = buyer → 403, blocked.
2. Same endpoint with `x-user` = ana and `x-role` = analyst → orders with masked name, email and phone.
3. **GET /v1/admin/audit-log** with `x-user` = dpo and `x-role` = admin → both tries are listed: the blocked one (`allowed` = false) and the allowed one.

## Seeing the data in each database

```bash
bash scripts/show_data.sh
```

This prints the PostgreSQL tables (orders, payment, stock_record, outbox_event, audit_log) and the Redis keys (stock counters and holds). Use it for the report screenshots.

## Files

| File | What it is |
|---|---|
| `docker-compose.yml` | Starts the three containers |
| `db/init.sql` | Tables and synthetic data |
| `api/app/main.py` | The whole API, with comments |
| `scripts/flash_sale.py` | Demo 1 load test |
| `scripts/show_data.sh` | Prints the data in PostgreSQL and Redis |

## If something goes wrong

| Problem | Fix |
|---|---|
| The page on port 8000 does not open | Run `docker compose ps`. If `api` is not "running", run `docker compose logs api` and read the last lines |
| "port is already allocated" | Run `docker compose down`, then `docker compose up -d` |
| You want a completely fresh start | Run `docker compose down -v`, then `docker compose up -d --build` |
| Numbers look odd after many tests | Use **POST /v1/demo/reset** |

## AI assistance

Claude (Anthropic) was used to help write and test this code. The group ran the demos, checked the results and is responsible for the content.
