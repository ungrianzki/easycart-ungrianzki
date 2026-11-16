"""
Demo 1: flash sale.  Many buyers try to buy the last units at the same moment.

Run it with:
    docker compose exec api python scripts/flash_sale.py
Change the size with:
    docker compose exec api python scripts/flash_sale.py --buyers 100 --stock 10

It runs the same sale twice:
  1. TO BE  = Redis reservation + PostgreSQL order      (POST /v1/checkout)
  2. AS IS  = PostgreSQL row lock held during payment   (POST /v1/checkout-asis)
"""
import argparse
import statistics
import threading
import time

import httpx

API = "http://localhost:8000"
SKU = 102


def run_sale(path: str, buyers: int) -> dict:
    results = [None] * buyers
    start_gun = threading.Barrier(buyers)

    def one_buyer(i: int):
        with httpx.Client(timeout=30) as client:
            start_gun.wait()                       # everyone clicks at the same moment
            t0 = time.perf_counter()
            try:
                code = client.post(f"{API}{path}", params={"buyer_id": i + 1, "sku_id": SKU}).status_code
            except Exception:
                code = 0
            results[i] = (code, (time.perf_counter() - t0) * 1000)

    threads = [threading.Thread(target=one_buyer, args=(i,)) for i in range(buyers)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.perf_counter() - t0

    times = sorted(ms for _, ms in results)
    return {
        "bought": sum(1 for c, _ in results if c == 201),
        "sold_out": sum(1 for c, _ in results if c == 409),
        "errors": sum(1 for c, _ in results if c not in (201, 409)),
        "median_ms": statistics.median(times),
        "p95_ms": times[int(len(times) * 0.95) - 1],
        "slowest_ms": times[-1],
        "total_s": total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyers", type=int, default=50)
    ap.add_argument("--stock", type=int, default=10)
    a = ap.parse_args()

    print(f"\nFLASH SALE: {a.buyers} buyers, {a.stock} units of SKU {SKU}\n")
    rows = []
    for label, path in (("TO BE (Redis + PostgreSQL)", "/v1/checkout"), ("AS IS (PostgreSQL row lock)", "/v1/checkout-asis")):
        httpx.post(f"{API}/v1/demo/reset", params={"sku_id": SKU, "stock": a.stock}, timeout=30)
        res = run_sale(path, a.buyers)
        res["oversold"] = max(0, res["bought"] - a.stock)
        rows.append((label, res))
        if path == "/v1/checkout":
            state = httpx.get(f"{API}/v1/demo/state", timeout=30).json()
            sku_state = next(s for s in state["stock"] if s["sku_id"] == SKU)

    head = f"{'':30}{'bought':>8}{'sold out':>10}{'errors':>8}{'oversold':>10}{'median':>10}{'p95':>10}{'slowest':>10}{'total':>9}"
    print(head)
    print("-" * len(head))
    for label, x in rows:
        print(f"{label:30}{x['bought']:>8}{x['sold_out']:>10}{x['errors']:>8}{x['oversold']:>10}"
              f"{x['median_ms']:>8.0f}ms{x['p95_ms']:>8.0f}ms{x['slowest_ms']:>8.0f}ms{x['total_s']:>8.2f}s")

    print("\nAfter the TO BE sale:")
    print(f"  Redis free stock      : {sku_state['redis_free']}")
    print(f"  PostgreSQL on_hand    : {sku_state['postgres_on_hand']}  (goes down only when an order is paid)")
    print(f"  PENDING orders        : {sku_state['pending_orders']}")
    print(f"  Redis matches Postgres: {sku_state['redis_matches_postgres']}")
    print("\nNote: the AS IS run holds the stock lock for 0.1 s per order to simulate the payment call")
    print("that the old system made inside the transaction. Numbers change a little on every run.\n")


if __name__ == "__main__":
    main()
