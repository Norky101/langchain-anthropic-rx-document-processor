# graphiteRxDemo

> **Universal order & document intake for healthcare commerce.** Drop a PDF, CSV, or SMS message in. A canonical `PurchaseOrder` comes out — DEA numbers and PHI scrubbed, every ingestion logged for 340B-grade audit. Built with LangChain + Claude.

> 📖 **Walkthrough doc:** [`DEMO.md`](DEMO.md) — self-contained read with architecture, real input/output examples, and a full explanation of how confidence scoring works.

---

## What this is

A reference implementation of a multi-format ingestion pipeline for a B2B pharma marketplace. It demonstrates how an LLM acts as a **universal adapter** between heterogeneous buyer-side inputs (faxed POs, emailed reorder spreadsheets, SMS orders) and one canonical schema in a marketplace database.

```
Input file (PDF / CSV / SMS .txt)
        │
        ▼
┌──────────────────┐
│ Format detector  │  ← extension + content sniff
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Extractor        │  PDF → pypdf  |  CSV → pandas  |  SMS → passthrough
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Sensitive-data   │  ← deterministic regex (DEA, account, PHI, phone, email)
│ scrubber         │     replaces with [REDACTED_TYPE] placeholders
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ LangChain LLM    │  ← init_chat_model("claude-sonnet-4-6", "anthropic")
│ structured       │     .with_structured_output(PurchaseOrder)
│ extraction       │
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Pydantic         │  ← PurchaseOrder + LineItem validation
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ SQLite           │  orders + line_items + audit_log
└──────────────────┘
```

## Why this shape

Suppliers on a B2B pharma marketplace receive orders from thousands of long-tail buyers — independent pharmacies, small clinics, hospital pharmacies — most of which **don't have EDI**. Orders arrive via:

- **Faxed PDFs** — purchase orders from a hospital pharmacy to a 503B compounder
- **Emailed CSVs** — pharmacy chain reorder spreadsheets, columns named however the buyer's staff typed them
- **SMS / texts** — independents texting their rep: *"need 20 bottles metformin 500mg, account 12345 — Joe at Maple Pharmacy"*

Today, supplier ops teams hand-key these documents into the marketplace or supplier ERP. That's slow, error-prone, and engineering-bottlenecked: every new buyer channel needs a new parser.

The pipeline collapses the "every new format needs new code" problem by routing every input through one LLM-backed extraction stage that aligns to a single Pydantic schema. New formats join automatically; the only thing that grows is the audit log.

The LLM does **only** what frontier models are uniquely good at: schema-aligned extraction from messy text. Everything sensitive — redaction, validation — stays deterministic so compliance reviewers get predictable behavior.

## Quick start

```bash
# install deps (uv handles Python 3.13 too)
uv sync

# get an Anthropic API key at https://console.anthropic.com (5 min, $5 credit)
cp .env.example .env
# edit .env and paste your key

# run the pipeline against the sample inputs
uv run python ingest.py samples/sms_orders.txt samples/reorder_list.csv samples/purchase_order.pdf

# inspect the audit log
uv run python ingest.py --show-audit

# or run the Streamlit UI
uv run streamlit run app.py
```

Demo cost: ~$0.10 against the bundled samples.

## Sample inputs

The `samples/` folder contains three deliberately-different inputs that exercise each stage:

| File | Channel | What it tests |
|---|---|---|
| `samples/purchase_order.pdf` | Faxed PO | pypdf extraction; regex catches DEA, account #, phone, email, PHI in the free-text note |
| `samples/reorder_list.csv` | Emailed buyer reorder | Non-canonical headers (`Acct, NDC#, item, strngth, pkg, qty, ship_by`) — the LLM does the schema mapping |
| `samples/sms_orders.txt` | SMS log from independents | The hardest case: each line is a free-text order, sometimes with DEA or account inline, sometimes ambiguous (one line is intentionally too vague — confidence drops below 0.5 and `flagged_fields` lights up) |

`scripts/make_pdf.py` regenerates the sample PDF if you want to tweak it.

## CLI

```
uv run python ingest.py <files...>     # ingest each file end-to-end
uv run python ingest.py --show-audit   # dump the audit log
```

Each ingestion writes:

* one row in `orders` (with foreign keys to one or more `line_items`)
* one row in `audit_log` (timestamps, redaction counts by type, LLM confidence, flagged fields, status)

## Streamlit UI

`uv run streamlit run app.py` opens a drop-zone that runs any file through the same pipeline and renders three columns:

1. The scrubbed source text (with `[REDACTED_*]` placeholders)
2. The canonical `PurchaseOrder` JSON the LLM produced
3. The audit-log row that was just appended

Deploys cleanly to [Streamlit Community Cloud](https://share.streamlit.io) — set `ANTHROPIC_API_KEY` as a secret under *Manage app → Secrets* before first run.

## Production deployment notes

The same code runs against AWS Bedrock by changing one argument to `init_chat_model`:

```python
# local / direct Anthropic
model = init_chat_model("claude-sonnet-4-6", model_provider="anthropic")

# Bedrock (HIPAA BAA AWS account)
model = init_chat_model("anthropic.claude-sonnet-4-6", model_provider="bedrock_converse")
```

`bedrock_converse` also supports Llama, Cohere, and Titan with the same call signature — multi-LLM orchestration without code changes. Set `LLM_PROVIDER` and `LLM_MODEL` env vars to swap at runtime.

For pharma deployments:

- SQLite → RDS (Postgres or Aurora) inside a HIPAA BAA AWS account.
- Streamlit → FastAPI service behind API Gateway, with a Next.js front-end on Cloudflare Pages or Vercel.
- Inbox watcher / SMS webhook / S3 ObjectCreated event triggers the pipeline; structured outputs feed the marketplace and the audit log lands in a tamper-evident store.

## What's not built here

| Skipped | How it'd ship |
|---|---|
| OCR for scanned faxes | Textract or Tesseract upstream of the extractor stage |
| Production-grade PHI/PII detection | AWS Comprehend Medical or Microsoft Presidio in place of regex |
| Column-aware CSV scrubbing | Regex catches free-text patterns; columnar PII (e.g. `Acct` column values) needs schema-aware redaction |
| Idempotency / dedupe | Content-hash + buyer-account-PO-number dedupe key + replay table |
| Multi-LLM live failover | LangChain's `with_fallbacks()` over multiple `bedrock_converse` model IDs |
| Authentication / multi-tenancy | API Gateway + per-supplier auth; SQLite has no concept of tenants |
| Tests | One smoke test would be the next addition; production gets unit + a regression-per-bug |

## Repo layout

```
graphiteRxDemo/
├── samples/                # PDF, CSV, SMS sample inputs
├── scripts/make_pdf.py     # regenerates the sample PDF
├── src/
│   ├── schema.py           # PurchaseOrder + LineItem Pydantic models
│   ├── detector.py         # format routing
│   ├── extractors.py       # pdf / csv / sms extractors
│   ├── scrubber.py         # regex DEA / account / PHI redaction
│   ├── llm.py              # LangChain structured-output chain
│   ├── storage.py          # SQLite ops + audit log
│   └── pipeline.py         # orchestration
├── ingest.py               # CLI entrypoint
└── app.py                  # Streamlit UI
```
