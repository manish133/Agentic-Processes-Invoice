"""Load PO / Vendor / GRN / Historical sheets from Excel using Pandas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from models.schemas import MasterData

# Expected sheet names (case-insensitive match)
SHEET_ALIASES = {
    "po master": "po_master",
    "po_master": "po_master",
    "vendor master": "vendor_master",
    "vendor_master": "vendor_master",
    "grn master": "grn_master",
    "grn_master": "grn_master",
    "historical invoices": "historical_invoices",
    "historical_invoices": "historical_invoices",
    "invoice history": "historical_invoices",
}


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    df = df.fillna("")
    records: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rec = {str(k).strip(): row[k] for k in df.columns}
        records.append(rec)
    return records


def load_master_excel(path: Path) -> MasterData:
    """Parse workbook; map sheets to MasterData fields."""
    xl = pd.ExcelFile(path)
    out = MasterData()
    for sheet in xl.sheet_names:
        key = SHEET_ALIASES.get(_norm(sheet))
        if not key:
            continue
        df = pd.read_excel(path, sheet_name=sheet)
        records = _df_to_records(df)
        setattr(out, key, records)
    return out


def build_sample_excel(target: Path) -> None:
    """Create a demo workbook under data/ for local runs."""
    target.parent.mkdir(parents=True, exist_ok=True)
    po = pd.DataFrame(
        [
            {
                "po_number": "PO-1001",
                "vendor_code": "V-001",
                "po_date": "2025-01-10",
                "amount": 500000.0,
                "unit_price": 450.0,
                "currency": "INR",
                "status": "open",
                "grn_ref": "GRN-501",
            },
            {
                "po_number": "PO-1002",
                "vendor_code": "V-002",
                "po_date": "2025-02-01",
                "amount": 120000.0,
                "unit_price": 0.0,
                "currency": "INR",
                "status": "closed",
                "grn_ref": "GRN-502",
            },
        ]
    )
    vendors = pd.DataFrame(
        [
            {
                "vendor_code": "V-001",
                "vendor_name": "Fresh Foods Ltd",
                "gst_number": "27AABCU9603R1ZX",
                "active": True,
                "currency": "INR",
            },
            {
                "vendor_code": "V-002",
                "vendor_name": "Spice Hub",
                "gst_number": "29ABCDE1234F1Z5",
                "active": True,
                "currency": "INR",
            },
        ]
    )
    grn = pd.DataFrame(
        [
            {
                "grn_number": "GRN-501",
                "po_number": "PO-1001",
                "grn_date": "2025-01-15",
                "line_item": "Tomato Ketchup 5L",
                "qty_received": 100.0,
            },
            {
                "grn_number": "GRN-502",
                "po_number": "PO-1002",
                "grn_date": "2025-02-05",
                "line_item": "Masala Mix",
                "qty_received": 50.0,
            },
        ]
    )
    hist = pd.DataFrame(
        [
            {"invoice_number": "INV-OLD-1", "vendor_code": "V-001", "amount": 1000.0},
        ]
    )
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        po.to_excel(writer, sheet_name="PO Master", index=False)
        vendors.to_excel(writer, sheet_name="Vendor Master", index=False)
        grn.to_excel(writer, sheet_name="GRN Master", index=False)
        hist.to_excel(writer, sheet_name="Historical Invoices", index=False)
