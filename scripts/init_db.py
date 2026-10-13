"""Creates the shop + insight schemas, tables and views in the NEW database.   python -m scripts.init_db"""
from pathlib import Path

from backend.config import require_settings
from backend.db import get_connection

if __name__ == "__main__":
    require_settings()
    conn = get_connection(register=False)   # the vector type does not exist until the script has run
    with conn.cursor() as cur:
        cur.execute((Path(__file__).resolve().parent.parent / "database" / "schema.sql").read_text())
    conn.close()
    print("Schema ready (schemas: shop, insight).")
