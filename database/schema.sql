-- AI Insight Hunter v2 - run once against the NEW database (scripts/init_db.py does this for you).
-- Two schemas, so nothing here can collide with the old app's public.reviews / public.analysis_runs tables.
--   shop    = stands in for the e-commerce database (read only for the app). Demo-seeded by scripts/seed_shop.py
--   insight = the app's own tables

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS shop;
CREATE SCHEMA IF NOT EXISTS insight;

-- ---------------- shop (e-commerce side) ----------------
CREATE TABLE IF NOT EXISTS shop.customers (
    customer_id TEXT PRIMARY KEY, segment TEXT, geography TEXT, new_or_returning TEXT,
    age INT, gender TEXT, signup_date DATE, preferred_channel TEXT, lifetime_orders INT, lifetime_value NUMERIC);

CREATE TABLE IF NOT EXISTS shop.products (
    product_id TEXT PRIMARY KEY, product_name TEXT, category TEXT, subcategory TEXT,
    brand TEXT, seller TEXT, price NUMERIC, product_description TEXT, seller_id TEXT,
    inventory_status TEXT, average_rating NUMERIC, review_count INT, size_chart_available BOOLEAN,
    product_image_count INT, launched_date DATE);

CREATE TABLE IF NOT EXISTS shop.orders (
    order_id TEXT PRIMARY KEY, customer_id TEXT, product_id TEXT, order_date DATE,
    order_value NUMERIC, returned BOOLEAN, return_date DATE, return_reason TEXT, cancelled BOOLEAN,
    promised_delivery_date DATE, actual_delivery_date DATE, refund_status TEXT,
    quantity INT, unit_price NUMERIC, payment_method TEXT, refund_amount NUMERIC, order_status TEXT);
CREATE INDEX IF NOT EXISTS orders_date_idx ON shop.orders (order_date);

-- ---------------- insight (our tables) ----------------
-- embedding is 384 numbers: the local all-MiniLM-L6-v2 model, same as the old app.
CREATE TABLE IF NOT EXISTS insight.reviews (
    upload_id TEXT NOT NULL, review_id TEXT NOT NULL,
    customer_id TEXT NOT NULL, product_id TEXT NOT NULL,
    review_date DATE NOT NULL, review_datetime TIMESTAMP NOT NULL, rating INT NOT NULL,
    review_title TEXT NOT NULL DEFAULT '', review_text TEXT NOT NULL,
    source TEXT NOT NULL, verified_purchase BOOLEAN NOT NULL, helpful_votes INT NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL, reviewer_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_matched BOOLEAN,
    issue_category TEXT, issue_subcategory TEXT, problem TEXT,
    sentiment TEXT, severity TEXT, evidence_quote TEXT,
    embedding VECTOR(384),
    suspicion_signals TEXT[], suspicion_level TEXT,
    PRIMARY KEY (upload_id, review_id));
CREATE INDEX IF NOT EXISTS reviews_upload_idx ON insight.reviews (upload_id, review_date);
CREATE INDEX IF NOT EXISTS reviews_product_idx ON insight.reviews (upload_id, product_id);
CREATE INDEX IF NOT EXISTS reviews_reviewer_idx ON insight.reviews (upload_id, reviewer_id);

CREATE TABLE IF NOT EXISTS insight.analysis_runs (
    upload_id TEXT PRIMARY KEY, results JSONB, created_at TIMESTAMP DEFAULT now());

-- Cache of identical (title, text) tagging results, so repeated wording is only sent to the LLM once.
CREATE TABLE IF NOT EXISTS insight.tag_cache (
    review_title TEXT NOT NULL, review_text TEXT NOT NULL,
    issue_category TEXT, issue_subcategory TEXT, problem TEXT, sentiment TEXT, severity TEXT, evidence_quote TEXT,
    PRIMARY KEY (review_title, review_text));

-- Shop wording -> our taxonomy sub-category. Used for the "return reason matches the complaint" tile.
CREATE TABLE IF NOT EXISTS insight.return_reason_map (return_reason TEXT PRIMARY KEY, issue_subcategory TEXT);
INSERT INTO insight.return_reason_map VALUES
    ('Size too large', 'Size/Fit'), ('Size too small', 'Size/Fit'),
    ('Late delivery', 'Late Delivery'), ('Product not as described', 'Product Information'),
    ('Quality issue', 'Quality'), ('Product damaged', 'Damaged Package')
ON CONFLICT DO NOTHING;
-- 'Changed mind' and 'Wrong item received' have no taxonomy equivalent, so they never count as a match.

-- ---------------- views ----------------
CREATE OR REPLACE VIEW insight.review_facts AS
SELECT r.*, p.product_name, p.category, p.subcategory, p.brand, p.seller, p.price,
       c.segment, c.geography, c.new_or_returning
FROM insight.reviews r
LEFT JOIN shop.products p ON p.product_id = r.product_id
LEFT JOIN shop.customers c ON c.customer_id = r.customer_id;

CREATE OR REPLACE VIEW insight.order_facts AS
SELECT o.*, p.category, p.subcategory, p.brand, p.seller, p.price,
       c.segment, c.geography, c.new_or_returning
FROM shop.orders o
LEFT JOIN shop.products p ON p.product_id = o.product_id
LEFT JOIN shop.customers c ON c.customer_id = o.customer_id;
