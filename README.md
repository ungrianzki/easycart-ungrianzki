# EasyCart Proof of Concept

AODB Group 6 ·  Designing a Modern Data Architecture for a Hypothetical Software System

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



