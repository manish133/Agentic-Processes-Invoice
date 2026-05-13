"""
Anthropic-backed LLM service for invoice extraction and stage evaluation.

Public surface is unchanged from the previous GROK/Groq implementation so
existing callers (mock_ocr.py, grok_validation.py, main.py) work unchanged:

  - use_grok_api_key(api_key)         per-job key override (context manager)
  - is_grok_enabled()                 True when an Anthropic API key is configured
  - get_last_grok_error()             last error message (string)
  - extract_invoice_with_grok(path)   vision/text OCR -> ExtractedInvoice | None
  - invoke_llm_json(system_prompt, user_prompt)
                                      generic JSON-mode LLM call -> dict

Environment:
  ANTHROPIC_API_KEY   primary key  (fallback: GROK_API_KEY for migration)
  ANTHROPIC_MODEL     override default model (default: claude-opus-4-7)
  ANTHROPIC_MAX_TOKENS  output cap (default 8192, max 16000 non-streaming)
"""

from __future__ import annotations

import base64
import io
import json
import os
import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import anthropic
import pdfplumber

from models.schemas import ExtractedInvoice, InvoiceItem
from services.cache_store import cache_get, cache_set

_DEFAULT_MODEL = "claude-opus-4-7"
_DEFAULT_MAX_TOKENS = 8192

_CTX_API_KEY: ContextVar[str] = ContextVar("_CTX_LLM_API_KEY", default="")
_CTX_LAST_ERROR: ContextVar[str] = ContextVar("_CTX_LLM_LAST_ERROR", default="")


def _get_api_key() -> str:
    ctx = _CTX_API_KEY.get().strip()
    if ctx:
        return ctx
    return (
        os.getenv("ANTHROPIC_API_KEY", "").strip()
        or os.getenv("GROK_API_KEY", "").strip()
        or os.getenv("GROQ_API_KEY", "").strip()
    )


def _get_model() -> str:
    return (os.getenv("ANTHROPIC_MODEL") or "").strip() or _DEFAULT_MODEL


def _get_max_tokens() -> int:
    raw = (os.getenv("ANTHROPIC_MAX_TOKENS") or "").strip()
    try:
        v = int(raw) if raw else _DEFAULT_MAX_TOKENS
    except ValueError:
        v = _DEFAULT_MAX_TOKENS
    return max(1024, min(v, 16000))


@contextmanager
def use_grok_api_key(api_key: str):
    """
    Per-job API key override (name kept for backwards compatibility — the value
    is now an Anthropic API key, not a GROK key).
    """
    token = _CTX_API_KEY.set((api_key or "").strip())
    try:
        yield
    finally:
        _CTX_API_KEY.reset(token)


def is_grok_enabled() -> bool:
    """Return True when an Anthropic API key is configured."""
    return bool(_get_api_key())


def get_last_grok_error() -> str:
    return _CTX_LAST_ERROR.get().strip()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _extract_pdf_text(path: Path) -> str:
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


def _invoice_text_for_llm(path: Path) -> str:
    """Cap PDF text size to keep payload bounded."""
    raw = _extract_pdf_text(path)
    if not raw.strip():
        return ""
    try:
        cap = int(os.getenv("LLM_MAX_INVOICE_CHARS") or os.getenv("GROK_MAX_INVOICE_CHARS") or "16000")
    except ValueError:
        cap = 16000
    cap = max(4000, min(cap, 100_000))
    if len(raw) <= cap:
        return raw
    return raw[:cap] + "\n\n[Text truncated; raise LLM_MAX_INVOICE_CHARS if needed.]"


def _parse_llm_json(content: str) -> dict[str, Any]:
    raw = (content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        l = raw.find("{")
        r = raw.rfind("}")
        if l >= 0 and r > l:
            return json.loads(raw[l : r + 1])
        raise


def _to_invoice(data: dict[str, Any], filename: str) -> ExtractedInvoice:
    items_in = data.get("items") or []
    items: list[InvoiceItem] = []
    for it in items_in:
        if not isinstance(it, dict):
            continue
        items.append(
            InvoiceItem(
                name=str(it.get("name", "")).strip(),
                qty=_safe_float(it.get("qty"), 0.0),
                price=_safe_float(it.get("price"), 0.0),
                tax=_safe_float(it.get("tax"), 0.0),
            )
        )

    return ExtractedInvoice(
        invoice_number=str(data.get("invoice_number", "")).strip(),
        invoice_date=str(data.get("invoice_date", "")).strip() or None,
        vendor_name=str(data.get("vendor_name", "")).strip(),
        vendor_code=str(data.get("vendor_code", "")).strip(),
        gst_number=str(data.get("gst_number", "")).strip().upper(),
        po_number=str(data.get("po_number", "")).strip(),
        currency=str(data.get("currency", "INR")).strip().upper() or "INR",
        items=items,
        total_amount=_safe_float(data.get("total_amount"), 0.0),
        stamp_present=bool(data.get("stamp_present", False)),
        source_filename=filename,
    )


def _build_prompt(filename: str) -> str:
    return (
        "Extract invoice fields as strict JSON only. "
        "Do not include markdown, comments, or extra keys. "
        "Use null for missing date and empty string for missing text fields.\n\n"
        "JSON schema:\n"
        "{\n"
        '  "invoice_number": "string",\n'
        '  "invoice_date": "YYYY-MM-DD or null",\n'
        '  "vendor_name": "string",\n'
        '  "vendor_code": "string",\n'
        '  "gst_number": "15-char GSTIN string",\n'
        '  "po_number": "string",\n'
        '  "currency": "INR|USD|EUR|... ",\n'
        '  "items": [\n'
        "    {\n"
        '      "name": "Description of Goods",\n'
        '      "qty": 0,\n'
        '      "price": 0,\n'
        '      "tax": 0\n'
        "    }\n"
        "  ],\n"
        '  "total_amount": 0,\n'
        '  "stamp_present": false\n'
        "}\n\n"
        f"Source filename: {filename}\n"
        "If multiple GSTIN values exist, prefer Vendor/Supplier GSTIN."
    )


_IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _encode_image_for_anthropic(path: Path) -> tuple[str, str]:
    """Return (media_type, base64_data). TIFF is converted to PNG via Pillow."""
    suf = path.suffix.lower()
    if suf in _IMAGE_MEDIA_TYPES:
        return _IMAGE_MEDIA_TYPES[suf], base64.b64encode(path.read_bytes()).decode("ascii")
    if suf in {".tif", ".tiff"}:
        from PIL import Image

        with Image.open(path) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            return "image/png", base64.b64encode(buf.getvalue()).decode("ascii")
    raise ValueError(f"Unsupported image type: {suf}")


def _build_client() -> anthropic.Anthropic:
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Set ANTHROPIC_API_KEY (or paste an Anthropic key in the upload form)."
        )
    return anthropic.Anthropic(api_key=api_key)


def _extract_text_from_response(message: anthropic.types.Message) -> str:
    parts: list[str] = []
    for block in message.content:
        if getattr(block, "type", "") == "text":
            parts.append(block.text)
    return "".join(parts)


def _call_anthropic(
    *,
    system: str,
    user_content: str | list[dict[str, Any]],
    max_tokens: int | None = None,
) -> str:
    """Single Messages API call. Returns the concatenated text from response."""
    client = _build_client()
    if isinstance(user_content, str):
        user_blocks: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
    else:
        user_blocks = user_content
    message = client.messages.create(
        model=_get_model(),
        max_tokens=max_tokens or _get_max_tokens(),
        system=system,
        messages=[{"role": "user", "content": user_blocks}],
    )
    return _extract_text_from_response(message)


def invoke_llm_json(*, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """
    Generic JSON-mode LLM call used by stage validation. Returns parsed dict.
    """
    text = _call_anthropic(system=system_prompt, user_content=user_prompt)
    return _parse_llm_json(text)


def extract_invoice_with_grok(path: Path) -> ExtractedInvoice | None:
    """
    Use Anthropic for invoice extraction. Public name kept for caller compatibility
    (mock_ocr.py imports this symbol). Returns None on failure so caller can fall
    back to mock OCR.
    """
    if not is_grok_enabled():
        _CTX_LAST_ERROR.set("ANTHROPIC_API_KEY missing")
        return None

    try:
        prompt = _build_prompt(path.name)
        model = _get_model()
        cache_payload = {
            "v": 4,
            "provider": "anthropic",
            "model": model,
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size": int(path.stat().st_size) if path.exists() else 0,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "",
        }
        cached = cache_get("llm_extract", cache_payload)
        if isinstance(cached, dict):
            _CTX_LAST_ERROR.set("")
            return _to_invoice(cached, path.name)

        suf = path.suffix.lower()
        system_prompt = "You are a precise invoice extraction engine."

        if suf == ".pdf":
            doc_text = _invoice_text_for_llm(path)
            if not doc_text:
                _CTX_LAST_ERROR.set("PDF produced no extractable text")
                return None
            user_content: str | list[dict[str, Any]] = f"{prompt}\n\nInvoice text:\n{doc_text}"
        elif suf in _IMAGE_MEDIA_TYPES or suf in {".tif", ".tiff"}:
            media_type, b64 = _encode_image_for_anthropic(path)
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
            ]
        else:
            _CTX_LAST_ERROR.set(f"Unsupported file type: {suf}")
            return None

        text = _call_anthropic(system=system_prompt, user_content=user_content)
        data = _parse_llm_json(text)
        cache_set("llm_extract", cache_payload, data)
        _CTX_LAST_ERROR.set("")
        return _to_invoice(data, path.name)
    except Exception as e:
        _CTX_LAST_ERROR.set(str(e)[:300])
        return None
