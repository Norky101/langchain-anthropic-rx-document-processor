"""Storage tests — schema initialization, insert, audit, fetch."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from src import storage


class TestInitDb:
    def test_creates_all_three_tables(self, tmp_db: Path) -> None:
        storage.init_db(tmp_db)
        with sqlite3.connect(tmp_db) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "select name from sqlite_master where type='table'"
                )
            }
        assert "orders" in tables
        assert "line_items" in tables
        assert "audit_log" in tables

    def test_idempotent(self, tmp_db: Path) -> None:
        storage.init_db(tmp_db)
        storage.init_db(tmp_db)  # second call should not raise


class TestInsertOrder:
    def test_inserts_order_and_line_items(self, tmp_db: Path, make_order) -> None:
        storage.init_db(tmp_db)
        order = make_order()
        with storage.connect(tmp_db) as conn:
            order_id = storage.insert_order(conn, order)
        assert order_id is not None and order_id > 0

        with sqlite3.connect(tmp_db) as conn:
            rows = conn.execute(
                "select buyer_org_name, confidence from orders where id=?",
                (order_id,),
            ).fetchall()
            assert rows[0][0] == "Test Pharmacy"
            assert rows[0][1] == 0.9

            li_count = conn.execute(
                "select count(*) from line_items where order_id=?", (order_id,)
            ).fetchone()[0]
            assert li_count == len(order.line_items)

    def test_flagged_fields_serialized_as_json(self, tmp_db: Path, make_order) -> None:
        storage.init_db(tmp_db)
        order = make_order(flagged=["line_items[0].ndc", "buyer_dea_redacted"])
        with storage.connect(tmp_db) as conn:
            order_id = storage.insert_order(conn, order)

        with sqlite3.connect(tmp_db) as conn:
            ff_text = conn.execute(
                "select flagged_fields from orders where id=?", (order_id,)
            ).fetchone()[0]
        ff = json.loads(ff_text)
        assert ff == ["line_items[0].ndc", "buyer_dea_redacted"]


class TestAudit:
    def test_appends_accepted_row(self, tmp_db: Path, make_order) -> None:
        storage.init_db(tmp_db)
        order = make_order()
        with storage.connect(tmp_db) as conn:
            order_id = storage.insert_order(conn, order)
            storage.append_audit(
                conn,
                source_file="x.txt",
                source_format="sms",
                redaction_counts={"DEA": 1, "PHI": 1},
                order_id=order_id,
                confidence=0.9,
                flagged_fields=[],
                status="accepted",
            )
            rows = storage.fetch_audit(conn)
        assert len(rows) == 1
        assert rows[0]["status"] == "accepted"
        assert rows[0]["llm_confidence"] == 0.9
        assert rows[0]["redaction_count_by_type"] == {"DEA": 1, "PHI": 1}
        assert rows[0]["flagged_fields"] == []

    def test_appends_rejected_row(self, tmp_db: Path) -> None:
        storage.init_db(tmp_db)
        with storage.connect(tmp_db) as conn:
            storage.append_audit(
                conn,
                source_file="bad.csv",
                source_format="csv",
                redaction_counts={},
                order_id=None,
                confidence=None,
                flagged_fields=[],
                status="rejected",
                reason="ValidationError: missing field 'drug_name'",
            )
            rows = storage.fetch_audit(conn)
        assert rows[0]["status"] == "rejected"
        assert rows[0]["order_id"] is None
        assert rows[0]["llm_confidence"] is None
        assert rows[0]["reason"].startswith("ValidationError")

    def test_fetch_audit_orders_descending(self, tmp_db: Path) -> None:
        storage.init_db(tmp_db)
        with storage.connect(tmp_db) as conn:
            for i in range(3):
                storage.append_audit(
                    conn,
                    source_file=f"f{i}.txt",
                    source_format="sms",
                    redaction_counts={},
                    order_id=None,
                    confidence=None,
                    flagged_fields=[],
                    status="accepted",
                )
            rows = storage.fetch_audit(conn)
        # Newest first
        assert rows[0]["source_file"] == "f2.txt"
        assert rows[2]["source_file"] == "f0.txt"
