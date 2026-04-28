"""LangChain LLM stage — the universal adapter.

One model call per source artifact. The model is initialized through
`init_chat_model` so swapping providers (Anthropic direct → AWS Bedrock →
OpenAI → Ollama) is a config change, not a code change. Today we default to
Anthropic for the demo; production deployments inside a HIPAA BAA AWS account
would set `LLM_PROVIDER=bedrock_converse`.

Reliability:
    * `LLM_TIMEOUT_S` (default 30) bounds each call.
    * Retries up to `LLM_MAX_ATTEMPTS` (default 3) with exponential backoff
      on transient Anthropic errors (rate-limit, timeout, 5xx).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from .schema import PurchaseOrder

load_dotenv()

logger = logging.getLogger("graphiterx.llm")


_TRANSIENT_ERRORS: tuple[type[Exception], ...] = (
    anthropic.RateLimitError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.InternalServerError,
)


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
7. Each call extracts exactly one PurchaseOrder. The pipeline pre-splits SMS
   logs so each invocation receives a single message; PDFs and CSVs that
   describe one order are passed whole. Treat the entire user message as the
   one order to extract — never try to emit multiple orders or skip an order.
"""


def _model():
    """Build the structured-output chain. Cheap to call; no module-level state
    so tests can override LLM_PROVIDER between runs.

    Default is Haiku 4.5 — fastest and cheapest production-grade Claude. Plenty
    capable for schema-aligned extraction on the document sizes this pipeline
    handles. Override with LLM_MODEL for a heavier model on harder corpora.

    Wraps the chat model with a bounded retry on transient Anthropic errors:
    rate limits, request timeouts, connection errors, and 5xx. Retries use
    exponential backoff with jitter and stop after `LLM_MAX_ATTEMPTS`.
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic")
    model_id = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")
    timeout_s = float(os.environ.get("LLM_TIMEOUT_S", "30"))
    max_attempts = int(os.environ.get("LLM_MAX_ATTEMPTS", "3"))

    chat = init_chat_model(
        model_id,
        model_provider=provider,
        timeout=timeout_s,
    )
    chat = chat.with_retry(
        retry_if_exception_type=_TRANSIENT_ERRORS,
        stop_after_attempt=max_attempts,
        wait_exponential_jitter=True,
    )
    return chat.with_structured_output(PurchaseOrder)


def extract_order(
    *,
    source_format: str,
    source_file: str,
    redacted_text: str,
) -> PurchaseOrder:
    """Run the LLM extractor over already-scrubbed source text.

    Returns a validated PurchaseOrder. Raises pydantic.ValidationError if the
    model returns something that doesn't conform to the schema. Transient
    Anthropic errors (rate-limit, timeout, 5xx) are retried automatically;
    after exhaustion the original exception bubbles to the caller.
    """
    request_id = uuid.uuid4().hex[:8]
    logger.info(
        "extract start request_id=%s format=%s file=%s text_len=%d",
        request_id,
        source_format,
        source_file,
        len(redacted_text),
    )
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
    try:
        result = model.invoke(messages)
    except Exception:
        logger.exception("extract failed request_id=%s", request_id)
        raise
    # Ensure source bookkeeping is correct regardless of what the model wrote.
    result.source_format = source_format  # type: ignore[assignment]
    result.source_file = source_file
    if not result.received_at:
        result.received_at = datetime.now(timezone.utc)
    logger.info(
        "extract done  request_id=%s confidence=%.2f line_items=%d flagged=%d",
        request_id,
        result.confidence,
        len(result.line_items),
        len(result.flagged_fields),
    )
    return result
