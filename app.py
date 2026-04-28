"""Streamlit drop-zone UI for the demo.

Run locally:
    uv run streamlit run app.py

Deploys cleanly to Streamlit Community Cloud — set ANTHROPIC_API_KEY as a
secret in the app's settings before first run.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from src import storage
from src.pipeline import process_file


load_dotenv()

st.set_page_config(
    page_title="graphiteRxDemo — Order Intake Pipeline",
    page_icon="📦",
    layout="wide",
)

# Brand palette sampled from graphiterx.com
NAVY = "#18193F"
TEAL = "#00A8B3"
TINT = "#D2E5E5"
SLATE = "#2F2E41"

st.markdown(
    f"""
    <style>
        /* Brand banner */
        .brand-banner {{
            background: linear-gradient(135deg, {NAVY} 0%, {SLATE} 100%);
            color: #FFFFFF;
            padding: 1.5rem 2rem;
            border-radius: 8px;
            margin-bottom: 1.5rem;
            border-left: 6px solid {TEAL};
        }}
        .brand-banner h1 {{
            color: #FFFFFF;
            margin: 0 0 0.25rem 0;
            font-size: 1.75rem;
            font-weight: 600;
        }}
        .brand-banner p {{
            color: {TINT};
            margin: 0;
            font-size: 0.95rem;
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
        /* Section dividers tinted with brand */
        hr {{
            border-top: 1px solid {TINT} !important;
        }}
        /* Sidebar headers */
        section[data-testid="stSidebar"] h3 {{
            color: {NAVY};
            border-bottom: 2px solid {TEAL};
            padding-bottom: 0.25rem;
        }}
        /* Code blocks subtle */
        code {{
            color: {NAVY};
            background: {TINT}40;
        }}
    </style>

    <div class="brand-banner">
      <h1>
        <span class="brand-pill">graphiteRx</span>
        Order &amp; Document Intake Pipeline
      </h1>
      <p>PDF · CSV · SMS in &nbsp;→&nbsp; canonical PurchaseOrder out &nbsp;·&nbsp;
         DEA / account / PHI scrubbed &nbsp;·&nbsp; 340B-grade audit log</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.caption(
    "Drop a PDF, CSV, or SMS log. The pipeline detects the format, scrubs "
    "sensitive data, asks Claude (via LangChain `with_structured_output`) to "
    "populate a canonical PurchaseOrder, validates it with Pydantic, and "
    "writes both the order and a per-ingestion audit row to SQLite."
)

if not os.environ.get("ANTHROPIC_API_KEY"):
    st.error(
        "**ANTHROPIC_API_KEY is not set.** "
        "On Streamlit Cloud, add it under Manage app → Secrets. "
        "Locally, copy `.env.example` to `.env` and paste your key from "
        "[console.anthropic.com](https://console.anthropic.com)."
    )
    st.stop()


with st.sidebar:
    st.subheader("Pipeline stages")
    st.markdown(
        """
1. **Detect** format (extension)
2. **Extract** text (pypdf / pandas / passthrough)
3. **Scrub** sensitive data (regex → typed placeholders)
4. **Extract structure** (Claude via LangChain `with_structured_output`)
5. **Validate** against `PurchaseOrder` Pydantic schema
6. **Persist** to SQLite + audit log
"""
    )
    st.subheader("Confidence scoring")
    st.markdown(
        """
The LLM **self-reports** confidence (0–1) and lists `flagged_fields`
it's unsure about. UI badges:

* 🟢 ≥ 0.70 — auto-route to marketplace
* 🟡 0.50–0.69 — review recommended
* 🔴 < 0.50 — **human review required**

Confidence is a soft signal, not a calibrated probability. See
[`DEMO.md`](https://github.com/Norky101/graphiteRxDemo/blob/main/DEMO.md#4-how-confidence-scoring-works)
for the full mechanism and what production-grade scoring would add.
"""
    )
    st.subheader("Why an LLM here")
    st.caption(
        "The LLM is the universal adapter. Three deterministic stages around "
        "it (detect, extract, scrub) plus one after (validate). New buyer "
        "channels join automatically — bytes through the same pipeline."
    )
    st.subheader("Production swap (not yet enabled)")
    st.code(
        'init_chat_model(\n  "anthropic.claude-sonnet-4-6",\n'
        '  model_provider="bedrock_converse",\n)',
        language="python",
    )
    st.caption(
        "Demo is on Anthropic direct (Haiku 4.5). Switching to Bedrock for a "
        "HIPAA BAA AWS deployment is one line of code plus ~20 min of infra: "
        "`uv add langchain-aws`, configure AWS creds, request Bedrock model "
        "access. The same `bedrock_converse` provider also exposes Llama / "
        "Cohere / Titan via the unified Converse API."
    )


uploaded = st.file_uploader(
    "Drop a sample file",
    type=["pdf", "csv", "txt"],
    accept_multiple_files=False,
    help="Try samples/purchase_order.pdf, samples/reorder_list.csv, or samples/sms_orders.txt",
)

if not uploaded:
    st.info(
        "Try one of the bundled samples in the `samples/` folder, or drop your own "
        "PDF / CSV / .txt above."
    )
    st.stop()


with tempfile.NamedTemporaryFile(
    delete=False, suffix=Path(uploaded.name).suffix
) as tmp:
    tmp.write(uploaded.getbuffer())
    tmp_path = Path(tmp.name)
# Streamlit gives us only the basename via uploaded.name; preserve it for display
# while keeping the temp path for the actual processing.
display_path = Path(uploaded.name)


with st.spinner(f"Running pipeline on {uploaded.name}…"):
    # Rename so the audit log shows the user-visible filename, not the tempfile.
    final_path = tmp_path.with_name(uploaded.name)
    tmp_path.rename(final_path)
    results = process_file(final_path)


accepted = sum(1 for r in results if r.accepted)
rejected = len(results) - accepted
n_units = len(results)
fmt = results[0].source_format if results else "?"

st.markdown(
    f"**{n_units}** extraction unit{'s' if n_units != 1 else ''} · "
    f"`{accepted}` accepted, `{rejected}` rejected · "
    f"format `{fmt}`"
)


def _render_one(result, *, header: str) -> None:
    st.markdown(f"### {header}")
    col_source, col_canonical, col_audit = st.columns([1.1, 1.4, 1])
    with col_source:
        st.caption(f"redactions: `{result.redaction_counts or '—'}`")
        st.text_area(
            label="scrubbed text",
            value=result.redacted_text,
            height=260,
            label_visibility="collapsed",
            key=f"text-{header}",
        )
    with col_canonical:
        if result.accepted and result.order:
            badge = (
                "🟢" if result.order.confidence >= 0.7 else "🟡"
                if result.order.confidence >= 0.5 else "🔴"
            )
            st.markdown(
                f"{badge} **confidence {result.order.confidence:.2f}**"
                + (
                    f"&nbsp;&nbsp;flagged: `{result.order.flagged_fields}`"
                    if result.order.flagged_fields
                    else ""
                )
            )
            st.json(result.order.model_dump(mode="json"), expanded=False)
        else:
            st.error("Rejected by validation / extractor")
            st.code(result.error or "no error captured")
    with col_audit:
        st.caption("audit row appended:")
        audit_summary = {
            "status": "accepted" if result.accepted else "rejected",
            "source_file": result.source_file,
            "source_format": result.source_format,
            "redaction_count_by_type": result.redaction_counts,
            "llm_confidence": (
                round(result.order.confidence, 3) if result.order else None
            ),
            "flagged_fields": (
                result.order.flagged_fields if result.order else []
            ),
        }
        st.json(audit_summary, expanded=False)


# For multi-unit results (SMS), render each one. For single-unit (PDF/CSV)
# the header is just the filename.
for r in results:
    label = (
        f"`{r.source_file}` — message {r.unit_index + 1} of {n_units}"
        if r.source_format == "sms" and n_units > 1
        else f"`{r.source_file}`"
    )
    _render_one(r, header=label)
    st.divider()


# Full audit log at the bottom — pulls every row from SQLite, not just this run.
storage.init_db()
with storage.connect() as conn:
    audit_rows = storage.fetch_audit(conn)

with st.expander(f"Full audit log ({len(audit_rows)} rows)"):
    st.code(json.dumps(audit_rows, indent=2), language="json")
