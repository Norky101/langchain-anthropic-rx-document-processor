# Order & Document Intake Pipeline — Walkthrough

A LangChain-powered ingestion pipeline that normalizes heterogeneous buyer-side documents (faxed PDF purchase orders, emailed CSV reorder lists, SMS reorder messages) into a single canonical `PurchaseOrder` schema, with deterministic redaction of sensitive identifiers and an append-only audit trail.

---

## 1. Problem

A B2B pharma marketplace serves suppliers (manufacturers, distributors, 503B compounders) selling to a long tail of independent pharmacies, small clinics, and hospital pharmacies. The bulk of those buyers do not run EDI. Orders arrive through three persistent channels:

- Faxed PDF purchase orders
- Emailed CSV reorder spreadsheets, with column names chosen by the buyer
- SMS messages from buyer staff to a supplier rep

Today these documents are hand-keyed into the marketplace or supplier ERP. The cost is operational (time per document), commercial (multi-day fulfillment lag), risk-related (NDC and quantity errors), and compliance-related (sensitive identifiers persist in inboxes; no consistent audit trail).

This pipeline replaces the hand-key step. Bytes in any of the three formats land as the same canonical record in the marketplace database, with sensitive identifiers redacted, every ingestion logged, and uncertain extractions explicitly routed to a human review queue.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  INBOUND DOCUMENT                                                │
│  PDF · CSV · SMS .txt                                            │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  1. DETECT                                  src/detector.py     │
│  Extension routing (.pdf → pdf, .csv → csv, .txt → sms)          │
│  Production: inbox watcher / SMS webhook / S3 ObjectCreated      │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. EXTRACT                                 src/extractors.py   │
│  PDF → pypdf.PdfReader.extract_text()                            │
│  CSV → pandas.read_csv with original headers preserved           │
│  SMS → split per non-empty line                                  │
│  Returns list[str]; PDF/CSV give one entry, SMS one per message  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. SCRUB                                   src/scrubber.py     │
│  Deterministic regex → typed placeholders                        │
│   DEA       [A-Z]{2}\d{7}              →  [REDACTED_DEA]         │
│   Account   acct/account/customer + #  →  [REDACTED_ACCOUNT]     │
│   Routing   routing/aba + 9-digit      →  [REDACTED_ROUTING]     │
│   SSN       \d{3}-\d{2}-\d{4}          →  [REDACTED_SSN]         │
│   Phone     ###-###-####               →  [REDACTED_PHONE]       │
│   Email     name@host                  →  [REDACTED_EMAIL]       │
│   PHI       "patient/pt/for" + name    →  [REDACTED_PHI]         │
│  Per-type counts surface in the audit log.                       │
│  Runs before the LLM stage; the model never sees raw values.     │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. STRUCTURED EXTRACTION  ←  the LLM       src/llm.py          │
│  langchain.chat_models.init_chat_model(                          │
│      "claude-haiku-4-5-20251001",                                │
│      model_provider="anthropic"                                  │
│  ).with_structured_output(PurchaseOrder)                         │
│  One LLM call per extraction unit. Provider swappable to         │
│  bedrock_converse via env var (LLM_PROVIDER, LLM_MODEL).         │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  5. VALIDATE                                src/schema.py       │
│  Pydantic v2 PurchaseOrder + LineItem                            │
│  ValidationError → audit row "rejected", no DB write             │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  6. PERSIST                                 src/storage.py      │
│  sqlite3. Three tables:                                          │
│    orders        — one row per accepted PurchaseOrder            │
│    line_items    — one row per LineItem (FK to orders.id)        │
│    audit_log     — one row per ingestion attempt (accepted OR    │
│                    rejected). Append-only.                       │
└──────────────────────────────────────────────────────────────────┘
```

Stack: Python 3.13 (uv) · LangChain + langchain-anthropic · Pydantic v2 · pypdf · pandas · sqlite3 · Streamlit · python-dotenv.

The pipeline has three deterministic stages before the LLM (detect, extract, scrub) and one after (validate). Schema enforcement and redaction stay predictable; the LLM is responsible only for schema-aligned extraction from natural language and tabular text.

---

## 3. Input → output examples

All output below is from live runs against `claude-haiku-4-5-20251001`.

### Example A — SMS

Raw input (one line of `samples/sms_orders.txt`):
```
hi this is sarah at lakeside rx, can we get 3 boxes of lisinopril 20 mg,
90 ct each — patient John Doe needs starting Monday — acct 55510
```

After the scrubber (text passed to the LLM):
```
hi this is sarah at lakeside rx, can we get 3 boxes of lisinopril 20 mg,
90 ct each — patient [REDACTED_PHI] needs starting Monday — acct [REDACTED_ACCOUNT]
```
Redactions: `{ACCOUNT: 1, PHI: 1}`.

Canonical PurchaseOrder written to SQLite:
```json
{
  "source_format": "sms",
  "source_file": "sms_orders.txt",
  "buyer_org_name": "Lakeside Rx",
  "buyer_account_ref": "[REDACTED_ACCOUNT]",
  "buyer_dea_redacted": null,
  "requested_ship_date": "2026-05-03",
  "line_items": [
    {
      "drug_name": "Lisinopril",
      "strength": "20 mg",
      "package_size": "90 ct",
      "quantity": 3,
      "unit_of_measure": "box"
    }
  ],
  "raw_excerpt_redacted": "hi this is sarah at lakeside rx, can we get 3 boxes of lisinopril 20 mg, 90 ct each — patient [REDACTED_PHI] needs starting Monday — acct [REDACTED_ACCOUNT]",
  "confidence": 0.85,
  "flagged_fields": []
}
```

### Example B — Ambiguous SMS

Raw input:
```
need to order more of the usual — Joe @ Maple
```

After scrubber: identical (no redaction patterns matched).

Canonical PurchaseOrder:
```json
{
  "source_format": "sms",
  "buyer_org_name": "Maple",
  "buyer_account_ref": "Maple",
  "line_items": [
    {
      "drug_name": "<UNKNOWN>",
      "strength": null,
      "quantity": 0,
      "unit_of_measure": null
    }
  ],
  "confidence": 0.15,
  "flagged_fields": [
    "line_items[0].drug_name",
    "line_items[0].quantity",
    "line_items[0].strength",
    "line_items[0].package_size",
    "line_items[0].unit_of_measure"
  ]
}
```

The model populates `<UNKNOWN>` for the drug name, sets quantity to zero, and sets `confidence` below the auto-route threshold. The audit log records this row with `status='accepted'` (Pydantic-valid) and the marketplace integration consumes only records where `confidence >= 0.7`. Anything below that threshold routes to a human review queue.

### Example C — CSV with non-canonical headers

Raw input (`samples/reorder_list.csv`):
```
Acct,NDC#,item,strngth,pkg,qty,ship_by
RV-7821,68180-0517-09,Lisinopril,20 mg,90 ct btl,8,2026-05-02
RV-7821,57664-0395-13,Atorvastatin,40 mg,90 ct btl,6,2026-05-02
…
```

Canonical PurchaseOrder (truncated):
```json
{
  "source_format": "csv",
  "buyer_account_ref": "RV-7821",
  "requested_ship_date": "2026-05-02",
  "line_items": [
    {
      "drug_name": "Lisinopril",
      "ndc": "68180-0517-09",
      "strength": "20 mg",
      "package_size": "90 ct bottle",
      "quantity": 8,
      "unit_of_measure": "bottle"
    },
    …
  ],
  "confidence": 0.95
}
```

The buyer's headers (`Acct`, `NDC#`, `item`, `strngth`, `pkg`, `qty`, `ship_by`) are mapped to the canonical schema fields without a hand-coded alias table. New buyer spreadsheets with different headers do not require code changes.

### Example D — Faxed PO PDF

Raw input (`samples/purchase_order.pdf`, after pypdf): a single-page faxed PO from St. Mary's Hospital Pharmacy to a 503B compounder, containing a DEA number, account number, fax/phone numbers, an email, and a free-text note `"Rush order — patient John Doe needs vancomycin starting Friday"`.

Scrubber output: 7 redactions across 5 types — `{DEA: 1, ACCOUNT: 1, PHONE: 3, EMAIL: 1, PHI: 1}`.

Canonical PurchaseOrder (excerpts):
```json
{
  "source_format": "pdf",
  "buyer_org_name": "St. Mary's Hospital Pharmacy",
  "buyer_account_ref": "[REDACTED_ACCOUNT]",
  "buyer_dea_redacted": "[REDACTED_DEA]",
  "po_reference": "SM-2026-0418-A",
  "requested_ship_date": "2026-04-30",
  "line_items": [
    { "drug_name": "Vancomycin (sterile compounded)", "ndc": "00074-6533-12",
      "strength": "1 g/vial", "package_size": "10 vial case", "quantity": 8 },
    { "drug_name": "Norepinephrine bitartrate", "ndc": "00641-6128-25",
      "strength": "4 mg / 4 mL", "package_size": "10 vial case", "quantity": 12 },
    { "drug_name": "Heparin sodium PF (preservative-free)", "ndc": "00641-2440-45",
      "strength": "5,000 U / 5 mL", "package_size": "25 vial case", "quantity": 4 },
    { "drug_name": "Sodium bicarbonate inj.", "ndc": "00074-1551-01",
      "strength": "8.4% 50mL", "package_size": "10 vial case", "quantity": 6 }
  ],
  "confidence": 0.95
}
```

The patient name in the free-text note is redacted at the scrubber stage and never reaches the model or the database.

---

## 4. Confidence scoring

### Mechanism

Confidence is produced by the same LLM call that produces the structured data. The `PurchaseOrder` schema includes two model-populated fields:

```python
confidence: float = Field(ge=0, le=1)
flagged_fields: list[str] = Field(default_factory=list)
```

The system prompt (`src/llm.py`) directs the model to:

1. Populate every required field. For fields not clearly stated in the source, infer where reasonable, or use a sentinel (`<UNKNOWN>`, `0`) and add the field path to `flagged_fields`.
2. Set `confidence` based on the proportion of fields it could ground in source text vs. infer or guess.
3. Lower `confidence` to under 0.5 when critical fields (drug name, quantity) are unrecoverable from the input.

The model has direct visibility into its own generation: for each field it emits, it can distinguish between values it located in the source text and values it generated by inference. Confidence is that introspection summarized as a scalar.

### Routing thresholds

| Range | Routing | Meaning |
|---|---|---|
| `>= 0.70` | Auto-route to marketplace | Required fields all sourced; no flags expected |
| `0.50 – 0.69` | Review queue, low priority | One or two inferred fields; reviewer confirms |
| `< 0.50` | Review queue, high priority | Critical fields missing or guessed |

The thresholds are configurable. They are surfaced to operators via the audit log and to the UI via colored badges.

### What confidence is, and what it is not

Confidence is a soft signal derived from a single forward pass. It is useful because the LLM has private information about its own generation that an external rule lacks. It is not a calibrated probability: two records with `confidence = 0.85` are not guaranteed to have identical accuracy, and scores will drift if the model or prompt changes.

The `flagged_fields` list is the more actionable output. It identifies exactly which fields a human reviewer should verify, which makes the human-in-the-loop step bounded.

### Production extensions

A production deployment would augment the LLM-derived score with:

1. Schema-level sanity rules. Example: NDC values must be 10 or 11 digits; the validator appends `line_items[*].ndc` to `flagged_fields` automatically when violated.
2. Cross-field plausibility. Example: a `Vancomycin` line item with a null `strength` is flagged regardless of model confidence.
3. Multi-shot voting. The pipeline calls the LLM `N` times; if any single call rates `confidence < 0.8` or any field disagrees across calls, the record routes to review.
4. Closed-loop calibration. Reviewer outcomes (accepted / corrected / rejected) feed back as training signal; thresholds are recalibrated periodically.

---

## 5. Outputs and downstream consumers

The pipeline normalizes inbound documents into a canonical `PurchaseOrder`. That record is the input to several downstream consumers; ingestion is not the terminal step. The demo ships three of those output artifacts as downloadable files so the input → output story is observable end-to-end:

| Artifact | Where in UI | Purpose | Format |
|---|---|---|---|
| **Canonical JSON** | per-result `Download canonical JSON` button | What the marketplace order-management API ingests. Single PurchaseOrder with embedded LineItem entries. | `application/json` |
| **Buyer confirmation PDF** | per-result `Download confirmation PDF` button | What an ops team emails back to the buyer to acknowledge receipt. Brand-aligned single-page receipt with header summary, line-items table, and routing-status footer. | `application/pdf` |
| **Audit log CSV** | `Export audit log → CSV` under the audit table | Tabular flatten of `audit_log` for offline compliance review. Opens cleanly in Excel; dict-typed columns are rendered as compact strings. | `text/csv` |

Generation is local (no additional API calls); see `src/exporters.py`.

### Production wiring (not enabled in the demo)

In a production deployment, the same canonical record fans out across additional consumers automatically on accept:

| Downstream | Trigger | Format |
|---|---|---|
| Marketplace order-management API | Confidence ≥ routing threshold (default 0.70) | REST POST · canonical JSON |
| Supplier ERP (NetSuite / SAP / Oracle) | On accept | ERP-specific adapter (Mulesoft / Workato / direct) |
| Buyer confirmation email | On accept | PDF (above), attached |
| EDI 855 acknowledgement | If the buyer is EDI-enabled | X12 855 over AS2 / SFTP / VAN |
| Review queue UI | Confidence below threshold OR `flagged_fields` non-empty | Same record; reviewer verifies and re-routes |
| Compliance archive | Always | Audit row → tamper-evident append-only store |
| Slack notification | On accept | Order summary message to the supplier rep |

The demo's per-result `What happens next` expander surfaces this routing decision tree inline so reviewers can see, for any individual order, where it would go next.

## 6. The audit log

Every ingestion attempt — accepted or rejected — appends one row to `audit_log`. Schema:

```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    source_file TEXT NOT NULL,
    source_format TEXT NOT NULL,
    order_id INTEGER REFERENCES orders(id),
    redaction_count_by_type TEXT NOT NULL,   -- JSON: {"DEA": 1, "ACCOUNT": 1, ...}
    llm_confidence REAL,
    flagged_fields TEXT NOT NULL,            -- JSON array of field paths
    status TEXT NOT NULL,                    -- 'accepted' or 'rejected'
    reason TEXT                              -- error detail if rejected
);
```

Surfaces:

- **CLI:** `uv run python ingest.py --show-audit` dumps the table.
- **SQL:** `sqlite3 store.db "select * from audit_log order by id desc"`.
- **Streamlit:** the per-result panel shows the audit row that was just written; the bottom of the page shows the full table.

The audit log supports 340B-relevant procurement compliance reviews by recording, per inbound document: the source channel, the redaction signature (which sensitive types were present), the model confidence, the reviewer-actionable flag list, and the accept/reject decision. In production the table would land in a tamper-evident store (append-only DynamoDB, Postgres with logical replication to S3 with object lock, etc.).

---

## 7. Sample reference

| File | Format | Description |
|---|---|---|
| `samples/purchase_order.pdf` | PDF | Faxed PO from a hospital pharmacy to a 503B compounder. Contains DEA, account, phone, email, and PHI in a free-text note. |
| `samples/reorder_list.csv` | CSV | Pharmacy chain weekly reorder. Buyer-named columns: `Acct, NDC#, item, strngth, pkg, qty, ship_by`. |
| `samples/sms_orders.txt` | SMS | Seven messages from independent pharmacies. Mix of clear orders, DEA + PHI, and one deliberately ambiguous request. |
| `samples/sms_quick.txt` | SMS | Three-message variant for short walkthroughs. |
| `samples/buyer_chaos.csv` | CSV | More extreme header variation: `Customer Number, DRUG, Dose / Form, Pkg, How Many, Want By`. |
| `samples/sms_edge_cases.txt` | SMS | Meta-cases: `"50 vials of nothing"`, abbreviated date formats, multi-line-item single SMS using prescriber shorthand. |

---

## 8. Out of scope (and the production answer)

| Skipped | Production answer |
|---|---|
| OCR for scanned faxes | Textract or Tesseract upstream of the extractor stage; one stage prepended, no downstream changes. |
| Production-grade PHI/PII detection | AWS Comprehend Medical or Microsoft Presidio in place of regex; same scrubber interface. |
| Column-aware CSV redaction | Schema-aware redaction by header name (the `Acct` column's values would be replaced inline). |
| Idempotency / dedupe | Content-hash + buyer-account-PO-number dedupe key; replay table for re-ingesting failures. |
| AWS Bedrock execution | One-line code change (`model_provider="bedrock_converse"`); operationally requires `langchain-aws`, AWS credentials, Bedrock model-access request. |
| Multi-LLM live failover | LangChain `with_fallbacks()` over multiple `bedrock_converse` model IDs (Claude → Llama → Cohere). |
| Multi-tenancy / authentication | API Gateway + per-supplier auth; row-level security or schema-per-supplier in RDS. |
| Tests | Per-stage unit + a regression-per-bug. |
| Production UI | Next.js on Cloudflare Pages or Vercel; FastAPI service running this LangChain pipeline behind API Gateway. |

---

## 9. Adjacent applications

The same six-stage pipeline applies to other supplier-side intake problems by changing the output Pydantic model and the system prompt. No code changes elsewhere.

- **Supplier catalog onboarding** — manufacturer product master in CSV/Excel/PDF spec sheets → canonical product schema in the marketplace PIM.
- **340B compliance documentation intake** — covered-entity attestations, eligibility letters, audit trails.
- **AP/AR document reconciliation** — invoices, credit memos, EOBs in PDF/CSV/email; matched against open POs.
- **Cross-supplier price/availability normalization** — competitor feeds in heterogeneous formats → canonical price comparison view.
