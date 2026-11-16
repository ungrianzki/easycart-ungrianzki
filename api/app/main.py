"""
EasyCart Proof of Concept API (AODB Group 6, MSIT 620)

What this small API proves:
  Demo 1  Flash sale    Redis reserves stock in one atomic step, so nothing is oversold (P1, P2)
  Demo 2  Payment       payment + order PAID + stock + outbox are saved together; duplicates ignored (P8)
  Demo 3  Unpaid order  the hold expires, the unit goes back on sale, the order is cancelled (P2, BR1)
  Demo 4  Governance    role check, masked personal data, and an audit log of every view (P11)

Everything here uses synthetic data. No real payments are made.
"""
import hashlib
import hmac
import json
import os
import threading
import time
import uuid

import redis
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://easycart:easycart@localhost:5432/easycart")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
HOLD_SECONDS = int(os.environ.get("HOLD_SECONDS", "30"))       # the real design uses 600 (10 minutes)
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "demo-secret").encode()
ASIS_PAYMENT_DELAY = 0.1   # seconds. Simulates the payment call the old system made INSIDE the stock lock.

app = FastAPI(
    title="EasyCart Proof of Concept",
    description="PostgreSQL (official record) + Redis (fast stock) + REST API. Synthetic data only.",
    version="1.0",
)

pool = ConnectionPool(DATABASE_URL, min_size=5, max_size=20, kwargs={"row_factory": dict_row}, open=False)
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)

# The same Lua script as in the Phase 4 report. Redis runs it as ONE atomic step:
# it only takes a unit when stock is above zero, and it creates the hold in the same step.
RESERVE_LUA = """
-- KEYS[1] = stock:{sku}   KEYS[2] = hold:{order}:{sku}   ARGV[1] = hold seconds
local left = tonumber(redis.call('GET', KEYS[1]) or '0')
if left < 1 then return -1 end            -- sold out
redis.call('DECR', KEYS[1])
redis.call('SET', KEYS[2], 1, 'EX', ARGV[1])
return left - 1
"""
reserve_script = r.register_script(RESERVE_LUA)


# ------------------------------------------------------------------ startup
def sync_redis_from_postgres():
    """Redis is only a fast copy. Rebuild it from the official count in PostgreSQL:
    free units = on_hand minus units held by PENDING orders."""
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT s.sku_id, s.on_hand - COALESCE(p.pending, 0) AS free
               FROM stock_record s
               LEFT JOIN (SELECT sku_id, SUM(qty) AS pending FROM orders
                          WHERE status = 'PENDING' AND method = 'TO_BE' GROUP BY sku_id) p USING (sku_id)"""
        ).fetchall()
    for row in rows:
        r.set(f"stock:{row['sku_id']}", int(row["free"]))


def expire_unpaid_orders_forever():
    """Compensating step of the saga: an order not paid within the hold time is cancelled
    and its unit goes back on sale."""
    while True:
        try:
            with pool.connection() as conn:
                with conn.transaction():
                    expired = conn.execute(
                        """UPDATE orders SET status = 'CANCELLED'
                           WHERE status = 'PENDING' AND method = 'TO_BE'
                             AND created_at < now() - make_interval(secs => %s)
                           RETURNING order_id, sku_id, qty""",
                        (HOLD_SECONDS,),
                    ).fetchall()
                    for o in expired:
                        conn.execute(
                            "INSERT INTO outbox_event (aggregate_id, event_type, payload) VALUES (%s, %s, %s)",
                            (str(o["order_id"]), "order.cancelled", Jsonb({"order_id": o["order_id"], "reason": "hold expired"})),
                        )
            for o in expired:                       # after the commit: give the unit back
                r.incrby(f"stock:{o['sku_id']}", o["qty"])
                r.delete(f"hold:{o['order_id']}:{o['sku_id']}")
        except Exception as exc:                    # keep the loop alive during the demo
            print("expiry loop error:", exc)
        time.sleep(2)


@app.on_event("startup")
def startup():
    pool.open(wait=True, timeout=60)
    sync_redis_from_postgres()
    threading.Thread(target=expire_unpaid_orders_forever, daemon=True).start()


@app.get("/", include_in_schema=False)
def home():
    return RedirectResponse("/docs")


# ------------------------------------------------------------------ catalog
@app.get("/v1/products", tags=["Catalog"], summary="List products with live stock from Redis")
def list_products():
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT k.sku_id, p.title, k.size, k.price_php, p.attributes
               FROM sku k JOIN product p USING (product_id) ORDER BY k.sku_id"""
        ).fetchall()
    for row in rows:
        row["free_stock"] = int(r.get(f"stock:{row['sku_id']}") or 0)
    return rows


# ------------------------------------------------------------------ Demo 1: checkout (TO BE)
@app.post("/v1/checkout", status_code=201, tags=["Demo 1: Checkout"],
          summary="TO BE checkout: reserve in Redis, then save the order in PostgreSQL")
def checkout(buyer_id: int, sku_id: int, idempotency_key: str | None = Header(default=None)):
    # A double-clicked checkout sends the same Idempotency-Key and gets the same order back.
    if idempotency_key:
        existing = r.get(f"idem:{idempotency_key}")
        if existing:
            return {"order_id": int(existing), "status": "PENDING", "note": "same Idempotency-Key: existing order returned"}

    with pool.connection() as conn:
        order_id = conn.execute("SELECT nextval('orders_order_id_seq') AS id").fetchone()["id"]

    # Step 1 (Redis): one atomic step. Returns -1 when sold out.
    left = reserve_script(keys=[f"stock:{sku_id}", f"hold:{order_id}:{sku_id}"], args=[HOLD_SECONDS])
    if left == -1:
        raise HTTPException(status_code=409, detail="Sold out")

    # Step 2 (PostgreSQL): the order and its outbox event are saved together, or not at all.
    try:
        with pool.connection() as conn:
            with conn.transaction():
                price = conn.execute("SELECT price_php FROM sku WHERE sku_id = %s", (sku_id,)).fetchone()["price_php"]
                conn.execute(
                    """INSERT INTO orders (order_id, buyer_id, sku_id, total_php, status, idempotency_key)
                       VALUES (%s, %s, %s, %s, 'PENDING', %s)""",
                    (order_id, buyer_id, sku_id, price, idempotency_key),
                )
                conn.execute(
                    "INSERT INTO outbox_event (aggregate_id, event_type, payload) VALUES (%s, %s, %s)",
                    (str(order_id), "order.created", Jsonb({"order_id": order_id, "sku_id": sku_id, "buyer_id": buyer_id})),
                )
    except Exception:
        # Compensation: the order could not be saved, so give the unit back.
        r.incr(f"stock:{sku_id}")
        r.delete(f"hold:{order_id}:{sku_id}")
        raise HTTPException(status_code=500, detail="Order could not be saved; reservation released")

    if idempotency_key:
        r.set(f"idem:{idempotency_key}", order_id, ex=86400)
    return {"order_id": order_id, "status": "PENDING", "units_left": left, "pay_within_seconds": HOLD_SECONDS}


# ------------------------------------------------------------------ Demo 1: checkout (AS IS, for comparison)
@app.post("/v1/checkout-asis", status_code=201, tags=["Demo 1: Checkout"],
          summary="AS IS checkout: row lock in PostgreSQL held while the payment call runs")
def checkout_asis(buyer_id: int, sku_id: int):
    """The old method from Phase 2: SELECT ... FOR UPDATE locks the stock row, and the lock is
    held while the payment provider is called. Every other buyer of that item waits in line."""
    try:
        with pool.connection() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '5s'")
                row = conn.execute("SELECT qty FROM asis_stock WHERE sku_id = %s FOR UPDATE", (sku_id,)).fetchone()
                if row is None or row["qty"] < 1:
                    raise HTTPException(status_code=409, detail="Sold out")
                conn.execute("SELECT pg_sleep(%s)", (ASIS_PAYMENT_DELAY,))   # simulated payment call inside the lock
                conn.execute("UPDATE asis_stock SET qty = qty - 1 WHERE sku_id = %s", (sku_id,))
                price = conn.execute("SELECT price_php FROM sku WHERE sku_id = %s", (sku_id,)).fetchone()["price_php"]
                order_id = conn.execute(
                    """INSERT INTO orders (buyer_id, sku_id, total_php, status, method)
                       VALUES (%s, %s, %s, 'PAID', 'AS_IS') RETURNING order_id""",
                    (buyer_id, sku_id, price),
                ).fetchone()["order_id"]
        return {"order_id": order_id, "status": "PAID", "method": "AS_IS"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Checkout timed out waiting for the stock lock: {type(exc).__name__}")


# ------------------------------------------------------------------ Demo 2: payment webhook
def sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET, body, hashlib.sha256).hexdigest()


def confirm_payment(event: dict) -> dict:
    """One PostgreSQL transaction: payment row + order PAID + stock_record - 1 + outbox event."""
    order_id, paymongo_id = int(event["order_id"]), str(event["id"])
    with pool.connection() as conn:
        with conn.transaction():
            saved = conn.execute(
                """INSERT INTO payment (order_id, paymongo_id, amount_php) VALUES (%s, %s, %s)
                   ON CONFLICT (paymongo_id) DO NOTHING RETURNING payment_id""",
                (order_id, paymongo_id, event["amount_php"]),
            ).fetchone()
            if saved is None:
                return {"result": "duplicate ignored", "paymongo_id": paymongo_id}
            order = conn.execute(
                "UPDATE orders SET status = 'PAID' WHERE order_id = %s AND status = 'PENDING' RETURNING sku_id, qty",
                (order_id,),
            ).fetchone()
            if order is None:
                # Raising here rolls back the payment row too: all or nothing.
                raise HTTPException(status_code=409, detail="Order is not PENDING (unknown, already paid or cancelled)")
            conn.execute(
                "UPDATE stock_record SET on_hand = on_hand - %s, version = version + 1, updated_at = now() WHERE sku_id = %s",
                (order["qty"], order["sku_id"]),
            )
            conn.execute(
                "INSERT INTO outbox_event (aggregate_id, event_type, payload) VALUES (%s, %s, %s)",
                (str(order_id), "payment.confirmed", Jsonb({"order_id": order_id, "paymongo_id": paymongo_id})),
            )
    r.delete(f"hold:{order_id}:{order['sku_id']}")   # the hold is no longer needed
    return {"result": "payment confirmed", "order_id": order_id, "status": "PAID"}


def verify_and_confirm(body: bytes, signature: str | None) -> dict:
    """Signature first. A webhook that is not signed with our secret is rejected before anything is saved."""
    if not signature or not hmac.compare_digest(signature, sign(body)):
        raise HTTPException(status_code=401, detail="Invalid signature: webhook rejected, nothing saved")
    return confirm_payment(json.loads(body))


@app.post("/v1/webhooks/paymongo", tags=["Demo 2: Payment"],
          summary="The real webhook endpoint: checks the signature first, then confirms the payment")
async def paymongo_webhook(request: Request, paymongo_signature: str | None = Header(default=None)):
    return verify_and_confirm(await request.body(), paymongo_signature)


def build_event(order_id: int) -> bytes:
    with pool.connection() as conn:
        order = conn.execute("SELECT total_php FROM orders WHERE order_id = %s", (order_id,)).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    event = {"id": f"pay_demo_{order_id}", "type": "payment.paid", "order_id": order_id, "amount_php": float(order["total_php"])}
    return json.dumps(event).encode()


@app.post("/v1/demo/pay/{order_id}", tags=["Demo 2: Payment"],
          summary="Pretend to be PayMongo: send a correctly signed webhook for this order")
def simulate_paymongo(order_id: int):
    """Click it twice for the same order to see the duplicate being ignored."""
    body = build_event(order_id)
    return {"webhook_sent": json.loads(body), "webhook_answer": verify_and_confirm(body, sign(body))}


@app.post("/v1/demo/pay-fake/{order_id}", tags=["Demo 2: Payment"],
          summary="Pretend to be an attacker: send the webhook with a wrong signature")
def simulate_fake_webhook(order_id: int):
    return verify_and_confirm(build_event(order_id), "not-the-real-signature")


@app.get("/v1/orders/{order_id}", tags=["Demo 2: Payment"], summary="Order status")
def get_order(order_id: int):
    with pool.connection() as conn:
        order = conn.execute(
            "SELECT order_id, sku_id, total_php, status, method, created_at FROM orders WHERE order_id = %s", (order_id,)
        ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    ttl = r.ttl(f"hold:{order_id}:{order['sku_id']}")
    order["hold_seconds_left"] = ttl if ttl and ttl > 0 else 0
    return order


# ------------------------------------------------------------------ Demo 4: governance
def mask_name(name: str) -> str:
    return " ".join(part[0] + "***" for part in name.split())


def mask_email(email: str) -> str:
    user, _, domain = email.partition("@")
    return user[0] + "***@" + domain


def mask_phone(phone: str) -> str:
    return phone[:4] + "*****" + phone[-2:]


def audit(actor: str, role: str, action: str, detail: str, allowed: bool):
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO audit_log (actor, role, action, detail, allowed) VALUES (%s, %s, %s, %s, %s)",
            (actor, role, action, detail, allowed),
        )


@app.get("/v1/admin/orders", tags=["Demo 4: Governance"],
         summary="Analyst view of recent orders: personal data is masked and the view is logged")
def analyst_orders(x_user: str = Header(description="Who you are, e.g. ana"),
                   x_role: str = Header(description="buyer, seller, analyst or admin")):
    if x_role not in ("analyst", "admin"):
        audit(x_user, x_role, "VIEW_ORDERS", "blocked: role not allowed", False)
        raise HTTPException(status_code=403, detail=f"Role '{x_role}' may not view customer orders")
    with pool.connection() as conn:
        rows = conn.execute(
            """SELECT o.order_id, o.status, o.total_php, b.name, b.email, b.phone
               FROM orders o JOIN buyer b USING (buyer_id) ORDER BY o.order_id DESC LIMIT 10"""
        ).fetchall()
    for row in rows:
        row["name"], row["email"], row["phone"] = mask_name(row["name"]), mask_email(row["email"]), mask_phone(row["phone"])
    audit(x_user, x_role, "VIEW_ORDERS", f"viewed {len(rows)} orders (masked)", True)
    return rows


@app.get("/v1/admin/audit-log", tags=["Demo 4: Governance"],
         summary="Admin (Data Protection Officer) view: who looked at personal data, and who was blocked")
def read_audit_log(x_user: str = Header(description="Who you are, e.g. dpo"),
                   x_role: str = Header(description="only admin is allowed")):
    if x_role != "admin":
        audit(x_user, x_role, "VIEW_AUDIT_LOG", "blocked: role not allowed", False)
        raise HTTPException(status_code=403, detail=f"Role '{x_role}' may not view the audit log")
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 20").fetchall()


# ------------------------------------------------------------------ demo helpers
@app.get("/v1/demo/state", tags=["Demo helpers"],
         summary="Compare Redis with PostgreSQL, and count orders, payments and outbox events")
def state():
    with pool.connection() as conn:
        stock = conn.execute(
            """SELECT s.sku_id, s.on_hand AS postgres_on_hand, COALESCE(p.pending, 0) AS pending_orders
               FROM stock_record s
               LEFT JOIN (SELECT sku_id, SUM(qty) AS pending FROM orders
                          WHERE status = 'PENDING' AND method = 'TO_BE' GROUP BY sku_id) p USING (sku_id)
               ORDER BY s.sku_id"""
        ).fetchall()
        orders = conn.execute("SELECT method, status, COUNT(*) AS n FROM orders GROUP BY method, status ORDER BY 1, 2").fetchall()
        payments = conn.execute("SELECT COUNT(*) AS n FROM payment").fetchone()["n"]
        outbox = conn.execute("SELECT event_type, COUNT(*) AS n FROM outbox_event GROUP BY event_type ORDER BY 1").fetchall()
        asis = conn.execute("SELECT sku_id, qty FROM asis_stock ORDER BY sku_id").fetchall()
    for row in stock:
        row["redis_free"] = int(r.get(f"stock:{row['sku_id']}") or 0)
        row["expected_free"] = int(row["postgres_on_hand"] - row["pending_orders"])
        row["redis_matches_postgres"] = row["redis_free"] == row["expected_free"]
    return {"stock": stock, "orders": orders, "payments": payments, "outbox_events": outbox, "asis_stock": asis}


@app.post("/v1/demo/reset", tags=["Demo helpers"], summary="Start again: clear orders and set the stock of one SKU")
def reset(sku_id: int = 102, stock: int = 10):
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute("TRUNCATE payment, orders, outbox_event RESTART IDENTITY")
            conn.execute("UPDATE stock_record SET on_hand = %s, version = 0 WHERE sku_id = %s", (stock, sku_id))
            conn.execute(
                "INSERT INTO asis_stock VALUES (%s, %s) ON CONFLICT (sku_id) DO UPDATE SET qty = EXCLUDED.qty", (sku_id, stock)
            )
    for key in r.scan_iter("hold:*"):
        r.delete(key)
    for key in r.scan_iter("idem:*"):
        r.delete(key)
    sync_redis_from_postgres()
    return {"reset": True, "sku_id": sku_id, "stock": stock}
