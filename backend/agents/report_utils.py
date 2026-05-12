"""Helpers for mock confidence scores and structured stage reports."""

from __future__ import annotations

import hashlib
from typing import Any


def field_confidence(value: str) -> float:
    """Deterministic pseudo-confidence in [0.85, 0.99] from value hash."""
    h = int(hashlib.sha256((value or "").encode()).hexdigest()[:8], 16)
    return round(0.85 + (h % 140) / 1000.0, 3)


def wrap_fields(inv: dict[str, Any], field_names: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in field_names:
        v = str(inv.get(f, "") or "")
        out[f] = {"value": v, "confidence": field_confidence(v)}
    return out
