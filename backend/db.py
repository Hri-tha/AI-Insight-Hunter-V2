"""Postgres helper. register_vector teaches psycopg2 the `vector` type (numpy arrays in, numpy arrays out)."""
import psycopg2
import psycopg2.extras
from pgvector.psycopg2 import register_vector

from backend.config import DATABASE_URL


def get_connection(register=True):
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True          # every statement commits; the pipeline is idempotent per step
    if register:
        register_vector(conn)       # needs the extension to exist: run scripts/init_db.py first
    return conn


def fetchall(conn, sql, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetchone(conn, sql, params=None):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
