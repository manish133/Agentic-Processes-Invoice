"""Build tabular KPI rows from a balance sheet snapshot."""

from __future__ import annotations

from balance_sheet_agent.models import BalanceSheetSnapshot


def _growth_pct(cur: float | None, pri: float | None) -> float | None:
    """Year-over-year style change: (current − prior) / |prior| × 100. None if not defined."""
    if cur is None or pri is None:
        return None
    if pri == 0:
        return None
    return (cur - pri) / abs(pri) * 100.0


def snapshot_to_kpi_rows(snapshot: BalanceSheetSnapshot) -> list[dict[str, str | float | None]]:
    """Rows suitable for `st.dataframe` / CSV: metric name, period values, and % growth."""

    p0, p1 = snapshot.reporting_periods
    col_cur = p0 or "Current"
    col_pri = p1 or "Prior"
    unit_global = snapshot.unit or ""

    rows: list[dict[str, str | float | None]] = []

    def add_metric(name: str, cur: float | None, pri: float | None, unit: str) -> None:
        rows.append(
            {
                "Metric": name,
                col_cur: cur,
                col_pri: pri,
                "Growth %": _growth_pct(cur, pri),
                "Unit": unit or unit_global,
            }
        )

    if snapshot.total_assets:
        add_metric(
            "Total assets",
            snapshot.total_assets.current,
            snapshot.total_assets.prior,
            snapshot.total_assets.unit or unit_global,
        )
    if snapshot.total_liabilities:
        add_metric(
            "Total liabilities",
            snapshot.total_liabilities.current,
            snapshot.total_liabilities.prior,
            snapshot.total_liabilities.unit or unit_global,
        )
    if snapshot.total_equity:
        add_metric(
            "Total equity",
            snapshot.total_equity.current,
            snapshot.total_equity.prior,
            snapshot.total_equity.unit or unit_global,
        )
    if snapshot.working_capital:
        add_metric(
            "Working capital",
            snapshot.working_capital.current,
            snapshot.working_capital.prior,
            snapshot.working_capital.unit or unit_global,
        )
    if snapshot.debt_to_equity:
        add_metric(
            "Debt-to-equity",
            snapshot.debt_to_equity.current,
            snapshot.debt_to_equity.prior,
            snapshot.debt_to_equity.unit or "ratio",
        )
    if snapshot.equity_ratio:
        add_metric(
            "Equity ratio (equity / assets)",
            snapshot.equity_ratio.current,
            snapshot.equity_ratio.prior,
            snapshot.equity_ratio.unit or "ratio",
        )

    for item in snapshot.line_items:
        add_metric(
            item.name,
            item.values.current,
            item.values.prior,
            item.values.unit or unit_global,
        )

    return rows
