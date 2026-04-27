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

st.title("Order & Document Intake Pipeline")
st.caption(
    "Drop a PDF, CSV, or SMS log. The pipeline detects the format, scrubs "
    "DEA/account/PHI, asks Claude to populate a canonical PurchaseOrder via "
    "LangChain, validates it with Pydantic, and writes both the order and a "
    "340B-grade audit row to SQLite."
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
    st.subheader("Why an LLM here")
    st.caption(
        "The LLM is the universal adapter. Three deterministic stages around "
        "it (detect, extract, scrub) plus one after (validate). New buyer "
        "channels join automatically — bytes through the same pipeline."
    )
    st.subheader("Production swap")
    st.code(
        'init_chat_model(\n  "anthropic.claude-sonnet-4-6",\n'
        '  model_provider="bedrock_converse",\n)',
        language="python",
    )
    st.caption(
        "Same code, AWS Bedrock inside a HIPAA BAA AWS account. Bedrock also "
        "exposes Llama / Cohere / Titan via the same call signature."
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
    # process_file uses the path's suffix for detection and the basename for display.
    # Rename the temp file so the audit log shows the user-visible name.
    final_path = tmp_path.with_name(uploaded.name)
    tmp_path.rename(final_path)
    result = process_file(final_path)


col_source, col_canonical, col_audit = st.columns([1.1, 1.4, 1])

with col_source:
    st.subheader("Scrubbed source")
    st.caption(f"Format: `{result.source_format}` · file: `{result.source_file}`")
    st.text_area(
        label="redacted text",
        value=result.redacted_text,
        height=520,
        label_visibility="collapsed",
    )
    st.caption(f"Redaction counts: {result.redaction_counts or '—'}")

with col_canonical:
    st.subheader("Canonical PurchaseOrder")
    if result.accepted and result.order:
        st.success(f"Accepted · confidence {result.order.confidence:.2f}")
        if result.order.flagged_fields:
            st.warning(f"Flagged fields: {result.order.flagged_fields}")
        st.json(result.order.model_dump(mode="json"))
    else:
        st.error("Rejected by validation / extractor")
        st.code(result.error or "no error captured")

with col_audit:
    st.subheader("Audit log")
    storage.init_db()
    with storage.connect() as conn:
        rows = storage.fetch_audit(conn)
    if not rows:
        st.caption("(no rows yet)")
    else:
        # Display the most recent (this run) row prominently, full log below.
        latest = rows[0]
        st.markdown(
            f"""
**Latest entry**

* `{latest['status']}` · `{latest['source_format']}` · `{latest['source_file']}`
* timestamp: `{latest['timestamp']}`
* confidence: `{latest['llm_confidence']}`
* redactions: `{latest['redaction_count_by_type']}`
* flagged: `{latest['flagged_fields']}`
"""
        )
        with st.expander("Full audit log"):
            st.code(json.dumps(rows, indent=2), language="json")
