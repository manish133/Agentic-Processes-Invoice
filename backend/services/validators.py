"""Business rules: GST, vendor, PO/GRN match, duplicates, tax math, tolerances."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from models.schemas import ExceptionRecord, Severity
from services.gst_service import validate_gst_format


def _parse_iso(d: Optional[str]) -> Optional[date]:
    if not d:
        return None
    d = str(d).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(d, fmt).date()
        except ValueError:
            continue
    return None


def _find_po(po_master: list[dict[str, Any]], po_number: str) -> Optional[dict[str, Any]]:
    for row in po_master:
        p = str(row.get("po_number") or row.get("EBELN_PO_NO", "")).strip()
        if p == po_number:
            return row
    return None


def _find_vendor(vendor_master: list[dict[str, Any]], code: str) -> Optional[dict[str, Any]]:
    for row in vendor_master:
        vc = str(row.get("vendor_code") or row.get("LIFNR_VENDOR_CODE", "")).strip()
        if vc == code:
            return row
    return None


def _find_grn_for_po(grn_master: list[dict[str, Any]], po_number: str) -> list[dict[str, Any]]:
    return [
        g
        for g in grn_master
        if str(g.get("po_number") or g.get("EBELN_PO_NO", "")).strip() == po_number
    ]


def _norm_match_text(s: str) -> str:
    return " ".join(str(s).lower().split())


def match_po_line_for_invoice_item(
    po_lines: list[dict[str, Any]],
    invoice_item_name: str,
) -> Optional[dict[str, Any]]:
    """
    Match invoice line (Description of Goods) to a PO line using TXZ01_MATERIAL_DESC
    (fallback: MATERIAL_DESC, MATNR_MATERIAL_CODE).
    """
    if not po_lines:
        return None
    raw = str(invoice_item_name).strip()
    if not raw:
        return None
    n = _norm_match_text(raw)
    substring_hits: list[tuple[int, dict[str, Any]]] = []
    token_best: tuple[float, Optional[dict[str, Any]]] = (0.0, None)

    for line in po_lines:
        desc = str(
            line.get("TXZ01_MATERIAL_DESC")
            or line.get("MATERIAL_DESC")
            or line.get("MATNR_MATERIAL_CODE")
            or ""
        ).strip()
        d = _norm_match_text(desc)
        if not d:
            continue
        if n == d:
            return line
        if n in d or d in n:
            substring_hits.append((min(len(n), len(d)), line))
        else:
            nt, dt = set(n.split()), set(d.split())
            if not nt or not dt:
                continue
            overlap = len(nt & dt)
            score = overlap / max(len(nt), len(dt), 1)
            if score > token_best[0]:
                token_best = (score, line)

    if substring_hits:
        substring_hits.sort(key=lambda x: -x[0])
        return substring_hits[0][1]
    if token_best[1] is not None and token_best[0] >= 0.34:
        return token_best[1]
    return None


def check_duplicate_invoice(
    invoice_number: str,
    historical: list[dict[str, Any]],
    batch_numbers: set[str],
) -> Optional[ExceptionRecord]:
    inv = invoice_number.strip()
    if not inv:
        return None
    for h in historical:
        if str(h.get("invoice_number", "")).strip() == inv:
            return ExceptionRecord(
                code="DUP_HIST",
                message=f"Invoice {inv} exists in historical invoices",
                severity=Severity.CRITICAL,
                field="invoice_number",
            )
    if inv in batch_numbers:
        return ExceptionRecord(
            code="DUP_BATCH",
            message=f"Duplicate invoice number in upload batch: {inv}",
            severity=Severity.CRITICAL,
            field="invoice_number",
        )
    return None


def validate_line_totals(
    items: list[dict[str, Any]],
    total_amount: float,
    currency: str,
    master: Optional[dict[str, Any]] = None,
    po_number: Optional[str] = None,
) -> list[ExceptionRecord]:
    errs: list[ExceptionRecord] = []
    line_net = sum(float(i.get("qty", 0)) * float(i.get("price", 0)) for i in items)
    tax_sum = sum(float(i.get("tax", 0)) for i in items)
    expected = line_net + tax_sum
    tot = float(total_amount)
    if abs(expected - tot) > 0.05 * max(1.0, tot):
        errs.append(
            ExceptionRecord(
                code="TOTAL_MISMATCH",
                message=f"Invoice total {total_amount} != line net+tax ({expected:.2f})",
                severity=Severity.CRITICAL,
                field="total_amount",
            )
        )

    if master and po_number:
        po = _find_po(master.get("po_master") or [], str(po_number).strip())
        if po:
            po_amt = float(po.get("amount", 0) or 0)
            if po_amt > 0:
                if tot > po_amt * 1.02:
                    errs.append(
                        ExceptionRecord(
                            code="TOTAL_VS_PO",
                            message=f"Invoice total {tot} exceeds PO amount {po_amt} (2% tolerance)",
                            severity=Severity.CRITICAL,
                            field="total_amount",
                        )
                    )
                else:
                    rel = abs(tot - po_amt) / po_amt
                    if tot > po_amt and rel > 0.02:
                        errs.append(
                            ExceptionRecord(
                                code="TOTAL_VS_PO",
                                message=f"Invoice total {tot} above PO amount {po_amt} beyond 2% band",
                                severity=Severity.CRITICAL,
                                field="total_amount",
                            )
                        )
    # Tax calculation plausibility per line
    for i, it in enumerate(items):
        q = float(it.get("qty", 0))
        p = float(it.get("price", 0))
        t = float(it.get("tax", 0))
        if q > 0 and p > 0 and t < 0:
            errs.append(
                ExceptionRecord(
                    code="TAX_NEG",
                    message=f"Line {i+1}: invalid tax",
                    severity=Severity.WARNING,
                    field="items",
                )
            )
    if currency and currency.upper() not in {"INR", "USD", "EUR"}:
        errs.append(
            ExceptionRecord(
                code="CURRENCY",
                message=f"Unsupported or inconsistent currency: {currency}",
                severity=Severity.WARNING,
                field="currency",
            )
        )
    return errs


def validate_vendor_and_po(
    master: dict[str, Any],
    inv: dict[str, Any],
    gst_active: bool,
) -> list[ExceptionRecord]:
    errs: list[ExceptionRecord] = []
    po_master = master.get("po_master") or []
    vendor_master = master.get("vendor_master") or []
    vcode = str(inv.get("vendor_code", "")).strip()
    po_num = str(inv.get("po_number", "")).strip()
    gst = str(inv.get("gst_number", "")).strip()

    if not validate_gst_format(gst):
        errs.append(
            ExceptionRecord(
                code="GST_FORMAT",
                message="Invalid GSTIN format",
                severity=Severity.CRITICAL,
                field="gst_number",
            )
        )

    vend = _find_vendor(vendor_master, vcode)
    if not vend:
        errs.append(
            ExceptionRecord(
                code="VENDOR_MISSING",
                message=f"Vendor code {vcode} not in Vendor Master",
                severity=Severity.CRITICAL,
                field="vendor_code",
            )
        )
    else:
        active = vend.get("active", True)
        if active is False or str(active).lower() == "false":
            errs.append(
                ExceptionRecord(
                    code="VENDOR_INACTIVE",
                    message=f"Vendor {vcode} is inactive",
                    severity=Severity.CRITICAL,
                    field="vendor_code",
                )
            )
        vm_gst = str(vend.get("gst_number", "")).strip()
        if vm_gst and gst and vm_gst.upper() != gst.upper():
            errs.append(
                ExceptionRecord(
                    code="GST_VENDOR_MISMATCH",
                    message="GST on invoice does not match vendor master",
                    severity=Severity.CRITICAL,
                    field="gst_number",
                )
            )

    po = _find_po(po_master, po_num) if po_num else None
    if not po:
        errs.append(
            ExceptionRecord(
                code="PO_MISSING",
                message=f"PO {po_num} not found in PO Master",
                severity=Severity.CRITICAL,
                field="po_number",
            )
        )
    else:
        status = str(po.get("status", "open")).lower()
        if status not in {"open", "partial"}:
            errs.append(
                ExceptionRecord(
                    code="PO_CLOSED",
                    message=f"PO {po_num} is not open",
                    severity=Severity.CRITICAL,
                    field="po_number",
                )
            )
        po_vc = str(po.get("vendor_code", "")).strip()
        if vcode and po_vc and po_vc != vcode:
            errs.append(
                ExceptionRecord(
                    code="PO_VENDOR_MISMATCH",
                    message="PO vendor does not match invoice vendor",
                    severity=Severity.CRITICAL,
                    field="vendor_code",
                )
            )

    if not gst_active:
        errs.append(
            ExceptionRecord(
                code="GST_INACTIVE",
                message="Mock GST API reports GST as inactive or invalid",
                severity=Severity.CRITICAL,
                field="gst_number",
            )
        )

    return errs


def validate_dates_and_grn(
    master: dict[str, Any],
    inv: dict[str, Any],
) -> list[ExceptionRecord]:
    errs: list[ExceptionRecord] = []
    grn_master = master.get("grn_master") or []
    po_master = master.get("po_master") or []
    po_num = str(inv.get("po_number", "")).strip()
    inv_date = _parse_iso(inv.get("invoice_date"))
    po = _find_po(po_master, po_num)
    if po and inv_date:
        pod = _parse_iso(str(po.get("po_date", "")))
        if pod and inv_date <= pod:
            errs.append(
                ExceptionRecord(
                    code="DATE_PO",
                    message="Invoice date must be strictly after PO date",
                    severity=Severity.CRITICAL,
                    field="invoice_date",
                )
            )
    grns = _find_grn_for_po(grn_master, po_num)
    if not grns:
        errs.append(
            ExceptionRecord(
                code="GRN_MISSING",
                message=f"No GRN for PO {po_num}",
                severity=Severity.CRITICAL,
                field="grn",
            )
        )
    else:
        for g in grns:
            gd = _parse_iso(str(g.get("grn_date", "")))
            if gd and inv_date and inv_date < gd:
                errs.append(
                    ExceptionRecord(
                        code="DATE_GRN",
                        message="Invoice date must be on or after GRN date",
                        severity=Severity.CRITICAL,
                        field="invoice_date",
                    )
                )
    return errs


def invoice_delay_warning(inv: dict[str, Any]) -> Optional[ExceptionRecord]:
    inv_date = _parse_iso(inv.get("invoice_date"))
    if not inv_date:
        return None
    days = (date.today() - inv_date).days
    if days > 30:
        return ExceptionRecord(
            code="DELAY_30",
            message=f"Invoice is {days} days after invoice date — possible delay",
            severity=Severity.WARNING,
            field="invoice_date",
        )
    return None


def matching_checks(
    master: dict[str, Any],
    inv: dict[str, Any],
    cumulative_on_po: float,
) -> list[ExceptionRecord]:
    """PO vs invoice amount, cumulative PO usage, price tolerance, GRN qty tolerance, 3-way."""
    errs: list[ExceptionRecord] = []
    po_master = master.get("po_master") or []
    grn_master = master.get("grn_master") or []
    po_num = str(inv.get("po_number", "")).strip()
    po = _find_po(po_master, po_num)
    total = float(inv.get("total_amount", 0))
    items = inv.get("items") or []

    if po:
        po_amt = float(po.get("amount", 0) or 0)
        if total > po_amt * 1.02:
            errs.append(
                ExceptionRecord(
                    code="PO_AMT",
                    message="Invoice amount exceeds PO amount beyond 2% tolerance",
                    severity=Severity.CRITICAL,
                    field="total_amount",
                )
            )
        if cumulative_on_po > po_amt * 1.02:
            errs.append(
                ExceptionRecord(
                    code="PO_CUMULATIVE",
                    message="Cumulative invoices on this PO exceed PO amount (2% tolerance)",
                    severity=Severity.CRITICAL,
                    field="total_amount",
                )
            )

    grns = _find_grn_for_po(grn_master, po_num)
    for line in items:
        name = str(line.get("name", "")).lower()
        qty = float(line.get("qty", 0))
        price = float(line.get("price", 0))
        matched_grn_qty = 0.0
        for g in grns:
            g_line = str(g.get("line_item", "")).lower()
            if name and (g_line in name or name in g_line):
                matched_grn_qty = float(g.get("qty_received", 0) or 0)
                break
        if grns and matched_grn_qty > 0:
            if abs(qty - matched_grn_qty) / max(matched_grn_qty, 1e-6) > 0.02:
                errs.append(
                    ExceptionRecord(
                        code="QTY_TOLERANCE",
                        message=f"Qty mismatch vs GRN beyond 2% for line {line.get('name')}",
                        severity=Severity.CRITICAL,
                        field="items",
                    )
                )
        if po:
            po_lines = po.get("lines") or []
            if po_lines:
                pl = match_po_line_for_invoice_item(po_lines, str(line.get("name", "")))
                if pl:
                    netpr = float(pl.get("NETPR_NET_PRICE", 0) or 0)
                    if netpr > 0 and price > 0 and abs(price - netpr) / max(netpr, 1e-6) > 0.02:
                        mat = str(
                            pl.get("TXZ01_MATERIAL_DESC")
                            or pl.get("MATNR_MATERIAL_CODE")
                            or ""
                        ).strip()
                        errs.append(
                            ExceptionRecord(
                                code="PRICE_TOLERANCE",
                                message=(
                                    f"Line rate {price} vs PO NETPR_NET_PRICE {netpr} for "
                                    f"'{line.get('name')}' ↔ '{mat}' (>2%)"
                                ),
                                severity=Severity.CRITICAL,
                                field="items",
                            )
                        )
                    po_qty = float(pl.get("MENGE_ORDER_QTY", 0) or 0)
                    if po_qty > 0 and qty > po_qty * 1.02:
                        errs.append(
                            ExceptionRecord(
                                code="PO_QTY_ORDER",
                                message=(
                                    f"Invoice qty {qty} exceeds PO MENGE_ORDER_QTY {po_qty} "
                                    f"for line {line.get('name')}"
                                ),
                                severity=Severity.CRITICAL,
                                field="items",
                            )
                        )
                elif price > 0:
                    errs.append(
                        ExceptionRecord(
                            code="PO_LINE_MATCH",
                            message=f"No PO line (TXZ01_MATERIAL_DESC) for invoice line: {line.get('name')}",
                            severity=Severity.CRITICAL,
                            field="items",
                        )
                    )
            else:
                po_unit = float(po.get("unit_price", 0) or 0)
                if po_unit and price > 0 and abs(price - po_unit) / max(po_unit, 1e-6) > 0.02:
                    errs.append(
                        ExceptionRecord(
                            code="PRICE_TOLERANCE",
                            message=f"Line price {price} differs from PO unit price {po_unit} by >2%",
                            severity=Severity.CRITICAL,
                            field="items",
                        )
                    )

    return errs


def stamp_check(inv: dict[str, Any]) -> Optional[ExceptionRecord]:
    if not inv.get("stamp_present"):
        return ExceptionRecord(
            code="STAMP",
            message="Authorized stamp not detected on invoice",
            severity=Severity.CRITICAL,
            field="stamp_present",
        )
    return None
