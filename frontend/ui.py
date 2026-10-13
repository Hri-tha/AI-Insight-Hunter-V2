"""Look and feel only: CSS plus the landing page shown before any file is analysed. No pipeline logic."""
import streamlit as st

CSS = """
<style>
.block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px;}
#MainMenu, footer {visibility: hidden;}

/* brand bar */
.brand {display:flex; align-items:center; gap:.75rem; margin-bottom:1rem;}
.brand .logo {width:42px; height:42px; border-radius:12px; display:flex; align-items:center; justify-content:center;
  font-size:1.4rem; background:linear-gradient(135deg,#4F46E5,#7C3AED); box-shadow:0 6px 16px rgba(79,70,229,.35);}
.brand .name {font-size:1.35rem; font-weight:700; color:#111827; line-height:1.1;}
.brand .tag {font-size:.8rem; color:#6B7280;}

/* hero */
.hero {border-radius:22px; padding:2.4rem 2.4rem 2.1rem; color:#fff; margin-bottom:1.2rem;
  background:linear-gradient(135deg,#4338CA 0%,#7C3AED 55%,#DB2777 130%); box-shadow:0 18px 40px rgba(79,70,229,.28);}
.hero h1 {font-size:2.2rem; line-height:1.15; margin:0 0 .6rem; color:#fff; font-weight:750; padding:0;}
.hero p {font-size:1.05rem; opacity:.93; margin:0 0 1.1rem; max-width:46rem;}
.hero .chip {display:inline-block; background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.28);
  border-radius:999px; padding:.28rem .8rem; margin:0 .4rem .4rem 0; font-size:.85rem;}

/* how-it-works cards */
.step {background:#fff; border:1px solid #E5E7EB; border-radius:16px; padding:1.1rem 1.1rem 1rem; height:100%;
  box-shadow:0 2px 10px rgba(17,24,39,.04);}
.step .num {width:28px; height:28px; border-radius:50%; background:#EEF2FF; color:#4F46E5; font-weight:700;
  display:flex; align-items:center; justify-content:center; margin-bottom:.6rem; font-size:.9rem;}
.step h4 {margin:0 0 .3rem; font-size:1rem; color:#111827; padding:0;}
.step p {margin:0; font-size:.88rem; color:#6B7280; line-height:1.45;}

.privacy {background:#F0FDF4; border:1px solid #BBF7D0; color:#166534; border-radius:12px;
  padding:.65rem .95rem; font-size:.88rem; margin:1rem 0 .4rem;}

/* dashboard polish */
[data-testid="stMetric"] {background:#fff; border:1px solid #E5E7EB; border-radius:14px; padding:.8rem 1rem;
  box-shadow:0 1px 6px rgba(17,24,39,.04);}
[data-testid="stMetricLabel"] {color:#6B7280;}
div[data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px;}
.panel-title {font-weight:700; font-size:1.05rem; color:#111827; margin:0 0 .15rem;}
.panel-sub {font-size:.82rem; color:#6B7280; margin:0 0 .6rem;}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def brand_bar():
    st.markdown('<div class="brand"><div class="logo">🔎</div><div><div class="name">AI Insight Hunter</div>'
                '<div class="tag">Customer review intelligence</div></div></div>', unsafe_allow_html=True)


def hero():
    st.markdown(
        '<div class="hero"><h1>Find out what your customers are really upset about</h1>'
        '<p>Upload a file of customer reviews. The AI tags every review, sizes each problem against your own order data, '
        'investigates the serious ones, and backs every claim with real reviews and numbers.</p>'
        '<span class="chip">✨ AI-tagged reviews</span><span class="chip">📦 Sized per 100 orders</span>'
        '<span class="chip">🧪 Root-cause investigation</span><span class="chip">💬 Chat with your data</span></div>',
        unsafe_allow_html=True)


def steps():
    items = [("1", "Upload your file", "Drop in the full workbook (.xlsx). Reviews, orders, products and customers are all read from it and kept up to date."),
             ("2", "AI reads every review", "Each review gets a problem, sentiment and severity. Only the title and text are shown to the AI."),
             ("3", "See what matters", "Problems ranked by size and trend, return impact, and tested explanations with evidence."),
             ("4", "Ask anything", "The chat answers from your computed numbers and cites the review IDs it used.")]
    for col, (n, title, text) in zip(st.columns(4), items):
        col.markdown(f'<div class="step"><div class="num">{n}</div><h4>{title}</h4><p>{text}</p></div>',
                     unsafe_allow_html=True)


def privacy_note():
    st.markdown('<div class="privacy">🔒 <b>Privacy:</b> customer, reviewer and order IDs are never sent to the AI model. '
                'Only review IDs are, so answers can cite their evidence.</div>', unsafe_allow_html=True)


def file_help():
    with st.expander("What should my file contain?"):
        st.markdown(
            "**Option A (recommended): the full workbook (.xlsx)** with the sheets `customer_reviews`, `review_metadata`, "
            "`customer_information`, `product_information` and `orders_returns`. Reviews are analysed, and customers, "
            "products and orders are added or updated in the database from the same file, so every review finds its order. "
            "Names, age and gender are never stored.\n\n"
            "**Option B: reviews only.** A workbook with just `customer_reviews` (+ optional `review_metadata`), or one flat "
            ".csv/.xlsx with the 14 columns `review_id, customer_id, product_id, review_date, review_datetime, rating, "
            "review_title, review_text, source, verified_purchase, helpful_votes, review_status, reviewer_id, order_id`. "
            "Order data is then taken from what is already in the database.\n\n"
            "Rows missing a review ID, customer, product, date, rating, text or order ID are dropped and counted in the data check. "
            "Only reviews with status *Published* are analysed.")
