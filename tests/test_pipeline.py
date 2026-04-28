"""Pipeline orchestration tests with the LLM stubbed.

These exercise the accepted/rejected branches without touching Anthropic, so
they run on CI with no API key.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from src import pipeline, storage
from src.schema import LineItem, PurchaseOrder


def _stub_extract(*, source_format, source_file, redacted_text):
    """Return a fixed PurchaseOrder regardless of input."""
    from datetime import datetime, timezone

    return PurchaseOrder(
        source_format=source_format,
        source_file=source_file,
        received_at=datetime.now(timezone.utc),
        buyer_org_name="Stubbed Buyer",
        buyer_account_ref="STUB",
        line_items=[LineItem(drug_name="Stub Drug", quantity=1)],
        raw_excerpt_redacted=redacted_text[:200],
        confidence=0.9,
    )


def _raising_extract(*, source_format, source_file, redacted_text):
    raise RuntimeError("simulated llm failure")


def _validation_error_extract(*, source_format, source_file, redacted_text):
    # Minimal way to construct a ValidationError
    PurchaseOrder.model_validate({})


class TestProcessFile:
    def test_sms_produces_one_result_per_line(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _stub_extract)
        sms = tmp_path / "log.txt"
        sms.write_text("first message\nsecond message\nthird message\n")
        results = pipeline.process_file(sms, db_path=tmp_path / "test.db")
        assert len(results) == 3
        assert all(r.accepted for r in results)
        assert results[0].unit_index == 0
        assert results[2].unit_index == 2

    def test_pdf_produces_one_result(
        self, sample_pdf: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _stub_extract)
        results = pipeline.process_file(sample_pdf, db_path=tmp_path / "test.db")
        assert len(results) == 1
        assert results[0].source_format == "pdf"
        assert results[0].accepted

    def test_csv_produces_one_result(
        self, sample_csv: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _stub_extract)
        results = pipeline.process_file(sample_csv, db_path=tmp_path / "test.db")
        assert len(results) == 1
        assert results[0].source_format == "csv"

    def test_writes_orders_and_audit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _stub_extract)
        sms = tmp_path / "log.txt"
        sms.write_text("hello world\n")
        db = tmp_path / "test.db"
        pipeline.process_file(sms, db_path=db)

        with sqlite3.connect(db) as conn:
            orders = conn.execute("select count(*) from orders").fetchone()[0]
            audit = conn.execute("select count(*) from audit_log").fetchone()[0]
            line_items = conn.execute("select count(*) from line_items").fetchone()[0]

        assert orders == 1
        assert line_items == 1
        assert audit == 1

    def test_llm_failure_writes_rejected_audit_row(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _raising_extract)
        sms = tmp_path / "log.txt"
        sms.write_text("doomed message\n")
        db = tmp_path / "test.db"
        results = pipeline.process_file(sms, db_path=db)

        assert len(results) == 1
        assert not results[0].accepted
        assert "RuntimeError" in (results[0].error or "")

        with sqlite3.connect(db) as conn:
            orders = conn.execute("select count(*) from orders").fetchone()[0]
            audit = conn.execute(
                "select status, reason from audit_log"
            ).fetchall()
        assert orders == 0
        assert audit[0][0] == "rejected"
        assert "simulated llm failure" in audit[0][1]

    def test_validation_error_writes_rejected_audit_row(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            pipeline.llm, "extract_order", _validation_error_extract
        )
        sms = tmp_path / "log.txt"
        sms.write_text("hello\n")
        db = tmp_path / "test.db"
        results = pipeline.process_file(sms, db_path=db)
        assert not results[0].accepted
        assert "validation" in (results[0].error or "").lower()

    def test_redaction_counts_propagate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(pipeline.llm, "extract_order", _stub_extract)
        sms = tmp_path / "log.txt"
        sms.write_text("acct 12345 dea AB1234567 patient John Doe\n")
        results = pipeline.process_file(sms, db_path=tmp_path / "test.db")
        counts = results[0].redaction_counts
        assert counts.get("ACCOUNT") == 1
        assert counts.get("DEA") == 1
        assert counts.get("PHI") == 1
