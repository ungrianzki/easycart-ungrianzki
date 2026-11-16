#!/bin/bash
# Prints what is stored in each database. Use it for the report screenshots.
#   bash scripts/show_data.sh
echo "================ PostgreSQL: orders (latest 5) ================"
docker compose exec -T postgres psql -U easycart -d easycart -c "SELECT order_id, buyer_id, sku_id, total_php, status, method, created_at FROM orders ORDER BY order_id DESC LIMIT 5;"
echo "================ PostgreSQL: payment ================"
docker compose exec -T postgres psql -U easycart -d easycart -c "SELECT * FROM payment ORDER BY payment_id DESC LIMIT 5;"
echo "================ PostgreSQL: stock_record (official count) ================"
docker compose exec -T postgres psql -U easycart -d easycart -c "SELECT * FROM stock_record ORDER BY sku_id;"
echo "================ PostgreSQL: outbox_event (latest 5) ================"
docker compose exec -T postgres psql -U easycart -d easycart -c "SELECT event_type, aggregate_id, payload, created_at, published_at FROM outbox_event ORDER BY created_at DESC LIMIT 5;"
echo "================ PostgreSQL: audit_log (latest 5) ================"
docker compose exec -T postgres psql -U easycart -d easycart -c "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT 5;"
echo "================ Redis: stock counters ================"
for k in $(docker compose exec -T redis redis-cli --scan --pattern 'stock:*' | sort); do
  echo "$k = $(docker compose exec -T redis redis-cli GET $k)"
done
echo "================ Redis: checkout holds (seconds left) ================"
for k in $(docker compose exec -T redis redis-cli --scan --pattern 'hold:*' | sort); do
  echo "$k expires in $(docker compose exec -T redis redis-cli TTL $k) s"
done
echo "(no lines above = no active holds)"
