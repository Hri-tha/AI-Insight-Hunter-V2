"""
STEP 5 hypotheses (LLM) -> STEP 6 evidence (embeddings + SQL) -> STEP 7 verdict and root cause (LLM).
The LLM never counts or joins: it receives numbers computed in SQL and judges them.
"""
from backend.analytics import PROBLEM_ROWS, rate_by, source_mix
from backend.config import MIN_GROUP_ORDERS
from backend.db import fetchall
from backend.llm import get_embedding, llm_json
from backend.taxonomy import BREAKDOWN_DIMS, COMPARE_DIMENSIONS, VERDICTS
import json
from decimal import Decimal


def to_json(obj):
    return json.dumps(obj, default=lambda o: float(o) if isinstance(o, Decimal) else str(o))


# ------------------------------------------------------------------ step 5
def breakdown_lines(p):
    lines = []
    for dim, rows in p["breakdowns"].items():
        top = rows[:4]
        if top:
            lines.append(f"By {dim}: " + "; ".join(f'{r["group"]} {r["per_100_orders"]} per 100 orders' for r in top))
    lines.append("By source (share of these complaints vs share of all reviews): " + "; ".join(
        f'{r["group"]} {r["share_of_problem_pct"]}% vs {r["share_of_all_reviews_pct"]}%' for r in p["source_mix"]))
    return "\n".join(lines)


def build_hypothesis_prompt(p):
    trend = f"{p['wow_pct']}% week over week" if p["wow_pct"] is not None else "trend not available"
    rate = (f"{p['per_100_orders']} complaints per 100 orders" if p["per_100_orders"] is not None
            else "complaints per 100 orders not available")
    return f"""A customer feedback problem needs investigating.
Problem: {p['problem']}
Category: {p['issue_category']}
Rate: {p['rate_pct']}% of reviews, {rate}, {trend}
Severity: {p['severity']}

Computed breakdowns (do not recalculate):
{breakdown_lines(p)}

Propose 2-5 plausible hypotheses for WHY this is happening.
For each hypothesis choose ONE way to test it:
- {{"type": "text", "query": "<short phrase to search in customer reviews>"}}
- {{"type": "compare", "dimension": "<one of {COMPARE_DIMENSIONS}>"}} (compares complaint rates across groups)

Return ONLY a JSON object:
{{"hypotheses": [{{"id": "H1", "statement": "...", "test": {{"type": "...", ...}}}}]}}"""


def generate_hypotheses(p):
    hyps = llm_json(build_hypothesis_prompt(p), default={"hypotheses": []}).get("hypotheses", [])
    clean = []
    for i, h in enumerate(hyps[:5]):
        if not isinstance(h, dict) or not h.get("statement"):
            continue
        h["id"] = f"H{i + 1}"
        test = h.get("test") if isinstance(h.get("test"), dict) else {}
        if test.get("type") == "compare" and test.get("dimension") in COMPARE_DIMENSIONS:
            h["test"] = {"type": "compare", "dimension": test["dimension"]}
        else:
            h["test"] = {"type": "text", "query": test.get("query") or h["statement"]}
        clean.append(h)
    return clean


# ------------------------------------------------------------------ step 6
def search_reviews(ctx, query_text, problem_label=None, k=10):
    """Vector search over this upload, respecting the active filters. Identical sentences are capped at 2 so the
    evidence is not ten copies of one review. Ranked by verified purchase, then helpful votes."""
    params = {**ctx.params(), "q": get_embedding(query_text), "k": k * 6}
    sql = f"""SELECT f.review_id, f.review_title, f.review_text, f.rating, f.verified_purchase,
                     f.helpful_votes, f.source, f.category, f.brand, f.segment, f.new_or_returning,
                     o.returned, o.return_reason
              FROM insight.review_facts f
              LEFT JOIN shop.orders o ON o.order_id = f.order_id AND f.order_matched
              WHERE {ctx.review_where} AND f.review_status = 'Published' AND f.embedding IS NOT NULL"""
    if problem_label:
        sql += " AND f.problem = %(problem)s"
        params["problem"] = problem_label
    sql += " ORDER BY f.embedding <=> %(q)s::vector LIMIT %(k)s"
    rows, seen = [], {}
    for r in fetchall(ctx.conn, sql, params):
        if seen.get(r["review_text"], 0) >= 2:
            continue
        seen[r["review_text"]] = seen.get(r["review_text"], 0) + 1
        rows.append(r)
        if len(rows) == k:
            break
    return sorted(rows, key=lambda r: (r["verified_purchase"], r["helpful_votes"]), reverse=True)


def gather_evidence(ctx, problem, hypotheses):
    for h in hypotheses:
        test, dim = h["test"], h["test"].get("dimension")
        if test["type"] == "compare" and dim == "source":
            h["evidence"], h["evidence_kind"] = source_mix(ctx, problem), "source"
        elif test["type"] == "compare" and dim in BREAKDOWN_DIMS and ctx.rates_available:
            h["evidence"] = rate_by(ctx, problem, dim)[:5]      # groups under MIN_GROUP_ORDERS already dropped
            h["evidence_kind"] = "numbers"
        else:                                                    # text test, or a numeric test that is unavailable
            h["evidence"] = search_reviews(ctx, test.get("query", h["statement"]), problem_label=problem)
            h["evidence_kind"] = "reviews"
    return hypotheses


# ------------------------------------------------------------------ step 7
LLM_REVIEW_FIELDS = ["rating", "verified_purchase", "helpful_votes", "source", "category", "brand",
                     "segment", "new_or_returning", "returned", "return_reason"]   # never customer/reviewer/order IDs


def context_of(r):
    return ", ".join(f"{k}={r[k]}" for k in LLM_REVIEW_FIELDS if r.get(k) is not None)


def evidence_block(h, overall):
    if h["evidence_kind"] == "numbers":
        lines = [f'- {r["group"]}: {r["problem_reviews"]} complaints / {r["orders"]} orders = '
                 f'{r["per_100_orders"]} per 100 orders (overall {overall})' for r in h["evidence"]]
    elif h["evidence_kind"] == "source":
        lines = [f'- {r["group"]}: {r["share_of_problem_pct"]}% of these complaints vs '
                 f'{r["share_of_all_reviews_pct"]}% of all reviews' for r in h["evidence"]]
    else:
        lines = [f'- [{r["review_id"]}] "{r["review_title"]}" {r["review_text"]} ({context_of(r)})'
                 for r in h["evidence"]]
    return "\n".join(lines) or "- (nothing found)"


def build_verdict_prompt(p, hypotheses):
    blocks = "\n\n".join(
        f'{h["id"]}: {h["statement"]}\nEvidence ({h["evidence_kind"]}):\n{evidence_block(h, p["per_100_orders"])}'
        for h in hypotheses)
    bi = p["business_impact"]
    impact = (f'Observed return rate for affected orders: {bi["return_rate_pct"]}% vs '
              f'{bi["overall_return_rate_pct"]}% overall.' if bi else "No order data available.")
    return f"""Problem: {p['problem']}
{impact}

Hypotheses and their evidence:
{blocks}

For EACH hypothesis decide a verdict from {VERDICTS} using ONLY the evidence shown.
For numbers evidence, a group is a hotspot only if its rate is at least 2x the overall rate
(and its orders are at least {MIN_GROUP_ORDERS}).
A source over-indexes only when its share of the complaints is clearly higher than its share of all reviews.
Review text that only restates the complaint does not by itself explain WHY it happens: use INCONCLUSIVE then.
Never claim the problem caused returns or other business outcomes; say "observed" or "associated with".
Then give:
- likely_cause: one sentence, with a confidence level (Low, Medium or High)
- opportunities: a short list of product ideas
- data_gaps: what extra data would confirm this

Return ONLY a JSON object:
{{"verdicts": [{{"id": "H1", "verdict": "...", "why": "..."}}],
"likely_cause": "...", "confidence": "...", "opportunities": ["..."], "data_gaps": ["..."]}}"""


def investigate(ctx, p):
    """Steps 5-7 for one problem. Returns (evidence, investigation)."""
    from backend import llm as llm_module
    hypotheses = generate_hypotheses(p)                                   # 5
    if not hypotheses:
        return [], {"hypotheses": [], "likely_cause": f"Investigation could not run ({llm_module.last_error or 'no hypotheses returned'}).",
                    "confidence": "Low", "opportunities": [], "data_gaps": []}
    hypotheses = gather_evidence(ctx, p["problem"], hypotheses)            # 6
    result = llm_json(build_verdict_prompt(p, hypotheses),                 # 7
                      default={"verdicts": [], "likely_cause": "", "confidence": "Low",
                               "opportunities": [], "data_gaps": []}, max_completion_tokens=3000)
    verdicts = {v.get("id"): v for v in result.get("verdicts", []) if isinstance(v, dict)}
    investigation = {
        "hypotheses": [{"id": h["id"], "statement": h["statement"], "tested_with": h["evidence_kind"],
                        "verdict": verdicts.get(h["id"], {}).get("verdict") if verdicts.get(h["id"], {}).get("verdict") in VERDICTS else "INCONCLUSIVE",
                        "why": verdicts.get(h["id"], {}).get("why", "No verdict returned.")}
                       for h in hypotheses],
        "likely_cause": result.get("likely_cause", ""), "confidence": result.get("confidence", "Low"),
        "opportunities": result.get("opportunities", []), "data_gaps": result.get("data_gaps", [])}

    reviews = {r["review_id"]: r for h in hypotheses if h["evidence_kind"] == "reviews" for r in h["evidence"]}
    if not reviews:                                                        # every test was numeric
        reviews = {r["review_id"]: r for r in search_reviews(ctx, p["problem"], p["problem"], k=5)}
    top = sorted(reviews.values(), key=lambda r: (r["verified_purchase"], r["helpful_votes"]), reverse=True)[:3]
    evidence = [{"review_id": r["review_id"], "title": r["review_title"], "quote": r["review_text"],
                 "rating": r["rating"], "verified": r["verified_purchase"],
                 "helpful_votes": r["helpful_votes"], "source": r["source"]} for r in top]
    return evidence, investigation
