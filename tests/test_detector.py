"""Format detector tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.detector import detect_format


class TestDetectFormat:
    def test_pdf(self, tmp_path: Path) -> None:
        p = tmp_path / "x.pdf"
        p.write_bytes(b"%PDF-")
        assert detect_format(p) == "pdf"

    def test_csv(self, tmp_path: Path) -> None:
        p = tmp_path / "x.csv"
        p.write_text("a,b\n1,2\n")
        assert detect_format(p) == "csv"

    def test_txt_is_sms(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("hi")
        assert detect_format(p) == "sms"

    def test_sms_extension(self, tmp_path: Path) -> None:
        p = tmp_path / "x.sms"
        p.write_text("hi")
        assert detect_format(p) == "sms"

    def test_uppercase_extension_supported(self, tmp_path: Path) -> None:
        p = tmp_path / "x.PDF"
        p.write_bytes(b"%PDF-")
        assert detect_format(p) == "pdf"

    def test_unknown_extension_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "x.docx"
        p.write_text("hi")
        with pytest.raises(ValueError, match="docx"):
            detect_format(p)

    def test_no_extension_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "x"
        p.write_text("hi")
        with pytest.raises(ValueError):
            detect_format(p)
