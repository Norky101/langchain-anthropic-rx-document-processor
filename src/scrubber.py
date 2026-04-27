"""Deterministic sensitive-data redaction.

Runs *before* the LLM stage so the model never sees raw DEA numbers, account
numbers, SSNs, or PHI. Compliance reviewers want predictable behavior here —
LLMs hallucinate, regexes don't. Each match is replaced with a typed
placeholder so the LLM can still understand the structure ("the buyer's DEA is
[REDACTED_DEA]") without seeing the underlying value.

Counts of redactions per type land in the audit log alongside each ingestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: longer / more-specific patterns first so a phone-shaped account
# number redacts as ACCOUNT, not PHONE.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # DEA: 2 letters + 7 digits. Format defined by DEA Form 224.
    ("DEA", re.compile(r"\b[A-Za-z]{2}\d{7}\b")),
    # SSN: 3-2-4 with dashes or spaces.
    ("SSN", re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")),
    # Account: "acct 12345", "account #12345", "account number is 778-2231" etc.
    # Capture must contain at least one digit (lookahead) so "number" / "is"
    # don't get redacted as account values.
    (
        "ACCOUNT",
        re.compile(
            r"\b(?:acct|account|customer)\s*(?:#|num|number)?\s*(?:is|=|:|-)?\s*"
            r"((?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,15})\b",
            re.IGNORECASE,
        ),
    ),
    # Routing / ABA: 9 digits.
    ("ROUTING", re.compile(r"\b(?:routing|aba)\s*[:#]?\s*(\d{9})\b", re.IGNORECASE)),
    # US phone: support several common shapes.
    (
        "PHONE",
        re.compile(
            r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
        ),
    ),
    # Email.
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Naive PHI: "patient <Cap Cap>" or "for <Cap Cap>" or "pt <Cap Cap>".
    # Production would use Comprehend Medical / Presidio; this is enough for the demo.
    (
        "PHI",
        re.compile(
            r"\b(?:patient|pt|for)\s+([A-Z][a-z]+\s+[A-Z][a-z]+)\b",
        ),
    ),
]


@dataclass
class ScrubResult:
    text: str
    redaction_counts: dict[str, int]

    @property
    def total_redactions(self) -> int:
        return sum(self.redaction_counts.values())


def scrub(text: str) -> ScrubResult:
    """Replace sensitive values with typed placeholders.

    For patterns that capture a sub-group (ACCOUNT, ROUTING, PHI), only the
    captured group is replaced — the surrounding context word stays so the LLM
    still sees "account [REDACTED_ACCOUNT]" rather than just "[REDACTED_ACCOUNT]".
    """
    counts: dict[str, int] = {}
    out = text

    for label, pattern in _PATTERNS:
        replacement = f"[REDACTED_{label}]"
        if pattern.groups == 0:
            new, n = pattern.subn(replacement, out)
        else:
            # Replace only group(1).
            def _sub(match: re.Match[str], _r: str = replacement) -> str:
                start, end = match.span(1)
                m_start, _m_end = match.span(0)
                return match.group(0)[: start - m_start] + _r + match.group(0)[end - m_start :]

            new, n = pattern.subn(_sub, out)
        if n:
            counts[label] = counts.get(label, 0) + n
        out = new

    return ScrubResult(text=out, redaction_counts=counts)
