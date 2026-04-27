"""Format-specific extractors.

Each returns a string of "extracted text" suitable for the scrubber and the LLM
stage. CSV is a special case: we preserve the original column names (which may
be non-canonical) so the LLM does the schema-mapping work, rather than us
hard-coding column aliases.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
from pypdf import PdfReader


def sms_extract(path: Path) -> str:
    """SMS log: each line is a separate order. Return as-is."""
    return path.read_text(encoding="utf-8")


def pdf_extract(path: Path) -> str:
    """Pull text from every page. Native (text-layer) PDFs only — scanned PDFs
    would need OCR upstream of this stage."""
    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def csv_extract(path: Path) -> str:
    """Render the CSV as a markdown-style table so the LLM sees the original
    column headers verbatim. This is the path that exercises the LLM's
    schema-mapping ability when buyers use non-canonical column names."""
    df = pd.read_csv(path)
    buf = io.StringIO()
    buf.write(f"CSV file: {path.name}\n")
    buf.write(f"Original columns (verbatim from buyer): {list(df.columns)}\n\n")
    buf.write(df.to_string(index=False))
    return buf.getvalue()
