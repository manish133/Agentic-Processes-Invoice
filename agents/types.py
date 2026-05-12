"""Shared types for agent traces."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AgentTrace:
    """One agent run: human-readable steps for UI display."""

    agent_id: str
    title: str
    lines: list[str] = field(default_factory=list)

    def append(self, line: str) -> None:
        self.lines.append(line)
