"""Rule-based parsing of standalone balance sheet text (Ind AS style)."""

from __future__ import annotations

import re

from .models import BalanceSheetSnapshot, LineItem, PeriodValues
from .numbers import parse_amount_token


def _two_amounts(pattern: str, text: str) -> tuple[float | None, float | None] | None:
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    a, b = m.group(1), m.group(2)
    return parse_amount_token(a), parse_amount_token(b)


def _unit_from_header(text: str) -> str:
    m = re.search(r"Balance\s+Sheet\s+as\s+at[^\n]*?\(([^)]+)\)", text, re.I)
    if m:
        return m.group(1).strip()
    m2 = re.search(r"`\s*in\s*lacs", text, re.I)
    if m2:
        return "INR lacs"
    return ""


def _period_labels(text: str) -> tuple[str, str]:
    """Try to read column headers like '31 March 2025 31 March 2024'."""

    m = re.search(
        r"(31\s+March\s+20\d{2})\s+(31\s+March\s+20\d{2})",
        text,
        re.I,
    )
    if m:
        return m.group(1), m.group(2)
    return ("current", "prior")


def parse_ind_as_standalone(text: str) -> BalanceSheetSnapshot:
    """
    Extract totals and selected lines from text similar to HDFC Securities sample.
    Tolerant to odd spaces between words (PDF extraction).
    """

    t = re.sub(r"\s+", " ", text)
    unit = _unit_from_header(text)
    c_lab, p_lab = _period_labels(text)

    snap = BalanceSheetSnapshot(
        unit=unit or "INR lacs",
        currency="INR",
        reporting_periods=(c_lab, p_lab),
    )

    def pv(cur: float | None, pri: float | None) -> PeriodValues:
        return PeriodValues(
            label_current=c_lab,
            label_prior=p_lab,
            current=cur,
            prior=pri,
            unit=snap.unit,
        )

    # Totals (flexible spacing / optional unicode junk between words)
    ta = _two_amounts(
        r"TOTAL\s+ASSETS\s*\([^)]*\)\s*([\d,]+)\s+([\d,]+)",
        t,
    )
    if ta:
        snap.total_assets = pv(*ta)

    tlv = _two_amounts(
        r"TOTAL\s+LIABILITIES\s+AND\s+EQUITY\s*\([^)]*\)\s*([\d,]+)\s+([\d,]+)",
        t,
    )

    tfl = _two_amounts(
        r"Total\s+Financial\s+Liabilities\s*\(III\)\s*([\d,]+)\s+([\d,]+)",
        t,
    )
    tnfl = _two_amounts(
        r"Total\s+Non[- ]?Financial\s+Liabilities\s*\(IV\)\s*([\d,]+)\s+([\d,]+)",
        t,
    )
    teq = _two_amounts(
        r"Total\s+Equity\s*\(V\)\s*([\d,]+)\s+([\d,]+)",
        t,
    )

    if tfl and tnfl:
        cur = (tfl[0] or 0) + (tnfl[0] or 0)
        pri = (tfl[1] or 0) + (tnfl[1] or 0)
        snap.total_liabilities = pv(cur, pri)
    elif tlv and teq and snap.total_assets:
        # L+E = total L&E; liabilities = total - equity
        cur = (snap.total_assets.current or 0) - (teq[0] or 0)
        pri = (snap.total_assets.prior or 0) - (teq[1] or 0)
        snap.total_liabilities = pv(cur, pri)

    if teq:
        snap.total_equity = pv(*teq)

    # Interest-bearing debt (typical minimal set)
    ds = _two_amounts(r"Debt\s+securities\s+\d+\s*([\d,]+)\s+([\d,]+)", t)
    br = _two_amounts(
        r"Borrowings\s*\([^)]*debt securities[^)]*\)\s+\d+\s*([\d,]+)\s+([\d,]+)",
        t,
    )
    cash = _two_amounts(r"Cash\s+and\s+cash\s+equivalents\s+\d+\s*([\d,]+)\s+([\d,]+)", t)

    for name, pair in (
        ("Debt securities", ds),
        ("Borrowings (other than debt securities)", br),
        ("Cash and cash equivalents", cash),
    ):
        if pair:
            snap.line_items.append(
                LineItem(name=name, values=pv(pair[0], pair[1])),
            )

    # Derived ratios
    if snap.total_equity and snap.total_equity.current and snap.total_equity.prior:
        d_cur = ((ds[0] or 0) + (br[0] or 0)) if ds and br else None
        d_pri = ((ds[1] or 0) + (br[1] or 0)) if ds and br else None
        if d_cur is not None and d_pri is not None and snap.total_equity.current and snap.total_equity.prior:
            snap.debt_to_equity = pv(
                d_cur / snap.total_equity.current,
                d_pri / snap.total_equity.prior,
            )
            snap.debt_to_equity.unit = "ratio"

        ta_c = snap.total_assets.current if snap.total_assets else None
        ta_p = snap.total_assets.prior if snap.total_assets else None
        if ta_c and ta_p:
            snap.equity_ratio = pv(
                snap.total_equity.current / ta_c,
                snap.total_equity.prior / ta_p,
            )
            snap.equity_ratio.unit = "ratio"

    return snap
