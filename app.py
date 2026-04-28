"""Streamlit UI for the order-intake pipeline.

Run locally:
    uv run streamlit run app.py

Configuration via environment (Streamlit Cloud → Manage app → Secrets):
    ANTHROPIC_API_KEY        required, the LLM provider key
    DEMO_INGEST_QUOTA        per-session LLM-call cap (default 5)
    DEMO_SHOW_RAW            set to "1" to expose the raw-input tab.
                             Default: hidden — public deploys never expose
                             unredacted user input even in the UI
    DEMO_MAX_FILE_MB         per-upload file-size cap in MB (default 5)
    DEMO_AUTO_THRESHOLD      confidence at/above which a record auto-routes
                             (default 0.70); below LOW_THRESHOLD goes to
                             high-priority review
    DEMO_LOW_THRESHOLD       low-priority-review boundary (default 0.50)
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import exporters, storage
from src.pipeline import process_file


load_dotenv()

logger = logging.getLogger("graphiterx.ui")

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


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Per-session ingestion cap so a public deploy cannot exhaust the API budget.
DEFAULT_QUOTA = _env_int("DEMO_INGEST_QUOTA", 5)
MAX_FILE_BYTES = _env_int("DEMO_MAX_FILE_MB", 5) * 1024 * 1024
AUTO_THRESHOLD = _env_float("DEMO_AUTO_THRESHOLD", 0.70)
LOW_THRESHOLD = _env_float("DEMO_LOW_THRESHOLD", 0.50)
SHOW_RAW = os.environ.get("DEMO_SHOW_RAW", "").lower() in {"1", "true", "yes"}

ALLOWED_EXTENSIONS = {".pdf", ".csv", ".txt"}
ALLOWED_MAGIC_PREFIXES = {
    ".pdf": (b"%PDF-",),
    ".csv": None,        # text-based; no reliable magic
    ".txt": None,
}


if "ingest_count" not in st.session_state:
    st.session_state.ingest_count = 0


def _quota_remaining() -> int:
    return max(0, DEFAULT_QUOTA - st.session_state.ingest_count)


def _validate_upload(uploaded) -> str | None:
    """Reject obviously malformed uploads. Returns an error message if invalid,
    or None if the file is acceptable."""
    suffix = Path(uploaded.name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return f"Unsupported file extension {suffix!r}. Allowed: .pdf, .csv, .txt"
    if uploaded.size > MAX_FILE_BYTES:
        max_mb = MAX_FILE_BYTES // (1024 * 1024)
        return (
            f"File is {uploaded.size / 1024 / 1024:.1f} MB; demo cap is {max_mb} MB. "
            f"Use a smaller sample for the demo."
        )
    if uploaded.size == 0:
        return "Uploaded file is empty."
    magic = ALLOWED_MAGIC_PREFIXES.get(suffix)
    if magic:
        # Peek the first bytes; for PDFs the file must start with %PDF-
        head = uploaded.getvalue()[: max(len(m) for m in magic)]
        if not any(head.startswith(m) for m in magic):
            return (
                f"File extension is {suffix!r} but contents do not match. "
                f"Refusing to process — possible disguised binary."
            )
    return None


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

    st.subheader("What the scrubber catches")
    st.markdown(
        """
Deterministic regex patterns. Every match is replaced with a typed
placeholder before the LLM sees the text.

| Type | Pattern matched | Placeholder |
|---|---|---|
| **DEA** | 2 letters + 7 digits (e.g. `AB1234567`) | `[REDACTED_DEA]` |
| **Account** | "acct/account/customer" + ID | `[REDACTED_ACCOUNT]` |
| **Routing** | "routing/aba" + 9-digit | `[REDACTED_ROUTING]` |
| **SSN** | `###-##-####` | `[REDACTED_SSN]` |
| **Phone** | US phone formats | `[REDACTED_PHONE]` |
| **Email** | `name@host.tld` | `[REDACTED_EMAIL]` |
| **PHI** | "patient/pt/for" + capitalized name | `[REDACTED_PHI]` |

**On PHI specifically** — HIPAA defines 18 PHI identifiers (names,
addresses, dates, MRN, etc.). This demo catches **patient names in
context** as a representative case. A production deployment uses
**AWS Comprehend Medical** or **Microsoft Presidio** for full HIPAA
PHI detection across all 18 identifier classes.
"""
    )

    st.subheader("Confidence routing")
    st.markdown(
        f"""
The model populates `confidence` (0–1) and `flagged_fields` as part of
the same call that produces the data. Routing is threshold-based:

* <span class="conf-badge conf-green">≥ {AUTO_THRESHOLD:.2f}</span> auto-route to marketplace
* <span class="conf-badge conf-amber">{LOW_THRESHOLD:.2f} – {AUTO_THRESHOLD:.2f}</span> review queue, low priority
* <span class="conf-badge conf-red">&lt; {LOW_THRESHOLD:.2f}</span> review queue, high priority

`flagged_fields` lists the specific paths a reviewer should verify.
Thresholds are configurable via `DEMO_AUTO_THRESHOLD` and
`DEMO_LOW_THRESHOLD` env vars. See
[DEMO.md §4](https://github.com/Norky101/graphiteRxDemo/blob/main/DEMO.md#4-confidence-scoring)
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
        f"Bundled samples live in `samples/`. Per-session cap: "
        f"{DEFAULT_QUOTA} ingestions · max upload size: "
        f"{MAX_FILE_BYTES // 1024 // 1024} MB."
    ),
)

if not uploaded:
    st.info(
        "**Bundled samples** in `samples/`: `purchase_order.pdf`, "
        "`reorder_list.csv`, `sms_orders.txt`. Each exercises a different "
        "path through the pipeline."
    )
    st.stop()

# Input validation: extension, size, magic-bytes for PDF
validation_error = _validate_upload(uploaded)
if validation_error:
    st.error(f"**Upload rejected.** {validation_error}")
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


request_id = uuid.uuid4().hex[:8]
logger.info(
    "ingest start  request_id=%s file=%s size=%d quota_remaining=%d",
    request_id,
    uploaded.name,
    uploaded.size,
    _quota_remaining(),
)

with st.spinner(f"Running pipeline on {uploaded.name}…"):
    try:
        final_path = _store_raw_for_uploaded(uploaded)
        results = process_file(final_path)
        st.session_state.ingest_count += 1
    except Exception as exc:  # noqa: BLE001 — last-line error UX guard
        logger.exception("ingest crash request_id=%s", request_id)
        st.error(
            "**Pipeline error.** The system encountered an unexpected error "
            f"processing this file. Error reference: `{request_id}`."
        )
        with st.expander("Technical detail"):
            st.code(f"{type(exc).__name__}: {exc}")
        st.stop()

logger.info(
    "ingest done   request_id=%s units=%d accepted=%d",
    request_id,
    len(results),
    sum(1 for r in results if r.accepted),
)


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
    if conf >= AUTO_THRESHOLD:
        cls, label = "conf-green", "AUTO-ROUTE"
    elif conf >= LOW_THRESHOLD:
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
        # Raw tab is gated by DEMO_SHOW_RAW. Default off — public deploys never
        # expose unredacted user input even in the UI.
        n_redactions = sum(result.redaction_counts.values()) or 0
        scrubbed_label = f"Scrubbed (sent to LLM) · {n_redactions} redactions"
        if SHOW_RAW:
            tab_scrub, tab_raw = st.tabs(
                [scrubbed_label, "Raw input (before scrubber)"]
            )
        else:
            (tab_scrub,) = st.tabs([scrubbed_label])

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
        if SHOW_RAW:
            with tab_raw:
                st.caption(
                    "**Local-only view.** The raw text contains unredacted "
                    "values. This tab is hidden by default on public "
                    "deployments (`DEMO_SHOW_RAW` env var)."
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

            # ── Outbound artifacts ──
            stem_for_filename = (
                order.buyer_org_name
                or order.po_reference
                or Path(order.source_file).stem
            )
            d_json, d_pdf, _ = st.columns([1, 1, 2])
            with d_json:
                st.download_button(
                    label="Download canonical JSON",
                    data=exporters.order_to_json_bytes(order),
                    file_name=exporters.safe_filename(stem_for_filename, "json"),
                    mime="application/json",
                    key=f"json-{idx}",
                    help="Machine-consumable canonical PurchaseOrder. "
                    "Shape matches the marketplace order-management API.",
                    use_container_width=True,
                )
            with d_pdf:
                st.download_button(
                    label="Download confirmation PDF",
                    data=exporters.order_to_confirmation_pdf(order),
                    file_name=exporters.safe_filename(
                        f"{stem_for_filename}-confirmation", "pdf"
                    ),
                    mime="application/pdf",
                    key=f"pdf-{idx}",
                    help="Buyer-facing acknowledgement. What an ops team would "
                    "email back to confirm receipt.",
                    use_container_width=True,
                )

            with st.expander("What happens next (production wiring — illustrative)"):
                conf_route = (
                    "**marketplace order-management API** (auto-route, ≥0.70 confidence)"
                    if order.confidence >= 0.7
                    else "**review queue UI** (held for human verification before routing)"
                )
                st.markdown(
                    f"""
This canonical record would flow to:

| Downstream | Trigger | Format |
|---|---|---|
| {conf_route} | On accept | REST POST · canonical JSON |
| **Supplier ERP** (NetSuite / SAP / Oracle) | On accept | ERP-specific adapter |
| **Buyer confirmation email** | On accept | The PDF above, attached |
| **EDI 855 acknowledgement** | If buyer is EDI-enabled | X12 855 over AS2 / SFTP |
| **Compliance archive** | Always | Audit row → tamper-evident store |
| **Slack to supplier rep** | On accept | Order summary message |

The downloads above (JSON + PDF) are the same artifacts the integrations
would emit; they are wired here for demo and reviewer inspection.
"""
                )
        else:
            st.error("Rejected by validation / extractor")
            st.code(result.error or "no error captured")


# ─── Per-unit results ────────────────────────────────────────────────────────
st.divider()
st.subheader("Extraction results")
st.markdown(
    """
One panel per **extraction unit**. PDFs and CSVs produce a single unit
per file. SMS logs produce one unit per non-empty line, since each
message is a separate buyer order. For each unit:

* **Left — Source** · two tabs. *Scrubbed* shows what the LLM actually
  received, with redaction tokens highlighted in teal. *Raw input*
  shows the unredacted text (demo only — production never exposes raw).
* **Right — Output** · the canonical `PurchaseOrder` the LLM produced,
  with a routing badge derived from confidence and any `flagged_fields`
  the model wants a human to verify.
"""
)

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
st.markdown(
    """
**Why this exists.** Procurement compliance reviewers need to answer
questions like *"show every ingestion in the last 7 days where model
confidence was below 0.8"* or *"show every PHI redaction event last
quarter"* without rummaging through inboxes. The audit log is the
authoritative record that makes those queries one SQL statement.

**What's recorded.** One row per ingestion attempt — accepted **or**
rejected. Each row captures the source artifact, the redaction
signature (which sensitive types matched and how many of each), the
LLM's self-reported confidence, the field paths the model flagged for
review, and the accept/reject decision. Append-only; rows persist in
`store.db → audit_log` across sessions.
"""
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
            "row": st.column_config.NumberColumn(
                "row",
                help="Audit row ID. Append-only; never reused.",
            ),
            "timestamp": st.column_config.TextColumn(
                "timestamp",
                help="Server-side ingestion time (UTC).",
            ),
            "file": st.column_config.TextColumn(
                "file",
                help="Source filename as ingested.",
            ),
            "fmt": st.column_config.TextColumn(
                "fmt",
                help="Detected format: pdf, csv, or sms.",
            ),
            "status": st.column_config.TextColumn(
                "status",
                help=(
                    "'accepted' = LLM produced a Pydantic-valid PurchaseOrder; "
                    "'rejected' = extractor or validator failed. Both still "
                    "leave an audit row."
                ),
            ),
            "confidence": st.column_config.NumberColumn(
                "confidence",
                format="%.2f",
                help=(
                    "LLM-self-reported confidence (0–1). Drives routing: "
                    "≥0.70 auto-route, 0.50–0.69 low-priority review, "
                    "<0.50 high-priority review."
                ),
            ),
            "redactions": st.column_config.TextColumn(
                "redactions",
                help=(
                    "Per-type counts of sensitive identifiers replaced by "
                    "the scrubber before the LLM saw the text. Format: "
                    "TYPE:count separated by '·'."
                ),
            ),
            "flagged": st.column_config.TextColumn(
                "flagged",
                help=(
                    "JSON-path field names the model said it was unsure "
                    "about. Used by reviewers to scope verification."
                ),
            ),
            "order": st.column_config.NumberColumn(
                "order",
                help=(
                    "Foreign key into the orders table. NULL when the "
                    "ingestion was rejected and no order was written."
                ),
            ),
        },
    )

    csv_col, _ = st.columns([1, 3])
    with csv_col:
        st.download_button(
            label=f"Export audit log → CSV ({len(audit_rows)} rows)",
            data=exporters.audit_log_to_csv_bytes(audit_rows),
            file_name=f"audit_log_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv",
            mime="text/csv",
            help=(
                "All audit rows flattened to CSV for offline compliance "
                "review. Opens cleanly in Excel."
            ),
            use_container_width=True,
        )

    with st.expander("Raw audit JSON"):
        st.code(json.dumps(audit_rows, indent=2), language="json")
