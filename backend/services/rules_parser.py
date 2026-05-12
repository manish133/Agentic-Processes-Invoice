"""Load 'Rules and Format' template: rule rows with #, description, logic, agent type."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "rules_format_default.xlsx"
)


def _find_header_row(df: pd.DataFrame) -> int | None:
    for i in range(min(25, len(df))):
        row = [str(x).lower() for x in df.iloc[i].tolist()]
        joined = " ".join(row)
        if "rule description" in joined and ("agent" in joined or "type" in joined):
            return i
    return None


def load_rules_format_excel(path: Path) -> list[dict[str, Any]]:
    """Parse Rules and Format workbook into rule rows (preserves template columns)."""
    df = pd.read_excel(path, sheet_name=0, header=None)
    hr = _find_header_row(df)
    if hr is None:
        return []

    header = [str(x).strip() if pd.notna(x) else "" for x in df.iloc[hr].tolist()]
    # Normalize header names
    col_map: dict[int, str] = {}
    for j, h in enumerate(header):
        hl = h.lower()
        if "#" in h or h.strip() == "#":
            col_map[j] = "rule_no"
        elif "description" in hl:
            col_map[j] = "rule_description"
        elif "logic" in hl or "validation" in hl:
            col_map[j] = "validation_logic"
        elif "agent" in hl:
            col_map[j] = "agent_type"
        elif "status" in hl:
            col_map[j] = "template_status"

    rows_out: list[dict[str, Any]] = []
    for i in range(hr + 1, len(df)):
        r = df.iloc[i]
        cells = [r.iloc[j] if j < len(r) else "" for j in range(len(header))]
        if all(pd.isna(c) or str(c).strip() == "" for c in cells[:4]):
            continue
        rec: dict[str, Any] = {}
        for j, c in enumerate(cells):
            key = col_map.get(j, f"col_{j}")
            val = c
            if pd.isna(val):
                val = ""
            elif isinstance(val, float):
                val = int(val) if val == int(val) else val
            rec[key] = val
        # rule number
        rid = rec.get("rule_no", "")
        if rid == "" or str(rid).lower() == "nan":
            continue
        try:
            rec["rule_id"] = int(float(str(rid).replace(",", ".").split(".")[0]))
        except (ValueError, TypeError):
            m = re.search(r"(\d+)", str(rid))
            rec["rule_id"] = int(m.group(1)) if m else i
        rows_out.append(rec)
    return rows_out


def ensure_default_rules_file() -> Path:
    """Copy bundled rules template next to sample masters if missing."""
    src = Path(__file__).resolve().parent.parent.parent / "Invoice Processing" / "Invoice Samples" / "Rules and Format.xlsx"
    DEFAULT_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DEFAULT_RULES_PATH.exists() and src.exists():
        DEFAULT_RULES_PATH.write_bytes(src.read_bytes())
    return DEFAULT_RULES_PATH if DEFAULT_RULES_PATH.exists() else src
