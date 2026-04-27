"""Canonical schema every input format normalizes into.

The LLM stage's job is to take heterogeneous (and scrubbed) text or rows and
populate a `PurchaseOrder`. Validation gating happens via Pydantic; if the LLM
returns something that doesn't satisfy the schema, the pipeline rejects the
record and routes it to human review via `flagged_fields`.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """One ordered SKU."""

    drug_name: str = Field(description="Brand or generic name of the drug.")
    ndc: str | None = Field(
        default=None,
        description="11-digit National Drug Code if present in the source. Leave null otherwise.",
    )
    strength: str | None = Field(
        default=None,
        description="Dosage strength such as '500mg' or '40 mg/mL'.",
    )
    package_size: str | None = Field(
        default=None,
        description="Container/pack size: '100 ct bottle', '10 vial case', etc.",
    )
    quantity: int = Field(description="Number of packages requested.")
    unit_of_measure: str | None = Field(
        default=None,
        description="Unit for `quantity`: 'bottle', 'vial', 'case', 'box', etc.",
    )
    requested_unit_price: float | None = Field(
        default=None,
        description="Price per unit if buyer specified one; null otherwise.",
    )


class PurchaseOrder(BaseModel):
    """One inbound order, regardless of input channel.

    The same schema is populated whether the source was a faxed PDF, an emailed
    CSV row-set, or an SMS. New input formats join the pipeline by routing into
    the same LangChain extraction stage; no new schema is needed.
    """

    source_format: Literal["pdf", "csv", "sms"] = Field(
        description="Channel the order arrived through."
    )
    source_file: str = Field(description="Path or filename of the source artifact.")
    received_at: datetime = Field(
        description="Server-side timestamp at ingestion (ISO 8601)."
    )
    buyer_org_name: str | None = Field(
        default=None,
        description="Buyer organization (e.g. 'St Mary's Hospital Pharmacy'). Null if unknown.",
    )
    buyer_account_ref: str = Field(
        description=(
            "Buyer account identifier. If present in the source it will already "
            "have been replaced with [REDACTED_ACCOUNT] before this stage; in "
            "that case use the placeholder verbatim."
        ),
    )
    buyer_dea_redacted: str | None = Field(
        default=None,
        description=(
            "Set to '[REDACTED_DEA]' if the source contained a DEA number "
            "(which the scrubber will have replaced). Null if no DEA was present."
        ),
    )
    po_reference: str | None = Field(
        default=None,
        description="Buyer's PO number if they provided one.",
    )
    requested_ship_date: date | None = Field(
        default=None,
        description="Date the buyer wants the order shipped. Null if not specified.",
    )
    line_items: list[LineItem] = Field(
        description="One entry per ordered SKU. Must contain at least one item."
    )
    raw_excerpt_redacted: str = Field(
        description=(
            "Up to 500 chars of the (already scrubbed) source text, for audit. "
            "Use whatever portion best supports human review."
        ),
    )
    confidence: float = Field(
        ge=0,
        le=1,
        description=(
            "Self-rated confidence in the overall extraction, 0-1. Below 0.5 "
            "should route to a human review queue rather than the marketplace."
        ),
    )
    flagged_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Field paths the model is uncertain about (e.g. 'line_items[0].ndc'). "
            "Empty list means full confidence."
        ),
    )
