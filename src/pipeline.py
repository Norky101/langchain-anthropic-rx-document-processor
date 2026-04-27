"""Orchestration: detect → extract → scrub → LLM → validate → store.

This is the file that proves the architecture. Every input format takes the
same path through the same five stages; the only stage that does anything
input-specific is the extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from . import extractors, llm, storage
from .detector import Format, detect_format
from .schema import PurchaseOrder
from .scrubber import ScrubResult, scrub


_EXTRACTORS = {
    "pdf": extractors.pdf_extract,
    "csv": extractors.csv_extract,
    "sms": extractors.sms_extract,
}


@dataclass
class IngestResult:
    """Captured for both successful and rejected ingestions so the audit log
    can be written either way."""

    source_file: str
    source_format: Format
    redaction_counts: dict[str, int]
    raw_text: str
    redacted_text: str
    order: PurchaseOrder | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.order is not None and self.error is None


def process_file(path: Path, *, db_path: Path = storage.DEFAULT_DB_PATH) -> IngestResult:
    """Run one file end-to-end and persist results.

    Returns an IngestResult either way; check `.accepted` to distinguish.
    """
    fmt: Format = detect_format(path)
    raw_text = _EXTRACTORS[fmt](path)
    scrubbed: ScrubResult = scrub(raw_text)

    error: str | None = None
    order: PurchaseOrder | None = None
    try:
        order = llm.extract_order(
            source_format=fmt,
            source_file=path.name,
            redacted_text=scrubbed.text,
        )
    except ValidationError as exc:
        error = f"validation: {exc.errors(include_url=False)}"
    except Exception as exc:  # noqa: BLE001 - audit log captures any failure mode
        error = f"{type(exc).__name__}: {exc}"

    storage.init_db(db_path)
    with storage.connect(db_path) as conn:
        order_id: int | None = None
        if order is not None:
            order_id = storage.insert_order(conn, order)
        storage.append_audit(
            conn,
            source_file=path.name,
            source_format=fmt,
            redaction_counts=scrubbed.redaction_counts,
            order_id=order_id,
            confidence=order.confidence if order else None,
            flagged_fields=order.flagged_fields if order else [],
            status="accepted" if order is not None else "rejected",
            reason=error,
        )

    return IngestResult(
        source_file=path.name,
        source_format=fmt,
        redaction_counts=scrubbed.redaction_counts,
        raw_text=raw_text,
        redacted_text=scrubbed.text,
        order=order,
        error=error,
    )
