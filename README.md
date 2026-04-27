# graphiteRxDemo

> **Universal order & document intake for healthcare commerce.** Drop a PDF, CSV, or SMS message in. A canonical `PurchaseOrder` comes out — DEA numbers and PHI scrubbed, every ingestion logged for 340B-grade audit. Built with LangChain + Claude.

## What this is

A reference implementation of a multi-format ingestion pipeline for a B2B pharma marketplace. It demonstrates how an LLM acts as a **universal adapter** between heterogeneous buyer-side inputs (faxed POs, emailed reorder spreadsheets, SMS orders) and a single canonical schema in a marketplace database.

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
│ Sensitive-data   │  ← deterministic regex (DEA, account, PHI)
│ scrubber         │     replaces with [REDACTED_TYPE] placeholders
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ LangChain LLM    │  ← init_chat_model("claude-sonnet-4-5", "anthropic")
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

## Quick start

```bash
# install deps
uv sync

# set your Anthropic API key
cp .env.example .env
# edit .env and paste your key from console.anthropic.com

# run the pipeline against the sample inputs
uv run python ingest.py samples/sms_orders.txt samples/reorder_list.csv samples/purchase_order.pdf

# inspect the audit log
uv run python ingest.py --show-audit

# or run the Streamlit UI
uv run streamlit run app.py
```

## Why an LLM here

The pipeline has three deterministic stages (detect, extract, scrub) and one LLM stage (canonicalize). The LLM is the **universal adapter** — when a new buyer channel arrives next month (an EHR-export JSON, a portal CSV, a different fax template), no new parser is needed. Bytes go through the same pipeline; the LLM does the schema-alignment work.

That's how you onboard long-tail buyers (independent pharmacies, small clinics) without engineering capacity becoming the bottleneck.

The LLM does **only** what frontier models are uniquely good at: schema-aligned extraction from messy text. Everything sensitive — redaction, validation — stays deterministic so compliance reviewers get predictable behavior.

## Production deployment notes

Same code runs against AWS Bedrock by changing one argument to `init_chat_model`:

```python
# local / direct Anthropic
model = init_chat_model("claude-sonnet-4-5", model_provider="anthropic")

# Bedrock (HIPAA BAA AWS account)
model = init_chat_model("anthropic.claude-sonnet-4-5", model_provider="bedrock_converse")
```

`bedrock_converse` also supports Llama, Cohere, and Titan with the same call signature — multi-LLM orchestration without code changes.

For pharma deployments: SQLite → RDS (Postgres or Aurora). Streamlit → FastAPI service behind API Gateway with a Next.js front-end on Cloudflare Pages or Vercel. Inbox watcher / SMS webhook / S3 ObjectCreated event triggers the pipeline; structured outputs feed the marketplace.

## What's not built here

| Skipped | How it'd ship |
|---|---|
| Real OCR for scanned faxes | Textract or Tesseract upstream of the extractor stage |
| Production-grade PHI/PII detection | AWS Comprehend Medical or Microsoft Presidio in place of regex |
| Idempotency / dedupe | Content-hash + buyer-account-PO-number dedupe key + replay table |
| Multi-LLM live failover | Implemented via `bedrock_converse` + LangChain's `with_fallbacks()` |
| Authentication / multi-tenancy | API Gateway + per-supplier auth; SQLite has no concept of tenants |

## Repo layout

```
graphiteRxDemo/
├── samples/                # PDF, CSV, SMS sample inputs
├── scripts/make_pdf.py     # generates the sample PDF
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
