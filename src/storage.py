"""SQLite persistence + 340B-grade audit log.

Three tables:

* `orders`        — one row per accepted PurchaseOrder.
* `line_items`    — one row per LineItem, foreign-keyed to orders.id.
* `audit_log`     — one row per ingestion attempt (success or rejection),
                    capturing source file, format, redaction counts by type,
                    LLM confidence, and any flagged fields.

The audit log is the compliance hook. It's append-only; in production it would
land in a tamper-evident store, but SQLite is fine for the demo.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .schema import PurchaseOrder


DEFAULT_DB_PATH = Path("store.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_format TEXT NOT NULL,
    source_file TEXT NOT NULL,
    received_at TEXT NOT NULL,
    buyer_org_name TEXT,
    buyer_account_ref TEXT NOT NULL,
    buyer_dea_redacted TEXT,
    po_reference TEXT,
    requested_ship_date TEXT,
    raw_excerpt_redacted TEXT NOT NULL,
    confidence REAL NOT NULL,
    flagged_fields TEXT NOT NULL,
    inserted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    drug_name TEXT NOT NULL,
    ndc TEXT,
    strength TEXT,
    package_size TEXT,
    quantity INTEGER NOT NULL,
    unit_of_measure TEXT,
    requested_unit_price REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    source_file TEXT NOT NULL,
    source_format TEXT NOT NULL,
    order_id INTEGER REFERENCES orders(id),
    redaction_count_by_type TEXT NOT NULL,
    llm_confidence REAL,
    flagged_fields TEXT NOT NULL,
    status TEXT NOT NULL,                 -- 'accepted' or 'rejected'
    reason TEXT
);
"""


@contextmanager
def connect(db_path: Path = DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def insert_order(conn: sqlite3.Connection, order: PurchaseOrder) -> int:
    cur = conn.execute(
        """
        INSERT INTO orders (
            source_format, source_file, received_at, buyer_org_name,
            buyer_account_ref, buyer_dea_redacted, po_reference,
            requested_ship_date, raw_excerpt_redacted, confidence, flagged_fields
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order.source_format,
            order.source_file,
            order.received_at.isoformat(),
            order.buyer_org_name,
            order.buyer_account_ref,
            order.buyer_dea_redacted,
            order.po_reference,
            order.requested_ship_date.isoformat() if order.requested_ship_date else None,
            order.raw_excerpt_redacted,
            order.confidence,
            json.dumps(order.flagged_fields),
        ),
    )
    order_id = cur.lastrowid
    assert order_id is not None
    for item in order.line_items:
        conn.execute(
            """
            INSERT INTO line_items (
                order_id, drug_name, ndc, strength, package_size,
                quantity, unit_of_measure, requested_unit_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                item.drug_name,
                item.ndc,
                item.strength,
                item.package_size,
                item.quantity,
                item.unit_of_measure,
                item.requested_unit_price,
            ),
        )
    return order_id


def append_audit(
    conn: sqlite3.Connection,
    *,
    source_file: str,
    source_format: str,
    redaction_counts: dict[str, int],
    order_id: int | None,
    confidence: float | None,
    flagged_fields: list[str],
    status: str,
    reason: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (
            source_file, source_format, order_id, redaction_count_by_type,
            llm_confidence, flagged_fields, status, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_file,
            source_format,
            order_id,
            json.dumps(redaction_counts),
            confidence,
            json.dumps(flagged_fields),
            status,
            reason,
        ),
    )


def fetch_audit(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        """
        SELECT id, timestamp, source_file, source_format, order_id,
               redaction_count_by_type, llm_confidence, flagged_fields,
               status, reason
        FROM audit_log
        ORDER BY id DESC
        """
    )
    rows: list[dict] = []
    for row in cur.fetchall():
        rows.append(
            {
                "id": row[0],
                "timestamp": row[1],
                "source_file": row[2],
                "source_format": row[3],
                "order_id": row[4],
                "redaction_count_by_type": json.loads(row[5]),
                "llm_confidence": row[6],
                "flagged_fields": json.loads(row[7]),
                "status": row[8],
                "reason": row[9],
            }
        )
    return rows
