"""Orchestrate PDF ingestion, parsing, and optional LLM refinement."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .ingest import find_balance_sheet_by_scan
from .models import BalanceSheetSnapshot, snapshot_to_flat_dict
from .parser import parse_ind_as_standalone


def _preview(text: str, max_len: int = 140) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def extract_metrics_from_pdf(
    pdf_path: str | Path,
    *,
    use_llm: bool | None = None,
    flow: list[str] | None = None,
) -> BalanceSheetSnapshot:
    """
    Read a PDF, locate a standalone balance sheet page, extract key metrics.

    If OPENAI_API_KEY is set and use_llm is True, merges LLM-extracted fields (optional).

    Pass ``flow=[]`` (or any list) to append human-readable pipeline steps for debugging.
    """

    def log(msg: str) -> None:
        if flow is not None:
            flow.append(msg)

    path = Path(pdf_path)
    log(f"1. Ingest - open `{path.resolve()}` ({path.stat().st_size // 1024} KB).")

    found = find_balance_sheet_by_scan(str(path))
    if not found:
        log("2. Locate - no page matched the standalone balance sheet heuristic.")
        raise FileNotFoundError(
            f"No standalone balance sheet page found in {path.name}. "
            "Try another PDF or add OCR for scanned pages.",
        )

    page_num, text = found
    log(
        "2. Locate - chose PDF page "
        f"{page_num} (text length {len(text)} chars after whitespace normalization)."
    )
    log(f"   Preview: {_preview(text)}")

    log("3. Parse - apply rule-based Ind AS patterns (totals, debt, cash, ratios).")
    snap = parse_ind_as_standalone(text)
    log(
        "   Extracted: "
        f"total_assets={'yes' if snap.total_assets else 'no'}, "
        f"total_liabilities={'yes' if snap.total_liabilities else 'no'}, "
        f"total_equity={'yes' if snap.total_equity else 'no'}, "
        f"line_items={len(snap.line_items)}, "
        f"derived_ratios={'yes' if snap.debt_to_equity or snap.equity_ratio else 'no'}."
    )

    if use_llm is None:
        use_llm = bool(os.environ.get("OPENAI_API_KEY"))
    if use_llm:
        log(
            "4. Optional LLM - OPENAI_API_KEY is set; merge structured JSON with the model "
            f"`{os.environ.get('OPENAI_BALANCE_SHEET_MODEL', 'gpt-4o-mini')}`."
        )
        snap = _merge_llm_snapshot(text, snap)
        log("   LLM merge finished (errors fall back to rule-based snapshot).")
    else:
        log(
            "4. Optional LLM - skipped (use `--llm` or set OPENAI_API_KEY to enable)."
        )

    log("5. Done - return `BalanceSheetSnapshot` (JSON below when using the CLI).")
    return snap


def metrics_as_json(snap: BalanceSheetSnapshot) -> str:
    """Serialize snapshot for display or APIs."""

    data: dict[str, Any] = json.loads(snap.model_dump_json())
    data["flat"] = snapshot_to_flat_dict(snap)
    return json.dumps(data, indent=2, ensure_ascii=False)


def _merge_llm_snapshot(text: str, baseline: BalanceSheetSnapshot) -> BalanceSheetSnapshot:
    """Optional OpenAI pass: fill gaps; keeps baseline totals when already present."""

    try:
        from openai import OpenAI
    except ImportError:
        return baseline

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return baseline

    client = OpenAI(api_key=api_key)
    prompt = (
        "You are a financial analyst. From the following balance sheet excerpt, "
        "extract JSON matching this schema keys: "
        "currency (string), unit (string), reporting_periods ([string, string]), "
        "total_assets ({current: number|null, prior: number|null}), "
        "total_liabilities, total_equity, line_items: [{name, values: {current, prior}}], "
        "debt_to_equity, equity_ratio as {current, prior} if inferable. "
        "Use numbers as printed (same scale as document). "
        "Respond with JSON only, no markdown.\n\n"
        f"EXCERPT:\n{text[:12000]}"
    )

    try:
        r = client.chat.completions.create(
            model=os.environ.get("OPENAI_BALANCE_SHEET_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = (r.choices[0].message.content or "").strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        merged = BalanceSheetSnapshot.model_validate({**baseline.model_dump(), **data})
        return merged
    except Exception:
        return baseline


def print_flow_diagram() -> None:
    """High-level architecture (same steps as runtime, for docs / `--flow`)."""

    print(
        """
--- Conceptual agent flow (mermaid) ---

```mermaid
flowchart LR
  A[PDF file] --> B[Page scan]
  B --> C[Pick standalone BS page]
  C --> D[Normalize text]
  D --> E[Rule-based parser]
  E --> F{LLM enabled?}
  F -->|no| G[BalanceSheetSnapshot]
  F -->|yes| H[Merge JSON from model]
  H --> G
  G --> I[JSON output]
```

--- Runtime steps match: ingest → locate → parse → optional LLM → result ---
"""
    )


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Extract key metrics from a balance sheet PDF.")
    p.add_argument("pdf", nargs="?", default="BS_25.pdf", help="Path to PDF")
    p.add_argument("--llm", action="store_true", help="Force OpenAI merge if API key set")
    p.add_argument("--no-llm", action="store_true", help="Disable LLM merge")
    p.add_argument(
        "--flow",
        action="store_true",
        help="Print step-by-step pipeline log, then JSON (see also --diagram).",
    )
    p.add_argument(
        "--diagram",
        action="store_true",
        help="Print a mermaid diagram of the conceptual agent flow.",
    )
    args = p.parse_args()

    if args.diagram:
        print_flow_diagram()
        if not args.flow:
            return

    use_llm = None
    if args.llm:
        use_llm = True
    if args.no_llm:
        use_llm = False

    flow_lines: list[str] | None = [] if args.flow else None
    snap = extract_metrics_from_pdf(args.pdf, use_llm=use_llm, flow=flow_lines)

    if args.flow and flow_lines:
        print("=== Balance sheet agent - runtime flow ===\n")
        for line in flow_lines:
            print(line)
        print("\n=== Extracted metrics (JSON) ===\n")

    print(metrics_as_json(snap))


if __name__ == "__main__":
    main()
