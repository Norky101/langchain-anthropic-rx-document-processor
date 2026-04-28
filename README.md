# graphiteRxDemo

[![CI](https://github.com/Norky101/graphiteRxDemo/actions/workflows/ci.yml/badge.svg)](https://github.com/Norky101/graphiteRxDemo/actions/workflows/ci.yml)

🔗 **Live demo:** [graphiterx-order-intake.streamlit.app](https://graphiterx-order-intake.streamlit.app/) — drop a sample, see the pipeline run end-to-end.

A LangChain-powered ingestion pipeline that normalizes heterogeneous buyer-side documents (faxed PDF purchase orders, emailed CSV reorder lists, SMS reorder messages) into a single canonical `PurchaseOrder` record, with deterministic redaction of sensitive identifiers and an append-only audit trail.

📖 **[`DEMO.md`](DEMO.md)** — full walkthrough: architecture, real input/output examples, confidence-scoring mechanism, production extensions.

---

## Architecture

```
Input file (PDF / CSV / SMS .txt)
        │
        ▼
┌──────────────────┐
│ Format detector  │  extension-based routing
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Extractor        │  PDF → pypdf  ·  CSV → pandas  ·  SMS → passthrough
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Sensitive-data   │  deterministic regex (DEA, account, phone, email, PHI)
│ scrubber         │  → typed [REDACTED_*] placeholders
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ LangChain LLM    │  init_chat_model("claude-haiku-4-5", "anthropic")
│ structured       │    .with_structured_output(PurchaseOrder)
│ extraction       │
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ Pydantic         │  PurchaseOrder + LineItem schema validation
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ SQLite           │  orders + line_items + audit_log (append-only)
└──────────────────┘
```

The pipeline has three deterministic stages before the LLM (detect, extract, scrub) and one after (validate). The LLM stage is responsible only for schema-aligned extraction from natural-language and tabular text. New input formats are added by extending the extractor map; no other stage changes.

## Why an LLM in this position

Buyers on a B2B pharma marketplace originate orders through three persistent channels: faxed PDFs, emailed CSVs with buyer-named columns, and SMS messages. Hand-coding a parser per channel does not scale to long-tail buyers. The LLM acts as a universal adapter from heterogeneous text to one Pydantic schema, so onboarding a new buyer channel does not require code changes — only new sample data and, if needed, a system-prompt revision.

Compliance-relevant work (redaction, schema enforcement) stays deterministic. The model never sees raw sensitive identifiers and cannot bypass schema validation.

## Quick start

```bash
uv sync

cp .env.example .env
# paste an Anthropic API key from console.anthropic.com

uv run python ingest.py samples/sms_orders.txt samples/reorder_list.csv samples/purchase_order.pdf
uv run python ingest.py --show-audit
uv run streamlit run app.py
```

Cost against the bundled samples: ~$0.10 on Haiku 4.5.

## Sample inputs

| File | Format | Description |
|---|---|---|
| `samples/purchase_order.pdf` | PDF | Faxed PO from a hospital pharmacy to a 503B compounder. DEA, account, phone, email, and PHI in a free-text note. |
| `samples/reorder_list.csv` | CSV | Pharmacy chain weekly reorder. Buyer-named columns (`Acct, NDC#, item, strngth, pkg, qty, ship_by`). |
| `samples/sms_orders.txt` | SMS | Seven messages from independent pharmacies. Mix of clear orders, DEA + PHI, and one deliberately ambiguous request. |
| `samples/sms_quick.txt` | SMS | Three-message variant for shorter walkthroughs. |
| `samples/buyer_chaos.csv` | CSV | More extreme header variation. |
| `samples/sms_edge_cases.txt` | SMS | Meta-cases: vague drug name, abbreviated date, multi-line-item single SMS. |

`scripts/make_pdf.py` regenerates the sample PDF.

## CLI

```
uv run python ingest.py <files...>     # ingest each file
uv run python ingest.py --show-audit   # dump the audit log
```

Each ingestion writes one row to `orders` (with FKs to `line_items`) and one row to `audit_log` (timestamps, redaction counts by type, LLM confidence, flagged fields, status).

## Streamlit UI

```bash
uv run streamlit run app.py
```

The UI presents per-document panels with three views: the original raw text (demo only), the scrubbed text passed to the LLM with redaction tokens highlighted, and the canonical `PurchaseOrder` output with confidence-routing badge. The full audit log renders as a sortable table at the bottom.

Each accepted record exposes downloadable artifacts:

- **Canonical JSON** — what the marketplace order-management API ingests
- **Buyer confirmation PDF** — single-page acknowledgement receipt for the buyer
- A `What happens next` expander listing the downstream consumers that would receive the canonical record in production (marketplace API, supplier ERP, EDI 855, review queue, compliance archive)

The audit log table has a CSV export for offline compliance review. See `src/exporters.py` and DEMO.md §5 for details.

The UI is rate-limited via `st.session_state` to a configurable number of ingestions per browser session (`DEMO_INGEST_QUOTA`, default `5`) to bound API spend on public deployments.

### Configuration reference

All configuration is via environment variables. Local: `.env` (gitignored). Streamlit Cloud: *Manage app → Secrets*.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes | — | Anthropic API key. The CLI and Streamlit both use it. |
| `DEMO_INGEST_QUOTA` | no | `5` | Per-browser-session ingestion cap. Bounds API spend on public URLs. |
| `DEMO_MAX_FILE_MB` | no | `5` | Per-upload size cap. Files above this are rejected before any processing. |
| `DEMO_SHOW_RAW` | no | unset | Set to `1` to expose the *Raw input (before scrubber)* tab. Off by default. Public deploys never expose unredacted user input. |
| `DEMO_AUTO_THRESHOLD` | no | `0.70` | Confidence floor for auto-routing to the marketplace. Below = review queue. |
| `DEMO_LOW_THRESHOLD` | no | `0.50` | Boundary between low- and high-priority review. |
| `LLM_PROVIDER` | no | `anthropic` | LangChain provider id. Set to `bedrock_converse` to switch to AWS Bedrock. |
| `LLM_MODEL` | no | `claude-haiku-4-5-20251001` | Model identifier passed to `init_chat_model`. |
| `LLM_TIMEOUT_S` | no | `30` | Per-LLM-call timeout in seconds. |
| `LLM_MAX_ATTEMPTS` | no | `3` | Retry attempts on transient Anthropic errors (rate-limit, timeout, 5xx). |

### Deployment to Streamlit Community Cloud

1. Push the repository to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create a new app pointing at `app.py` on the `main` branch.
3. Under *Manage app → Secrets*, paste at minimum:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   DEMO_INGEST_QUOTA = "5"
   DEMO_MAX_FILE_MB = "5"
   # Leave DEMO_SHOW_RAW unset to keep the raw-input tab hidden on the public URL.
   ```
4. Set a monthly spend cap on the Anthropic API key at [console.anthropic.com → Billing](https://console.anthropic.com/settings/billing) — second line of defense against runaway cost.

## Testing

```bash
uv run pytest                        # 86 tests, ~0.5 s on a laptop
uv run pytest --cov                  # with coverage report (fail_under=90)
uv run pytest tests/test_scrubber.py # one module
uv run pytest -k pdf                 # filter by name
```

`tests/` covers every deterministic module in `src/` to 100% line coverage. The Anthropic API call in `src/llm.py` is omitted from coverage and is exercised manually against the bundled samples (the `samples/` folder contains real-LLM verification inputs).

CI runs the same tests on every push and pull request to `main` via `.github/workflows/ci.yml`.

## Security notes

- **API keys are never committed.** `.env` is gitignored. `.env.example` ships only a placeholder.
- **Sensitive identifiers are redacted before the LLM stage.** The model never sees raw DEA, account, SSN, phone, email, or PHI patterns. See `src/scrubber.py` for the regex set; production deployments should swap this for AWS Comprehend Medical or Microsoft Presidio for full HIPAA PHI detection.
- **The raw-input UI tab is hidden by default.** Set `DEMO_SHOW_RAW=1` only for local development or deployments inside a trust boundary.
- **File uploads are validated** for extension, size cap, and (for PDFs) magic-byte signature before the pipeline runs.
- **Per-session ingestion quota** caps API spend; pair with the Anthropic monthly spend cap.
- **The audit log is the authoritative record.** SQLite for the demo; production should land it in a tamper-evident append-only store (DynamoDB, Postgres with logical replication to S3 with object lock).

## Production deployment

The same code targets AWS Bedrock by changing the model provider:

```python
init_chat_model("anthropic.claude-sonnet-4-6", model_provider="bedrock_converse")
```

`bedrock_converse` exposes Claude, Llama, Cohere, and Titan via a unified Converse API, so multi-LLM orchestration is a configuration change. Operationally, Bedrock requires `langchain-aws`, AWS credentials, and a Bedrock model-access request.

A pharma deployment would replace SQLite with RDS (Postgres or Aurora) inside a HIPAA BAA AWS account, run this pipeline as a FastAPI service behind API Gateway, and trigger ingestion from inbox watchers, SMS webhooks, or S3 `ObjectCreated` events.

## Out of scope

| Skipped | Production answer |
|---|---|
| OCR for scanned faxes | Textract or Tesseract upstream of the extractor stage |
| Production-grade PHI/PII detection | AWS Comprehend Medical or Microsoft Presidio in place of regex |
| Column-aware CSV scrubbing | Schema-aware redaction by header name |
| Idempotency / dedupe | Content-hash + buyer-account-PO-number dedupe key with replay table |
| Multi-LLM live failover | LangChain `with_fallbacks()` over multiple `bedrock_converse` model IDs |
| Authentication / multi-tenancy | API Gateway + per-supplier auth; row-level security in RDS |
| Test coverage | Per-stage unit + a regression-per-bug |

## Repository layout

```
graphiteRxDemo/
├── DEMO.md                            # full walkthrough document
├── README.md                          # this file
├── pyproject.toml                     # uv project + pytest + coverage config
├── .github/workflows/ci.yml           # GitHub Actions CI
├── .streamlit/config.toml             # Streamlit theme (brand palette)
├── samples/                           # PDF / CSV / SMS sample inputs
├── scripts/
│   ├── make_pdf.py                    # regenerates the sample PDF
│   └── make_slides.py                 # generates slides/graphiterx_demo.pptx
├── slides/graphiterx_demo.pptx        # generated 5-slide deck
├── src/
│   ├── schema.py                      # PurchaseOrder + LineItem Pydantic models
│   ├── detector.py                    # format routing
│   ├── extractors.py                  # pdf / csv / sms extractors
│   ├── scrubber.py                    # regex sensitive-data redaction
│   ├── llm.py                         # LangChain structured-output chain
│   ├── storage.py                     # SQLite ops + audit log
│   ├── exporters.py                   # canonical JSON · confirmation PDF · audit CSV
│   └── pipeline.py                    # orchestration
├── tests/                             # pytest suite (86 tests, 100% on src/ ex llm)
├── ingest.py                          # CLI entrypoint
└── app.py                             # Streamlit UI
```
