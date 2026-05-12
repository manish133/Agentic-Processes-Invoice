"""Compile LangGraph workflow: supervisor → … → email."""

from __future__ import annotations

from typing import Any, Callable, Optional

from langgraph.graph import END, StateGraph

from agents.nodes import build_nodes
from agents.state import InvoiceState


def build_invoice_graph(
    log: Callable[[str], None],
    on_agent: Optional[Callable[[str], None]] = None,
) -> Any:
    nodes = build_nodes(log, on_agent)
    g = StateGraph(InvoiceState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    g.set_entry_point("supervisor")
    g.add_edge("supervisor", "extraction")
    g.add_edge("extraction", "screening")
    g.add_edge("screening", "validation")
    g.add_edge("validation", "matching")
    g.add_edge("matching", "exception")
    g.add_edge("exception", "email")
    g.add_edge("email", END)
    return g.compile()


def run_invoice_job(
    graph: Any,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Execute compiled graph with initial state."""
    init: dict[str, Any] = {
        "job_id": payload["job_id"],
        "master": payload.get("master") or {},
        "invoice_paths": payload.get("invoice_paths") or [],
        "exceptions": [],
    }
    return graph.invoke(init)
