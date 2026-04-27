"""CLI entrypoint.

Examples:

    uv run python ingest.py samples/sms_orders.txt
    uv run python ingest.py samples/*.pdf samples/*.csv samples/*.txt
    uv run python ingest.py --show-audit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src import storage
from src.pipeline import process_file


def _print_result(path: Path, result) -> None:
    if result.accepted:
        print(f"\n=== {path.name}  ({result.source_format})  ✓ ACCEPTED ===")
        print(f"redactions: {result.redaction_counts}")
        order = result.order
        print(f"confidence: {order.confidence:.2f}")
        if order.flagged_fields:
            print(f"flagged: {order.flagged_fields}")
        print(json.dumps(order.model_dump(mode="json"), indent=2))
    else:
        print(f"\n=== {path.name}  ({result.source_format})  ✗ REJECTED ===")
        print(f"error: {result.error}")
        print(f"redactions: {result.redaction_counts}")


def _show_audit() -> None:
    storage.init_db()
    with storage.connect() as conn:
        rows = storage.fetch_audit(conn)
    if not rows:
        print("audit log is empty")
        return
    for row in rows:
        print(json.dumps(row, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest PDF / CSV / SMS files through the LangChain pipeline."
    )
    parser.add_argument("paths", nargs="*", type=Path, help="Files to ingest.")
    parser.add_argument(
        "--show-audit",
        action="store_true",
        help="Dump the audit log instead of ingesting.",
    )
    args = parser.parse_args(argv)

    if args.show_audit:
        _show_audit()
        return 0

    if not args.paths:
        parser.error("provide at least one file or pass --show-audit")

    accepted = 0
    rejected = 0
    for path in args.paths:
        if not path.exists():
            print(f"skip: {path} not found", file=sys.stderr)
            rejected += 1
            continue
        result = process_file(path)
        _print_result(path, result)
        if result.accepted:
            accepted += 1
        else:
            rejected += 1

    print(f"\nsummary: {accepted} accepted, {rejected} rejected")
    return 0 if rejected == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
