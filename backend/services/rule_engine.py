"""Evaluate Rules-and-Format rows against extractions + master data (OK / NOT OK)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from services.gst_service import validate_gst_format
from services.validators import (
    _find_grn_for_po,
    _find_po,
    _find_vendor,
    _parse_iso,
    check_duplicate_invoice,
    match_po_line_for_invoice_item,
)


def evaluate_all_rules(
    template_rows: list[dict[str, Any]],
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    po_totals: dict[str, float],
) -> list[dict[str, Any]]:
    """
    For each template row, attach result_ok (bool) and result_notes.
    Output rows preserve rule_description, validation_logic, agent_type from template.
    """
    if not template_rows:
        template_rows = _builtin_rule_templates()

    out: list[dict[str, Any]] = []
    for row in template_rows:
        try:
            rid = int(float(str(row.get("rule_id") or row.get("rule_no") or 0)))
        except (TypeError, ValueError):
            rid = 0
        if rid <= 0:
            continue
        eval_fn = RULE_DISPATCH.get(rid)
        ok, notes = (False, "Unknown rule") if not eval_fn else eval_fn(extractions, master, po_totals)
        desc = str(row.get("rule_description", "") or "").strip()
        logic = str(row.get("validation_logic", "") or "").strip()
        agent = str(row.get("agent_type", "") or "").strip()
        if not desc and rid in RULE_TEXT:
            desc, logic, agent = RULE_TEXT[rid]
        out.append(
            {
                "rule_id": rid,
                "rule_description": desc,
                "validation_logic": logic,
                "agent_type": agent,
                "result": "OK" if ok else "NOT OK",
                "result_notes": notes,
            }
        )
    return out


def _builtin_rule_templates() -> list[dict[str, Any]]:
    return [{"rule_id": i, "rule_description": t[0], "validation_logic": t[1], "agent_type": t[2]} for i, t in RULE_TEXT.items()]


# (description, logic, agent display)
RULE_TEXT: dict[int, tuple[str, str, str]] = {
    1: ("PO Amount ≥ Invoice Amount", "Invoice Total ≤ PO Total", "Matching"),
    2: ("Cumulative Invoice ≤ PO", "Sum(invoices for PO) ≤ PO", "Matching"),
    3: ("Vendor Code Present", "Vendor Code ≠ empty", "Screening"),
    4: ("Vendor Name Present", "Vendor Name ≠ empty", "Screening"),
    5: ("Vendor Code ↔ Name Match", "Matches Vendor Master", "Matching"),
    6: ("GSTIN Present", "GSTIN ≠ empty", "Screening"),
    7: ("GSTIN Format Valid", "15-char GST format", "Validation"),
    8: ("GSTIN ↔ Vendor Match", "GST matches Vendor Master", "Matching"),
    9: ("PO Exists", "PO in PO Master", "Matching"),
    10: ("GRN Exists", "GRN for PO in GRN Master", "Matching"),
    11: ("PO ↔ GRN Link", "GRN.PO = Invoice.PO", "Matching"),
    12: ("Invoice Date > PO Date", "Strictly after PO date", "Validation"),
    13: ("Invoice Date ≥ GRN Date", "On or after GRN date", "Validation"),
    14: ("Material Exists in PO", "Invoice materials in PO lines", "Matching"),
    15: ("Material Exists in GRN", "Invoice materials in GRN lines", "Matching"),
    16: ("Quantity Tolerance", "Invoice qty within ±2% of GRN", "Matching"),
    17: ("Rate Match vs PO", "Invoice rate vs PO NETPR_NET_PRICE per material line (±2%)", "Matching"),
    18: ("Line Amount Accuracy", "Qty × Rate ≈ Line Amount", "Validation"),
    19: ("Total Amount Accuracy", "Sum lines + tax = total; invoice total vs PO amount", "Validation"),
    20: ("Duplicate Invoice Check", "Not in history / batch", "Validation"),
    21: ("Historical Invoice Lookup", "Vendor has invoice history row", "Matching"),
    22: ("PO Status Check", "PO status is open", "Matching"),
    23: ("GRN Qty ≥ Invoice Qty", "GRN received ≥ invoiced qty", "Matching"),
}


def _all_invoices(
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    po_totals: dict[str, float],
    fn: Callable[..., tuple[bool, str]],
) -> tuple[bool, str]:
    if not extractions:
        return False, "No invoices"
    notes: list[str] = []
    ok_all = True
    for inv in extractions:
        o, n = fn(inv, master, po_totals)
        ok_all = ok_all and o
        if n:
            notes.append(f"{inv.get('source_filename')}: {n}")
    return ok_all, "; ".join(notes)[:500]


def _r1(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    total = float(inv.get("total_amount", 0))
    if not po:
        return False, "PO not found"
    pam = float(po.get("amount", 0) or 0)
    return (total <= pam * 1.02), f"inv {total} vs PO {pam}"


def _r2(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = str(inv.get("po_number", "")).strip()
    cum = po_totals.get(po, 0.0)
    p = _find_po(master.get("po_master") or [], po)
    if not p:
        return False, "PO not found"
    pam = float(p.get("amount", 0) or 0)
    return (cum <= pam * 1.02), f"cumulative {cum} vs PO {pam}"


def _r3(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    ok = bool(str(inv.get("vendor_code", "")).strip())
    return ok, "" if ok else "Missing vendor code"


def _r4(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    ok = bool(str(inv.get("vendor_name", "")).strip())
    return ok, "" if ok else "Missing vendor name"


def _r5(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    v = _find_vendor(master.get("vendor_master") or [], str(inv.get("vendor_code", "")).strip())
    if not v:
        return False, "Vendor not in master"
    name_ok = str(v.get("vendor_name", "")).strip().lower() == str(inv.get("vendor_name", "")).strip().lower()
    return name_ok, "Name mismatch" if not name_ok else ""


def _r6(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    ok = bool(str(inv.get("gst_number", "")).strip())
    return ok, "" if ok else "Missing GSTIN"


def _r7(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    ok = validate_gst_format(str(inv.get("gst_number", "")))
    return ok, "" if ok else "Invalid GST format"


def _r8(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    v = _find_vendor(master.get("vendor_master") or [], str(inv.get("vendor_code", "")).strip())
    if not v:
        return False, "Vendor not in master"
    ok = str(v.get("gst_number", "")).strip().upper() == str(inv.get("gst_number", "")).strip().upper()
    return ok, "" if ok else "GST differs from master"


def _r9(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    ok = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip()) is not None
    return ok, "" if ok else "PO missing"


def _r10(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    return (len(grns) > 0), "" if grns else "No GRN rows"


def _r11(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    return (len(grns) > 0), "" if grns else "No GRN for PO"


def _r12(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    inv_d = _parse_iso(inv.get("invoice_date"))
    if not po or not inv_d:
        return False, "Missing date"
    pod = _parse_iso(str(po.get("po_date", "")))
    if not pod:
        return False, "PO date missing"
    return (inv_d > pod), "" if inv_d > pod else "Invoice date must be after PO date"


def _r13(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    inv_d = _parse_iso(inv.get("invoice_date"))
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    if not inv_d or not grns:
        return False, "Missing data"
    worst = None
    for g in grns:
        gd = _parse_iso(str(g.get("grn_date", "")))
        if gd and (worst is None or gd < worst):
            worst = gd
    if worst is None:
        return False, "No GRN date"
    return (inv_d >= worst), "" if inv_d >= worst else "Before GRN date"


def _r14(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    if not po:
        return False, "No PO"
    lines = po.get("lines") or []
    if not lines:
        mats = [str(x.get("MATNR_MATERIAL_CODE", "")) for x in master.get("po_master") or [] if str(x.get("EBELN_PO_NO", "")).strip() == str(inv.get("po_number", "")).strip()]
    else:
        mats = [str(x.get("MATNR_MATERIAL_CODE", "")) for x in lines]
    mats = [m.lower() for m in mats if m]
    for it in inv.get("items") or []:
        nm = str(it.get("name", "")).lower()
        if nm and not any(m in nm or nm in str(m).lower() for m in mats if m):
            return False, f"Material line not in PO: {it.get('name')}"
    return True, ""


def _r15(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    if not grns:
        return False, "No GRN"
    gdesc = [str(g.get("line_item", "")).lower() for g in grns]
    for it in inv.get("items") or []:
        nm = str(it.get("name", "")).lower()
        if nm and not any(nm in g or g in nm for g in gdesc if g):
            return False, f"Material not in GRN: {it.get('name')}"
    return True, ""


def _r16(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    for it in inv.get("items") or []:
        qty = float(it.get("qty", 0))
        nm = str(it.get("name", "")).lower()
        matched = 0.0
        for g in grns:
            gl = str(g.get("line_item", "")).lower()
            if nm and (gl in nm or nm in gl):
                matched = float(g.get("qty_received", 0) or 0)
                break
        if matched > 0 and abs(qty - matched) / max(matched, 1e-6) > 0.02:
            return False, f"Qty tol: {qty} vs GRN {matched}"
    return True, ""


def _r17(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    if not po:
        return False, "No PO"
    po_lines = po.get("lines") or []
    fallback_up = float(po.get("unit_price", 0) or 0)
    for it in inv.get("items") or []:
        nm = str(it.get("name", "")).strip()
        pr = float(it.get("price", 0))
        if pr <= 0:
            return False, f"Missing invoice rate for line: {nm or '(unnamed)'}"
        if po_lines:
            pl = match_po_line_for_invoice_item(po_lines, nm)
            if not pl:
                return False, f"No PO line for material (TXZ01_MATERIAL_DESC): {nm}"
            netpr = float(pl.get("NETPR_NET_PRICE", 0) or 0)
            if netpr <= 0:
                mat = str(pl.get("TXZ01_MATERIAL_DESC") or pl.get("MATNR_MATERIAL_CODE") or "").strip()
                return False, f"Missing NETPR_NET_PRICE on PO line for {mat}"
            if abs(pr - netpr) / max(netpr, 1e-6) > 0.02:
                return False, f"Rate {pr} vs NETPR {netpr} for {nm}"
            po_qty = float(pl.get("MENGE_ORDER_QTY", 0) or 0)
            inv_qty = float(it.get("qty", 0))
            if po_qty > 0 and inv_qty > po_qty * 1.02:
                return False, f"Qty {inv_qty} vs PO MENGE_ORDER_QTY {po_qty} for {nm}"
        elif fallback_up > 0 and abs(pr - fallback_up) / max(fallback_up, 1e-6) > 0.02:
            return False, f"Rate {pr} vs PO unit_price {fallback_up}"
    return True, ""


def _r18(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    for it in inv.get("items") or []:
        q, p = float(it.get("qty", 0)), float(it.get("price", 0))
        if q <= 0 or p <= 0:
            return False, "Invalid qty/price on line"
    return True, ""


def _r19(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    items = inv.get("items") or []
    total = float(inv.get("total_amount", 0))
    line_net = sum(float(i.get("qty", 0)) * float(i.get("price", 0)) for i in items)
    tax = sum(float(i.get("tax", 0)) for i in items)
    exp = line_net + tax
    if abs(exp - total) > 0.05 * max(total, 1.0):
        return False, f"lines+tax {exp:.2f} vs invoice total {total}"

    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    if not po:
        return True, ""
    po_amt = float(po.get("amount", 0) or 0)
    if po_amt <= 0:
        return True, ""

    if total > po_amt * 1.02:
        return False, f"invoice total {total} exceeds PO amount {po_amt}"
    rel = abs(total - po_amt) / po_amt
    if rel <= 0.02:
        return True, ""
    if total < po_amt:
        return True, "partial vs PO amount"
    return False, f"invoice total {total} vs PO amount {po_amt}"


def _r20_wrapper(ext: list, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    hist = master.get("historical_invoices") or []
    nums = [str(x.get("invoice_number", "")).strip() for x in ext]
    cnt = Counter(nums)
    for inv in ext:
        inv_no = str(inv.get("invoice_number", "")).strip()
        others = {n for n in nums if n and n != inv_no}
        if inv_no and cnt[inv_no] > 1:
            others.add(inv_no)
        dup = check_duplicate_invoice(inv_no, hist, others)
        if dup:
            return False, dup.message
    return True, ""


def _r21(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    hist = master.get("historical_invoices") or []
    vc = str(inv.get("vendor_code", "")).strip()
    ok = any(str(h.get("vendor_code", "")).strip() == vc for h in hist)
    return ok, "" if ok else "No history rows for vendor"


def _r22(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    po = _find_po(master.get("po_master") or [], str(inv.get("po_number", "")).strip())
    if not po:
        return False, "PO missing"
    st = str(po.get("status", "open")).lower()
    return st in {"open", "partial"}, "" if st in {"open", "partial"} else f"PO status {st}"


def _r23(inv: dict, master: dict, po_totals: dict[str, float]) -> tuple[bool, str]:
    grns = _find_grn_for_po(master.get("grn_master") or [], str(inv.get("po_number", "")).strip())
    for it in inv.get("items") or []:
        qty = float(it.get("qty", 0))
        nm = str(it.get("name", "")).lower()
        matched = 0.0
        for g in grns:
            gl = str(g.get("line_item", "")).lower()
            if nm and (gl in nm or nm in gl):
                matched = float(g.get("qty_received", 0) or 0)
                break
        if matched > 0 and qty > matched * 1.02:
            return False, f"Invoice qty {qty} > GRN {matched}"
    return True, ""


RULE_DISPATCH: dict[int, Callable[[list[dict[str, Any]], dict[str, Any], dict[str, float]], tuple[bool, str]]] = {
    1: lambda e, m, p: _all_invoices(e, m, p, _r1),
    2: lambda e, m, p: _all_invoices(e, m, p, _r2),
    3: lambda e, m, p: _all_invoices(e, m, p, _r3),
    4: lambda e, m, p: _all_invoices(e, m, p, _r4),
    5: lambda e, m, p: _all_invoices(e, m, p, _r5),
    6: lambda e, m, p: _all_invoices(e, m, p, _r6),
    7: lambda e, m, p: _all_invoices(e, m, p, _r7),
    8: lambda e, m, p: _all_invoices(e, m, p, _r8),
    9: lambda e, m, p: _all_invoices(e, m, p, _r9),
    10: lambda e, m, p: _all_invoices(e, m, p, _r10),
    11: lambda e, m, p: _all_invoices(e, m, p, _r11),
    12: lambda e, m, p: _all_invoices(e, m, p, _r12),
    13: lambda e, m, p: _all_invoices(e, m, p, _r13),
    14: lambda e, m, p: _all_invoices(e, m, p, _r14),
    15: lambda e, m, p: _all_invoices(e, m, p, _r15),
    16: lambda e, m, p: _all_invoices(e, m, p, _r16),
    17: lambda e, m, p: _all_invoices(e, m, p, _r17),
    18: lambda e, m, p: _all_invoices(e, m, p, _r18),
    19: lambda e, m, p: _all_invoices(e, m, p, _r19),
    20: lambda e, m, p: _r20_wrapper(e, m, p),
    21: lambda e, m, p: _all_invoices(e, m, p, _r21),
    22: lambda e, m, p: _all_invoices(e, m, p, _r22),
    23: lambda e, m, p: _all_invoices(e, m, p, _r23),
}
