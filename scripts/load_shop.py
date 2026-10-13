"""
OPTIONAL admin step. Uploading the full workbook in the app already does this automatically; use this script
only to load the order data without running an analysis.

In production the e-commerce app owns and feeds these tables, so you would NOT run this. Here the e-commerce
data comes from the same workbook the reviews come from (sheets customer_information, product_information,
orders_returns), so load it once and every uploaded review can find its order.

    python -m scripts.load_shop --src path/to/AI_Customer_Intelligence_MVP_Datasets.xlsx

It adds new rows and updates existing ones (nothing is deleted). It never touches the insight schema (uploads, tags, results).
Personal fields (names, age, gender) are not loaded; the app never needs them.
"""
import argparse

import pandas as pd

from backend.config import require_settings
from backend.db import get_connection
from backend.shop_sync import SHOP_SHEETS, has_shop_sheets, prepare, sync_shop


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="the .xlsx workbook with the 5 sheets")
    src = ap.parse_args().src

    book = pd.read_excel(src, sheet_name=None)
    if not has_shop_sheets(book):
        raise SystemExit(f"Workbook is missing sheet(s): {[s for s in SHOP_SHEETS if s not in book]}")
    require_settings()
    conn = get_connection(register=False)
    for name, n in sync_shop(conn, prepare(book)).items():
        print(f"shop.{name}: {n:,} rows loaded or updated")
    conn.close()


if __name__ == "__main__":
    main()
