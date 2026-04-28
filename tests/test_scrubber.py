"""Scrubber tests — security-critical, fully deterministic.

Each test asserts both that the redaction happened (count > 0) and that the
underlying sensitive value no longer appears in the scrubbed output.
"""

from __future__ import annotations

import re

import pytest

from src.scrubber import scrub


# ─── Per-pattern coverage ────────────────────────────────────────────────────
class TestDEA:
    def test_basic(self) -> None:
        result = scrub("DEA #: AB1234567")
        assert result.redaction_counts.get("DEA") == 1
        assert "AB1234567" not in result.text
        assert "[REDACTED_DEA]" in result.text

    def test_lowercase(self) -> None:
        result = scrub("dea ab1234567")
        assert result.redaction_counts.get("DEA") == 1
        assert "ab1234567" not in result.text

    def test_multiple(self) -> None:
        result = scrub("AB1234567 and CD9876543")
        assert result.redaction_counts.get("DEA") == 2

    def test_no_match_too_short(self) -> None:
        result = scrub("AB123")
        assert "DEA" not in result.redaction_counts


class TestAccount:
    def test_acct_prefix(self) -> None:
        result = scrub("acct 12345")
        assert result.redaction_counts.get("ACCOUNT") == 1
        assert "12345" not in result.text

    def test_account_word_form(self) -> None:
        result = scrub("account number is 778-2231")
        assert result.redaction_counts.get("ACCOUNT") == 1
        assert "778-2231" not in result.text
        # Sanity: the surrounding "number is" is NOT consumed
        assert "number is [REDACTED_ACCOUNT]" in result.text

    def test_alphanumeric_account(self) -> None:
        result = scrub("acct SM-44012")
        assert result.redaction_counts.get("ACCOUNT") == 1
        assert "SM-44012" not in result.text

    def test_no_match_when_value_has_no_digits(self) -> None:
        # "account" alone with no value should not match
        result = scrub("the standard account, please")
        assert "ACCOUNT" not in result.redaction_counts


class TestRouting:
    def test_routing_label(self) -> None:
        result = scrub("routing: 021000021")
        assert result.redaction_counts.get("ROUTING") == 1
        assert "021000021" not in result.text


class TestSSN:
    def test_dashed(self) -> None:
        result = scrub("123-45-6789")
        assert result.redaction_counts.get("SSN") == 1
        assert "123-45-6789" not in result.text

    def test_spaced(self) -> None:
        result = scrub("123 45 6789")
        assert result.redaction_counts.get("SSN") == 1


class TestPhone:
    def test_dashed(self) -> None:
        result = scrub("call 615-555-0142")
        assert result.redaction_counts.get("PHONE") == 1
        assert "615-555-0142" not in result.text

    def test_dotted(self) -> None:
        result = scrub("615.555.0142")
        assert result.redaction_counts.get("PHONE") == 1

    def test_paren_format(self) -> None:
        result = scrub("(615) 555-0142")
        assert result.redaction_counts.get("PHONE") == 1


class TestEmail:
    def test_basic(self) -> None:
        result = scrub("write us at pharmacy@stmarys.example")
        assert result.redaction_counts.get("EMAIL") == 1
        assert "pharmacy@stmarys.example" not in result.text

    def test_plus_addressing(self) -> None:
        result = scrub("user+tag@example.com")
        assert result.redaction_counts.get("EMAIL") == 1


class TestPHI:
    def test_patient_name(self) -> None:
        result = scrub("patient John Doe needs starting Monday")
        assert result.redaction_counts.get("PHI") == 1
        assert "John Doe" not in result.text

    def test_for_name(self) -> None:
        result = scrub("for Jane Roe immediately")
        assert result.redaction_counts.get("PHI") == 1
        assert "Jane Roe" not in result.text

    def test_pt_abbreviation(self) -> None:
        result = scrub("pt Robert Smith")
        assert result.redaction_counts.get("PHI") == 1


# ─── Aggregate behavior ──────────────────────────────────────────────────────
class TestAggregate:
    def test_empty_input(self) -> None:
        result = scrub("")
        assert result.text == ""
        assert result.redaction_counts == {}
        assert result.total_redactions == 0

    def test_no_matches(self) -> None:
        result = scrub("Plain text with no sensitive identifiers.")
        assert result.text == "Plain text with no sensitive identifiers."
        assert result.redaction_counts == {}
        assert result.total_redactions == 0

    def test_multiple_types(self) -> None:
        text = (
            "Order from Riverbend Drugs · DEA AB1234567 · acct 12345 · "
            "patient John Doe · phone 615-555-0142 · email rep@example.com"
        )
        result = scrub(text)
        assert result.redaction_counts.get("DEA") == 1
        assert result.redaction_counts.get("ACCOUNT") == 1
        assert result.redaction_counts.get("PHI") == 1
        assert result.redaction_counts.get("PHONE") == 1
        assert result.redaction_counts.get("EMAIL") == 1
        assert result.total_redactions == 5

    def test_no_sensitive_pattern_leaks(self) -> None:
        """Belt-and-suspenders: post-scrub text must not contain any
        recognizable sensitive pattern from the input."""
        text = "AB1234567 plus 615-555-0142 plus 123-45-6789"
        result = scrub(text)
        # Primary patterns
        assert not re.search(r"\b[A-Z]{2}\d{7}\b", result.text)
        assert not re.search(r"\b\d{3}-\d{3}-\d{4}\b", result.text)
        assert not re.search(r"\b\d{3}-\d{2}-\d{4}\b", result.text)

    def test_idempotent_on_already_scrubbed_text(self) -> None:
        first = scrub("acct 12345 and DEA AB1234567")
        second = scrub(first.text)
        # Second pass should find nothing new
        assert second.redaction_counts == {}
