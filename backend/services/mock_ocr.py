"""Mock OCR: extract invoice-like JSON from PDF text or image filename heuristics."""

from __future__ import annotations

import re
import os
from pathlib import Path
from typing import Any
from contextlib import contextmanager
from contextvars import ContextVar

import pdfplumber

from models.schemas import ExtractedInvoice, InvoiceItem
from services.grok_ocr import extract_invoice_with_grok, get_last_grok_error

_CTX_OCR_BACKEND: ContextVar[str] = ContextVar("_CTX_OCR_BACKEND", default="")


@contextmanager
def use_ocr_backend(backend: str):
    token = _CTX_OCR_BACKEND.set((backend or "").strip().lower())
    try:
        yield
    finally:
        _CTX_OCR_BACKEND.reset(token)


def _parse_date(text: str) -> str | None:
    # dd/mm/yyyy or yyyy-mm-dd
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", text)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    return None


def _find(r: str, text: str, default: str = "") -> str:
    m = re.search(r, text, re.I | re.M)
    return m.group(1).strip() if m else default


# Indian GSTIN: 15 chars; used to avoid matching random 15-char tokens
_GSTIN_15 = r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]"


def _extract_gstin(text: str) -> str:
    """Prefer vendor line GSTIN when multiple GSTINs appear in the document."""
    m = re.search(r"Vendor\s*GSTIN\s*[:#]?\s*(" + _GSTIN_15 + ")", text, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?:GSTIN|GST)\s*[:#]?\s*(" + _GSTIN_15 + ")", text, re.I)
    if m:
        return m.group(1).upper()
    found = re.findall(_GSTIN_15, text, re.I)
    return found[0].upper() if found else ""


def _extract_po(text: str) -> str:
    """Avoid matching the literal 'PO' inside 'PO No.' as the PO code (old bug: captured 'No')."""
    for pat in (
        r"PO\s*No\.?\s*([A-Z0-9][A-Z0-9\-]*)",
        r"(?:Purchase\s*Order|P\.O\.)\s*No\.?\s*([A-Z0-9\-]+)",
    ):
        m = re.search(pat, text, re.I)
        if m:
            g = m.group(1).strip()
            if g.upper() not in {"NO", "N"}:
                return g
    m = re.search(
        r"(?:PO|purchase\s*order)\s*[:#]\s*([A-Z0-9\-]+)",
        text,
        re.I,
    )
    if m:
        return m.group(1).strip()
    return ""


def _extract_invoice_number(text: str, filename: str) -> str:
    for pat in (
        r"Invoice\s*No\.?\s*([A-Z0-9\-]+)",
        r"(?:invoice\s*#?|inv\.?)\s*[:#]?\s*([A-Z0-9\-]+)",
        r"\b(INV\d+)\b",
    ):
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip()
    m = re.search(r"\b(INV-[A-Z0-9\-]+)\b", text, re.I)
    if m:
        return m.group(1).strip()
    return f"INV-{filename[:16]}"


def _extract_total_amount(text: str, items: list[InvoiceItem]) -> float:
    """
    Parse document total. Must not treat a lone '.' after the word 'amount' as a number
    (e.g. 'PO amount.' matched by a naive Total|Amount regex caused extract_from_pdf to crash).
    """
    patterns = (
        r"Grand\s*Total\s*(?:\([^)]*\))?\s*([\d,]+\.?\d*)",
        r"(?:Nett?\s*)?Total\s*(?:\(n\))?\s*([\d,]+\.?\d*)",
        r"Invoice\s*Total\s*[:#]?\s*([\d,]+\.?\d*)",
        r"Amount\s*Payable\s*[:#]?\s*([\d,]+\.?\d*)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        raw = m.group(1).replace(",", "").strip()
        if not raw or raw == ".":
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return float(sum(i.qty * i.price + i.tax for i in items))


def extract_from_pdf(path: Path) -> ExtractedInvoice:
    """Pull text with pdfplumber; map patterns to fields (mock OCR)."""
    text_parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ""
            text_parts.append(t)
    text = "\n".join(text_parts)
    return _text_to_invoice(text, path.name)


def extract_from_image(path: Path) -> ExtractedInvoice:
    """No Tesseract: derive mock data from filename tokens."""
    stem = path.stem.replace("_", " ")
    text = f"Invoice {stem}\n"
    text += "GST: 27AABCU9603R1ZX\n"
    text += "PO: PO-1001\n"
    text += "Date: 2025-01-20\n"
    text += "Vendor: Fresh Foods Ltd (V-001)\n"
    text += "Line: Tomato Ketchup 5L qty 100 price 450 tax 8100\n"
    text += "Total: 53100\n"
    text += "Stamp: yes\n"
    return _text_to_invoice(text, path.name)


def _text_to_invoice(text: str, filename: str) -> ExtractedInvoice:
    inv_no = _extract_invoice_number(text, filename)

    po = _extract_po(text)
    gst = _extract_gstin(text)
    vendor_name = _find(r"(?:Vendor|Supplier)\s*[:#]?\s*([^\n]+)", text, "")
    vendor_code = _find(r"(?:Vendor\s*Code)\s*[:#]?\s*([A-Z0-9\-]+)", text, "")
    if not vendor_code:
        vendor_code = _find(r"\((V-\d+)\)", text, "")

    d = _parse_date(text) or "2025-01-20"

    items: list[InvoiceItem] = []
    for line in text.splitlines():
        if re.search(r"qty|quantity", line, re.I) and re.search(r"price|rate", line, re.I):
            qty_m = re.search(r"qty\s*(\d+(?:\.\d+)?)", line, re.I)
            pr_m = re.search(r"price\s*(\d+(?:\.\d+)?)", line, re.I)
            tx_m = re.search(r"tax\s*(\d+(?:\.\d+)?)", line, re.I)
            nm = line.split("qty")[0].strip(" :-")
            if qty_m and pr_m:
                items.append(
                    InvoiceItem(
                        name=nm or "Item",
                        qty=float(qty_m.group(1)),
                        price=float(pr_m.group(1)),
                        tax=float(tx_m.group(1)) if tx_m else 0.0,
                    )
                )

    if not items:
        items = [
            InvoiceItem(name="Default Line Item", qty=1.0, price=1000.0, tax=180.0),
        ]

    total = _extract_total_amount(text, items)

    stamp = bool(re.search(r"stamp\s*[:#]?\s*(yes|present|true)", text, re.I))

    return ExtractedInvoice(
        invoice_number=inv_no,
        invoice_date=d,
        vendor_name=vendor_name or "Unknown Vendor",
        vendor_code=vendor_code,
        gst_number=gst,
        po_number=po,
        currency="INR",
        items=items,
        total_amount=total,
        stamp_present=stamp,
        source_filename=filename,
    )


def extract_invoice_file_with_engine(path: Path) -> tuple[ExtractedInvoice, str, str]:
    """Route by extension and return (invoice, engine_used, engine_note)."""
    backend = _CTX_OCR_BACKEND.get().strip().lower() or os.getenv("OCR_BACKEND", "").strip().lower()
    if backend in {"", "auto", "grok"}:
        grok_inv = extract_invoice_with_grok(path)
        if grok_inv is not None:
            return grok_inv, "grok", ""
        err = get_last_grok_error()
    else:
        err = ""

    suf = path.suffix.lower()
    if suf == ".pdf":
        try:
            return extract_from_pdf(path), "mock", err
        except Exception:
            return _text_to_invoice("", path.name), "mock", err
    if suf in {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}:
        return extract_from_image(path), "mock", err
    return _text_to_invoice("", path.name), "mock", err


def extract_invoice_file(path: Path) -> ExtractedInvoice:
    """Backward-compatible wrapper for callers that only need invoice."""
    inv, _, _ = extract_invoice_file_with_engine(path)
    return inv


def invoice_to_dict(inv: ExtractedInvoice) -> dict[str, Any]:
    return inv.model_dump()
