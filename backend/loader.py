# """Upload parsing, validation and database loading.

# Primary format: one XLSX workbook with customer_reviews, review_metadata,
# customer_information, product_information, and orders_returns sheets.
# Also supports a legacy review-only workbook or a flat CSV. A combined CSV can
# populate reference tables when it contains the corresponding customer/product/order columns.
# """
# import uuid
# from datetime import date, datetime

# import pandas as pd
# from psycopg2.extras import execute_values

# from backend.db import execute, fetchone

# REQUIRED = ["review_id", "customer_id", "product_id", "review_date", "review_datetime", "rating",
#             "review_title", "review_text", "source", "verified_purchase", "helpful_votes",
#             "review_status", "reviewer_id", "order_id"]
# MUST_HAVE_VALUE = ["review_id", "customer_id", "product_id", "review_date", "review_datetime",
#                    "rating", "review_text", "order_id"]
# DEFAULTS = {"review_title": "", "source": "Unknown", "verified_purchase": False,
#             "helpful_votes": 0, "review_status": "Unknown"}
# ALIASES = {"source_channel": "source"}
# EXPECTED_SHEETS = {"customer_reviews", "review_metadata", "customer_information",
#                    "product_information", "orders_returns"}


# def parse_bool(v):
#     if pd.isna(v):
#         return False
#     return str(v).strip().lower() in ("true", "yes", "y", "1", "1.0", "t")


# def _clean_columns(df):
#     df = df.copy()
#     df.columns = [str(c).strip().lower() for c in df.columns]
#     return df


# def _read_source(file):
#     """Read the source once and return (review frame, derived columns, all sheets, source kind)."""
#     name = getattr(file, "name", str(file)).lower()
#     if hasattr(file, "seek"):
#         file.seek(0)
#     if name.endswith((".xlsx", ".xls")):
#         sheets = {str(k).strip().lower(): _clean_columns(v)
#                   for k, v in pd.read_excel(file, sheet_name=None).items()}
#         if "customer_reviews" not in sheets:
#             # Preserve support for simple one-sheet review workbooks.
#             first = next(iter(sheets.values()))
#             return first, {}, sheets, "single_sheet_workbook"
#         df = sheets["customer_reviews"].copy()
#         meta = sheets.get("review_metadata")
#         if meta is not None:
#             if "review_id" not in meta.columns:
#                 raise ValueError("The review_metadata sheet must contain review_id.")
#             duplicate_meta = int(meta["review_id"].duplicated().sum())
#             if duplicate_meta:
#                 raise ValueError(f"review_metadata contains {duplicate_meta} duplicate review_id values. Fix these before uploading.")
#             extra = [c for c in meta.columns if c == "review_id" or c not in df.columns]
#             df = df.merge(meta[extra], on="review_id", how="left", validate="one_to_one")
#         return df, {}, sheets, "workbook"
#     if hasattr(file, "seek"):
#         file.seek(0)
#     df = _clean_columns(pd.read_csv(file))
#     return df, {}, {}, "csv"


# def read_upload(file):
#     """Legacy helper: returns a normalized review dataframe and derived-column report."""
#     df, derived, _sheets, _kind = _read_source(file)
#     df = df.rename(columns=ALIASES)
#     if "review_datetime" not in df.columns and "review_date" in df.columns:
#         df["review_datetime"] = df["review_date"]
#         derived["review_datetime"] = "copied from review_date (file has no time of day, so hourly bursts cannot be detected)"
#     if "reviewer_id" not in df.columns and "customer_id" in df.columns:
#         df["reviewer_id"] = df["customer_id"]
#         derived["reviewer_id"] = "customer_id used as reviewer_id"
#     if "review_status" not in df.columns:
#         df["review_status"] = "Published"
#         derived["review_status"] = "column missing, every review treated as Published"
#     return df, derived


# def _ensure_reference_columns(conn):
#     """Safe additive migration for existing databases; does not drop user data."""
#     statements = [
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS age INT",
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS gender TEXT",
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS signup_date DATE",
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS preferred_channel TEXT",
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS lifetime_orders INT",
#         "ALTER TABLE shop.customers ADD COLUMN IF NOT EXISTS lifetime_value NUMERIC",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS product_description TEXT",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS seller_id TEXT",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS inventory_status TEXT",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS average_rating NUMERIC",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS review_count INT",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS size_chart_available BOOLEAN",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS product_image_count INT",
#         "ALTER TABLE shop.products ADD COLUMN IF NOT EXISTS launched_date DATE",
#         "ALTER TABLE shop.orders ADD COLUMN IF NOT EXISTS quantity INT",
#         "ALTER TABLE shop.orders ADD COLUMN IF NOT EXISTS unit_price NUMERIC",
#         "ALTER TABLE shop.orders ADD COLUMN IF NOT EXISTS payment_method TEXT",
#         "ALTER TABLE shop.orders ADD COLUMN IF NOT EXISTS refund_amount NUMERIC",
#         "ALTER TABLE shop.orders ADD COLUMN IF NOT EXISTS order_status TEXT",
#     ]
#     for sql in statements:
#         execute(conn, sql)


# def _require_columns(df, required, sheet_name):
#     missing = [c for c in required if c not in df.columns]
#     if missing:
#         raise ValueError(f"Sheet '{sheet_name}' is missing required columns: {missing}")


# def _check_unique_ids(df, key, sheet_name):
#     if key not in df.columns:
#         raise ValueError(f"Sheet '{sheet_name}' is missing required column '{key}'.")
#     blank = df[key].isna() | df[key].astype(str).str.strip().eq("")
#     if blank.any():
#         raise ValueError(f"Sheet '{sheet_name}' has {int(blank.sum())} blank {key} values.")
#     dupes = int(df[key].astype(str).str.strip().duplicated().sum())
#     if dupes:
#         raise ValueError(f"Sheet '{sheet_name}' has {dupes} duplicate {key} values. Fix these before uploading.")


# def _to_date(v):
#     if pd.isna(v) or v == "":
#         return None
#     parsed = pd.to_datetime(v, errors="coerce")
#     return None if pd.isna(parsed) else parsed.date()


# def _num(v):
#     if pd.isna(v) or v == "":
#         return None
#     value = pd.to_numeric(v, errors="coerce")
#     return None if pd.isna(value) else float(value)


# def _text(v):
#     if pd.isna(v):
#         return None
#     s = str(v).strip()
#     return s or None


# def _insert_reference_data(conn, sheets):
#     """Validate and replace all three reference tables from the uploaded workbook."""
#     cust = sheets["customer_information"].copy()
#     prod = sheets["product_information"].copy()
#     orders = sheets["orders_returns"].copy()
#     _require_columns(cust, ["customer_id", "customer_segment"], "customer_information")
#     _require_columns(prod, ["product_id", "product_name", "category"], "product_information")
#     _require_columns(orders, ["order_id", "customer_id", "product_id", "order_date"], "orders_returns")
#     _check_unique_ids(cust, "customer_id", "customer_information")
#     _check_unique_ids(prod, "product_id", "product_information")
#     _check_unique_ids(orders, "order_id", "orders_returns")

#     # Map the workbook's business-friendly columns to the database's stable schema.
#     cust_rows = []
#     for _, r in cust.iterrows():
#         city, state = _text(r.get("city")), _text(r.get("state"))
#         geography = ", ".join(x for x in (city, state) if x) or None
#         lifetime_orders = _num(r.get("lifetime_orders"))
#         new_returning = ("Returning" if lifetime_orders and float(lifetime_orders) > 1 else "New")
#         cust_rows.append((_text(r.get("customer_id")), _text(r.get("customer_segment")), geography,
#                           new_returning, _num(r.get("age")), _text(r.get("gender")), _to_date(r.get("signup_date")),
#                           _text(r.get("preferred_channel")), int(lifetime_orders) if lifetime_orders is not None else None,
#                           _num(r.get("lifetime_value_usd"))))

#     prod_rows = []
#     for _, r in prod.iterrows():
#         prod_rows.append((_text(r.get("product_id")), _text(r.get("product_name")), _text(r.get("category")),
#                           _text(r.get("subcategory")), _text(r.get("brand")), _text(r.get("seller_name")),
#                           _num(r.get("price_usd")), _text(r.get("product_description")), _text(r.get("seller_id")),
#                           _text(r.get("inventory_status")), _num(r.get("average_rating")),
#                           int(_num(r.get("review_count"))) if _num(r.get("review_count")) is not None else None,
#                           parse_bool(r.get("size_chart_available")),
#                           int(_num(r.get("product_image_count"))) if _num(r.get("product_image_count")) is not None else None,
#                           _to_date(r.get("launched_date"))))

#     order_rows = []
#     for _, r in orders.iterrows():
#         status = _text(r.get("order_status"))
#         cancelled = bool(status and "cancel" in status.lower())
#         returned = parse_bool(r.get("return_flag")) or bool(status and "return" in status.lower())
#         order_rows.append((_text(r.get("order_id")), _text(r.get("customer_id")), _text(r.get("product_id")),
#                            _to_date(r.get("order_date")), _num(r.get("order_value_usd")), returned,
#                            _to_date(r.get("return_date")), _text(r.get("return_reason")), cancelled,
#                            _to_date(r.get("promised_delivery_date")), _to_date(r.get("actual_delivery_date")),
#                            _text(r.get("refund_status")),
#                            int(_num(r.get("quantity"))) if _num(r.get("quantity")) is not None else None,
#                            _num(r.get("unit_price_usd")), _text(r.get("payment_method")),
#                            _num(r.get("refund_amount_usd")), status))

#     # All validation/mapping above happens before any existing reference data is deleted.
#     execute(conn, "TRUNCATE TABLE shop.orders, shop.products, shop.customers")
#     with conn.cursor() as cur:
#         if cust_rows:
#             execute_values(cur, """INSERT INTO shop.customers
#                 (customer_id, segment, geography, new_or_returning, age, gender, signup_date,
#                  preferred_channel, lifetime_orders, lifetime_value) VALUES %s""", cust_rows, page_size=1000)
#         if prod_rows:
#             execute_values(cur, """INSERT INTO shop.products
#                 (product_id, product_name, category, subcategory, brand, seller, price, product_description,
#                  seller_id, inventory_status, average_rating, review_count, size_chart_available,
#                  product_image_count, launched_date) VALUES %s""", prod_rows, page_size=1000)
#         if order_rows:
#             execute_values(cur, """INSERT INTO shop.orders
#                 (order_id, customer_id, product_id, order_date, order_value, returned, return_date,
#                  return_reason, cancelled, promised_delivery_date, actual_delivery_date, refund_status,
#                  quantity, unit_price, payment_method, refund_amount, order_status) VALUES %s""", order_rows, page_size=1000)
#     return {"customers_loaded": len(cust_rows), "products_loaded": len(prod_rows), "orders_loaded": len(order_rows)}


# def _populate_references_from_combined_csv(conn, df):
#     """If a flat CSV contains joined reference columns, derive distinct reference rows from it."""
#     required = {"customer_id", "product_id", "order_id"}
#     if not required.issubset(df.columns):
#         return None
#     has_customer = "customer_segment" in df.columns or "segment" in df.columns
#     has_product = "product_name" in df.columns
#     has_order = "order_value_usd" in df.columns or "order_value" in df.columns
#     if not (has_customer and has_product and has_order):
#         return None
#     d = df.copy()
#     # Construct workbook-shaped frames so there is only one mapping implementation.
#     customers = pd.DataFrame({
#         "customer_id": d["customer_id"],
#         "customer_segment": d.get("customer_segment", d.get("segment", "Unknown")),
#         "city": d.get("city"), "state": d.get("state"), "age": d.get("age"), "gender": d.get("gender"),
#         "signup_date": d.get("signup_date"), "preferred_channel": d.get("preferred_channel"),
#         "lifetime_orders": d.get("lifetime_orders"), "lifetime_value_usd": d.get("lifetime_value_usd")}).drop_duplicates("customer_id")
#     products = pd.DataFrame({
#         "product_id": d["product_id"], "product_name": d["product_name"], "category": d.get("category", "Unknown"),
#         "subcategory": d.get("subcategory"), "brand": d.get("brand"), "seller_name": d.get("seller_name", d.get("seller")),
#         "price_usd": d.get("price_usd", d.get("price")), "product_description": d.get("product_description"),
#         "seller_id": d.get("seller_id"), "inventory_status": d.get("inventory_status"),
#         "average_rating": d.get("average_rating"), "review_count": d.get("review_count"),
#         "size_chart_available": d.get("size_chart_available"), "product_image_count": d.get("product_image_count"),
#         "launched_date": d.get("launched_date")}).drop_duplicates("product_id")
#     orders = pd.DataFrame({
#         "order_id": d["order_id"], "customer_id": d["customer_id"], "product_id": d["product_id"],
#         "order_date": d.get("order_date"), "order_value_usd": d.get("order_value_usd", d.get("order_value")),
#         "quantity": d.get("quantity"), "unit_price_usd": d.get("unit_price_usd", d.get("unit_price")),
#         "payment_method": d.get("payment_method"), "promised_delivery_date": d.get("promised_delivery_date"),
#         "actual_delivery_date": d.get("actual_delivery_date"), "order_status": d.get("order_status"),
#         "return_flag": d.get("return_flag", d.get("returned")), "return_date": d.get("return_date"),
#         "return_reason": d.get("return_reason"), "refund_status": d.get("refund_status"),
#         "refund_amount_usd": d.get("refund_amount_usd", d.get("refund_amount"))}).drop_duplicates("order_id")
#     return _insert_reference_data(conn, {"customer_information": customers, "product_information": products,
#                                          "orders_returns": orders})


# def load_reviews(file, conn):
#     """Backward-compatible review-only loader. New pipeline uses load_all_upload_data."""
#     df, derived = read_upload(file)
#     return _insert_reviews(df, conn, derived)


# def _insert_reviews(df, conn, derived):
#     df = df.rename(columns=ALIASES).copy()
#     missing = [c for c in REQUIRED if c not in df.columns]
#     if missing:
#         raise ValueError(f"Review data is missing required columns: {missing}")
#     df = df[REQUIRED].copy()
#     df["review_datetime"] = pd.to_datetime(df["review_datetime"], errors="coerce")
#     df["review_date"] = pd.to_datetime(df["review_date"], errors="coerce").dt.date
#     df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
#     for c in ("review_id", "customer_id", "product_id", "order_id", "review_text"):
#         df[c] = df[c].where(df[c].isna(), df[c].astype(str).str.strip()).replace("", pd.NA)
#     rows_in_file = len(df)
#     df = df.dropna(subset=MUST_HAVE_VALUE).drop_duplicates(subset="review_id").copy()
#     if df.empty:
#         raise ValueError("No valid review rows remain after removing rows with missing required values.")
#     dropped_rows = rows_in_file - len(df)
#     invalid_rating = int((~df["rating"].between(1, 5)).sum())
#     if invalid_rating:
#         raise ValueError(f"Review data contains {invalid_rating} ratings outside the expected 1-5 range.")
#     defaulted = {c: int(df[c].isna().sum()) for c in [*DEFAULTS, "reviewer_id"]}
#     df["reviewer_id"] = df["reviewer_id"].fillna("anon-" + df["review_id"].astype(str))
#     for c, v in DEFAULTS.items():
#         df[c] = df[c].fillna(v)
#     df["verified_purchase"] = df["verified_purchase"].map(parse_bool)
#     df["helpful_votes"] = pd.to_numeric(df["helpful_votes"], errors="coerce").fillna(0).astype(int)
#     date_mismatch = int((df["review_datetime"].dt.date != df["review_date"]).sum())
#     upload_id = str(uuid.uuid4())[:8]
#     rows = [(upload_id, str(r.review_id), str(r.customer_id), str(r.product_id), r.review_date,
#              r.review_datetime.to_pydatetime(), int(r.rating), str(r.review_title), str(r.review_text),
#              str(r.source), bool(r.verified_purchase), int(r.helpful_votes), str(r.review_status),
#              str(r.reviewer_id), str(r.order_id)) for r in df.itertuples(index=False)]
#     with conn.cursor() as cur:
#         execute_values(cur, """INSERT INTO insight.reviews (upload_id, review_id, customer_id, product_id, review_date,
#             review_datetime, rating, review_title, review_text, source, verified_purchase, helpful_votes,
#             review_status, reviewer_id, order_id) VALUES %s""", rows, page_size=500)
#     return upload_id, {"rows_in_file": rows_in_file, "dropped_rows": dropped_rows,
#                        "defaulted_cells": defaulted, "date_mismatch": date_mismatch,
#                        "derived_columns": derived}


# def load_all_upload_data(file, conn):
#     """Load a workbook or CSV within the caller's database transaction."""
#     df, derived, sheets, kind = _read_source(file)
#     df = df.rename(columns=ALIASES).copy()
#     if "review_datetime" not in df.columns and "review_date" in df.columns:
#         df["review_datetime"] = df["review_date"]
#         derived["review_datetime"] = "copied from review_date (file has no time of day, so hourly bursts cannot be detected)"
#     if "reviewer_id" not in df.columns and "customer_id" in df.columns:
#         df["reviewer_id"] = df["customer_id"]
#         derived["reviewer_id"] = "customer_id used as reviewer_id"
#     if "review_status" not in df.columns:
#         df["review_status"] = "Published"
#         derived["review_status"] = "column missing, every review treated as Published"

#     if kind == "workbook" and {"customer_information", "product_information", "orders_returns"}.intersection(sheets):
#         missing_sheets = sorted(EXPECTED_SHEETS - set(sheets))
#         if missing_sheets:
#             raise ValueError(f"This workbook has some of the full-dataset sheets but is missing: {missing_sheets}. Upload all five dataset sheets together.")
#         _ensure_reference_columns(conn)
#         reference_report = _insert_reference_data(conn, sheets)
#         mode = "five_sheet_workbook"
#     elif kind == "csv":
#         # A CSV cannot contain sheets. Support a denormalized CSV only when it has
#         # review plus customer, product, and order columns; otherwise preserve references.
#         combined = {"customer_id", "product_id", "order_id"}.issubset(df.columns) and (
#             ("customer_segment" in df.columns or "segment" in df.columns)
#             and "product_name" in df.columns
#             and ("order_value_usd" in df.columns or "order_value" in df.columns)
#         )
#         if combined:
#             _ensure_reference_columns(conn)
#             reference_report = _populate_references_from_combined_csv(conn, df)
#             mode = "combined_csv"
#         else:
#             reference_report = {"reference_data": "not present as joined customer/product/order fields in CSV; existing reference tables preserved"}
#             mode = "review_only_csv"
#     else:
#         mode = "review_only_workbook"
#         reference_report = {"reference_data": "not present in upload; existing reference tables preserved"}

#     upload_id, load_report = _insert_reviews(df, conn, derived)
#     load_report.update(reference_report)
#     load_report["upload_mode"] = mode
#     return upload_id, load_report


# def check_orders(conn, upload_id):
#     """True if the review's order_id exists AND belongs to the same customer and product."""
#     execute(conn, """UPDATE insight.reviews r SET order_matched = EXISTS (
#         SELECT 1 FROM shop.orders o WHERE o.order_id = r.order_id AND o.customer_id = r.customer_id
#         AND o.product_id = r.product_id) WHERE r.upload_id = %s""", (upload_id,))


# def data_check(conn, upload_id, load_report):
#     row = fetchone(conn, """SELECT COUNT(*) AS reviews,
#         ROUND(100.0 * AVG((p.product_id IS NOT NULL)::int), 0) AS products_matched_pct,
#         ROUND(100.0 * AVG((c.customer_id IS NOT NULL)::int), 0) AS customers_matched_pct,
#         ROUND(100.0 * AVG(r.order_matched::int), 0) AS linked_to_order_pct,
#         COUNT(*) FILTER (WHERE NOT r.order_matched) AS order_id_not_matched,
#         ROUND(100.0 * AVG((r.review_status = 'Published')::int), 0) AS published_pct
#         FROM insight.reviews r LEFT JOIN shop.products p ON p.product_id = r.product_id
#         LEFT JOIN shop.customers c ON c.customer_id = r.customer_id WHERE r.upload_id = %s""", (upload_id,))
#     return {**{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in row.items()}, **load_report}


"""
STEP 1 - Upload and data check. No AI.

Accepts either
  (a) the two-sheet workbook your team already has: sheet `customer_reviews` (+ optional `review_metadata`,
      joined on review_id), or
  (b) a flat .csv/.xlsx that already has the 14 columns from the design doc.
Columns the file lacks entirely (review_datetime, reviewer_id, review_status) are derived and reported
in the data check ("derived_columns"), never silently invented.
"""
import uuid

import pandas as pd
from psycopg2.extras import execute_values

from backend.db import execute, fetchone
from backend.shop_sync import has_shop_sheets, prepare as prepare_shop, sync_shop

REQUIRED = ["review_id", "customer_id", "product_id", "review_date", "review_datetime", "rating",
            "review_title", "review_text", "source", "verified_purchase", "helpful_votes",
            "review_status", "reviewer_id", "order_id"]
MUST_HAVE_VALUE = ["review_id", "customer_id", "product_id", "review_date", "review_datetime",
                   "rating", "review_text", "order_id"]          # blank in any of these -> row dropped
DEFAULTS = {"review_title": "", "source": "Unknown", "verified_purchase": False,
            "helpful_votes": 0, "review_status": "Unknown"}
ALIASES = {"source_channel": "source"}


def parse_bool(v):
    return str(v).strip().lower() in ("true", "yes", "y", "1", "1.0")


def read_upload(file):
    """Returns (dataframe with the 14 columns where possible, {column: how it was derived}, shop tables or None).
    The shop tables are only present when the workbook has all three e-commerce sheets."""
    name = getattr(file, "name", str(file)).lower()
    shop = None
    if name.endswith((".xlsx", ".xls")):
        sheets = pd.read_excel(file, sheet_name=None)
        if has_shop_sheets(sheets):
            shop = prepare_shop(sheets)
        if "customer_reviews" in sheets:
            df = sheets["customer_reviews"]
            meta = sheets.get("review_metadata")
            if meta is not None:
                extra = [c for c in meta.columns if c == "review_id" or c not in df.columns]
                df = df.merge(meta[extra], on="review_id", how="left")
        else:
            df = next(iter(sheets.values()))
    else:
        df = pd.read_csv(file)

    df = df.rename(columns=ALIASES)
    derived = {}
    if "review_datetime" not in df.columns and "review_date" in df.columns:
        df["review_datetime"] = df["review_date"]
        derived["review_datetime"] = "copied from review_date (file has no time of day, so hourly bursts cannot be detected)"
    if "reviewer_id" not in df.columns and "customer_id" in df.columns:
        df["reviewer_id"] = df["customer_id"]
        derived["reviewer_id"] = "customer_id used as reviewer_id"
    if "review_status" not in df.columns:
        df["review_status"] = "Published"
        derived["review_status"] = "column missing, every review treated as Published"
    return df, derived, shop


def load_reviews(file, conn):
    df, derived, shop = read_upload(file)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"Review file is missing required columns: {missing}")

    shop_sync = sync_shop(conn, shop) if shop else None       # customers / products / orders from the same workbook
    df = df[REQUIRED].copy()
    df["review_datetime"] = pd.to_datetime(df["review_datetime"], errors="coerce")
    df["review_date"] = pd.to_datetime(df["review_date"], errors="coerce").dt.date
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce")
    for c in ("review_id", "customer_id", "product_id", "order_id", "review_text"):
        df[c] = df[c].where(df[c].isna(), df[c].astype(str).str.strip()).replace("", pd.NA)

    rows_in_file = len(df)
    df = df.dropna(subset=MUST_HAVE_VALUE).drop_duplicates(subset="review_id").copy()
    dropped_rows = rows_in_file - len(df)

    defaulted = {c: int(df[c].isna().sum()) for c in [*DEFAULTS, "reviewer_id"]}
    df["reviewer_id"] = df["reviewer_id"].fillna("anon-" + df["review_id"].astype(str))
    for c, v in DEFAULTS.items():
        df[c] = df[c].fillna(v)
    df["verified_purchase"] = df["verified_purchase"].map(parse_bool)
    df["helpful_votes"] = pd.to_numeric(df["helpful_votes"], errors="coerce").fillna(0).astype(int)
    date_mismatch = int((df["review_datetime"].dt.date != df["review_date"]).sum())

    upload_id = str(uuid.uuid4())[:8]
    rows = [(upload_id, r.review_id, r.customer_id, r.product_id, r.review_date,
             r.review_datetime.to_pydatetime(), int(r.rating), str(r.review_title), r.review_text,
             str(r.source), bool(r.verified_purchase), int(r.helpful_votes), str(r.review_status),
             str(r.reviewer_id), r.order_id)
            for r in df.itertuples(index=False)]
    with conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO insight.reviews (upload_id, review_id, customer_id, product_id, review_date,
                review_datetime, rating, review_title, review_text, source, verified_purchase,
                helpful_votes, review_status, reviewer_id, order_id) VALUES %s""", rows, page_size=500)

    return upload_id, {"rows_in_file": rows_in_file, "dropped_rows": dropped_rows,
                       "defaulted_cells": defaulted, "date_mismatch": date_mismatch,
                       "derived_columns": derived, "shop_sync": shop_sync}


def check_orders(conn, upload_id):
    """True if the review's order_id exists AND belongs to the same customer and product."""
    execute(conn, """
        UPDATE insight.reviews r SET order_matched = EXISTS (
            SELECT 1 FROM shop.orders o
            WHERE o.order_id = r.order_id AND o.customer_id = r.customer_id AND o.product_id = r.product_id)
        WHERE r.upload_id = %s""", (upload_id,))


def data_check(conn, upload_id, load_report):
    row = fetchone(conn, """
        SELECT COUNT(*) AS reviews,
               ROUND(100.0 * AVG((p.product_id IS NOT NULL)::int), 0) AS products_matched_pct,
               ROUND(100.0 * AVG((c.customer_id IS NOT NULL)::int), 0) AS customers_matched_pct,
               ROUND(100.0 * AVG(r.order_matched::int), 0) AS linked_to_order_pct,
               COUNT(*) FILTER (WHERE NOT r.order_matched) AS order_id_not_matched,
               ROUND(100.0 * AVG((r.review_status = 'Published')::int), 0) AS published_pct
        FROM insight.reviews r
        LEFT JOIN shop.products p ON p.product_id = r.product_id
        LEFT JOIN shop.customers c ON c.customer_id = r.customer_id
        WHERE r.upload_id = %s""", (upload_id,))
    return {**{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in row.items()}, **load_report}
