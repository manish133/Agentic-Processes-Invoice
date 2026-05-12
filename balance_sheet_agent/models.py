"""Structured output for balance-sheet extraction."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PeriodValues(BaseModel):
    """Numeric values for two reporting dates (e.g. current and prior year)."""

    label_current: str = Field(
        default="current",
        description="Column label for the latest period (e.g. 31 March 2025).",
    )
    label_prior: str = Field(
        default="prior",
        description="Column label for the comparison period.",
    )
    current: float | None = None
    prior: float | None = None
    unit: str = Field(
        default="",
        description="Unit from the document, e.g. INR lacs, USD thousands.",
    )


class LineItem(BaseModel):
    """A single row from the statement with optional note reference."""

    name: str
    note: str | None = None
    values: PeriodValues


class BalanceSheetSnapshot(BaseModel):
    """Key totals and aggregates typically needed from a balance sheet."""

    currency: str = "INR"
    unit: str = ""
    reporting_periods: tuple[str, str] = ("", "")

    total_assets: PeriodValues | None = None
    total_liabilities: PeriodValues | None = None
    total_equity: PeriodValues | None = None

    # Common asset buckets (names vary by standard — store what we find)
    line_items: list[LineItem] = Field(default_factory=list)

    # Derived (computed after parse; same unit as source)
    working_capital: PeriodValues | None = None
    debt_to_equity: PeriodValues | None = None
    equity_ratio: PeriodValues | None = None


def snapshot_to_flat_dict(snapshot: BalanceSheetSnapshot) -> dict[str, Any]:
    """Flatten key numbers for export or APIs."""

    def pv(prefix: str, pv_: PeriodValues | None) -> dict[str, Any]:
        if pv_ is None:
            return {}
        return {
            f"{prefix}_current": pv_.current,
            f"{prefix}_prior": pv_.prior,
            f"{prefix}_unit": pv_.unit or snapshot.unit,
        }

    out: dict[str, Any] = {
        "currency": snapshot.currency,
        "unit": snapshot.unit,
        "reporting_periods": snapshot.reporting_periods,
    }
    out.update(pv("total_assets", snapshot.total_assets))
    out.update(pv("total_liabilities", snapshot.total_liabilities))
    out.update(pv("total_equity", snapshot.total_equity))
    out.update(pv("working_capital", snapshot.working_capital))
    out.update(pv("debt_to_equity", snapshot.debt_to_equity))
    out.update(pv("equity_ratio", snapshot.equity_ratio))
    return out
