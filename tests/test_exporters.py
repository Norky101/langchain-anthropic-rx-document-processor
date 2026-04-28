"""Exporter tests — JSON, PDF, and CSV artifacts."""

from __future__ import annotations

import csv
import io
import json

import pytest

from src import exporters
from src.schema import LineItem, PurchaseOrder


# ─── JSON ────────────────────────────────────────────────────────────────────
class TestOrderToJson:
    def test_returns_bytes(self, make_order) -> None:
        order = make_order()
        out = exporters.order_to_json_bytes(order)
        assert isinstance(out, bytes)

    def test_round_trips(self, make_order) -> None:
        order = make_order()
        out = exporters.order_to_json_bytes(order)
        parsed = json.loads(out)
        assert parsed["source_format"] == order.source_format
        assert parsed["confidence"] == order.confidence
        assert len(parsed["line_items"]) == len(order.line_items)

    def test_indented(self, make_order) -> None:
        out = exporters.order_to_json_bytes(make_order())
        # Indented output has newlines
        assert b"\n" in out


# ─── PDF ─────────────────────────────────────────────────────────────────────
class TestOrderToPdf:
    def test_returns_pdf_magic(self, make_order) -> None:
        out = exporters.order_to_confirmation_pdf(make_order())
        assert out.startswith(b"%PDF-")

    def test_non_empty(self, make_order) -> None:
        out = exporters.order_to_confirmation_pdf(make_order())
        # A reasonable receipt with one line item is at least 1KB
        assert len(out) > 1000

    def test_handles_zero_confidence(self, make_order) -> None:
        order = make_order(confidence=0.0, flagged=["line_items[0].quantity"])
        out = exporters.order_to_confirmation_pdf(order)
        assert out.startswith(b"%PDF-")

    def test_handles_high_confidence(self, make_order) -> None:
        order = make_order(confidence=1.0)
        out = exporters.order_to_confirmation_pdf(order)
        assert out.startswith(b"%PDF-")

    def test_handles_many_line_items(self, make_order) -> None:
        items = [
            LineItem(drug_name=f"Drug {i}", quantity=i + 1)
            for i in range(15)
        ]
        order = make_order(line_items=items)
        out = exporters.order_to_confirmation_pdf(order)
        assert out.startswith(b"%PDF-")

    def test_handles_null_optional_fields(self, make_order) -> None:
        # Minimal line items — no NDC, no strength, no pack
        item = LineItem(drug_name="Mystery", quantity=1)
        order = make_order(line_items=[item])
        out = exporters.order_to_confirmation_pdf(order)
        assert out.startswith(b"%PDF-")


# ─── Audit CSV ───────────────────────────────────────────────────────────────
class TestAuditLogToCsv:
    def _row(self, **overrides) -> dict:
        base = dict(
            id=1,
            timestamp="2026-04-28T01:00:00",
            source_file="x.txt",
            source_format="sms",
            order_id=1,
            redaction_count_by_type={"DEA": 1, "PHI": 1},
            llm_confidence=0.9,
            flagged_fields=[],
            status="accepted",
            reason=None,
        )
        base.update(overrides)
        return base

    def test_header_columns(self) -> None:
        out = exporters.audit_log_to_csv_bytes([self._row()])
        reader = csv.reader(io.StringIO(out.decode()))
        header = next(reader)
        assert header == [
            "id",
            "timestamp",
            "source_file",
            "source_format",
            "status",
            "llm_confidence",
            "order_id",
            "redaction_signature",
            "flagged_fields",
        ]

    def test_no_reason_column(self) -> None:
        out = exporters.audit_log_to_csv_bytes([self._row()])
        assert "reason" not in out.decode().splitlines()[0]

    def test_redaction_signature_renders(self) -> None:
        out = exporters.audit_log_to_csv_bytes(
            [self._row(redaction_count_by_type={"DEA": 1, "PHI": 2})]
        )
        body = out.decode()
        assert "DEA:1" in body
        assert "PHI:2" in body

    def test_empty_redactions(self) -> None:
        out = exporters.audit_log_to_csv_bytes(
            [self._row(redaction_count_by_type={})]
        )
        # Should not crash, signature is empty string
        reader = csv.DictReader(io.StringIO(out.decode()))
        rows = list(reader)
        assert rows[0]["redaction_signature"] == ""

    def test_multiple_rows(self) -> None:
        rows = [self._row(id=i) for i in range(1, 6)]
        out = exporters.audit_log_to_csv_bytes(rows)
        reader = csv.DictReader(io.StringIO(out.decode()))
        materialized = list(reader)
        assert len(materialized) == 5
        assert materialized[0]["id"] == "1"
        assert materialized[4]["id"] == "5"

    def test_rejected_row_has_no_order_id(self) -> None:
        out = exporters.audit_log_to_csv_bytes(
            [self._row(status="rejected", order_id=None, llm_confidence=None)]
        )
        reader = csv.DictReader(io.StringIO(out.decode()))
        row = next(reader)
        assert row["status"] == "rejected"
        assert row["order_id"] == ""
        assert row["llm_confidence"] == ""


# ─── Filename helper ─────────────────────────────────────────────────────────
class TestSafeFilename:
    def test_simple(self) -> None:
        assert exporters.safe_filename("Maple Pharmacy", "pdf") == "maple-pharmacy.pdf"

    def test_strips_punctuation(self) -> None:
        assert exporters.safe_filename("St. Mary's Hospital", "pdf") == (
            "st.-mary-s-hospital.pdf"
        )

    def test_handles_empty(self) -> None:
        # Empty input falls back to 'order'
        assert exporters.safe_filename("", "json") == "order.json"

    def test_handles_only_punctuation(self) -> None:
        assert exporters.safe_filename("!!!", "json") == "order.json"

    def test_normalizes_suffix_dot(self) -> None:
        assert exporters.safe_filename("x", ".pdf") == "x.pdf"
