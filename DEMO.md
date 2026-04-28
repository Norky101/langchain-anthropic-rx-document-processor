# Demo Walkthrough

> **A self-contained read.** If you didn't write this, you should still be able to walk through it and understand what's happening, why it matters, and what the LLM is actually doing — without anyone explaining.

---

## 1. The problem this solves

A B2B pharma marketplace platform serves suppliers (manufacturers, distributors, 503B compounders) selling to thousands of long-tail buyers — independent pharmacies, small clinics, hospital pharmacies. **Most of those buyers don't have EDI.** So orders show up as:

- Faxed PDFs from a hospital pharmacy
- Emailed CSVs from a pharmacy chain's POS export
- SMS texts from an independent: *"need 20 bottles metformin 500mg, account 12345 — Joe at Maple"*

Today, supplier ops teams hand-key these into the marketplace or supplier ERP. That's the bottleneck on supplier onboarding and on serving the long-tail buyer. It's also error-prone (wrong NDC = recall risk) and compliance-hostile (no consistent audit trail; sensitive data sits in inboxes).

**This demo is the universal-adapter solution.** Bytes in any of those three formats land as the same canonical `PurchaseOrder` in the marketplace database, with sensitive data scrubbed, every ingestion logged, and uncertain extractions explicitly flagged for human review.

---

## 2. Architecture and tech stack

```
┌──────────────────────────────────────────────────────────────────┐
│  INBOUND DOCUMENT                                                │
│  ─────────────────                                               │
│  PDF · CSV · SMS .txt                                            │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  1. DETECT                                  src/detector.py     │
│  ─────────                                                       │
│  Extension routing (.pdf → pdf, .csv → csv, .txt → sms)          │
│                                                                  │
│  In production, this is the seam where an inbox watcher /        │
│  SMS webhook / S3 ObjectCreated event hands off into the         │
│  pipeline.                                                       │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  2. EXTRACT                                 src/extractors.py   │
│  ──────────                                                      │
│  PDF → pypdf.PdfReader(path).extract_text()                      │
│  CSV → pandas.read_csv(path).to_string() — original headers      │
│        preserved verbatim so the LLM does the schema mapping     │
│  SMS → split per line, one extraction unit per message           │
│                                                                  │
│  Each extractor returns list[str]. PDF and CSV give one entry;   │
│  SMS gives N entries (one per message). The pipeline iterates.   │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  3. SCRUB                                   src/scrubber.py     │
│  ────────                                                        │
│  Deterministic regex → typed placeholders                        │
│                                                                  │
│    DEA       [A-Z]{2}\d{7}              →  [REDACTED_DEA]        │
│    Account   acct/account/customer + value → [REDACTED_ACCOUNT]  │
│    Routing   routing/aba + 9-digit       →  [REDACTED_ROUTING]   │
│    SSN       \d{3}-\d{2}-\d{4}           →  [REDACTED_SSN]       │
│    Phone     ###-###-####                →  [REDACTED_PHONE]     │
│    Email     name@host                   →  [REDACTED_EMAIL]     │
│    PHI       "patient/pt/for" + Cap Cap  →  [REDACTED_PHI]       │
│                                                                  │
│  Per-type counts surface in the audit log. Runs BEFORE the LLM   │
│  so the model never sees raw sensitive values — minimizes        │
│  data-handling risk for the model provider.                      │
│                                                                  │
│  Why deterministic, not LLM-based: compliance reviewers want     │
│  predictable, auditable redaction. LLMs hallucinate.             │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  4. STRUCTURED EXTRACTION  ←  the LLM      src/llm.py           │
│  ──────────────────────────                                      │
│  langchain.chat_models.init_chat_model(                          │
│      "claude-haiku-4-5-20251001",                                │
│      model_provider="anthropic"                                  │
│  ).with_structured_output(PurchaseOrder)                         │
│                                                                  │
│  One call per extraction unit. The model receives:               │
│    - a system prompt explaining the canonical schema, the        │
│      placeholder convention, and the confidence/flag rules       │
│    - the scrubbed source text                                    │
│  And returns a populated PurchaseOrder Pydantic object.          │
│                                                                  │
│  Provider swap to AWS Bedrock is one argument:                   │
│    init_chat_model("anthropic.claude-...", "bedrock_converse")   │
│  Plus AWS creds, langchain-aws install, model-access request.    │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  5. VALIDATE                                src/schema.py       │
│  ───────────                                                     │
│  Pydantic v2 PurchaseOrder + LineItem                            │
│  Hard schema gate. ValidationError → audit row "rejected",       │
│  no DB write, surfaces in CLI / Streamlit as a red card.         │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  6. PERSIST                                 src/storage.py      │
│  ──────────                                                      │
│  sqlite3 (stdlib). Three tables:                                 │
│    orders        — one row per accepted PurchaseOrder            │
│    line_items    — one row per LineItem, FK to orders            │
│    audit_log     — one row per ingestion attempt (accepted OR    │
│                    rejected): timestamps, source file, format,   │
│                    redaction counts BY TYPE, llm_confidence,     │
│                    flagged_fields, status, reason                │
│                                                                  │
│  Append-only audit_log is the 340B-grade compliance trail.       │
│  In prod: SQLite → RDS Postgres / Aurora; tamper-evident store.  │
└──────────────────────────────────────────────────────────────────┘
```

**Stack summary:** Python 3.13 + uv · LangChain + langchain-anthropic · Pydantic v2 · pypdf · pandas · sqlite3 · Streamlit · python-dotenv · reportlab (sample-PDF generation only).

**Why this shape:** three deterministic stages before the LLM (detect, extract, scrub) and one after (validate). The LLM is the *only* non-deterministic stage. Anything compliance-relevant — redaction, schema enforcement — stays predictable.

---

## 3. End-to-end flow: real input → real output

These are actual outputs from running the pipeline with `claude-haiku-4-5-20251001`. Nothing in this section is invented.

### Example A — SMS, the textbook case

**Raw input** (one line of `samples/sms_orders.txt`):
```
hi this is sarah at lakeside rx, can we get 3 boxes of lisinopril 20 mg,
90 ct each — patient John Doe needs starting Monday — acct 55510
```

**After the scrubber** (what the LLM actually sees):
```
hi this is sarah at lakeside rx, can we get 3 boxes of lisinopril 20 mg,
90 ct each — patient [REDACTED_PHI] needs starting Monday — acct [REDACTED_ACCOUNT]
```
Redactions: `{ACCOUNT: 1, PHI: 1}` — the patient name and account number never reach the LLM.

**Canonical PurchaseOrder** (what comes out, written to SQLite):
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

What the LLM did that's hard with rules alone:
- Inferred the buyer name (`Lakeside Rx`) from a casual greeting (*"hi this is sarah at lakeside rx"*)
- Resolved the relative date (*"starting Monday"*) to a concrete `2026-05-03`
- Pulled `quantity=3` and `unit_of_measure="box"` from natural prose
- Preserved the redaction placeholders verbatim

### Example B — SMS, the human-review case

**Raw input** (last line of `samples/sms_orders.txt`):
```
need to order more of the usual — Joe @ Maple
```

**After the scrubber**: identical (nothing matches a redaction pattern).

**Canonical PurchaseOrder**:
```json
{
  "source_format": "sms",
  "buyer_org_name": "Maple",
  "buyer_account_ref": "Maple",
  "line_items": [
    {
      "drug_name": "<UNKNOWN>",
      "strength": null,
      "package_size": null,
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

This is the demo's **money moment**. The LLM doesn't pretend to know what *"more of the usual"* means. It explicitly flags every uncertain field and drops `confidence` to 0.15. Anything below 0.5 routes to a human-review queue rather than the marketplace — the audit log captures it for the rep to call Joe back.

### Example C — CSV, the schema-mapping case

**Raw input** (`samples/reorder_list.csv`, headers chosen by the buyer):
```
Acct,NDC#,item,strngth,pkg,qty,ship_by
RV-7821,68180-0517-09,Lisinopril,20 mg,90 ct btl,8,2026-05-02
RV-7821,57664-0395-13,Atorvastatin,40 mg,90 ct btl,6,2026-05-02
RV-7821,00378-0114-77,Metformin HCl,500 mg,500 ct btl,4,2026-05-02
…
```

**After scrubber**: 0 redactions (no preceding label words like "acct" / "patient"; CSV column-aware redaction is a documented scope cut).

**Canonical PurchaseOrder** (truncated to two of six line items):
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
    {
      "drug_name": "Atorvastatin",
      "ndc": "57664-0395-13",
      "strength": "40 mg",
      "package_size": "90 ct bottle",
      "quantity": 6,
      "unit_of_measure": "bottle"
    }
    // … 4 more
  ],
  "confidence": 0.95,
  "flagged_fields": []
}
```

The buyer typed `Acct, NDC#, item, strngth, pkg, qty, ship_by` — none of those are the canonical field names. Nobody hand-coded a column-aliasing table. The LLM mapped them all on its own, including `pkg=90 ct btl` → `package_size="90 ct bottle"` (it expanded the abbreviation). This is the part that makes the pipeline survivable when a new buyer shows up next month with a different spreadsheet.

### Example D — PDF, the full faxed-PO case

**Raw input** (`samples/purchase_order.pdf`, after pypdf): contains DEA `AB1234567`, account `SM-44012`, fax/phone numbers, an email, and a free-text note *"Rush order — patient John Doe needs vancomycin starting Friday"*.

**After scrubber**: 7 redactions across 5 types: `{DEA: 1, ACCOUNT: 1, PHONE: 3, EMAIL: 1, PHI: 1}`.

**Canonical PurchaseOrder** (excerpts):
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
  "confidence": 0.95,
  "flagged_fields": []
}
```

The free-text note's patient PHI never reaches the marketplace. The DEA and account number are present in the canonical record only as typed placeholders, which means downstream analytics can join on `buyer_account_ref` without ever touching the actual account number.

---

## 4. How confidence scoring works

This is the most important section to be honest about. **Confidence is LLM-self-reported**, not a calibrated probability.

### The mechanism
The `PurchaseOrder` schema has two fields the LLM populates alongside the data:

```python
confidence: float = Field(ge=0, le=1, description="Self-rated 0–1")
flagged_fields: list[str] = Field(
    description="Field paths the model is uncertain about"
)
```

The system prompt (`src/llm.py`) instructs the model:

> *Set confidence honestly. If the buyer didn't specify a quantity or drug strength, mark those fields in flagged_fields and lower confidence accordingly. A confidence below 0.5 will route the record to human review.*

So confidence comes from the same forward pass that produces the data. The model writes the JSON and rates its own work in one shot.

### What confidence values mean in practice

| Range | UI badge | Meaning | Real example from the demo data |
|---|---|---|---|
| 0.85–1.00 | 🟢 | All required fields clearly present | Faxed PO with structured table; CSV with named columns |
| 0.65–0.84 | 🟡 | One or two fields inferred or ambiguous | SMS with shorthand drug name (*"amox"* → "Amoxicillin"), inferred packaging |
| 0.50–0.64 | 🟡 | Multiple fields inferred; review recommended | SMS missing quantity but drug clear |
| < 0.50 | 🔴 | Critical fields unknown; **route to human review** | *"need to order more of the usual"* → 0.15 |

### Why this is a useful signal even though it's not calibrated

It's a **soft signal**, but it's the one signal the LLM is best positioned to give. The model knows what the input said and what its own output guessed, so it can compare them. That's harder for an external rule.

The score is paired with `flagged_fields`, which is the more actionable output — it tells you *which* fields the human reviewer needs to confirm. Together they let the system route confidently extracted records straight to the marketplace and uncertain ones to a queue.

### What this doesn't claim

- It isn't a calibrated probability. Two records with `confidence=0.85` are not guaranteed to have the same true accuracy.
- The model can be overconfident on hallucinated fields (any LLM can). For high-stakes deployments you'd add cross-checks: NDC format validation against a master list, drug name checks against an FDA orange-book lookup, etc.
- It's a single-shot estimate. More rigorous setups vote across N samples, or use a separate judge model.

### What I'd add for production

1. **Schema-level sanity rules** — *"if NDC is set, it must be 10 or 11 digits."* `flagged_fields` gets `ndc` appended automatically when violated.
2. **Cross-field plausibility** — *"if drug_name is 'Vancomycin' and strength missing, flag."*
3. **Multi-shot voting** — call the LLM N=3 times; if confidence < 0.8 and outputs disagree, route to review even if any single call rated high.
4. **Telemetry** — log confidence vs. eventual outcome (accepted/edited/rejected by humans) and recalibrate the threshold.

For a 3-hour demo, the LLM-self-report is the right primitive. For production it's the foundation, not the whole compliance story.

---

## 5. What you'll see when you run it

```bash
# 1. one-shot CLI on all samples
uv run python ingest.py samples/sms_orders.txt samples/reorder_list.csv samples/purchase_order.pdf

# 2. inspect the audit trail
uv run python ingest.py --show-audit

# 3. interactive UI
uv run streamlit run app.py
```

The Streamlit UI is what someone unfamiliar with the project would interact with: a drop-zone, then for each input a three-column panel showing scrubbed source, canonical JSON, and the audit row. SMS files render as one panel per message; PDFs and CSVs render as one panel for the file.

---

## 6. What's not here (and the answer if asked)

| Skipped | Production answer |
|---|---|
| OCR for scanned faxes | Textract or Tesseract upstream of the extractor stage. One stage prepended; no other changes. |
| Production-grade PHI/PII detection | AWS Comprehend Medical or Microsoft Presidio in place of regex. Same scrubber interface. |
| Column-aware CSV redaction | Schema-aware redaction by header name. The `Acct` column would have its values replaced. |
| Idempotency / dedupe | Content-hash + buyer-account-PO-number dedupe key, with a replay table for re-ingesting failures. |
| AWS Bedrock execution | Code change: one line. Infra: install `langchain-aws`, configure AWS credentials, request Bedrock model access, swap model ID format. ~20 min if AWS account is set up. |
| Multi-LLM live failover | LangChain's `with_fallbacks()` over multiple `bedrock_converse` model IDs (Claude → Llama → Cohere). |
| Multi-tenancy / authentication | API Gateway + per-supplier auth; SQLite has no concept of tenants. RDS schema-per-supplier or row-level-security. |
| Tests | One smoke test per stage would be the immediate next addition. Production gets unit + a regression-per-bug. |
| Real React/Next.js UI | UI is Streamlit because of the time-box. Production would be Next.js on Cloudflare Pages or Vercel, talking to a FastAPI service running this LangChain pipeline. |

---

## 7. Adjacent things this same pipeline does

The cost of adding a new ingestion target is **one Pydantic model** (and maybe a different system prompt). The detect → extract → scrub → LLM → validate → persist shape stays identical. Examples:

- **Supplier catalog onboarding** — manufacturer product master in CSV/Excel/PDF spec sheets → canonical product schema in the marketplace PIM.
- **340B compliance documentation intake** — covered-entity attestations, eligibility letters, audit trails. The audit-log story becomes the centerpiece.
- **AP/AR document reconciliation** — invoices, credit memos, EOBs in PDF/CSV/email. Match against open POs in the marketplace.
- **Long-tail buyer onboarding** — new pharmacy joining the marketplace, bringing reorder history from a POS export. Same pipeline.
- **Cross-supplier price/availability normalization** — competitor feeds in heterogeneous formats → canonical price comparison view.

Each of these is one Pydantic schema and a system-prompt edit away. The infrastructure already exists.
