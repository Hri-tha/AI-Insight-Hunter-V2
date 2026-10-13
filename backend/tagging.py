"""
STEP 2 - Review understanding (tagging). The LLM sees ONLY the review title and text.
A local embedding model turns every review into numbers for vector search and the duplicate check.

Speed-up: identical (title, text) pairs are tagged once and the answer is reused (insight.tag_cache).
Temperature is 0, so the same input would get the same tags anyway - this just avoids paying twice.
Your demo data has ~56 distinct sentences across ~2,000 reviews, so this cuts LLM calls ~4x.
"""
import json

from backend.config import TAG_BATCH_SIZE
from backend.db import execute, fetchall, fetchone
from backend.llm import get_embedding, llm_json
from backend.taxonomy import ISSUE_TAXONOMY, SENTIMENT, SEED_PROBLEMS, SEVERITY


def chunks(rows, n):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def build_tagging_prompt(batch, known_problems):
    reviews_block = "\n".join(
        f'{r["k"]}: title="{r["review_title"]}" text="{r["review_text"]}"' for r in batch)
    return f"""Tag each review below.
Judge by the review TEXT. The title is only a hint: if the title contradicts the text
(for example title "Excellent" but the text describes a problem), trust the text. Ignore star ratings.

issue_category and issue_subcategory: choose ONLY from this list:
{json.dumps(ISSUE_TAXONOMY)}

sentiment: choose ONLY from {SENTIMENT}
severity: choose ONLY from {SEVERITY}
problem: a short label for the specific issue (for example "Incorrect Fit").
Reuse one of these labels if it fits: {known_problems}
Only invent a new label if none fit.
If the review has no complaint, use issue_category "None", issue_subcategory "None",
problem "No Specific Problem", sentiment "Positive" or "Neutral" and severity "Low".
evidence_quote: the exact phrase from the review text that supports your tags.

Reviews:
{reviews_block}

Return ONLY a JSON object with the list inside it:
{{"tags": [{{"k": "...", "issue_category": "...", "issue_subcategory": "...",
"problem": "...", "sentiment": "...", "severity": "...", "evidence_quote": "..."}}]}}"""


def valid_tag(t):
    return (t.get("issue_category") in ISSUE_TAXONOMY
            and t.get("issue_subcategory") in ISSUE_TAXONOMY[t["issue_category"]]
            and t.get("sentiment") in SENTIMENT and t.get("severity") in SEVERITY
            and bool(t.get("problem")))


APPLY_CACHE_SQL = """
    UPDATE insight.reviews r SET issue_category = c.issue_category, issue_subcategory = c.issue_subcategory,
           problem = c.problem, sentiment = c.sentiment, severity = c.severity, evidence_quote = c.evidence_quote
    FROM insight.tag_cache c
    WHERE r.upload_id = %s AND r.review_status = 'Published' AND r.problem IS NULL
      AND c.review_title = r.review_title AND c.review_text = r.review_text"""

UNTAGGED_SQL = """SELECT COUNT(*) AS n FROM insight.reviews
                  WHERE upload_id = %s AND review_status = 'Published' AND problem IS NULL"""


def tag_reviews(conn, upload_id, batch_size=TAG_BATCH_SIZE, progress_callback=None):
    execute(conn, APPLY_CACHE_SQL, (upload_id,))             # reuse earlier answers first
    pairs = fetchall(conn, """SELECT DISTINCT review_title, review_text FROM insight.reviews
                              WHERE upload_id = %s AND review_status = 'Published' AND problem IS NULL""",
                     (upload_id,))
    for i, p in enumerate(pairs):
        p["k"] = f"T{i}"

    known = list(SEED_PROBLEMS)
    batches = list(chunks(pairs, batch_size))
    for idx, batch in enumerate(batches):
        by_k = {p["k"]: p for p in batch}
        result = llm_json(build_tagging_prompt(batch, known), default={"tags": []}, max_completion_tokens=4000)
        for t in result.get("tags", []):
            p = by_k.get(t.get("k"))
            if p is None or not valid_tag(t):
                continue                                      # stays untagged, counted below
            if t["problem"] not in known:
                known.append(t["problem"])
            execute(conn, """INSERT INTO insight.tag_cache VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                             ON CONFLICT DO NOTHING""",
                    (p["review_title"], p["review_text"], t["issue_category"], t["issue_subcategory"],
                     t["problem"], t["sentiment"], t["severity"], t.get("evidence_quote") or ""))
        execute(conn, APPLY_CACHE_SQL, (upload_id,))
        if progress_callback:
            progress_callback(idx + 1, len(batches), stage="tagging")

    embed_reviews(conn, upload_id, progress_callback)
    return fetchone(conn, UNTAGGED_SQL, (upload_id,))["n"]   # reviews the LLM output could not tag


def embed_reviews(conn, upload_id, progress_callback=None):
    """Every review (title + text together). Identical wording is embedded once."""
    pairs = fetchall(conn, """SELECT DISTINCT review_title, review_text FROM insight.reviews
                              WHERE upload_id = %s AND embedding IS NULL""", (upload_id,))
    for i, p in enumerate(pairs):
        vec = get_embedding(f'{p["review_title"]}. {p["review_text"]}')
        execute(conn, """UPDATE insight.reviews SET embedding = %s
                         WHERE upload_id = %s AND review_title = %s AND review_text = %s AND embedding IS NULL""",
                (vec, upload_id, p["review_title"], p["review_text"]))
        if progress_callback and i % 10 == 0:
            progress_callback(i + 1, len(pairs), stage="embedding")
