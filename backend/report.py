"""STEP 8 (report summary) and the chat. Numbers always come from the computed results, never from the LLM."""
import json
import logging
import re

import pandas as pd

from backend.analytics import PROBLEM_ROWS
from backend.db import fetchall
from backend.investigation import context_of, search_reviews, to_json
from backend.llm import friendly_error, is_too_large, llm
from backend.prompts import CHAT_SYSTEM_PROMPT

log = logging.getLogger(__name__)

GREETING_NO_DATA = (
    "Hello! I am AI Insight Hunter. Upload your customer review file and I will be ready to explain your "
    "problems, show trends and find reviews as evidence.")
NO_DATA_REPLY = (
    "I don't have any data to look at yet, so I can't answer that.\n\n"
    "Please upload your customer review file (.xlsx or .csv) and click **Run analysis**. Then ask me again.")
WELCOME_REPLY = (
    "Hello! I am AI Insight Hunter. Your data is ready. I can explain your customer problems, show trends and "
    "charts, and find reviews as evidence. What would you like to know?")


def write_summary(ctx, results):
    if not results["problems"]:
        results["summary"] = "No known problems were found in the published reviews."
    else:
        facts = [{k: p[k] for k in ("problem", "rate_pct", "per_100_orders", "wow_pct", "severity", "decision")}
                 for p in results["problems"][:8]]
        results["summary"] = llm(f"""Write a 2-3 sentence summary for a product manager.
Facts (use ONLY these numbers, do not calculate or estimate anything):
{to_json(facts)}
Mention the biggest or fastest-growing problem first. Do not claim causes; say "observed".""",
                                 json_mode=False)
    for e in results["emerging"]:
        examples = fetchall(ctx.conn, f"""SELECT review_title, review_text FROM insight.review_facts
                                          WHERE {ctx.review_where} AND problem = %(problem)s LIMIT 5""",
                            {**ctx.params(), "problem": e["problem"]})
        e["description"] = llm("In one sentence, describe the customer problem in these reviews:\n"
                               + "\n".join(f'- {r["review_title"]}: {r["review_text"]}' for r in examples),
                               json_mode=False)
    return results


CHART_METRICS = ("reviews", "rate_pct", "per_100_orders", "avg_rating", "wow_pct")


def build_chart(spec, results):
    """The LLM only picks WHICH metric to plot; the numbers come from the computed results."""
    if not isinstance(spec, dict) or spec.get("metric") not in CHART_METRICS:
        return None
    metric = spec["metric"]
    rows = [p for p in results["problems"] if p.get(metric) is not None]
    if not rows:
        return None
    data = pd.DataFrame({metric: [float(p[metric]) for p in rows]}, index=[p["problem"] for p in rows])
    return {"type": "bar", "title": spec.get("title") or metric, "data": data}


def compact_problem(p, groups=2):
    """Small enough for a free-tier prompt: headline numbers, top groups per breakdown, likely cause."""
    out = {k: p.get(k) for k in ("problem", "issue_category", "reviews", "rate_pct", "per_100_orders", "wow_pct",
                                 "severity", "avg_rating", "decision", "concentration")}
    bi = p.get("business_impact")
    if bi:
        out["business_impact"] = {k: bi.get(k) for k in ("affected_orders", "return_rate_pct",
                                                         "overall_return_rate_pct", "return_reason_match_pct")}
    if groups:
        out["top_groups_per_100_orders"] = {d: rows[:groups] for d, rows in (p.get("breakdowns") or {}).items()}
    if p.get("investigation"):
        out["likely_cause"] = (p["investigation"].get("likely_cause") or "")[:300]
    return out


META_KEYS = ("total_reviews", "reviews_uploaded", "orders_in_period", "first_review_date", "last_review_date",
             "order_window", "filters")


def build_chat_prompt(question, results, reviews, small=False):
    """small=True is the fallback after the provider says the request was too big: fewer problems, no groups,
    fewer reviews."""
    meta = {k: results["meta"].get(k) for k in META_KEYS}          # no upload_id or other internal ids
    problems = [compact_problem(p, groups=0 if small else 2) for p in results["problems"][: 5 if small else 10]]
    reviews = reviews[: 4 if small else 10]
    evidence = "\n".join(f'- [{r["review_id"]}] "{r["review_title"]}" {r["review_text"]} ({context_of(r)})'
                         for r in reviews) or "(no reviews retrieved)"
    return f"""Stats already computed:
{to_json({"totals": meta, "problems": problems, "emerging": [e["problem"] for e in results.get("emerging", [])][:5]})}

Relevant customer reviews:
{evidence}

User message: {question}"""


def parse_reply(raw):
    """The model should return a JSON object. If it returns something else, show its text instead of failing."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for candidate in (raw, re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"answer": raw, "chart": None}


def ask_chat_model(prompt):
    """Returns (reply_dict, None) or (None, error_text). Never raises, so the chat always answers something."""
    try:
        reply = parse_reply(llm(prompt, json_mode=True, system=CHAT_SYSTEM_PROMPT, max_completion_tokens=1500,
                                   max_retries=3))   # a person is waiting: give up after ~10 s, not ~45 s
    except Exception as e:
        log.warning("chat call failed: %s: %s", type(e).__name__, e)
        return None, f"{type(e).__name__}: {e}"
    if not reply or not reply.get("answer"):
        return None, "The model returned an empty reply."
    return reply, None


def answer_question(ctx, question, results):
    """Returns (answer, review_ids, chart_or_None). Respects the filters currently set in ctx.
    Without results (nothing uploaded yet) it answers with NO_DATA_REPLY and makes no AI call."""
    if not results:
        return NO_DATA_REPLY, [], None
    try:
        reviews = search_reviews(ctx, question, k=10)
    except Exception as e:                       # e.g. embedding model unavailable: still answer from the stats
        log.warning("review search failed: %s: %s", type(e).__name__, e)
        reviews = []
    reply, error = ask_chat_model(build_chat_prompt(question, results, reviews))
    if reply is None and is_too_large(error):
        reply, error = ask_chat_model(build_chat_prompt(question, results, reviews, small=True))
    if reply is None:
        return friendly_error(error), [], None
    return reply["answer"], [r["review_id"] for r in reviews], build_chart(reply.get("chart"), results)
