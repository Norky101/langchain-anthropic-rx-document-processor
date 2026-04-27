"""LangChain LLM stage — the universal adapter.

One model call per source artifact. The model is initialized through
`init_chat_model` so swapping providers (Anthropic direct → AWS Bedrock →
OpenAI → Ollama) is a config change, not a code change. Today we default to
Anthropic for the demo; production deployments inside a HIPAA BAA AWS account
would set `LLM_PROVIDER=bedrock_converse`.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from .schema import PurchaseOrder

load_dotenv()


_SYSTEM_PROMPT = """\
You are an order-intake extractor for a B2B pharmaceutical marketplace.

You receive text that has already been (a) extracted from a PDF, CSV, or SMS,
and (b) passed through a deterministic redactor. The redactor replaces DEA
numbers, account numbers, routing numbers, SSNs, phones, emails, and patient
names with typed placeholders such as [REDACTED_DEA], [REDACTED_ACCOUNT],
[REDACTED_PHI]. Treat any placeholder you see as the literal value of that
field — do not invent the redacted value.

Your job is to populate a PurchaseOrder. A few rules:

1. Every PurchaseOrder has at least one line_item. If you cannot identify any
   ordered SKU, set confidence below 0.3 and add 'line_items' to flagged_fields.
2. If the source text mentions an account number, set buyer_account_ref to the
   placeholder you saw (e.g. '[REDACTED_ACCOUNT]'). If no account is present,
   use the buyer organization name as the ref (e.g. 'Maple Pharmacy').
3. If the source mentions a DEA number, set buyer_dea_redacted to '[REDACTED_DEA]';
   otherwise leave it null.
4. Normalize frequencies and units when possible: 'BID' for twice daily,
   'bottle' / 'vial' / 'case' for unit_of_measure.
5. Populate raw_excerpt_redacted with up to ~500 characters of the source you
   found most diagnostic. Never include the unredacted form of any sensitive
   value.
6. Set confidence honestly. If the buyer didn't specify a quantity or drug
   strength, mark those fields in flagged_fields and lower confidence
   accordingly. A confidence below 0.5 will route the record to human review.
7. Each input may contain multiple distinct orders. If so, extract one
   PurchaseOrder per logical order; the wrapper will call you once per order.
   For this call, focus on the single order described in the user message.
"""


def _model():
    """Build the structured-output chain. Cheap to call; no module-level state
    so tests can override LLM_PROVIDER between runs."""
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    model_id = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
    chat = init_chat_model(model_id, model_provider=provider)
    return chat.with_structured_output(PurchaseOrder)


def extract_order(
    *,
    source_format: str,
    source_file: str,
    redacted_text: str,
) -> PurchaseOrder:
    """Run the LLM extractor over already-scrubbed source text.

    Returns a validated PurchaseOrder. Raises pydantic.ValidationError if the
    model returns something that doesn't conform to the schema.
    """
    model = _model()
    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"Source format: {source_format}\n"
                f"Source file: {source_file}\n"
                f"Received at: {datetime.now(timezone.utc).isoformat()}\n\n"
                f"--- BEGIN REDACTED SOURCE ---\n{redacted_text}\n--- END REDACTED SOURCE ---"
            )
        ),
    ]
    result = model.invoke(messages)
    # Ensure source bookkeeping is correct regardless of what the model wrote.
    result.source_format = source_format  # type: ignore[assignment]
    result.source_file = source_file
    if not result.received_at:
        result.received_at = datetime.now(timezone.utc)
    return result
