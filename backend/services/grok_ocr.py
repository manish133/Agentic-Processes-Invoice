"""GROK-backed invoice extraction (best-effort; falls back to mock OCR on failure)."""

from __future__ import annotations

import base64
import json
import os
import hashlib
from pathlib import Path
from typing import Any
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
import pdfplumber

from models.schemas import ExtractedInvoice, InvoiceItem
from services.cache_store import cache_get, cache_set

_DEFAULT_BASE_URL = "https://api.x.ai/v1"
_DEFAULT_MODEL = "grok-4-0709"
# Groq uses the same /chat/completions shape; default text model for invoice JSON extraction:
_DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
_CTX_GROK_API_KEY: ContextVar[str] = ContextVar("_CTX_GROK_API_KEY", default="")
_CTX_GROK_LAST_ERROR: ContextVar[str] = ContextVar("_CTX_GROK_LAST_ERROR", default="")


def _get_api_key() -> str:
    ctx = _CTX_GROK_API_KEY.get().strip()
    if ctx:
        return ctx
    return (
        os.getenv("GROK_API_KEY", "").strip()
        or os.getenv("GROQ_API_KEY", "").strip()
    )


def _is_groq_key(key: str) -> bool:
    k = (key or "").strip()
    return k.startswith("gsk_")


@contextmanager
def use_grok_api_key(api_key: str):
    """
    Per-job GROK key override.
    Keeps key scoped to the current execution context/thread.
    """
    token = _CTX_GROK_API_KEY.set((api_key or "").strip())
    try:
        yield
    finally:
        _CTX_GROK_API_KEY.reset(token)


def is_grok_enabled() -> bool:
    """Return True when a GROK API key is configured."""
    return bool(_get_api_key())


def get_last_grok_error() -> str:
    return _CTX_GROK_LAST_ERROR.get().strip()


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
    """Cap PDF text size to avoid huge payloads (slow / timeout on xAI Grok)."""
    raw = _extract_pdf_text(path)
    if not raw.strip():
        return ""
    try:
        cap = int(os.getenv("GROK_MAX_INVOICE_CHARS", "16000"))
    except ValueError:
        cap = 16000
    cap = max(4000, min(cap, 100_000))
    if len(raw) <= cap:
        return raw
    return raw[:cap] + "\n\n[Text truncated for API; raise GROK_MAX_INVOICE_CHARS if needed.]"


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


def _resolve_llm_base_and_model() -> tuple[str, str]:
    """
    xAI Grok: api.x.ai + grok-* models.
    Groq: api.groq.com + OpenAI-compatible models (keys usually start with gsk_).
    Override anytime with GROK_BASE_URL / GROK_MODEL (or GROQ_MODEL when using Groq).
    """
    api_key = _get_api_key()
    explicit_base = (os.getenv("GROK_BASE_URL") or "").strip().rstrip("/")
    explicit_model = (os.getenv("GROK_MODEL") or "").strip()
    groq_model = (os.getenv("GROQ_MODEL") or "").strip()

    if explicit_base and explicit_model:
        return explicit_base, explicit_model
    if _is_groq_key(api_key):
        base = explicit_base or _DEFAULT_GROQ_BASE_URL
        model = (
            explicit_model
            or groq_model
            or _DEFAULT_GROQ_MODEL
        )
        return base, model
    base = explicit_base or _DEFAULT_BASE_URL
    model = explicit_model or _DEFAULT_MODEL
    return base, model


def _call_grok(messages: list[dict[str, Any]]) -> dict[str, Any]:
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError("Set GROK_API_KEY or GROQ_API_KEY (or paste key in the upload form)")
    base, model = _resolve_llm_base_and_model()
    # xAI Grok can exceed 45s on long docs; short default caused "read operation timed out" → mock fallback.
    read_sec = _safe_float(os.getenv("GROK_TIMEOUT_SEC"), 180.0)
    connect_sec = _safe_float(os.getenv("GROK_CONNECT_TIMEOUT_SEC"), 30.0)
    read_sec = max(60.0, min(read_sec, 600.0))
    timeout = httpx.Timeout(connect=connect_sec, read=read_sec, write=120.0, pool=30.0)

    payload = {
        "model": model,
        "temperature": 0,
        "messages": messages,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base}/chat/completions", headers=headers, json=payload)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = (resp.text or "").strip()
            snippet = body[:400] if body else "<empty response body>"
            hint = ""
            if resp.status_code == 403:
                hint = (
                    " | Fix: (1) Open https://console.x.ai and add credits/licenses for your team, "
                    "OR (2) Use Groq instead: set env GROQ_API_KEY to a key starting with gsk_ "
                    "(or paste it in the upload form) — no x.ai account required."
                )
            raise RuntimeError(f"LLM API {resp.status_code}: {snippet}{hint}") from e
        return resp.json()


def _call_grok_with_retry(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """One retry on timeout (xAI can be slow on first token)."""
    try:
        return _call_grok(messages)
    except httpx.TimeoutException:
        try:
            return _call_grok(messages)
        except httpx.TimeoutException as e2:
            raise RuntimeError(
                "LLM API timed out. Set env GROK_TIMEOUT_SEC=300 (or higher) and restart the backend."
            ) from e2


def invoke_llm_json(*, system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """
    Shared GROK/Groq JSON call for non-OCR tasks (e.g., validation).
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    out = _call_grok_with_retry(messages)
    msg = (((out.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
    return _parse_llm_json(msg)


def extract_invoice_with_grok(path: Path) -> ExtractedInvoice | None:
    """
    Use GROK for invoice extraction.
    Returns None when disabled or on any failure, so caller can fallback gracefully.
    """
    if not is_grok_enabled():
        _CTX_GROK_LAST_ERROR.set("GROK_API_KEY or GROQ_API_KEY missing")
        return None

    try:
        prompt = _build_prompt(path.name)
        base, model = _resolve_llm_base_and_model()
        cache_payload = {
            "v": 3,
            "base": base,
            "model": model,
            "name": path.name,
            "suffix": path.suffix.lower(),
            "size": int(path.stat().st_size) if path.exists() else 0,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "",
        }
        cached = cache_get("grok_extract", cache_payload)
        if isinstance(cached, dict):
            _CTX_GROK_LAST_ERROR.set("")
            return _to_invoice(cached, path.name)
        suf = path.suffix.lower()
        if suf == ".pdf":
            doc_text = _invoice_text_for_llm(path)
            if not doc_text:
                return None
            messages = [
                {"role": "system", "content": "You are a precise invoice extraction engine."},
                {"role": "user", "content": f"{prompt}\n\nInvoice text:\n{doc_text}"},
            ]
        elif suf in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = "image/jpeg" if suf in {".jpg", ".jpeg"} else f"image/{suf.lstrip('.')}"
            messages = [
                {"role": "system", "content": "You are a precise invoice extraction engine."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                },
            ]
        else:
            return None

        out = _call_grok_with_retry(messages)
        msg = (((out.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
        data = _parse_llm_json(msg)
        cache_set("grok_extract", cache_payload, data)
        _CTX_GROK_LAST_ERROR.set("")
        return _to_invoice(data, path.name)
    except Exception as e:
        _CTX_GROK_LAST_ERROR.set(str(e)[:300])
        return None

