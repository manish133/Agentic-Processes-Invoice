"""Summarise agent: turns numeric snapshot into narrative balance sheet insights."""

from __future__ import annotations

import os
from typing import Any

from balance_sheet_agent.models import BalanceSheetSnapshot

from .types import AgentTrace


def _pct_change(cur: float | None, pri: float | None) -> str | None:
    if cur is None or pri is None:
        return None
    if pri == 0:
        return None
    return f"{(cur - pri) / abs(pri) * 100:+.1f}%"


def _rule_based_summary(snap: BalanceSheetSnapshot) -> str:
    p0, p1 = snap.reporting_periods
    lines: list[str] = [
        f"**Reporting periods:** {p0 or 'current'} vs {p1 or 'prior'} ({snap.unit or 'units as per source'}).",
        "",
        "### Highlights",
    ]

    ta, tl, te = snap.total_assets, snap.total_liabilities, snap.total_equity
    if ta and ta.current is not None and ta.prior is not None:
        ch = _pct_change(ta.current, ta.prior)
        lines.append(
            f"- **Total assets** moved from {ta.prior:,.2f} to {ta.current:,.2f}"
            + (f" ({ch} year-on-year)." if ch else "."),
        )
    if te and te.current is not None and te.prior is not None:
        ch = _pct_change(te.current, te.prior)
        lines.append(
            f"- **Total equity** changed from {te.prior:,.2f} to {te.current:,.2f}"
            + (f" ({ch})." if ch else "."),
        )
    if tl and tl.current is not None and tl.prior is not None:
        ch = _pct_change(tl.current, tl.prior)
        lines.append(
            f"- **Total liabilities** are {tl.current:,.2f} vs {tl.prior:,.2f}"
            + (f" ({ch})." if ch else "."),
        )

    lines.extend(["", "### Capital structure"])
    if snap.debt_to_equity and snap.debt_to_equity.current is not None:
        lines.append(
            f"- **Debt-to-equity (current period):** {snap.debt_to_equity.current:.4f}. "
            "Lower values generally imply less leverage; interpret with industry context.",
        )
    if snap.equity_ratio and snap.equity_ratio.current is not None:
        if snap.equity_ratio.prior is not None:
            lines.append(
                f"- **Equity ratio (equity / assets):** {snap.equity_ratio.current:.4f} "
                f"(prior: {snap.equity_ratio.prior:.4f}).",
            )
        else:
            lines.append(f"- **Equity ratio:** {snap.equity_ratio.current:.4f}.")
    elif snap.total_equity and snap.total_assets and snap.total_equity.current and snap.total_assets.current:
        er = snap.total_equity.current / snap.total_assets.current
        lines.append(f"- **Implied equity / assets:** {er:.4f}.")

    if snap.working_capital and snap.working_capital.current is not None:
        lines.append(
            f"- **Working capital (current):** {snap.working_capital.current:,.2f}.",
        )

    lines.extend(["", "### Data quality"])
    lines.append(
        "- Figures come from automated PDF parsing; validate against the original filing "
        "before making investment or lending decisions.",
    )

    return "\n".join(lines)


def _llm_summary(snap: BalanceSheetSnapshot, excerpt: str) -> str | None:
    try:
        from openai import OpenAI
    except ImportError:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None

    client = OpenAI(api_key=api_key)
    payload = snap.model_dump_json(indent=2)[:14000]
    prompt = (
        "You are a financial analyst. Using the JSON snapshot and optional excerpt, "
        "write a concise executive summary (markdown): sections ### Overview, "
        "### Liquidity and leverage, ### Year-over-year, ### Caveats. "
        "Stay factual; no investment advice. 300-500 words.\n\n"
        f"JSON:\n{payload}\n\nEXCERPT (optional):\n{excerpt[:8000]}"
    )
    try:
        r = client.chat.completions.create(
            model=os.environ.get("OPENAI_BALANCE_SHEET_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return None


class SummariseAgent:
    """Produces a markdown summary. Uses OpenAI when `use_llm=True` and a key is set."""

    def __init__(self, agent_id: str = "summarise") -> None:
        self.agent_id = agent_id
        self.title = "Summarise Agent"

    def run(
        self,
        snapshot: BalanceSheetSnapshot,
        *,
        source_text_excerpt: str = "",
        use_llm: bool | None = None,
    ) -> tuple[str, AgentTrace]:
        trace = AgentTrace(agent_id=self.agent_id, title=self.title)
        trace.append("Received structured snapshot; building narrative.")

        if use_llm is None:
            use_llm = bool(os.environ.get("OPENAI_API_KEY"))

        text: str
        if use_llm:
            trace.append("Attempting LLM summary (OPENAI_API_KEY present).")
            merged = _llm_summary(snapshot, source_text_excerpt)
            if merged:
                text = merged
                trace.append("LLM summary generated.")
            else:
                text = _rule_based_summary(snapshot)
                trace.append("LLM unavailable or failed; used rule-based summary.")
        else:
            text = _rule_based_summary(snapshot)
            trace.append("Rule-based summary (set OPENAI_API_KEY and enable LLM for richer text).")

        return text, trace
