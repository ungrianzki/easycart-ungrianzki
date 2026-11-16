-- EasyCart PoC database (PostgreSQL 17). Synthetic data only.
-- This is a small part of the Phase 4 model (Figure 17).

CREATE TABLE buyer (
  buyer_id   BIGINT PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL,   -- personal data (the full design encrypts these columns)
  phone      TEXT NOT NULL,   -- personal data
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE product (
  product_id BIGINT PRIMARY KEY,
  title      TEXT NOT NULL,
  attributes JSONB NOT NULL            -- document model: flexible attributes
);

CREATE TABLE sku (
  sku_id     BIGINT PRIMARY KEY,
  product_id BIGINT NOT NULL REFERENCES product(product_id),
  size       TEXT NOT NULL,
  price_php  NUMERIC(10,2) NOT NULL
);

-- The official stock count. Redis only holds the fast copy.
CREATE TABLE stock_record (
  sku_id     BIGINT PRIMARY KEY REFERENCES sku(sku_id),
  on_hand    INT NOT NULL CHECK (on_hand >= 0),
  version    INT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE orders (
  order_id        BIGSERIAL PRIMARY KEY,
  buyer_id        BIGINT NOT NULL REFERENCES buyer(buyer_id),
  sku_id          BIGINT NOT NULL REFERENCES sku(sku_id),
  qty             INT NOT NULL DEFAULT 1,
  total_php       NUMERIC(10,2) NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('PENDING','PAID','CANCELLED')),
  idempotency_key TEXT UNIQUE,
  method          TEXT NOT NULL DEFAULT 'TO_BE',   -- TO_BE or AS_IS (for the comparison demo)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE payment (
  payment_id  BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES orders(order_id),
  paymongo_id TEXT NOT NULL UNIQUE,     -- stops the same webhook from being saved twice
  amount_php  NUMERIC(10,2) NOT NULL,
  paid_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transactional outbox: written in the same transaction as the business change.
CREATE TABLE outbox_event (
  event_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  aggregate_id TEXT NOT NULL,
  event_type   TEXT NOT NULL,
  payload      JSONB NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ               -- stays empty here: Kafka is not part of the PoC
);

-- Append-only log of who looked at personal data.
CREATE TABLE audit_log (
  log_id     BIGSERIAL PRIMARY KEY,
  actor      TEXT NOT NULL,
  role       TEXT NOT NULL,
  action     TEXT NOT NULL,
  detail     TEXT,
  allowed    BOOLEAN NOT NULL,
  logged_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stock table used only by the AS IS comparison (row lock method).
CREATE TABLE asis_stock (
  sku_id BIGINT PRIMARY KEY,
  qty    INT NOT NULL
);

-- ---------- synthetic data ----------
INSERT INTO buyer (buyer_id, name, email, phone)
SELECT g,
       (ARRAY['Maria','Jose','Ana','Juan','Liza','Mark','Grace','Paolo','Carla','Miguel'])[1 + g % 10] || ' ' ||
       (ARRAY['Santos','Reyes','Cruz','Bautista','Garcia','Mendoza','Torres','Flores','Ramos','Aquino','Navarro'])[1 + g % 11],
       'buyer' || g || '@example.com',
       '0917' || lpad(g::text, 7, '0')
FROM generate_series(1, 300) g;

INSERT INTO product VALUES
 (1, 'Girls'' Witch Costume',  '{"character":"Witch","theme":["Halloween","UN Day"],"age_range":"6-8"}'),
 (2, 'Boys'' Barong Tagalog',  '{"character":"Filipiniana","theme":["Buwan ng Wika"],"age_range":"6-8"}'),
 (3, 'Kids'' Superhero Cape',  '{"character":"Superhero","theme":["Halloween"],"age_range":"4-6"}');

INSERT INTO sku VALUES
 (101, 1, 'S', 799.00), (102, 1, 'M', 799.00), (103, 1, 'L', 849.00),
 (201, 2, 'S', 650.00), (202, 2, 'M', 650.00),
 (301, 3, 'One size', 299.00);

INSERT INTO stock_record (sku_id, on_hand) VALUES
 (101, 25), (102, 10), (103, 25), (201, 40), (202, 40), (301, 60);

INSERT INTO asis_stock VALUES (102, 10);
