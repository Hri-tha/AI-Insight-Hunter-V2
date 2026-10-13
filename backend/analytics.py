"""
STEP 3 - Analytics and review quality, STEP 4 - decision. SQL + Python only, no LLM.
Counts, rates per 100 orders, trends, business impact, review-quality signals.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

from backend import config
from backend.db import execute, fetchall, fetchone
from backend.taxonomy import (BREAKDOWN_DIMS, CONCENTRATION_DIMS, DIMENSIONS, REVIEW_ONLY_FILTERS,
                              SEED_PROBLEMS, SHARED_FILTERS)

PROBLEM_ROWS = ("problem <> 'No Specific Problem' AND sentiment IN ('Negative', 'Mixed') "
                "AND review_status = 'Published'")


@dataclass
class Ctx:
    conn: object
    upload_id: str
    first: date                      # first review date in scope
    last: date                       # last review date in scope = the "as of" date for trends
    lag_days: int = config.LAG_DAYS
    flt: dict = field(default_factory=dict)         # shared filters, e.g. {"category": "Footwear"}
    review_flt: dict = field(default_factory=dict)  # review-only filters, e.g. {"source": "Mobile App"}

    def params(self):
        return {"id": self.upload_id, "first": self.first, "last": self.last, "as_of": self.last,
                "start": self.first - timedelta(days=self.lag_days), "end": self.last,
                "min_orders": config.MIN_GROUP_ORDERS,
                **{f"f_{k}": v for k, v in self.flt.items()},
                **{f"rf_{k}": v for k, v in self.review_flt.items()}}

    @property
    def review_where(self):
        assert set(self.flt) <= SHARED_FILTERS and set(self.review_flt) <= REVIEW_ONLY_FILTERS
        return ("upload_id = %(id)s AND review_date BETWEEN %(first)s AND %(last)s"
                + "".join(f" AND {k} = %(f_{k})s" for k in self.flt)
                + "".join(f" AND {k} = %(rf_{k})s" for k in self.review_flt))

    @property
    def order_where(self):
        assert set(self.flt) <= SHARED_FILTERS
        return "order_date BETWEEN %(start)s AND %(end)s" + "".join(f" AND {k} = %(f_{k})s" for k in self.flt)

    @property
    def rates_available(self):
        return not self.review_flt   # orders have no source / verified column, so rates need no review-only filter


def make_ctx(conn, upload_id, flt=None, review_flt=None, date_range=None):
    row = fetchone(conn, "SELECT MIN(review_date) AS a, MAX(review_date) AS b FROM insight.reviews WHERE upload_id = %s",
                   (upload_id,))
    first, last = date_range or (row["a"], row["b"])
    return Ctx(conn, upload_id, first, last, flt=flt or {}, review_flt=review_flt or {})


def full_date_range(conn, upload_id):
    row = fetchone(conn, "SELECT MIN(review_date) AS a, MAX(review_date) AS b FROM insight.reviews WHERE upload_id = %s",
                   (upload_id,))
    return row["a"], row["b"]


# ------------------------------------------------------------------ stats
def compute_stats(ctx):
    p0 = ctx.params()
    orders = fetchone(ctx.conn, f"SELECT COUNT(*) AS n FROM insight.order_facts WHERE {ctx.order_where}", p0)["n"]
    total = fetchone(ctx.conn, f"SELECT COUNT(*) AS n FROM insight.review_facts WHERE {ctx.review_where} "
                               f"AND review_status = 'Published'", p0)["n"]
    if not total:
        return [], {"total_reviews": 0, "orders_in_period": orders}

    base = fetchall(ctx.conn, f"""
        SELECT problem, MODE() WITHIN GROUP (ORDER BY issue_category) AS issue_category,
               COUNT(*) AS reviews, COUNT(DISTINCT reviewer_id) AS unique_reviewers,
               ROUND(AVG(rating), 1) AS avg_rating,
               ROUND(100.0 * AVG(verified_purchase::int), 0) AS verified_pct,
               SUM(helpful_votes) AS helpful_votes,
               MODE() WITHIN GROUP (ORDER BY severity) AS severity
        FROM insight.review_facts
        WHERE {ctx.review_where} AND {PROBLEM_ROWS} AND problem = ANY(%(seed)s)
        GROUP BY problem ORDER BY reviews DESC""", {**p0, "seed": SEED_PROBLEMS})

    rates = ctx.rates_available and orders > 0
    for p in base:
        name = p["problem"]
        p["rate_pct"] = round(100 * p["reviews"] / total, 2)
        p["per_100_orders"] = round(100 * p["reviews"] / orders, 2) if rates else None
        p["wow_pct"] = compute_wow(ctx, name)
        p["concentration"] = {d: compute_concentration(ctx, name, d) for d in CONCENTRATION_DIMS}
        p["breakdowns"] = {d: rate_by(ctx, name, d) for d in BREAKDOWN_DIMS} if rates else {}
        p["hotspots"] = hotspots(ctx, name) if rates else []
        p["source_mix"] = source_mix(ctx, name)
        p["business_impact"] = compute_business_impact(ctx, name)
    return base, {"total_reviews": total, "orders_in_period": orders}


def compute_wow(ctx, problem):
    row = fetchone(ctx.conn, f"""
        SELECT COUNT(*) FILTER (WHERE review_date BETWEEN %(as_of)s::date - 6 AND %(as_of)s::date) AS this_week,
               COUNT(*) FILTER (WHERE review_date BETWEEN %(as_of)s::date - 13 AND %(as_of)s::date - 7) AS last_week
        FROM insight.review_facts
        WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS}""",
                   {**ctx.params(), "problem": problem})
    if not row["last_week"]:
        return None
    return round(100.0 * (row["this_week"] - row["last_week"]) / row["last_week"], 1)


def compute_concentration(ctx, problem, dim):
    assert dim in DIMENSIONS
    rows = fetchall(ctx.conn, f"""
        SELECT {dim} AS name, COUNT(*) AS cnt FROM insight.review_facts
        WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} AND {dim} IS NOT NULL
        GROUP BY 1 ORDER BY cnt DESC""", {**ctx.params(), "problem": problem})
    total = sum(r["cnt"] for r in rows)
    if not total:
        return None
    return {"by": dim, "name": rows[0]["name"], "share_pct": round(100 * rows[0]["cnt"] / total)}


def rate_by(ctx, problem, dim):
    """Complaints per 100 orders by group. Groups with fewer than MIN_GROUP_ORDERS orders are dropped."""
    assert dim in DIMENSIONS
    return fetchall(ctx.conn, f"""
        WITH o AS (SELECT {dim} AS grp, COUNT(*) AS orders
                   FROM insight.order_facts WHERE {ctx.order_where} AND {dim} IS NOT NULL GROUP BY 1),
             r AS (SELECT {dim} AS grp, COUNT(*) AS problem_reviews
                   FROM insight.review_facts
                   WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} GROUP BY 1)
        SELECT o.grp AS "group", o.orders, COALESCE(r.problem_reviews, 0) AS problem_reviews,
               ROUND(100.0 * COALESCE(r.problem_reviews, 0) / o.orders, 2) AS per_100_orders
        FROM o LEFT JOIN r USING (grp) WHERE o.orders >= %(min_orders)s
        ORDER BY per_100_orders DESC""", {**ctx.params(), "problem": problem})


def hotspots(ctx, problem, dim="subcategory", top=3):
    """Same idea as the doc's product hotspots, but by sub-category: in this data a product has 1-9 orders,
    so the 30-order minimum can only be met by a group of products."""
    assert dim in DIMENSIONS
    return fetchall(ctx.conn, f"""
        WITH o AS (SELECT {dim} AS grp, COUNT(*) AS orders
                   FROM insight.order_facts WHERE {ctx.order_where} AND {dim} IS NOT NULL GROUP BY 1),
             r AS (SELECT {dim} AS grp, COUNT(*) AS problem_reviews
                   FROM insight.review_facts
                   WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} GROUP BY 1)
        SELECT o.grp AS name, o.orders, COALESCE(r.problem_reviews, 0) AS problem_reviews,
               ROUND(100.0 * COALESCE(r.problem_reviews, 0) / o.orders, 1) AS per_100_orders
        FROM o LEFT JOIN r USING (grp) WHERE o.orders >= %(min_orders)s
        ORDER BY per_100_orders DESC, problem_reviews DESC LIMIT %(top)s""",
                    {**ctx.params(), "problem": problem, "top": top})


def source_mix(ctx, problem):
    return fetchall(ctx.conn, f"""
        WITH allr AS (SELECT source AS grp, COUNT(*) AS n FROM insight.review_facts
                      WHERE {ctx.review_where} AND review_status = 'Published' GROUP BY 1),
             pr AS (SELECT source AS grp, COUNT(*) AS n FROM insight.review_facts
                    WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} GROUP BY 1)
        SELECT allr.grp AS "group", COALESCE(pr.n, 0) AS problem_reviews,
               ROUND(100.0 * COALESCE(pr.n, 0) / NULLIF((SELECT SUM(n) FROM pr), 0), 0) AS share_of_problem_pct,
               ROUND(100.0 * allr.n / (SELECT SUM(n) FROM allr), 0) AS share_of_all_reviews_pct
        FROM allr LEFT JOIN pr USING (grp) ORDER BY share_of_problem_pct DESC NULLS LAST""",
                    {**ctx.params(), "problem": problem})


def compute_business_impact(ctx, problem):
    """Orders linked to a problem review vs all orders in the period. Each order counted once, even if
    several reviews point at it. Observed association only - nothing here says the problem CAUSED a return."""
    p = {**ctx.params(), "problem": problem}
    a = fetchone(ctx.conn, f"""
        WITH affected AS (
            SELECT DISTINCT order_id FROM insight.review_facts
            WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} AND order_matched)
        SELECT COUNT(*) AS orders,
               ROUND(100.0 * AVG(returned::int), 1) AS return_rate_pct,
               ROUND(100.0 * AVG(cancelled::int), 1) AS cancel_rate_pct,
               ROUND(AVG(order_value), 0) AS avg_order_value,
               COALESCE(SUM(CASE WHEN returned THEN order_value END), 0) AS returned_value,
               ROUND(100.0 * AVG((actual_delivery_date > promised_delivery_date)::int), 1) AS late_rate_pct
        FROM shop.orders WHERE order_id IN (SELECT order_id FROM affected)""", p)
    if not a["orders"]:
        return None
    overall = fetchone(ctx.conn, f"""
        SELECT ROUND(100.0 * AVG(returned::int), 1) AS rate,
               ROUND(100.0 * AVG((actual_delivery_date > promised_delivery_date)::int), 1) AS late
        FROM insight.order_facts WHERE {ctx.order_where}""", p)
    reason = fetchone(ctx.conn, f"""
        WITH affected AS (
            SELECT DISTINCT ON (order_id) order_id, issue_subcategory FROM insight.review_facts
            WHERE {ctx.review_where} AND problem = %(problem)s AND {PROBLEM_ROWS} AND order_matched
            ORDER BY order_id)
        SELECT COUNT(*) AS returned_orders,
               ROUND(100.0 * AVG(COALESCE(m.issue_subcategory = a.issue_subcategory, false)::int), 1) AS match_pct
        FROM affected a JOIN shop.orders o USING (order_id)
        LEFT JOIN insight.return_reason_map m ON m.return_reason = o.return_reason
        WHERE o.returned""", p)
    return {"affected_orders": a["orders"],
            "return_rate_pct": float(a["return_rate_pct"] or 0),
            "overall_return_rate_pct": float(overall["rate"] or 0),
            "cancel_rate_pct": float(a["cancel_rate_pct"] or 0),
            "avg_order_value": float(a["avg_order_value"] or 0),
            "returned_value": float(a["returned_value"]),
            "returned_orders": reason["returned_orders"],
            "return_reason_match_pct": float(reason["match_pct"]) if reason["match_pct"] is not None else None,
            "late_delivery_rate_pct": float(a["late_rate_pct"]) if a["late_rate_pct"] is not None else None,
            "overall_late_delivery_rate_pct": float(overall["late"]) if overall["late"] is not None else None}


def compute_emerging(ctx):
    rows = fetchall(ctx.conn, f"""
        SELECT problem, COUNT(*) AS occurrences FROM insight.review_facts
        WHERE {ctx.review_where} AND {PROBLEM_ROWS} AND problem <> ALL(%(seed)s)
        GROUP BY problem HAVING COUNT(*) >= 5 ORDER BY occurrences DESC""",
                   {**ctx.params(), "seed": SEED_PROBLEMS})
    for r in rows:
        r["wow_pct"] = compute_wow(ctx, r["problem"])
        r["needs_human_review"] = True
    return rows


# ------------------------------------------------------------------ review quality
SIGNALS_SQL = """
UPDATE insight.reviews r SET suspicion_signals = ARRAY_REMOVE(ARRAY[
  CASE WHEN NOT r.verified_purchase THEN 'unverified purchase' END,
  CASE WHEN NOT r.order_matched THEN 'no matching order' END,
  CASE WHEN r.order_matched AND EXISTS (
         SELECT 1 FROM shop.orders o WHERE o.order_id = r.order_id AND o.cancelled)
       THEN 'review on cancelled order' END,
  CASE WHEN r.order_matched AND EXISTS (
         SELECT 1 FROM shop.orders o WHERE o.order_id = r.order_id AND o.order_date > r.review_date)
       THEN 'review before purchase' END,
  CASE WHEN (SELECT COUNT(*) FROM insight.reviews x WHERE x.upload_id = r.upload_id
               AND x.product_id = r.product_id
               AND date_trunc('hour', x.review_datetime) = date_trunc('hour', r.review_datetime)) >= %(pb)s
       THEN 'product review burst' END,
  CASE WHEN (SELECT COUNT(*) FROM insight.reviews x WHERE x.upload_id = r.upload_id
               AND x.reviewer_id = r.reviewer_id
               AND x.review_datetime BETWEEN r.review_datetime - INTERVAL '24 hours'
                                         AND r.review_datetime + INTERVAL '24 hours') >= %(rb)s
       THEN 'reviewer burst' END,
  CASE WHEN (SELECT COUNT(*) FROM insight.reviews x WHERE x.upload_id = r.upload_id
               AND x.reviewer_id = r.reviewer_id AND x.product_id = r.product_id) >= 2
       THEN 'repeat review of product' END,
  CASE WHEN EXISTS (SELECT 1 FROM insight.reviews x WHERE x.upload_id = r.upload_id
               AND x.product_id = r.product_id
               AND x.review_id <> r.review_id AND x.reviewer_id <> r.reviewer_id
               AND x.embedding <=> r.embedding < %(dd)s)
       THEN 'near-duplicate wording' END
], NULL)
WHERE r.upload_id = %(id)s"""
# CHANGES vs the design doc (both because of what is in your data):
#  * 'review on cancelled order' is new: ~20% of your reviews sit on cancelled orders, which cannot have been received.
#  * near-duplicate wording is only checked among reviews of the SAME product. Your data reuses ~56 sentences
#    across thousands of reviews, so a store-wide check would flag almost every review.


def score_review_quality(ctx):
    execute(ctx.conn, SIGNALS_SQL, {"id": ctx.upload_id, "pb": config.PRODUCT_BURST_SIZE,
                                    "rb": config.REVIEWER_BURST_SIZE, "dd": config.DUPLICATE_DISTANCE})
    execute(ctx.conn, """UPDATE insight.reviews SET suspicion_level = CASE
                           WHEN CARDINALITY(suspicion_signals) >= 3 THEN 'High'
                           WHEN CARDINALITY(suspicion_signals) = 2 THEN 'Medium' ELSE 'Low' END
                         WHERE upload_id = %s""", (ctx.upload_id,))


def review_quality_summary(ctx):
    row = fetchone(ctx.conn, f"""
        SELECT ROUND(100.0 * AVG(verified_purchase::int), 0) AS verified_pct,
               ROUND(100.0 * AVG((review_status = 'Published')::int), 0) AS published_pct,
               ROUND(100.0 * AVG((suspicion_level IN ('Medium', 'High'))::int), 1) AS potentially_suspicious_pct
        FROM insight.review_facts WHERE {ctx.review_where}""", ctx.params())
    row["duplicate_pairs"] = fetchone(ctx.conn, """
        SELECT COUNT(*) AS n FROM insight.reviews a JOIN insight.reviews b
          ON a.upload_id = b.upload_id AND a.product_id = b.product_id
         AND a.review_id < b.review_id AND a.reviewer_id <> b.reviewer_id
        WHERE a.upload_id = %s AND a.embedding <=> b.embedding < %s""",
                                      (ctx.upload_id, config.DUPLICATE_DISTANCE))["n"]
    row["signals"] = fetchall(ctx.conn, """
        SELECT s AS signal, COUNT(*) AS reviews FROM insight.reviews r, UNNEST(r.suspicion_signals) AS s
        WHERE r.upload_id = %s GROUP BY s ORDER BY reviews DESC""", (ctx.upload_id,))
    return row


# ------------------------------------------------------------------ step 4
def decide(p):
    if p["reviews"] < config.MIN_REVIEWS_TO_JUDGE:
        return "INSUFFICIENT_DATA", "Too few reviews to judge."
    signs = [p["rate_pct"] >= config.DECISION_RATE_PCT,
             (p["wow_pct"] or 0) >= config.DECISION_WOW_PCT,
             p["severity"] in ("High", "Critical")]
    # Optional 4th sign from order data (your call):
    # bi = p["business_impact"]
    # signs.append(bool(bi) and bi["return_rate_pct"] >= 1.5 * bi["overall_return_rate_pct"])
    count = sum(signs)
    if count >= 2:
        return "INVESTIGATE", f"{count} of {len(signs)} warning signs"
    return "MONITOR", f"Only {count} of {len(signs)} warning signs"
