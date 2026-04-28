"""Streamlit UI for the order-intake pipeline.

Run locally:
    uv run streamlit run app.py

Deployment notes:
    - Set ANTHROPIC_API_KEY as a secret on Streamlit Community Cloud.
    - Set DEMO_INGEST_QUOTA to cap LLM calls per browser session (default 5).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import storage
from src.pipeline import process_file


load_dotenv()

st.set_page_config(
    page_title="graphiteRx — Order Intake Pipeline",
    page_icon="📦",
    layout="wide",
)

# Brand palette sampled from graphiterx.com
NAVY = "#18193F"
TEAL = "#00A8B3"
TINT = "#D2E5E5"
SLATE = "#2F2E41"
AMBER = "#E6B592"

# Per-session ingestion cap so a public deploy cannot exhaust the API budget.
DEFAULT_QUOTA = int(os.environ.get("DEMO_INGEST_QUOTA", "5"))

if "ingest_count" not in st.session_state:
    st.session_state.ingest_count = 0


def _quota_remaining() -> int:
    return max(0, DEFAULT_QUOTA - st.session_state.ingest_count)


st.markdown(
    f"""
    <style>
        .brand-banner {{
            background: linear-gradient(135deg, {NAVY} 0%, {SLATE} 100%);
            color: #FFFFFF;
            padding: 1.4rem 2rem;
            border-radius: 8px;
            margin-bottom: 1.25rem;
            border-left: 6px solid {TEAL};
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }}
        .brand-banner h1 {{
            color: #FFFFFF;
            margin: 0 0 0.25rem 0;
            font-size: 1.55rem;
            font-weight: 600;
        }}
        .brand-banner p {{
            color: {TINT};
            margin: 0;
            font-size: 0.92rem;
        }}
        .brand-pill {{
            display: inline-block;
            background: {TEAL};
            color: #FFFFFF;
            font-size: 0.7rem;
            font-weight: 600;
            padding: 0.15rem 0.6rem;
            border-radius: 999px;
            margin-right: 0.5rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }}
        .quota-chip {{
            font-size: 0.75rem;
            color: {TINT};
            background: rgba(0, 168, 179, 0.18);
            border: 1px solid {TEAL};
            padding: 0.3rem 0.7rem;
            border-radius: 999px;
            white-space: nowrap;
        }}
        .redaction-mark {{
            display: inline-block;
            background: {TEAL};
            color: #FFFFFF;
            padding: 1px 6px;
            border-radius: 4px;
            font-size: 0.82em;
            font-weight: 600;
            font-family: ui-monospace, SFMono-Regular, monospace;
            margin: 0 1px;
        }}
        .raw-pre {{
            background: #FFF7F0;
            border-left: 3px solid {AMBER};
            padding: 0.7rem 0.9rem;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 0.82rem;
            white-space: pre-wrap;
            max-height: 240px;
            overflow-y: auto;
            color: {NAVY};
        }}
        .scrub-pre {{
            background: #F2F8F8;
            border-left: 3px solid {TEAL};
            padding: 0.7rem 0.9rem;
            border-radius: 4px;
            font-family: ui-monospace, SFMono-Regular, monospace;
            font-size: 0.82rem;
            white-space: pre-wrap;
            max-height: 240px;
            overflow-y: auto;
            color: {NAVY};
        }}
        .conf-badge {{
            display: inline-block;
            padding: 0.18rem 0.7rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 600;
            margin-right: 0.5rem;
        }}
        .conf-green {{ background: #E1F5EE; color: #0D6E4D; }}
        .conf-amber {{ background: #FFF1DA; color: #8A5A1A; }}
        .conf-red   {{ background: #FBE2E2; color: #9C2424; }}
        hr {{ border-top: 1px solid {TINT} !important; }}
        section[data-testid="stSidebar"] h3 {{
            color: {NAVY};
            border-bottom: 2px solid {TEAL};
            padding-bottom: 0.25rem;
        }}
        code {{ color: {NAVY}; background: {TINT}40; }}
    </style>

    <div class="brand-banner">
      <div>
        <h1>
          <span class="brand-pill">graphiteRx</span>
          Order &amp; Document Intake Pipeline
        </h1>
        <p>PDF · CSV · SMS &nbsp;→&nbsp; canonical PurchaseOrder &nbsp;·&nbsp;
           DEA / account / PHI scrubbed &nbsp;·&nbsp; append-only audit log</p>
      </div>
      <div class="quota-chip">
        Demo quota: {_quota_remaining()} of {DEFAULT_QUOTA} ingestions remaining
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error(
        "**ANTHROPIC_API_KEY is not set.** "
        "On Streamlit Cloud, configure it under *Manage app → Secrets*. "
        "Locally, copy `.env.example` to `.env`."
    )
    st.stop()


# ─── Sidebar ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.subheader("Pipeline stages")
    st.markdown(
        """
1. **Detect** format (extension)
2. **Extract** text (pypdf · pandas · passthrough)
3. **Scrub** sensitive data (regex → typed placeholders)
4. **Extract structure** (Claude · LangChain `with_structured_output`)
5. **Validate** against `PurchaseOrder` schema (Pydantic v2)
6. **Persist** to SQLite + audit log
"""
    )

    st.subheader("Confidence routing")
    st.markdown(
        f"""
The model populates `confidence` (0–1) and `flagged_fields` as part of
the same call that produces the data. Routing is threshold-based:

* <span class="conf-badge conf-green">≥ 0.70</span> auto-route to marketplace
* <span class="conf-badge conf-amber">0.50 – 0.69</span> review queue, low priority
* <span class="conf-badge conf-red">&lt; 0.50</span> review queue, high priority

`flagged_fields` lists the specific paths a reviewer should verify.
See [DEMO.md §4](https://github.com/Norky101/graphiteRxDemo/blob/main/DEMO.md#4-confidence-scoring)
for the full mechanism and production extensions.
""",
        unsafe_allow_html=True,
    )

    st.subheader("Production deployment")
    st.code(
        'init_chat_model(\n  "anthropic.claude-sonnet-4-6",\n'
        '  model_provider="bedrock_converse",\n)',
        language="python",
    )
    st.caption(
        "Demo runs on Anthropic direct (Haiku 4.5). Bedrock deployment in a "
        "HIPAA BAA AWS account is one-line model swap plus operational setup: "
        "`uv add langchain-aws`, AWS credentials, Bedrock model-access request."
    )


# ─── Drop-zone ───────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "Upload a document",
    type=["pdf", "csv", "txt"],
    accept_multiple_files=False,
    help=(
        "Bundled samples live in `samples/`. Each session is capped at "
        f"{DEFAULT_QUOTA} ingestions to protect the demo API budget."
    ),
)

if not uploaded:
    st.info(
        "**Bundled samples** in `samples/`: `purchase_order.pdf`, "
        "`reorder_list.csv`, `sms_orders.txt`. Each exercises a different "
        "path through the pipeline."
    )
    st.stop()

if _quota_remaining() == 0:
    st.warning(
        "**Demo quota exhausted for this session.** Refresh the page to start "
        "a new session, or contact the project owner for a larger budget."
    )
    st.stop()


# ─── Run pipeline ────────────────────────────────────────────────────────────
def _store_raw_for_uploaded(uploaded_file):
    """Persist the upload to a tempfile path that uses the visible filename."""
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(uploaded_file.getbuffer())
        tmp_path = Path(tmp.name)
    final_path = tmp_path.with_name(uploaded_file.name)
    tmp_path.rename(final_path)
    return final_path


with st.spinner(f"Running pipeline on {uploaded.name}…"):
    final_path = _store_raw_for_uploaded(uploaded)
    results = process_file(final_path)
    st.session_state.ingest_count += 1


accepted = sum(1 for r in results if r.accepted)
rejected = len(results) - accepted
n_units = len(results)
fmt = results[0].source_format if results else "?"

cols = st.columns(4)
cols[0].metric("Format", fmt.upper())
cols[1].metric("Extraction units", n_units)
cols[2].metric("Accepted", accepted)
cols[3].metric("Rejected", rejected)


# ─── Helpers ─────────────────────────────────────────────────────────────────
_REDACT_RE = re.compile(r"\[REDACTED_(\w+)\]")


def _highlight_redactions(text: str) -> str:
    return _REDACT_RE.sub(
        lambda m: f'<span class="redaction-mark">REDACTED_{m.group(1)}</span>',
        st_escape(text),
    )


def st_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _confidence_badge_html(conf: float) -> str:
    if conf >= 0.7:
        cls, label = "conf-green", "AUTO-ROUTE"
    elif conf >= 0.5:
        cls, label = "conf-amber", "LOW-PRIORITY REVIEW"
    else:
        cls, label = "conf-red", "HIGH-PRIORITY REVIEW"
    return (
        f'<span class="conf-badge {cls}">{label} · {conf:.2f}</span>'
    )


def _render_unit(result, *, header: str, idx: int) -> None:
    st.markdown(f"#### {header}")

    src_col, out_col = st.columns([1, 1.1])

    # ── Source: raw vs scrubbed ──
    with src_col:
        tab_scrub, tab_raw = st.tabs(
            [
                f"Scrubbed (sent to LLM) · {sum(result.redaction_counts.values()) or 0} redactions",
                "Raw input (before scrubber)",
            ]
        )
        with tab_scrub:
            st.markdown(
                f'<div class="scrub-pre">{_highlight_redactions(result.redacted_text)}</div>',
                unsafe_allow_html=True,
            )
            if result.redaction_counts:
                chips = " ".join(
                    f'<span class="redaction-mark">{k}: {v}</span>'
                    for k, v in result.redaction_counts.items()
                )
                st.markdown(
                    f"<div style='margin-top:.5rem;'>{chips}</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.caption("no patterns matched")
        with tab_raw:
            st.caption(
                "**Demo only.** The raw text below contains the unredacted "
                "values. In production the raw text never leaves the scrubber stage."
            )
            st.markdown(
                f'<div class="raw-pre">{st_escape(result.raw_text)}</div>',
                unsafe_allow_html=True,
            )

    # ── Output: canonical record + status ──
    with out_col:
        if result.accepted and result.order:
            order = result.order
            st.markdown(
                _confidence_badge_html(order.confidence)
                + (
                    f"<span style='font-size:0.78rem;color:#7A2424;margin-left:.5rem;'>"
                    f"flagged: {', '.join(f'<code>{f}</code>' for f in order.flagged_fields)}</span>"
                    if order.flagged_fields
                    else ""
                ),
                unsafe_allow_html=True,
            )
            st.json(order.model_dump(mode="json"), expanded=False)
        else:
            st.error("Rejected by validation / extractor")
            st.code(result.error or "no error captured")


# ─── Per-unit results ────────────────────────────────────────────────────────
st.divider()
st.subheader("Extraction results")

for i, r in enumerate(results):
    label = (
        f"`{r.source_file}` — message {r.unit_index + 1} of {n_units}"
        if r.source_format == "sms" and n_units > 1
        else f"`{r.source_file}`"
    )
    _render_unit(r, header=label, idx=i)
    if i < len(results) - 1:
        st.divider()


# ─── Audit log (always visible) ──────────────────────────────────────────────
st.divider()
st.subheader("Audit log · append-only · 340B-relevant")
st.caption(
    "Every ingestion attempt — accepted or rejected — appends one row. "
    "Rows persist in `store.db → audit_log` and survive across sessions."
)

storage.init_db()
with storage.connect() as conn:
    audit_rows = storage.fetch_audit(conn)

if not audit_rows:
    st.info("No audit rows yet. Ingest a document to populate the log.")
else:
    df = pd.DataFrame(audit_rows)
    # Compact display: derive a redaction signature column for readability.
    df["redaction_signature"] = df["redaction_count_by_type"].apply(
        lambda d: " · ".join(f"{k}:{v}" for k, v in d.items()) if d else "—"
    )
    df["flagged"] = df["flagged_fields"].apply(
        lambda fs: ", ".join(fs) if fs else "—"
    )
    display = df[
        [
            "id",
            "timestamp",
            "source_file",
            "source_format",
            "status",
            "llm_confidence",
            "redaction_signature",
            "flagged",
            "order_id",
        ]
    ].rename(
        columns={
            "id": "row",
            "source_file": "file",
            "source_format": "fmt",
            "llm_confidence": "confidence",
            "redaction_signature": "redactions",
            "order_id": "order",
        }
    )
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=True,
        column_config={
            "confidence": st.column_config.NumberColumn(
                "confidence",
                format="%.2f",
                help="LLM-self-reported confidence (0–1).",
            ),
        },
    )

    with st.expander("Raw audit JSON"):
        st.code(json.dumps(audit_rows, indent=2), language="json")
