"""Generate samples/purchase_order.pdf — a fake faxed PO for the demo.

Run once:  uv run python scripts/make_pdf.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


OUT = Path(__file__).resolve().parents[1] / "samples" / "purchase_order.pdf"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Heading1"], alignment=1)
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)

    story = [
        Paragraph("PURCHASE ORDER — FAX TRANSMITTAL", title),
        Spacer(1, 6),
        Paragraph("To: Cardinal Sterile Compounding Solutions (503B)", body),
        Paragraph("From: St. Mary's Hospital Pharmacy", body),
        Paragraph("Phone: 615-555-0142    Fax: 615-555-0143", body),
        Paragraph("DEA #: AB1234567    Account #: SM-44012", body),
        Paragraph("PO #: SM-2026-0418-A    Requested ship date: 2026-04-30", body),
        Spacer(1, 12),
    ]

    line_items = [
        ["Item", "NDC", "Strength", "Pack", "Qty"],
        [
            "Vancomycin (sterile compounded)",
            "00074-6533-12",
            "1 g/vial",
            "10 vial case",
            "8",
        ],
        [
            "Norepinephrine bitartrate",
            "00641-6128-25",
            "4 mg / 4 mL",
            "10 vial case",
            "12",
        ],
        [
            "Heparin sodium PF (preservative-free)",
            "00641-2440-45",
            "5,000 U / 5 mL",
            "25 vial case",
            "4",
        ],
        ["Sodium bicarbonate inj.", "00074-1551-01", "8.4% 50mL", "10 vial case", "6"],
    ]

    table = Table(line_items, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(table)

    story.append(Spacer(1, 18))
    story.append(
        Paragraph(
            "<b>Notes:</b> Rush order — patient John Doe needs vancomycin "
            "starting Friday. Confirm receipt to pharmacy@stmarys.example "
            "or 615-555-0142.",
            body,
        )
    )
    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "Authorized signatory: Dr. Helen Park, PharmD &nbsp;&nbsp;&nbsp;&nbsp; "
            "Date: 2026-04-18",
            small,
        )
    )

    doc.build(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
