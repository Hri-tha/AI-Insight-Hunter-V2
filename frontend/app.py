"""
AI INSIGHT HUNTER v2 - Streamlit UI. Layout follows the technical design doc: upload -> data check -> filters ->
tiles -> summary -> problem cards -> emerging -> review quality, with the chat always visible in the right-hand column.
No pipeline logic here; it only calls backend/.

RUN (from the project root):
    streamlit run frontend/app.py
"""
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from backend.analytics import full_date_range, make_ctx
from backend.config import MIN_GROUP_ORDERS, require_settings
from backend.db import fetchall, get_connection
from backend.pipeline import refresh, reinvestigate, run_pipeline
from backend.report import GREETING_NO_DATA, WELCOME_REPLY, answer_question
from frontend import ui

log = logging.getLogger("insight.app")

SEV_BADGE = {"Critical": "red", "High": "red", "Medium": "orange", "Low": "gray"}
VERDICT_ICON = {"SUPPORTED": "🟢", "MIXED": "🟡", "INCONCLUSIVE": "⚪"}
FILTER_COLS = {"Category": "category", "Brand": "brand", "Segment": "segment"}
SUGGESTIONS = ["What is the biggest problem?", "Which problems are rising fastest?", "Chart reviews per problem"]

st.set_page_config(page_title="AI Insight Hunter", page_icon="🔎", layout="wide")
ui.inject_css()

try:
    require_settings()
except RuntimeError as e:
    st.error(str(e))
    st.stop()

for key, default in {"base": None, "results": None, "upload_id": None, "sig": None,
                     "chat": [], "options": {}, "uploader_n": 0}.items():
    st.session_state.setdefault(key, default)


def fmt(v, suffix="", none="n/a"):
    return none if v is None else f"{v}{suffix}"


def money(v):
    return f"${v:,.0f}"


def store_investigation(p, evidence, investigation):
    """Save into the card being shown AND the full-upload results (only those two fields, never the filtered stats)."""
    p["evidence"], p["investigation"] = evidence, investigation
    for q in st.session_state.base["problems"]:
        if q["problem"] == p["problem"]:
            q["evidence"], q["investigation"] = evidence, investigation


def current_ctx(conn):
    flt, rflt, (a, b) = st.session_state.sig
    return make_ctx(conn, st.session_state.upload_id, dict(flt), dict(rflt), (a, b))


# ----------------------------------------------------------------------------- 1. upload
def run_analysis(source_file):
    bar = st.progress(0, text="Starting...")

    def on_progress(i, n, stage="tagging"):
        label = {"tagging": "Tagging reviews", "embedding": "Generating embeddings",
                 "investigating": "Investigating problems"}[stage]
        bar.progress(min(i / n, 1.0), text=f"{label}... ({i}/{n})")

    conn = get_connection()
    try:
        results, ctx = run_pipeline(source_file, conn, progress_callback=on_progress)
    except ValueError as e:                      # the file itself is wrong (missing columns...): say exactly what
        bar.empty()
        st.error(str(e))
        return
    except Exception as e:
        bar.empty()
        log.exception("analysis failed")
        st.error(f"Something went wrong while analysing the file: {e}")
        return
    finally:
        conn.close()
    bar.empty()
    rng = (date.fromisoformat(results["meta"]["first_review_date"]), date.fromisoformat(results["meta"]["last_review_date"]))
    st.session_state.update(base=results, results=results, upload_id=results["meta"]["upload_id"],
                            sig=((), (), rng), chat=[], options={}, uploader_n=st.session_state.uploader_n + 1)
    untagged = results["data_check"].get("untagged_reviews")
    st.session_state.flash = (f"{untagged} reviews could not be tagged (the model returned an invalid label or the call failed)."
                              if untagged else None)
    st.rerun()


def upload_box(title="Upload your review file"):
    uploaded = st.file_uploader(title, type=["xlsx", "csv"], key=f"upload_{st.session_state.uploader_n}",
                                label_visibility="collapsed" if title is None else "visible")
    clicked = st.button("Run analysis", type="primary", disabled=uploaded is None, use_container_width=True,
                        key=f"run_{st.session_state.uploader_n}")
    if clicked and uploaded is not None:
        run_analysis(uploaded)


def landing():
    ui.hero()
    with st.container(border=True):
        st.markdown('<div class="panel-title">Start here</div><div class="panel-sub">.xlsx or .csv, up to 50 MB. '
                    'Your file is analysed in this session only.</div>', unsafe_allow_html=True)
        upload_box(title="Upload your review file")
        ui.file_help()
    st.write("")
    ui.steps()
    ui.privacy_note()


# ----------------------------------------------------------------------------- dashboard
def dashboard():
    base = st.session_state.base
    uid = st.session_state.upload_id
    conn = get_connection()
    try:
        if st.session_state.get("flash"):
            st.warning(st.session_state.pop("flash"))
        with st.expander("Analyse a different file"):
            st.caption("Starts a new analysis. Your earlier upload is not changed or mixed in.")
            upload_box()
        if not st.session_state.options:
            st.session_state.options = {
                label: [r["v"] for r in fetchall(conn, f"SELECT DISTINCT {col} AS v FROM insight.review_facts "
                                                       f"WHERE upload_id = %s AND {col} IS NOT NULL ORDER BY 1", (uid,))]
                for label, col in {**FILTER_COLS, "Source": "source"}.items()}
        opts = st.session_state.options
        first, last = full_date_range(conn, uid)

        f1, f2, f3, f4, f5, f6 = st.columns([2.2, 1.4, 1.2, 1.2, 1.4, 1.2])
        dr = f1.date_input("Date range", value=st.session_state.sig[2], min_value=first, max_value=last)
        cat = f2.selectbox("Category", ["All"] + opts["Category"])
        brand = f3.selectbox("Brand", ["All"] + opts["Brand"])
        seg = f4.selectbox("Segment", ["All"] + opts["Segment"])
        src = f5.selectbox("Source", ["All"] + opts["Source"])
        verified_only = f6.toggle("Verified only")

        dr = (dr[0], dr[1]) if isinstance(dr, (tuple, list)) and len(dr) == 2 else st.session_state.sig[2]
        flt = {k: v for k, v in {"category": cat, "brand": brand, "segment": seg}.items() if v != "All"}
        rflt = {}
        if src != "All":
            rflt["source"] = src
        if verified_only:
            rflt["verified_purchase"] = True
        sig = (tuple(sorted(flt.items())), tuple(sorted(rflt.items())), dr)

        if sig != st.session_state.sig:      # only the SQL steps (3 and 4) re-run
            with st.spinner("Updating..."):
                st.session_state.results, _ = refresh(conn, uid, base, flt, rflt, dr)
            st.session_state.sig = sig
        results = st.session_state.results
        meta, check, quality = results["meta"], results["data_check"], results["review_quality"]
        filtered = bool(flt or rflt or dr != (first, last))

        # ------------------------------------------------------------------------- 2. data check
        st.caption(f"Data check: **{check['reviews']:,}** reviews · {check.get('dropped_rows', 0)} dropped · "
                   f"{check['products_matched_pct']:.0f}% products found · {check['customers_matched_pct']:.0f}% customers found · "
                   f"{check['linked_to_order_pct']:.0f}% linked to an order · {check['published_pct']:.0f}% published")
        with st.expander("Data check details"):
            st.write({k: check.get(k) for k in ("rows_in_file", "dropped_rows", "defaulted_cells", "date_mismatch",
                                                "order_id_not_matched", "untagged_reviews")})
            sync = check.get("shop_sync")
            st.write("**Order data from this file:** " + (
                f"{sync['customers']:,} customers, {sync['products']:,} products, {sync['orders']:,} orders added or updated."
                if sync else "none in this file (used what is already in the database)."))
            if check.get("derived_columns"):
                st.write("**Columns the file did not contain (derived by the app):**")
                st.write(check["derived_columns"])

        # ------------------------------------------------------------------------- 4. tiles
        problems = results["problems"]
        shown = [p for p in problems if p["decision"] != "INSUFFICIENT_DATA"]
        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Reviews analysed", f"{meta['total_reviews']:,}")
        t1.caption(f"of {meta['reviews_uploaded']:,} uploaded")
        t2.metric("Orders in period", f"{meta['orders_in_period']:,}", help=f"Order window: {meta['order_window']}")
        t3.metric("Problems found", len(shown))
        t4.metric("Emerging", len(results["emerging"]))
        if rflt:
            st.caption("Source / Verified-only filters are set: per-100-orders numbers, breakdowns and hotspots are hidden "
                       "because orders have no such fields.")

        # ------------------------------------------------------------------------- 5. summary
        if results.get("summary"):
            st.success(results["summary"] + ("  \n*(written for the full upload, not the current filters)*" if filtered else ""))

        # ------------------------------------------------------------------------- 6. problem cards
        if not shown:
            st.info("No known problems in the current selection.")
        for p in shown:
            key = p["problem"].replace(" ", "_").replace("/", "_")
            with st.container(border=True):
                tag = ":red-badge[Investigate]" if p["decision"] == "INVESTIGATE" else ":orange-badge[Monitor]"
                st.markdown(f"#### {p['problem']}  :{SEV_BADGE.get(p['severity'], 'gray')}-badge[{p['severity']}]  {tag}")
                rate_line = f"{p['rate_pct']}% of reviews"
                if p["per_100_orders"] is not None:
                    rate_line += f" · {p['per_100_orders']} per 100 orders"
                rate_line += f" · {fmt(p['wow_pct'], '%')} WoW · {p['decision_reason']}"
                st.write(rate_line)
                st.caption(f"{p['reviews']} reviews from {p['unique_reviewers']} reviewers · {p['verified_pct']:.0f}% verified · "
                           f"{p['helpful_votes']} helpful votes · avg rating {p['avg_rating']}")

                bi = p.get("business_impact")
                if bi:
                    b1, b2, b3 = st.columns(3)
                    b1.metric("Return rate (affected vs all orders)",
                              f"{bi['return_rate_pct']}% vs {bi['overall_return_rate_pct']}%")
                    b2.metric("Value of returned orders", money(bi["returned_value"]))
                    b3.metric("Returns citing the same reason",
                              fmt(bi["return_reason_match_pct"], "%"), help=f"{bi['returned_orders']} returned orders")
                    st.caption("Observed association in order data, not proof the problem caused the returns. "
                               f"{bi['affected_orders']} orders linked to these reviews · cancel rate {bi['cancel_rate_pct']}%.")
                    if p["issue_category"] == "Delivery" and bi.get("late_delivery_rate_pct") is not None:
                        st.caption(f"Delivered later than promised: {bi['late_delivery_rate_pct']}% of affected orders "
                                   f"vs {bi['overall_late_delivery_rate_pct']}% of all orders.")

                if p["breakdowns"]:
                    left, right = st.columns(2)
                    dim = left.selectbox("Break down by", list(p["breakdowns"]), key=f"dim_{key}")
                    rows = p["breakdowns"][dim]
                    if rows:
                        left.bar_chart(pd.DataFrame(rows).set_index("group")["per_100_orders"], height=220)
                    else:
                        left.caption("No group has enough orders.")
                    right.caption("Where complaints come from (share of complaints vs share of all reviews)")
                    sm = pd.DataFrame(p["source_mix"])
                    if not sm.empty:
                        right.bar_chart(sm.set_index("group")[["share_of_problem_pct", "share_of_all_reviews_pct"]], height=220)
                    if p["hotspots"]:
                        st.caption(f"Sub-category hotspots (min. {MIN_GROUP_ORDERS} orders)")
                        st.dataframe(pd.DataFrame(p["hotspots"])[["name", "orders", "problem_reviews", "per_100_orders"]],
                                     hide_index=True)
                else:
                    sm = pd.DataFrame(p["source_mix"])
                    if not sm.empty:
                        st.caption("Where complaints come from")
                        st.bar_chart(sm.set_index("group")[["share_of_problem_pct", "share_of_all_reviews_pct"]], height=200)

                if p["evidence"]:
                    st.markdown("**Evidence**")
                    for e in p["evidence"]:
                        st.markdown(f"> “{e['quote']}” — *{e['title']}* · {'✔ verified' if e['verified'] else 'unverified'} · "
                                    f"{e['helpful_votes']} helpful · {e['source']} · `{e['review_id']}`")

                if p["decision"] == "INVESTIGATE":
                    if p["investigation"] is None:
                        if st.button("Investigate", key=f"inv_{key}"):
                            with st.spinner("Investigating..."):
                                ev, inv = reinvestigate(current_ctx(conn), p)
                            store_investigation(p, ev, inv)
                            st.rerun()
                    else:
                        h1, h2 = st.columns([4, 1])
                        h1.markdown("**Investigation**")
                        if h2.button("Re-investigate", key=f"re_{key}"):
                            with st.spinner("Investigating..."):
                                ev, inv = reinvestigate(current_ctx(conn), p)
                            store_investigation(p, ev, inv)
                            st.rerun()
                        inv = p["investigation"]
                        for h in inv["hypotheses"]:
                            st.markdown(f"{VERDICT_ICON.get(h['verdict'], '⚪')} **{h['id']} {h['verdict']}** · "
                                        f":gray-badge[{h['tested_with']}] · {h['statement']}  \n&nbsp;&nbsp;&nbsp;&nbsp;*{h['why']}*")
                        st.markdown(f"**Likely cause:** {inv['likely_cause']} ({inv['confidence']} confidence)")
                        if inv["opportunities"]:
                            st.markdown("**Opportunities:** " + " · ".join(inv["opportunities"]))
                        if inv["data_gaps"]:
                            st.markdown("**Data we would need:** " + " · ".join(inv["data_gaps"]))

        # ------------------------------------------------------------------------- 7. emerging
        if results["emerging"]:
            with st.container(border=True):
                st.markdown("**Emerging problems (need human review)**")
                st.dataframe(pd.DataFrame(results["emerging"])[["problem", "occurrences", "wow_pct", "description"]],
                             hide_index=True)

        # ------------------------------------------------------------------------- 8. review quality
        with st.container(border=True):
            st.markdown("**Review quality**")
            q1, q2, q3, q4 = st.columns(4)
            q1.metric("Verified purchase", f"{quality['verified_pct']:.0f}%")
            q2.metric("Published", f"{quality['published_pct']:.0f}%")
            q3.metric("Potentially suspicious", f"{quality['potentially_suspicious_pct']}%")
            q4.metric("Possible duplicate pairs", quality["duplicate_pairs"])
            with st.expander("What drives this"):
                st.caption("Potentially suspicious based on the available signals (2 or more signals). Not a finding of fake reviews.")
                st.dataframe(pd.DataFrame(quality["signals"]), hide_index=True)

    finally:
        conn.close()


# ----------------------------------------------------------------------------- chat (always visible)
def chat_panel():
    results = st.session_state.results
    st.markdown('<div class="panel-title">Ask a question</div><div class="panel-sub">'
                + ("Answers come from your computed numbers and real reviews."
                   if results else "Upload a file and I will answer questions about it.") + '</div>',
                unsafe_allow_html=True)
    box = st.container(height=560, border=True)
    slot = st.container()                        # holds the suggestion buttons, drawn just above the input
    typed = st.chat_input("Ask about your customer problems..")
    chosen = None
    if results and not st.session_state.chat and not typed:
        with slot:
            for s in SUGGESTIONS:
                if st.button(s, key=f"sugg_{s}", use_container_width=True):
                    chosen = s
    question = typed or chosen

    with box:
        with st.chat_message("assistant"):
            st.write(WELCOME_REPLY if results else GREETING_NO_DATA)
        for m in st.session_state.chat:
            with st.chat_message(m["role"]):
                st.write(m["content"])
                if m.get("chart"):
                    st.caption(m["chart"]["title"])
                    st.bar_chart(m["chart"]["data"])
        if question:
            st.session_state.chat.append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.write(question)
            chart = None
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        if results:
                            conn = get_connection()
                            try:
                                answer, _, chart = answer_question(current_ctx(conn), question, results)
                            finally:
                                conn.close()
                        else:
                            answer, _, chart = answer_question(None, question, None)   # asks the user to upload
                    except Exception as e:
                        log.exception("chat failed")
                        answer = f"Sorry, something went wrong while answering: {e}"
                st.write(answer)
                if chart:
                    st.caption(chart["title"])
                    st.bar_chart(chart["data"])
            st.session_state.chat.append({"role": "assistant", "content": answer, "chart": chart})


# ----------------------------------------------------------------------------- page
ui.brand_bar()
main_col, chat_col = st.columns([2, 1], gap="large")
with main_col:
    if st.session_state.results:
        dashboard()
    else:
        landing()
with chat_col:
    chat_panel()
