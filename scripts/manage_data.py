"""
Backend-only data management. There is deliberately no delete button in the app.

Every upload gets its own upload_id, and every query in the app filters on it, so a new upload never changes or
mixes with an earlier one. Old uploads just stay in the database (reviews + embeddings) until you remove them here.

    python -m scripts.manage_data list                          # what is stored, newest first
    python -m scripts.manage_data delete --upload-id 1a2b3c4d   # one upload
    python -m scripts.manage_data delete --older-than-days 30   # everything older than 30 days
    python -m scripts.manage_data delete --keep-last 3          # keep the 3 newest uploads, delete the rest
    python -m scripts.manage_data delete --all                  # every upload
    python -m scripts.manage_data clear-tag-cache               # forget remembered tags (see note below)

Add --yes to skip the confirmation question (for cron jobs). The shop tables are never touched.

Tag cache: tags are remembered per (title, text) so repeated wording is not sent to the AI twice, and it is shared by
all uploads. Deleting uploads keeps it on purpose (the next upload is faster). Clear it only after you change the
taxonomy or the model, otherwise old tags would be reused.
"""
import argparse

from backend.config import require_settings
from backend.db import execute, fetchall, fetchone, get_connection

LIST_SQL = """
    SELECT r.upload_id, COUNT(*) AS reviews, MIN(r.review_date) AS first_review, MAX(r.review_date) AS last_review,
           COALESCE(a.created_at, now()) AS analysed_at, (a.upload_id IS NOT NULL) AS has_results
    FROM insight.reviews r LEFT JOIN insight.analysis_runs a USING (upload_id)
    GROUP BY r.upload_id, a.created_at, a.upload_id ORDER BY analysed_at DESC"""


def list_uploads(conn):
    rows = fetchall(conn, LIST_SQL)
    if not rows:
        print("No uploads stored.")
        return rows
    print(f"{'upload_id':<10} {'reviews':>8}  {'review dates':<25} {'analysed at':<20} results")
    for r in rows:
        print(f"{r['upload_id']:<10} {r['reviews']:>8,}  {str(r['first_review']) + ' .. ' + str(r['last_review']):<25} "
              f"{r['analysed_at']:%Y-%m-%d %H:%M}    {'yes' if r['has_results'] else 'no'}")
    return rows


def pick_ids(conn, args):
    rows = fetchall(conn, LIST_SQL)             # newest first
    if args.all:
        return [r["upload_id"] for r in rows]
    if args.upload_id:
        return [r["upload_id"] for r in rows if r["upload_id"] == args.upload_id]
    if args.keep_last is not None:
        return [r["upload_id"] for r in rows[args.keep_last:]]
    cutoff = fetchall(conn, "SELECT upload_id FROM insight.analysis_runs WHERE created_at < now() - make_interval(days => %s)",
                      (args.older_than_days,))
    return [r["upload_id"] for r in cutoff]


def delete_uploads(conn, ids):
    n = 0
    for uid in ids:
        execute(conn, "DELETE FROM insight.analysis_runs WHERE upload_id = %s", (uid,))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM insight.reviews WHERE upload_id = %s", (uid,))
            n += cur.rowcount
    if ids:
        execute(conn, "VACUUM ANALYZE insight.reviews")      # give the space (mostly embeddings) back
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    d = sub.add_parser("delete")
    which = d.add_mutually_exclusive_group(required=True)
    which.add_argument("--upload-id")
    which.add_argument("--older-than-days", type=int)
    which.add_argument("--keep-last", type=int)
    which.add_argument("--all", action="store_true")
    d.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    c = sub.add_parser("clear-tag-cache")
    c.add_argument("--yes", action="store_true")
    args = ap.parse_args()

    require_settings()
    conn = get_connection(register=False)
    try:
        if args.cmd == "list":
            list_uploads(conn)
        elif args.cmd == "clear-tag-cache":
            n = fetchone(conn, "SELECT COUNT(*) AS n FROM insight.tag_cache")["n"]
            if args.yes or input(f"Delete {n:,} remembered tags? [y/N] ").lower() == "y":
                execute(conn, "TRUNCATE insight.tag_cache")
                print("Tag cache cleared.")
        else:
            ids = pick_ids(conn, args)
            if not ids:
                print("Nothing matches. Nothing deleted.")
                return
            print(f"About to delete {len(ids)} upload(s): {', '.join(ids)}")
            if args.yes or input("Type 'yes' to continue: ").strip().lower() == "yes":
                print(f"Deleted {delete_uploads(conn, ids):,} review rows and their results.")
            else:
                print("Cancelled.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
