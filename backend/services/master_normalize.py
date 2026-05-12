"""Map SAP-style / alternate Excel column names to internal validator schema."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any


def _has(rows: list[dict[str, Any]], key: str) -> bool:
    return bool(rows) and key in rows[0]


def normalize_master_dict(master: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of master data with po_master, vendor_master, grn_master, historical_invoices normalized."""
    out = dict(master)
    vm = master.get("vendor_master") or []
    pm = master.get("po_master") or []
    gm = master.get("grn_master") or []
    hi = master.get("historical_invoices") or []

    if vm and _has(vm, "LIFNR_VENDOR_CODE"):
        out["vendor_master"] = [_norm_vendor(r) for r in vm]
    if pm and _has(pm, "EBELN_PO_NO"):
        out["po_master"] = _aggregate_po_sap(pm)
    if gm and _has(gm, "MBLNR_GRN_NO"):
        out["grn_master"] = [_norm_grn_sap(r) for r in gm]
    if hi and _has(hi, "XBLNR_INVOICE_NO"):
        out["historical_invoices"] = [_norm_hist_sap(r) for r in hi]

    return out


def _norm_vendor(r: dict[str, Any]) -> dict[str, Any]:
    st = str(r.get("VENDOR_STATUS", "")).upper()
    active = st in {"ACTIVE", "A", "1", "YES", "TRUE"} or (st not in {"BLOCKED", "INACTIVE", "X"})
    return {
        "vendor_code": str(r.get("LIFNR_VENDOR_CODE", "")).strip(),
        "vendor_name": str(r.get("VENDOR_NAME", "") or r.get("LEGAL_NAME", "")).strip(),
        "gst_number": str(r.get("GSTIN", "")).strip(),
        "active": active,
        "currency": str(r.get("WAERS_CURRENCY", "INR") or "INR").upper(),
    }


def _aggregate_po_sap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_po: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        pono = str(r.get("EBELN_PO_NO", "")).strip()
        if pono:
            by_po[pono].append(r)
    out: list[dict[str, Any]] = []
    for pono, lines in by_po.items():
        first = lines[0]
        total = sum(float(x.get("NETWR_LINE_AMOUNT", 0) or 0) for x in lines)
        prices = [float(x.get("NETPR_NET_PRICE", 0) or 0) for x in lines if float(x.get("NETPR_NET_PRICE", 0) or 0) > 0]
        unit_price = sum(prices) / len(prices) if prices else 0.0
        pd = first.get("BEDAT_PO_DATE")
        po_date = _excel_date(pd)
        pst = str(first.get("PO_STATUS", "")).upper()
        status = "open" if pst in {"OPEN", "O", "A", "ACTIVE", "RELEASED"} else "closed"
        return_currency = str(first.get("WAERS_CURRENCY", "INR") or "INR").upper()
        out.append(
            {
                "po_number": pono,
                "vendor_code": str(first.get("LIFNR_VENDOR_CODE", "")).strip(),
                "po_date": po_date,
                "amount": total,
                "unit_price": unit_price,
                "currency": return_currency,
                "status": status,
                "grn_ref": "",
                "lines": lines,
            }
        )
    return out


def _norm_grn_sap(r: dict[str, Any]) -> dict[str, Any]:
    gd = r.get("BUDAT_GRN_DATE")
    return {
        "grn_number": str(r.get("MBLNR_GRN_NO", "")).strip(),
        "po_number": str(r.get("EBELN_PO_NO", "")).strip(),
        "grn_date": _excel_date(gd),
        "line_item": str(r.get("MATERIAL_DESC", "") or r.get("MATNR_MATERIAL_CODE", "")).strip(),
        "qty_received": float(r.get("MENGE_RECEIVED_QTY", 0) or r.get("ACCEPTED_QTY", 0) or 0),
    }


def _norm_hist_sap(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoice_number": str(r.get("XBLNR_INVOICE_NO", "")).strip(),
        "vendor_code": str(r.get("LIFNR_VENDOR_CODE", "")).strip(),
        "amount": float(r.get("INVOICE_AMOUNT", 0) or 0),
    }


def _excel_date(val: Any) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if hasattr(val, "strftime"):
        try:
            return val.strftime("%Y-%m-%d")  # type: ignore[union-attr]
        except Exception:
            pass
    s = str(val).strip()[:10]
    return s
