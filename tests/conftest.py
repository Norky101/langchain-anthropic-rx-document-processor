"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.schema import LineItem, PurchaseOrder


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def samples_dir(repo_root: Path) -> Path:
    return repo_root / "samples"


@pytest.fixture
def sample_pdf(samples_dir: Path) -> Path:
    return samples_dir / "purchase_order.pdf"


@pytest.fixture
def sample_csv(samples_dir: Path) -> Path:
    return samples_dir / "reorder_list.csv"


@pytest.fixture
def sample_sms(samples_dir: Path) -> Path:
    return samples_dir / "sms_orders.txt"


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def make_order():
    """Factory: produce a fully-populated PurchaseOrder for tests."""

    def _make(
        *,
        confidence: float = 0.9,
        flagged: list[str] | None = None,
        format_: str = "sms",
        line_items: list[LineItem] | None = None,
    ) -> PurchaseOrder:
        return PurchaseOrder(
            source_format=format_,
            source_file="test.txt",
            received_at=datetime.now(timezone.utc),
            buyer_org_name="Test Pharmacy",
            buyer_account_ref="[REDACTED_ACCOUNT]",
            buyer_dea_redacted=None,
            po_reference="TST-1",
            requested_ship_date=date(2026, 5, 1),
            line_items=line_items
            or [
                LineItem(
                    drug_name="Lisinopril",
                    strength="20 mg",
                    package_size="90 ct bottle",
                    quantity=4,
                    unit_of_measure="bottle",
                )
            ],
            raw_excerpt_redacted="redacted excerpt",
            confidence=confidence,
            flagged_fields=flagged or [],
        )

    return _make
