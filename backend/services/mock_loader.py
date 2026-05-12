"""Load demo master data from packaged sample Excel (Pandas)."""

from __future__ import annotations

from pathlib import Path

from services.excel_loader import build_sample_excel, load_master_excel

_DATA = Path(__file__).resolve().parent.parent / "data" / "sample_masters.xlsx"


def ensure_demo_excel() -> Path:
    """Create `data/sample_masters.xlsx` if missing (QSR demo PO/Vendor/GRN/Historical)."""
    if not _DATA.exists():
        build_sample_excel(_DATA)
    return _DATA


def load_demo_masters():
    """Return MasterData from the demo workbook."""
    return load_master_excel(ensure_demo_excel())
