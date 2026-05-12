"""PDF agent: renders the insights summary (and KPI table) into a PDF on disk."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .types import AgentTrace


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class PDFAgent:
    """
    Writes a PDF under the Agent Project directory (default: `balance_sheet_exports/`).
    """

    def __init__(self, agent_id: str = "pdf") -> None:
        self.agent_id = agent_id
        self.title = "PDF Converter Agent"

    def run(
        self,
        summary_markdown: str,
        kpi_rows: list[dict[str, Any]],
        *,
        output_dir: Path | None = None,
        filename_prefix: str = "balance_sheet_summary",
    ) -> tuple[Path, AgentTrace]:
        trace = AgentTrace(agent_id=self.agent_id, title=self.title)
        base = output_dir if output_dir is not None else _project_root() / "balance_sheet_exports"
        base.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = base / f"{filename_prefix}_{ts}.pdf"
        trace.append(f"Target file: `{out_path}`")

        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            "Body",
            parent=styles["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=6,
        )
        h1 = ParagraphStyle(
            "H1",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=12,
        )

        story: list[Any] = []
        story.append(Paragraph("Balance sheet — executive summary", h1))
        story.append(
            Paragraph(
                f"Generated (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
                styles["Normal"],
            ),
        )
        story.append(Spacer(1, 0.4 * cm))

        # Strip minimal markdown for reportlab Paragraph (bold **text**)
        plain_summary = summary_markdown.replace("### ", "").replace("**", "")
        for block in plain_summary.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            story.append(Paragraph(block.replace("\n", "<br/>"), body))

        story.append(Spacer(1, 0.6 * cm))
        story.append(Paragraph("Key metrics", h1))

        if kpi_rows:
            keys = list(kpi_rows[0].keys())
            data: list[list[str]] = [keys]
            for row in kpi_rows:
                data.append([_cell(k, row.get(k)) for k in keys])
            tbl = Table(data, repeatRows=1)
            tbl.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5282")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ],
                ),
            )
            story.append(tbl)
        else:
            story.append(Paragraph("No KPI rows available.", body))

        doc = SimpleDocTemplate(
            str(out_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        doc.build(story)
        trace.append(f"PDF written successfully ({out_path.stat().st_size // 1024} KB).")

        return out_path, trace


def _cell(key: str, v: Any) -> str:
    if v is None:
        return "—"
    if key == "Growth %" and isinstance(v, (int, float)):
        sign = "+" if v > 0 else ""
        return f"{sign}{v:.2f}%"
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return str(v)
