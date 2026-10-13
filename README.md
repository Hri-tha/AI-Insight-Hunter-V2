# AI Insight Hunter v2

Implements the "AI Insight Hunter v2 Technical Design": Streamlit + Postgres/pgvector + Groq, 8-step pipeline,
`shop` (e-commerce, read only) and `insight` (our tables) schemas. It is a **separate project with its own database**:
nothing here reads or writes the old app's tables, and it uses its own `.env`.

## 1. Set up (about 10 minutes)

1. **New database.** Create a NEW Supabase project (or any Postgres with pgvector). Copy its connection string.
2. `cp .env.example .env` and fill in:
   - `GROQ_API_KEY` / `GROQ_MODEL`: the same values as the old app (nothing else to change)
   - `DATABASE_URL`: the NEW database
3. Install and create the tables:

       pip install -r requirements.txt
       python -m scripts.init_db        # schemas shop + insight, tables, views
       python -m scripts.seed_shop      # demo e-commerce data: 3,763 customers, 3,757 products, 4,544 orders

4. Run:

       streamlit run frontend/app.py --server.fileWatcherType none

   Click **Use sample data** (1,974 reviews) or upload your own file.

## 2. What the upload looks like

Either the two-sheet workbook you already have (`customer_reviews` + `review_metadata`, joined on `review_id`),
or one flat .csv/.xlsx with the 14 columns from the design doc. Your workbook has no `review_datetime`,
`reviewer_id` or `review_status`, so the app derives them and says so in the *Data check details* expander:
`review_datetime` = review_date (midnight), `reviewer_id` = customer_id, `review_status` = Published.

## 3. Rebuilding the sample from the full 15k / 25k workbook

    python -m scripts.build_demo_data --src AI_Customer_Intelligence_MVP_Datasets.xlsx [--start 2026-07-08 --end 2026-09-30]

Takes every review in the date range (real rows, unedited) and the orders / customers / products they need.

## 4. Project layout

    database/schema.sql      shop + insight schemas, views, tag cache, return-reason map
    backend/config.py        .env + all tunable thresholds in one place
    backend/taxonomy.py      fixed label lists + dimensions
    backend/loader.py        step 1  upload, cleaning, order matching, data check
    backend/tagging.py       step 2  LLM tags (title+text only) + local embeddings
    backend/analytics.py     steps 3-4  SQL stats, rates per 100 orders, business impact, review quality, decision
    backend/investigation.py steps 5-7  hypotheses, evidence, verdict
    backend/report.py        step 8 summary + chat
    backend/pipeline.py      chaining, filter refresh, re-investigate, run cache
    frontend/app.py         Streamlit UI (chat on the right)
    scripts/                init_db, seed_shop, build_demo_data
    tests/offline_checks.py  checks that need no database (loader, prompts, decision rule)

## 5. Differences from the design doc (all forced by your data, all editable)

| Design doc | Here | Why |
|---|---|---|
| Embedding VECTOR(1536) | VECTOR(384), local MiniLM | You already use it; no embedding API key needed |
| psycopg 3 + SQLAlchemy | psycopg2 | Matches your existing app |
| Taxonomy: Product/Delivery/Refund/Service | + `Returns`, `Platform` (payment, pricing, app) and 8 matching seed problems | ~1/3 of your reviews are about checkout, pricing, app crashes, return pickups, seller replies |
| Seller breakdown, product hotspots (>=30 orders) | Brand / category / segment / city breakdowns, **sub-category** hotspots | 900 sellers with ~5 orders each; products have 1-9 orders. Seller can be re-enabled in `taxonomy.py` |
| `review_datetime` hourly burst | Day-level in practice | The file has dates only |
| Near-duplicate wording store-wide | Same product only | Your data reuses ~56 sentences thousands of times; a store-wide check flags everything |
| 7 quality signals | + `review on cancelled order` | ~20% of your reviews are on cancelled orders |
| Title and text both decide tags | Text wins when they conflict | Titles/ratings in this data are unrelated to the text (5 stars on "refund still pending") |
| Return-reason match = same wording | `insight.return_reason_map` table | Your shop says "Size too small", taxonomy says "Size/Fit" |
| Currency Rs | $ | Your data is `*_usd` |
| LLM call per batch | + identical (title, text) cache | ~56 distinct sentences, so ~4x fewer Groq calls |

## 6. Limits worth knowing

- The sample is real data and has **no planted patterns**: text, category, returns and dates are statistically independent.
  Expect flat trends, few Investigate cards and mostly INCONCLUSIVE verdicts. That is the pipeline being honest, not a bug.
- Groq free tier: first run is roughly 50-70 tagging calls + 2 per investigated problem, at ~2 s per call.
- Review quality thresholds, decision thresholds and the duplicate distance are starting guesses (`backend/config.py`).
