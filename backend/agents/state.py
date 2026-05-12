"""LangGraph shared state for invoice processing."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict


def merge_stage_outputs(old: Optional[dict[str, Any]], new: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Shallow merge per-agent report blobs (extraction, screening, ...)."""
    out = dict(old or {})
    if new:
        out.update(new)
    return out


class InvoiceState(TypedDict, total=False):
    job_id: str
    master: dict[str, Any]
    rules_template_rows: list[dict[str, Any]]
    invoice_paths: list[str]
    extractions: list[dict[str, Any]]
    po_totals: dict[str, float]
    exceptions: Annotated[list[dict[str, Any]], operator.add]
    critical_stop: bool
    failed_agent: Optional[str]
    halt_pipeline: bool
    email_draft: dict[str, Any]
    # One key per agent stage: supervisor, extraction, screening, validation, matching, exception, email
    stage_outputs: Annotated[dict[str, Any], merge_stage_outputs]
    # Internal-only cache for cross-stage LLM reuse (must not be exposed in stage_outputs/status payloads)
    grok_stage_bundle: dict[str, Any]
