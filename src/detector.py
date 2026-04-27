"""Format detection.

Trivial extension-based router today. In production this is the seam where an
inbox watcher / SMS webhook / S3 ObjectCreated event hands off into the
pipeline; the detector would also do mime-sniffing for files that arrive
without trustworthy extensions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

Format = Literal["pdf", "csv", "sms"]


def detect_format(path: Path) -> Format:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".csv":
        return "csv"
    if suffix in {".txt", ".sms"}:
        return "sms"
    raise ValueError(f"Unsupported file extension for {path.name}: {suffix!r}")
