"""Extract agent: ingests PDF and produces a structured balance sheet snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from balance_sheet_agent.agent import extract_metrics_from_pdf
from balance_sheet_agent.models import BalanceSheetSnapshot

from .kpis import snapshot_to_kpi_rows
from .types import AgentTrace


class ExtractAgent:
    """
    Reads an uploaded balance sheet PDF, locates the face statement, and extracts KPIs.
    Delegates parsing to `balance_sheet_agent`; records every step in `AgentTrace`.
    """

    def __init__(self, agent_id: str = "extract") -> None:
        self.agent_id = agent_id
        self.title = "Extract Agent"

    def run(
        self,
        pdf_path: str | Path,
        *,
        use_llm: bool | None = None,
    ) -> tuple[BalanceSheetSnapshot, list[dict[str, Any]], AgentTrace]:
        trace = AgentTrace(agent_id=self.agent_id, title=self.title)
        flow: list[str] = []

        trace.append(f"Starting extraction from `{Path(pdf_path).name}`.")

        snapshot = extract_metrics_from_pdf(pdf_path, use_llm=use_llm, flow=flow)

        for line in flow:
            trace.append(line)

        kpi_rows = snapshot_to_kpi_rows(snapshot)
        trace.append(f"Structured {len(kpi_rows)} KPI row(s) for display.")

        return snapshot, kpi_rows, trace
