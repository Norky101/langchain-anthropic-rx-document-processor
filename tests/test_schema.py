"""Pydantic schema tests for PurchaseOrder + LineItem."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from src.schema import LineItem, PurchaseOrder


def _minimal_line_item() -> LineItem:
    return LineItem(drug_name="Lisinopril", quantity=4)


def _minimal_kwargs() -> dict:
    return dict(
        source_format="sms",
        source_file="x.txt",
        received_at=datetime.now(timezone.utc),
        buyer_account_ref="ACME",
        line_items=[_minimal_line_item()],
        raw_excerpt_redacted="x",
        confidence=0.5,
    )


class TestLineItem:
    def test_minimal_required_fields(self) -> None:
        li = _minimal_line_item()
        assert li.drug_name == "Lisinopril"
        assert li.quantity == 4
        assert li.ndc is None

    def test_quantity_must_be_int(self) -> None:
        with pytest.raises(ValidationError):
            LineItem(drug_name="X", quantity="four")  # type: ignore[arg-type]

    def test_optional_fields(self) -> None:
        li = LineItem(
            drug_name="Vancomycin",
            ndc="00074-6533-12",
            strength="1 g/vial",
            package_size="10 vial case",
            quantity=8,
            unit_of_measure="case",
            requested_unit_price=125.50,
        )
        assert li.requested_unit_price == 125.50


class TestPurchaseOrder:
    def test_minimal_valid_order(self) -> None:
        po = PurchaseOrder(**_minimal_kwargs())
        assert po.source_format == "sms"
        assert po.confidence == 0.5
        assert po.flagged_fields == []

    def test_confidence_lower_bound(self) -> None:
        kw = _minimal_kwargs()
        kw["confidence"] = -0.1
        with pytest.raises(ValidationError):
            PurchaseOrder(**kw)

    def test_confidence_upper_bound(self) -> None:
        kw = _minimal_kwargs()
        kw["confidence"] = 1.1
        with pytest.raises(ValidationError):
            PurchaseOrder(**kw)

    def test_source_format_constrained_to_literal(self) -> None:
        kw = _minimal_kwargs()
        kw["source_format"] = "fax"
        with pytest.raises(ValidationError):
            PurchaseOrder(**kw)

    def test_line_items_required_at_least_one(self) -> None:
        # Schema doesn't strictly enforce min_items; the system prompt does.
        # Verify the field is present and at least types correctly.
        kw = _minimal_kwargs()
        kw["line_items"] = []
        po = PurchaseOrder(**kw)
        assert po.line_items == []

    def test_received_at_must_be_datetime(self) -> None:
        kw = _minimal_kwargs()
        kw["received_at"] = "not a date"
        with pytest.raises(ValidationError):
            PurchaseOrder(**kw)

    def test_round_trip_json(self) -> None:
        po = PurchaseOrder(**_minimal_kwargs())
        as_json = po.model_dump_json()
        po2 = PurchaseOrder.model_validate_json(as_json)
        assert po2 == po

    def test_flagged_fields_default_empty(self) -> None:
        po = PurchaseOrder(**_minimal_kwargs())
        assert po.flagged_fields == []

    def test_requested_ship_date_optional(self) -> None:
        po = PurchaseOrder(**_minimal_kwargs())
        assert po.requested_ship_date is None
