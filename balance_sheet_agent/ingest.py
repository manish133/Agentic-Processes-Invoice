"""Locate and extract balance-sheet text from annual-report PDFs."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pdfplumber


def normalize_ws(text: str) -> str:
    """Collapse whitespace and strip; keeps content readable for regex."""

    t = text.replace("\u00a0", " ").replace("\u2009", " ")
    # PDFs sometimes embed ASCII BEL (0x07) between words instead of spaces.
    t = t.replace("\x07", " ")
    return re.sub(r"[ \t\r\f\v]+", " ", t).strip()


@dataclass
class ExtractedPage:
    pdf_page_index: int  # 0-based
    text: str


def extract_all_pages(path: str) -> list[ExtractedPage]:
    out: list[ExtractedPage] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            raw = page.extract_text() or ""
            out.append(ExtractedPage(pdf_page_index=i, text=normalize_ws(raw)))
    return out


def find_standalone_balance_sheet_block(pages: list[ExtractedPage]) -> tuple[int, str] | None:
    """
    Find the first page that looks like a standalone Ind AS balance sheet.
    Returns (1-based PDF page number, concatenated text for that page(s)).
    """

    for ep in pages:
        t = ep.text
        if not t:
            continue
        if re.search(r"BALANCE\s+SHEET", t, re.I) and (
            re.search(r"Standalone.*Balance\s+Sheet", t, re.I)
            or re.search(r"TOTAL\s+ASSETS", t, re.I) and "ASSETS" in t and "LIABILITIES" in t
        ):
            return (ep.pdf_page_index + 1, t)
    return None


def find_balance_sheet_by_scan(path: str, max_pages: int = 220) -> tuple[int, str] | None:
    """Open PDF and return standalone balance sheet page text if found."""

    with pdfplumber.open(path) as pdf:
        n = min(len(pdf.pages), max_pages)
        candidates: list[tuple[int, str, int]] = []
        for i in range(n):
            page = pdf.pages[i]
            raw = normalize_ws(page.extract_text() or "")
            if not raw:
                continue
            if not re.search(r"BALANCE\s+SHEET", raw, re.I):
                continue
            if not re.search(r"Standalone.*Balance\s+Sheet|Balance\s+Sheet\s+as\s+at", raw, re.I):
                continue
            # Notes and schedules also say "balance sheet"; require face-statement totals.
            if not re.search(r"TOTAL\s+ASSETS", raw, re.I):
                continue
            if not re.search(r"TOTAL\s+LIABILITIES\s+AND\s+EQUITY", raw, re.I):
                continue
            score = 0
            if re.search(r"TOTAL\s+ASSETS\s*\(", raw, re.I):
                score += 2
            if "EQUITY AND LIABILITIES" in raw.replace(" ", "") or "LIABILITIES AND EQUITY" in raw:
                score += 1
            candidates.append((i + 1, raw, score))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[2], x[0]))
        best = candidates[0]
        return (best[0], best[1])
    return None
