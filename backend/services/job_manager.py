"""In-memory job registry + WebSocket fan-out for live logs."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import WebSocket

from models.database import get_job, init_db, upsert_job
from models.schemas import AgentName, AgentStateSnapshot, JobStatus, MasterData
from services.excel_loader import load_master_excel
from services.master_normalize import normalize_master_dict
from services.rules_parser import ensure_default_rules_file, load_rules_format_excel


@dataclass
class JobRuntime:
    job_id: str
    upload_dir: Path
    excel_path: Optional[Path] = None
    invoice_paths: list[Path] = field(default_factory=list)
    master: Optional[MasterData] = None
    phase: str = "uploaded"
    current_agent: Optional[str] = None
    logs: list[str] = field(default_factory=list)
    extractions: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    email_draft: dict[str, Any] = field(default_factory=dict)
    workflow_stopped: bool = False
    failed_agent: Optional[str] = None
    graph_result: Optional[dict[str, Any]] = None
    stage_outputs: dict[str, Any] = field(default_factory=dict)
    master_normalized: Optional[dict[str, Any]] = None
    rules_template_rows: list[dict[str, Any]] = field(default_factory=list)
    grok_api_key: str = ""
    ocr_backend: str = "auto"


class JobManager:
    def __init__(self) -> None:
        init_db()
        self._jobs: dict[str, JobRuntime] = {}
        self._ws: dict[str, list[WebSocket]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def create_job(self, upload_dir: Path) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = JobRuntime(job_id=job_id, upload_dir=upload_dir)
        upsert_job(job_id, phase="uploaded", logs=[])
        return job_id

    def get(self, job_id: str) -> Optional[JobRuntime]:
        return self._jobs.get(job_id)

    def attach_files(
        self,
        job_id: str,
        excel_path: Optional[Path],
        invoice_paths: list[Path],
        rules_format_path: Optional[Path] = None,
        grok_api_key: str = "",
        ocr_backend: str = "auto",
    ) -> None:
        jr = self._jobs[job_id]
        jr.excel_path = excel_path
        jr.invoice_paths = invoice_paths
        if excel_path and excel_path.exists():
            jr.master = load_master_excel(excel_path)
            jr.master_normalized = normalize_master_dict(jr.master.model_dump())
        else:
            jr.master_normalized = None
        rp = rules_format_path
        if rp and rp.exists():
            jr.rules_template_rows = load_rules_format_excel(rp)
        else:
            default_rf = ensure_default_rules_file()
            jr.rules_template_rows = load_rules_format_excel(default_rf) if default_rf.exists() else []
        jr.grok_api_key = (grok_api_key or "").strip()
        jr.ocr_backend = (ocr_backend or "auto").strip().lower() or "auto"
        jr.phase = "ready"
        upsert_job(
            job_id,
            phase="ready",
            master=jr.master.model_dump() if jr.master else {},
        )

    async def register_ws(self, job_id: str, ws: WebSocket) -> None:
        """Append an already-accepted WebSocket to the job fan-out list."""
        async with self._lock:
            self._ws[job_id].append(ws)

    async def unregister_ws(self, job_id: str, ws: WebSocket) -> None:
        async with self._lock:
            if job_id in self._ws and ws in self._ws[job_id]:
                self._ws[job_id].remove(ws)

    async def broadcast(self, job_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            clients = list(self._ws.get(job_id, []))
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        async with self._lock:
            for ws in dead:
                if ws in self._ws[job_id]:
                    self._ws[job_id].remove(ws)

    def append_log(self, job_id: str, line: str) -> None:
        jr = self._jobs.get(job_id)
        if not jr:
            return
        ts = datetime.utcnow().strftime("%H:%M:%S")
        entry = f"[{ts}] {line}"
        jr.logs.append(entry)
        upsert_job(job_id, logs=jr.logs)

    def build_agent_snapshots(
        self,
        current: Optional[str],
        failed: Optional[str],
        phase: str = "",
    ) -> list[AgentStateSnapshot]:
        order = [a.value for a in (
            AgentName.SUPERVISOR,
            AgentName.EXTRACTION,
            AgentName.SCREENING,
            AgentName.VALIDATION,
            AgentName.MATCHING,
            AgentName.EXCEPTION,
            AgentName.EMAIL,
        )]
        if phase == "completed" and not failed:
            return [AgentStateSnapshot(name=n, status="done") for n in order]

        fail_idx = order.index(failed) if failed in order else -1
        cur_idx = order.index(current) if current in order else -1
        snaps: list[AgentStateSnapshot] = []
        for i, name in enumerate(order):
            if failed and name == failed:
                status = "failed"
            elif fail_idx >= 0 and i > fail_idx:
                status = "waiting"
            elif fail_idx >= 0 and i < fail_idx:
                status = "done"
            elif current == name:
                status = "running"
            elif cur_idx >= 0 and i < cur_idx:
                status = "done"
            elif cur_idx >= 0 and i > cur_idx:
                status = "waiting"
            else:
                status = "waiting"
            snaps.append(AgentStateSnapshot(name=name, status=status))
        return snaps

    def status_model(self, job_id: str) -> JobStatus:
        jr = self._jobs.get(job_id)
        if not jr:
            row = get_job(job_id)
            if not row:
                raise KeyError(job_id)
            return JobStatus(
                job_id=job_id,
                phase=row.get("phase", "unknown"),
                current_agent=row.get("current_agent"),
                agents=[],
                logs=row.get("logs") or [],
                workflow_stopped=bool(row.get("workflow_stopped")),
                failed_agent=row.get("failed_agent"),
                stage_outputs={},
            )
        return JobStatus(
            job_id=job_id,
            phase=jr.phase,
            current_agent=jr.current_agent,
            agents=self.build_agent_snapshots(jr.current_agent, jr.failed_agent, jr.phase),
            logs=jr.logs,
            workflow_stopped=jr.workflow_stopped,
            failed_agent=jr.failed_agent,
            stage_outputs=jr.stage_outputs or {},
        )


job_manager = JobManager()
