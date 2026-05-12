"""Mock GST verification API (no external calls)."""

from __future__ import annotations

import re
from typing import Any


_GST_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def validate_gst_format(gst: str) -> bool:
    gst = (gst or "").strip().upper()
    return bool(_GST_PATTERN.match(gst))


def verify_gst_sync(gst: str, vendor_name_hint: str = "") -> dict[str, Any]:
    """Synchronous mock GST portal verification (no I/O)."""
    ok = validate_gst_format(gst)
    active = ok and not gst.upper().endswith("ZZZZZ")
    return {
        "valid_format": ok,
        "active": active,
        "gstin": gst.upper() if ok else gst,
        "legal_name": vendor_name_hint or "MOCK REGISTERED NAME",
        "mock": True,
    }


async def verify_gst_mock(gst: str, vendor_name_hint: str = "") -> dict[str, Any]:
    """Async wrapper — same payload as sync (for tests)."""
    return verify_gst_sync(gst, vendor_name_hint)
