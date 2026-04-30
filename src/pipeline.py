"""Orchestration: detect → extract → scrub → LLM → validate → store.

This is the file that proves the architecture. Every input format takes the
same path through the same five stages; the only stage that does anything
input-specific is the extractor (and even that just produces N strings, where
N=1 for PDF and CSV and N=lines for SMS).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from . import extractors, llm, storage
from .detector import Format, detect_format
from .schema import PurchaseOrder
from .scrubber import ScrubResult, scrub

logger = logging.getLogger("rx_doc_processor.pipeline")


_EXTRACTORS = {
    "pdf": extractors.pdf_extract,
    "csv": extractors.csv_extract,
    "sms": extractors.sms_extract,
}


@dataclass
class IngestResult:
    """One extraction unit's result. A single SMS log produces multiple of
    these (one per message); a PDF or CSV produces exactly one."""

    source_file: str
    source_format: Format
    unit_index: int                     # 0 for PDF/CSV; 0..N-1 for SMS lines
    redaction_counts: dict[str, int]
    raw_text: str
    redacted_text: str
    order: PurchaseOrder | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.order is not None and self.error is None


def process_file(
    path: Path,
    *,
    db_path: Path = storage.DEFAULT_DB_PATH,
) -> list[IngestResult]:
    """Run one file end-to-end. Returns one IngestResult per extraction unit.

    For PDF/CSV that's a single-element list; for SMS one entry per message.
    Each unit gets its own audit-log row so the trail is granular even when a
    single source file produces many orders.
    """
    fmt: Format = detect_format(path)
    units: list[str] = _EXTRACTORS[fmt](path)
    logger.info(
        "pipeline start file=%s format=%s units=%d",
        path.name, fmt, len(units),
    )

    storage.init_db(db_path)
    results: list[IngestResult] = []

    with storage.connect(db_path) as conn:
        for idx, raw_text in enumerate(units):
            t_start = time.perf_counter()
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

            elapsed_ms = (time.perf_counter() - t_start) * 1000
            logger.info(
                "pipeline unit  file=%s idx=%d status=%s elapsed_ms=%d redactions=%s",
                path.name,
                idx,
                "accepted" if order is not None else "rejected",
                int(elapsed_ms),
                scrubbed.redaction_counts or {},
            )

            results.append(
                IngestResult(
                    source_file=path.name,
                    source_format=fmt,
                    unit_index=idx,
                    redaction_counts=scrubbed.redaction_counts,
                    raw_text=raw_text,
                    redacted_text=scrubbed.text,
                    order=order,
                    error=error,
                )
            )

    return results
