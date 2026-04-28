"""Outbound artifact generators for the canonical record and audit log.

The pipeline normalizes inbound documents into a canonical PurchaseOrder.
This module turns those records into the artifacts a downstream system or a
human consumer would actually receive:

* `order_to_json_bytes`         — machine-readable canonical record. Shape
                                  matches the marketplace order-management API.
* `order_to_confirmation_pdf`   — buyer-facing acknowledgement PDF. What an
                                  ops team would email back to the buyer.
* `audit_log_to_csv_bytes`      — tabular export of audit_log rows for
                                  compliance reviewers running offline reports.

All functions are pure: input → bytes, no I/O beyond the in-memory buffer.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .schema import PurchaseOrder


# Brand palette (sampled from graphiterx.com)
NAVY = colors.HexColor("#18193F")
TEAL = colors.HexColor("#00A8B3")
TINT = colors.HexColor("#D2E5E5")
SLATE = colors.HexColor("#2F2E41")
INK = colors.HexColor("#495051")
LIGHT_BG = colors.HexColor("#F2F8F8")


def order_to_json_bytes(order: PurchaseOrder) -> bytes:
    """Canonical JSON, indented for human readability.

    This is the shape the marketplace order-management API consumes:
    a single PurchaseOrder with embedded LineItem entries.
    """
    return json.dumps(order.model_dump(mode="json"), indent=2).encode("utf-8")


def order_to_confirmation_pdf(order: PurchaseOrder) -> bytes:
    """Generate a buyer-facing 'Order Received' acknowledgement PDF.

    The artifact an ops team would attach to a confirmation email after the
    pipeline accepts an inbound order. Brand-aligned to graphiteRx (navy +
    teal + soft tint), single page for typical line-item counts.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Order confirmation · {order.po_reference or order.source_file}",
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "title",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=NAVY,
        leading=26,
        spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "subtitle",
        parent=styles["BodyText"],
        fontSize=10,
        textColor=TEAL,
        leading=14,
        spaceAfter=14,
        fontName="Helvetica-Bold",
    )
    label = ParagraphStyle(
        "label",
        parent=styles["BodyText"],
        fontSize=8,
        textColor=INK,
        leading=10,
        fontName="Helvetica",
        spaceAfter=2,
    )
    value = ParagraphStyle(
        "value",
        parent=styles["BodyText"],
        fontSize=11,
        textColor=NAVY,
        leading=14,
        fontName="Helvetica-Bold",
        spaceAfter=10,
    )
    body = ParagraphStyle(
        "body",
        parent=styles["BodyText"],
        fontSize=9,
        textColor=INK,
        leading=12,
    )

    story = []
    story.append(Paragraph("ORDER RECEIVED", title))
    story.append(
        Paragraph(
            f"Acknowledged at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            subtitle,
        )
    )

    # Header summary table — buyer + PO reference + ship date
    summary_rows = [
        [
            Paragraph("BUYER", label),
            Paragraph("PO REFERENCE", label),
            Paragraph("REQUESTED SHIP DATE", label),
        ],
        [
            Paragraph(order.buyer_org_name or "&mdash;", value),
            Paragraph(order.po_reference or "&mdash;", value),
            Paragraph(
                order.requested_ship_date.isoformat()
                if order.requested_ship_date
                else "&mdash;",
                value,
            ),
        ],
    ]
    summary = Table(summary_rows, colWidths=[2.6 * inch, 2.4 * inch, 2.3 * inch])
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BG),
                ("BOX", (0, 0), (-1, -1), 0, colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(summary)
    story.append(Spacer(1, 18))

    # Line items table
    item_header = ["#", "Drug", "NDC", "Strength", "Pack", "Qty", "Unit"]
    rows = [item_header]
    for i, li in enumerate(order.line_items, 1):
        rows.append(
            [
                str(i),
                li.drug_name or "—",
                li.ndc or "—",
                li.strength or "—",
                li.package_size or "—",
                str(li.quantity),
                li.unit_of_measure or "—",
            ]
        )

    items_table = Table(
        rows,
        colWidths=[
            0.3 * inch,
            2.2 * inch,
            1.1 * inch,
            1.0 * inch,
            1.2 * inch,
            0.5 * inch,
            0.6 * inch,
        ],
        repeatRows=1,
    )
    items_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("ALIGN", (5, 0), (5, -1), "RIGHT"),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("TEXTCOLOR", (0, 1), (-1, -1), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), 1, TEAL),
            ]
        )
    )
    story.append(items_table)
    story.append(Spacer(1, 18))

    # Footer with confidence + scope statement
    badge = (
        "AUTO-ROUTE TO MARKETPLACE"
        if order.confidence >= 0.7
        else "ROUTED TO REVIEW QUEUE"
    )
    footer_lines = [
        f"<b>Acknowledgement status:</b> {badge} (confidence {order.confidence:.2f})",
    ]
    if order.flagged_fields:
        footer_lines.append(
            "<b>Reviewer attention requested on:</b> "
            + ", ".join(f"<font color='#9C2424'>{f}</font>" for f in order.flagged_fields)
        )
    footer_lines.append(
        "<b>Sensitive identifiers</b> (DEA, account, phone, email, PHI) were "
        "redacted before extraction; no unredacted values are stored or "
        "transmitted with this acknowledgement."
    )
    footer_lines.append(
        "<b>Source:</b> "
        f"{order.source_format.upper()} &middot; {order.source_file}"
    )

    for line in footer_lines:
        story.append(Paragraph(line, body))
        story.append(Spacer(1, 4))

    doc.build(story)
    return buf.getvalue()


def audit_log_to_csv_bytes(rows: list[dict]) -> bytes:
    """Flatten audit_log rows into CSV for compliance review.

    The dict-typed columns (`redaction_count_by_type`, `flagged_fields`)
    are rendered as compact strings so the CSV opens cleanly in Excel
    without escaping headaches.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
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
    )
    for row in rows:
        redactions = row.get("redaction_count_by_type") or {}
        sig = " · ".join(f"{k}:{v}" for k, v in redactions.items()) if redactions else ""
        flagged = row.get("flagged_fields") or []
        writer.writerow(
            [
                row["id"],
                row["timestamp"],
                row["source_file"],
                row["source_format"],
                row["status"],
                row.get("llm_confidence"),
                row.get("order_id"),
                sig,
                ", ".join(flagged),
            ]
        )
    return buf.getvalue().encode("utf-8")


def safe_filename(stem: str, suffix: str) -> str:
    """Slug a buyer org or filename for use in a download filename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-").lower()
    return f"{cleaned or 'order'}.{suffix.lstrip('.')}"
