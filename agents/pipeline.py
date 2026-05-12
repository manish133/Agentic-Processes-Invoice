"""Orchestrates extract → summarise → optional PDF agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from balance_sheet_agent.models import BalanceSheetSnapshot

from .extract_agent import ExtractAgent
from .pdf_agent import PDFAgent
from .summarise_agent import SummariseAgent
from .types import AgentTrace


@dataclass
class PipelineResult:
    snapshot: BalanceSheetSnapshot
    kpi_rows: list[dict[str, Any]]
    summary_markdown: str
    pdf_path: Path | None
    traces: list[AgentTrace] = field(default_factory=list)
    error: str | None = None


def run_pipeline(
    pdf_path: str | Path,
    *,
    use_llm_extract: bool | None = None,
    use_llm_summary: bool | None = None,
    write_pdf: bool = True,
    pdf_output_dir: Path | None = None,
    source_text_excerpt: str = "",
) -> PipelineResult:
    """
    Run all three agents in sequence. On extraction failure, `error` is set and the rest is skipped.
    """

    traces: list[AgentTrace] = []
    extract = ExtractAgent()
    summarise = SummariseAgent()
    pdf_writer = PDFAgent()

    try:
        snapshot, kpi_rows, t1 = extract.run(pdf_path, use_llm=use_llm_extract)
    except Exception as e:
        t_err = AgentTrace(agent_id="extract", title="Extract Agent")
        t_err.append(f"Failed: {e}")
        traces.append(t_err)
        return PipelineResult(
            snapshot=BalanceSheetSnapshot(),
            kpi_rows=[],
            summary_markdown="",
            pdf_path=None,
            traces=traces,
            error=str(e),
        )

    traces.append(t1)

    summary, t2 = summarise.run(
        snapshot,
        source_text_excerpt=source_text_excerpt,
        use_llm=use_llm_summary,
    )
    traces.append(t2)

    pdf_path: Path | None = None
    if write_pdf:
        pdf_path, t3 = pdf_writer.run(summary, kpi_rows, output_dir=pdf_output_dir)
        traces.append(t3)
    else:
        t_skip = AgentTrace(agent_id="pdf", title="PDF Converter Agent")
        t_skip.append("Skipped (write_pdf=False).")
        traces.append(t_skip)

    return PipelineResult(
        snapshot=snapshot,
        kpi_rows=kpi_rows,
        summary_markdown=summary,
        pdf_path=pdf_path,
        traces=traces,
        error=None,
    )
