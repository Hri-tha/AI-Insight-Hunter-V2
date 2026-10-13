"""
The 8 steps chained, plus refresh (filter change) and re-investigate. No UI code here.

  1 loader.py          upload + data check          (no AI)
  2 tagging.py         LLM tags + embeddings
  3 analytics.py       counts, rates, quality       (SQL/Python)
  4 analytics.decide   Investigate / Monitor        (Python)
  5-7 investigation.py hypotheses, evidence, verdict
  8 report.py          summary
"""
import json
from datetime import timedelta

from backend import config
from backend.analytics import (compute_emerging, compute_stats, decide, make_ctx, review_quality_summary,
                               score_review_quality)
from backend.db import execute, fetchone
from backend.investigation import investigate, to_json
from backend.loader import check_orders, data_check, load_reviews
from backend.report import write_summary
from backend.tagging import tag_reviews


def normalise(obj):
    """Decimal/date -> plain numbers/strings, so results can be stored as JSON and shown without surprises."""
    return json.loads(to_json(obj))


def decide_all(problems, cached=None):
    cached = cached or {}
    for p in problems:
        p["decision"], p["decision_reason"] = decide(p)
        if p["decision"] == "INVESTIGATE" and p["problem"] in cached:
            p["evidence"], p["investigation"] = cached[p["problem"]]
        else:
            p["evidence"], p["investigation"] = [], None


def build_meta(ctx, check, totals):
    return {"upload_id": ctx.upload_id, "reviews_uploaded": check["reviews"], **totals,
            "first_review_date": str(ctx.first), "last_review_date": str(ctx.last),
            "order_window": f"{ctx.first - timedelta(days=ctx.lag_days)} to {ctx.last}",
            "lag_days": ctx.lag_days, "filters": {**ctx.flt, **ctx.review_flt}}


def discard_upload(conn, upload_id):
    """Remove the rows of an upload that failed half way, so failed runs do not pile up in the database.
    Remembered tags (insight.tag_cache) are kept, so running the same file again is fast."""
    execute(conn, "DELETE FROM insight.analysis_runs WHERE upload_id = %s", (upload_id,))
    execute(conn, "DELETE FROM insight.reviews WHERE upload_id = %s", (upload_id,))


def run_pipeline(file, conn, progress_callback=None):
    upload_id, load_report = load_reviews(file, conn)                    # 1
    try:
        check_orders(conn, upload_id)
        ctx = make_ctx(conn, upload_id)
        check = data_check(conn, upload_id, load_report)
        check["untagged_reviews"] = tag_reviews(conn, upload_id, progress_callback=progress_callback)   # 2
        score_review_quality(ctx)                                        # 3
        problems, totals = compute_stats(ctx)
        emerging = compute_emerging(ctx)
        quality = review_quality_summary(ctx)
        decide_all(problems)                                             # 4

        todo = [p for p in problems if p["decision"] == "INVESTIGATE"]
        for i, p in enumerate(todo):                                     # 5, 6, 7
            if progress_callback:
                progress_callback(i, max(len(todo), 1), stage="investigating")
            p["evidence"], p["investigation"] = investigate(ctx, p)

        results = {"meta": build_meta(ctx, check, totals), "data_check": check, "review_quality": quality,
                   "problems": problems, "emerging": emerging}
        results = normalise(write_summary(ctx, results))                 # 8
        save_run(conn, upload_id, results)
    except Exception:
        discard_upload(conn, upload_id)
        raise
    return results, ctx


def refresh(conn, upload_id, base_results, flt, review_flt, date_range):
    """A filter changed: re-run only steps 3 and 4. Tags and embeddings are stored; investigations are reused
    from the first run. Returns (results, ctx)."""
    cached = {p["problem"]: (p["evidence"], p["investigation"])
              for p in base_results["problems"] if p.get("investigation")}
    ctx = make_ctx(conn, upload_id, flt, review_flt, date_range)
    problems, totals = compute_stats(ctx)
    decide_all(problems, cached)
    results = {"meta": build_meta(ctx, base_results["data_check"], totals),
               "data_check": base_results["data_check"], "review_quality": review_quality_summary(ctx),
               "problems": problems, "emerging": compute_emerging(ctx)}
    results["summary"] = base_results.get("summary", "")
    results["emerging"] = _keep_descriptions(results["emerging"], base_results)
    return normalise(results), ctx


def _keep_descriptions(emerging, base):
    old = {e["problem"]: e.get("description", "") for e in base.get("emerging", [])}
    for e in emerging:
        e["description"] = old.get(e["problem"], "")
    return emerging


def reinvestigate(ctx, problem):
    """The 'Re-investigate' button: re-run steps 5-7 for one problem under the current filters."""
    return normalise(list(investigate(ctx, problem)))


def save_run(conn, upload_id, results):
    execute(conn, """INSERT INTO insight.analysis_runs (upload_id, results) VALUES (%s, %s)
                     ON CONFLICT (upload_id) DO UPDATE SET results = EXCLUDED.results, created_at = now()""",
            (upload_id, json.dumps(results, default=str)))


def load_run(conn, upload_id):
    row = fetchone(conn, "SELECT results FROM insight.analysis_runs WHERE upload_id = %s", (upload_id,))
    return row["results"] if row else None
