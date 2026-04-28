"""Format-specific extractors.

Each returns a list of `extraction units` — strings that map 1:1 onto a
PurchaseOrder. SMS is the multi-unit case: each line of an SMS log is a
separate order from a separate buyer, so we split per line. PDFs and CSVs
typically represent a single order each, so they return a list of length 1.
The pipeline runs the LLM once per unit.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from pypdf import PdfReader


def sms_extract(path: Path) -> list[str]:
    """SMS log: one order per non-empty line."""
    text = path.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def pdf_extract(path: Path) -> list[str]:
    """Pull text from every page. Native (text-layer) PDFs only — scanned PDFs
    would need OCR upstream of this stage. One order per file."""
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return ["\n".join(pages)]


def csv_extract(path: Path) -> list[str]:
    """Render the CSV with original column headers preserved so the LLM does
    the schema-mapping work. Treated as one order with multiple line items."""
    df = pd.read_csv(path)
    buf = io.StringIO()
    buf.write(f"CSV file: {path.name}\n")
    buf.write(f"Original columns (verbatim from buyer): {list(df.columns)}\n\n")
    buf.write(df.to_string(index=False))
    return [buf.getvalue()]
