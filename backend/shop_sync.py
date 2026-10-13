"""
Order-data sync. When the uploaded workbook also contains the sheets customer_information, product_information and
orders_returns, they are loaded into the shop tables (customers, products, orders) BEFORE the reviews are checked
against them. Rows are upserted: new ids are added, existing ids are updated, nothing is deleted, so earlier uploads
keep working. A flat .csv or a workbook without those three sheets leaves the shop tables as they are.

Only the columns the app needs are taken. Names, age and gender in customer_information are never read into the database.
"""
import pandas as pd
from psycopg2.extras import execute_values

SHOP_SHEETS = ("customer_information", "product_information", "orders_returns")
PRIMARY_KEYS = {"customers": "customer_id", "products": "product_id", "orders": "order_id"}


def has_shop_sheets(book):
    return all(s in book for s in SHOP_SHEETS)


def _nullable(df):
    """NaN/NaT -> None so Postgres stores NULL."""
    return df.astype(object).where(df.notna(), None)


def _day(s):
    return pd.to_datetime(s, errors="coerce").dt.date


def prepare(book):
    cust, prod, orders = (book[s] for s in SHOP_SHEETS)
    tables = {
        "customers": pd.DataFrame({
            "customer_id": cust.customer_id, "segment": cust.customer_segment, "geography": cust.city,
            "new_or_returning": (cust.customer_segment == "New Customer").map({True: "New", False: "Returning"})}),
        "products": pd.DataFrame({
            "product_id": prod.product_id, "product_name": prod.product_name, "category": prod.category,
            "subcategory": prod.subcategory, "brand": prod.brand, "seller": prod.seller_name, "price": prod.price_usd}),
        "orders": pd.DataFrame({
            "order_id": orders.order_id, "customer_id": orders.customer_id, "product_id": orders.product_id,
            "order_date": _day(orders.order_date), "order_value": orders.order_value_usd,
            "returned": orders.return_flag.astype(bool), "return_date": _day(orders.return_date),
            "return_reason": orders.return_reason, "cancelled": orders.order_status.eq("Cancelled"),
            "promised_delivery_date": _day(orders.promised_delivery_date),
            "actual_delivery_date": _day(orders.actual_delivery_date), "refund_status": orders.refund_status}),
    }
    for name, pk in PRIMARY_KEYS.items():
        tables[name] = _nullable(tables[name].dropna(subset=[pk]).drop_duplicates(subset=pk, keep="last"))
    return tables


def sync_shop(conn, tables):
    """Upsert the three tables. Returns {table: rows_in_file}."""
    counts = {}
    with conn.cursor() as cur:
        for name, df in tables.items():
            pk, cols = PRIMARY_KEYS[name], list(df.columns)
            updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != pk)
            execute_values(
                cur, f"INSERT INTO shop.{name} ({', '.join(cols)}) VALUES %s "
                     f"ON CONFLICT ({pk}) DO UPDATE SET {updates}",
                list(df.itertuples(index=False, name=None)), page_size=2000)
            counts[name] = len(df)
    return counts
