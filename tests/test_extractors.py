"""Extractor tests against the bundled samples."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.extractors import csv_extract, pdf_extract, sms_extract


class TestSmsExtract:
    def test_returns_list_per_line(self, tmp_path: Path) -> None:
        p = tmp_path / "log.txt"
        p.write_text("first message\nsecond message\nthird")
        units = sms_extract(p)
        assert len(units) == 3
        assert units[0] == "first message"
        assert units[2] == "third"

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "log.txt"
        p.write_text("line one\n\n   \nline two\n")
        units = sms_extract(p)
        assert units == ["line one", "line two"]

    def test_empty_file(self, tmp_path: Path) -> None:
        p = tmp_path / "log.txt"
        p.write_text("")
        assert sms_extract(p) == []


class TestCsvExtract:
    def test_returns_single_unit(self, sample_csv: Path) -> None:
        units = csv_extract(sample_csv)
        assert len(units) == 1

    def test_preserves_original_headers(self, sample_csv: Path) -> None:
        units = csv_extract(sample_csv)
        text = units[0]
        # Buyer's chosen headers appear verbatim, so the LLM can map them
        assert "Acct" in text
        assert "NDC#" in text
        assert "strngth" in text

    def test_includes_filename(self, sample_csv: Path) -> None:
        units = csv_extract(sample_csv)
        assert "reorder_list.csv" in units[0]


class TestPdfExtract:
    def test_returns_single_unit(self, sample_pdf: Path) -> None:
        units = pdf_extract(sample_pdf)
        assert len(units) == 1

    def test_extracts_known_text(self, sample_pdf: Path) -> None:
        units = pdf_extract(sample_pdf)
        text = units[0]
        # The fake PO has these strings
        assert "PURCHASE ORDER" in text
        assert "Vancomycin" in text or "Norepinephrine" in text

    def test_extracts_dea_for_scrubber(self, sample_pdf: Path) -> None:
        units = pdf_extract(sample_pdf)
        # Pre-scrub, the DEA should be in the extracted text so the scrubber
        # has something to redact downstream.
        assert "AB1234567" in units[0]
