"""
FastAPI entrypoint: upload masters + invoices, run LangGraph pipeline, WebSocket logs.
"""

from __future__ import annotations

import asyncio
import sys
import os
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.graph import build_invoice_graph  # noqa: E402
from models.database import upsert_job  # noqa: E402
from services.excel_loader import build_sample_excel  # noqa: E402
from services.grok_ocr import use_grok_api_key  # noqa: E402
from services.job_manager import job_manager  # noqa: E402
from services.mock_ocr import use_ocr_backend  # noqa: E402

SAMPLE_EXCEL = ROOT / "data" / "sample_masters.xlsx"
executor = ThreadPoolExecutor(max_workers=4)


def _ensure_sample_data() -> None:
    if not SAMPLE_EXCEL.exists():
        build_sample_excel(SAMPLE_EXCEL)
    try:
        from services.rules_parser import ensure_default_rules_file

        ensure_default_rules_file()
    except OSError:
        pass


app = FastAPI(title="QSR Invoice Multi-Agent API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    _ensure_sample_data()


class ExecuteBody(BaseModel):
    job_id: str


def _broadcast_safe(job_id: str, payload: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
    try:
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(job_manager.broadcast(job_id, payload), loop)
    except RuntimeError:
        pass


def _sync_run_graph(job_id: str, loop: asyncio.AbstractEventLoop) -> None:
    jr = job_manager.get(job_id)
    if not jr or not jr.master:
        return

    def log(line: str) -> None:
        job_manager.append_log(job_id, line)
        _broadcast_safe(job_id, {"type": "log", "message": line}, loop)

    def on_agent(name: str) -> None:
        jr_inner = job_manager.get(job_id)
        if jr_inner:
            jr_inner.current_agent = name
            jr_inner.phase = "running"
        upsert_job(job_id, phase="running", current_agent=name)
        _broadcast_safe(job_id, {"type": "agent", "agent": name, "status": "running"}, loop)

    graph = build_invoice_graph(log, on_agent)
    init = {
        "job_id": job_id,
        "master": jr.master_normalized or (jr.master.model_dump() if jr.master else {}),
        "rules_template_rows": jr.rules_template_rows or [],
        "invoice_paths": [str(p) for p in jr.invoice_paths],
        "exceptions": [],
        "stage_outputs": {},
    }

    try:
        jr.phase = "running"
        # Use per-job key from upload form if provided; otherwise env fallback remains.
        with use_grok_api_key(jr.grok_api_key):
            with use_ocr_backend(jr.ocr_backend):
                final: dict[str, Any] = graph.invoke(init)
        jr.graph_result = final
        exc_list = (final or {}).get("exceptions") or []
        crit = bool((final or {}).get("critical_stop"))
        jr.extractions = (final or {}).get("extractions") or []
        jr.exceptions = exc_list
        jr.email_draft = (final or {}).get("email_draft") or {}
        jr.stage_outputs = (final or {}).get("stage_outputs") or {}
        jr.workflow_stopped = crit
        jr.failed_agent = (final or {}).get("failed_agent")
        jr.phase = "completed" if not crit else "stopped"
        jr.current_agent = None
        upsert_job(
            job_id,
            phase=jr.phase,
            current_agent=None,
            workflow_stopped=jr.workflow_stopped,
            failed_agent=jr.failed_agent,
            extractions=jr.extractions,
            exceptions=jr.exceptions,
            email=jr.email_draft,
            logs=jr.logs,
        )
        _broadcast_safe(job_id, {"type": "complete", "critical": crit}, loop)
    except Exception as e:  # noqa: BLE001
        jr.phase = "error"
        jr.failed_agent = jr.current_agent or "supervisor"
        jr.workflow_stopped = True
        job_manager.append_log(job_id, f"ERROR: {e}")
        upsert_job(job_id, phase="error", failed_agent=jr.failed_agent, logs=jr.logs)
        _broadcast_safe(job_id, {"type": "error", "message": str(e)}, loop)


async def _run_graph_job(job_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(executor, _sync_run_graph, job_id, loop)


def _safe_upload_name(index: int, filename: str | None) -> str:
    """Avoid collisions when multiple PNGs share the same name (e.g. Screenshot.png)."""
    base = (filename or "invoice").replace("\\", "_").replace("/", "_").strip() or "invoice.bin"
    return f"{index:02d}_{base}"


@app.post("/upload")
async def upload(
    invoices: Annotated[list[UploadFile], File(description="One or more invoice PDFs or images")],
    excel: Optional[UploadFile] = File(default=None),
    rules_format: Optional[UploadFile] = File(default=None),
    grok_api_key: str = Form(default=""),
    ocr_backend: str = Form(default="auto"),
) -> dict[str, Any]:
    """Store Excel masters + invoice PDFs/images under uploads/{job_id}/."""
    _ensure_sample_data()
    job_id = job_manager.create_job(ROOT / "uploads")
    jr = job_manager.get(job_id)
    assert jr is not None
    up = jr.upload_dir
    up.mkdir(parents=True, exist_ok=True)

    excel_path: Optional[Path] = None
    if excel and excel.filename:
        dest = up / _safe_upload_name(0, excel.filename)
        content = await excel.read()
        dest.write_bytes(content)
        excel_path = dest
    else:
        # Demo default masters if user uploads invoices only
        dest = up / "sample_masters.xlsx"
        dest.write_bytes(SAMPLE_EXCEL.read_bytes())
        excel_path = dest

    inv_paths: list[Path] = []
    for i, inv in enumerate(invoices):
        if not inv.filename:
            continue
        p = up / _safe_upload_name(i + 1, inv.filename)
        p.write_bytes(await inv.read())
        inv_paths.append(p)

    if not inv_paths:
        raise HTTPException(400, "At least one invoice file is required")

    rules_path: Optional[Path] = None
    if rules_format and rules_format.filename:
        rules_path = up / _safe_upload_name(50, rules_format.filename)
        rules_path.write_bytes(await rules_format.read())

    job_manager.attach_files(
        job_id,
        excel_path,
        inv_paths,
        rules_path,
        grok_api_key=grok_api_key,
        ocr_backend=ocr_backend,
    )
    return {"job_id": job_id, "excel": str(excel_path), "invoices": [str(p) for p in inv_paths], "rules": str(rules_path) if rules_path else None}


@app.post("/execute")
async def execute(body: ExecuteBody, background: BackgroundTasks) -> dict[str, Any]:
    jr = job_manager.get(body.job_id)
    if not jr:
        raise HTTPException(404, "job not found")
    if jr.phase in ("running", "completed", "stopped", "error"):
        return {"started": False, "job_id": body.job_id, "reason": f"job already {jr.phase}"}
    background.add_task(_run_graph_job, body.job_id)
    return {"started": True, "job_id": body.job_id}


@app.get("/status/{job_id}")
async def status(job_id: str) -> dict[str, Any]:
    try:
        st = job_manager.status_model(job_id)
        return st.model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, "job not found") from None


@app.get("/exceptions/{job_id}")
async def exceptions(job_id: str) -> dict[str, Any]:
    jr = job_manager.get(job_id)
    if not jr:
        raise HTTPException(404, "job not found")
    return {
        "job_id": job_id,
        "exceptions": jr.exceptions,
        "email_draft": jr.email_draft,
        "workflow_stopped": jr.workflow_stopped,
        "failed_agent": jr.failed_agent,
        "documents": [str(p) for p in jr.invoice_paths],
    }


class ApproveBody(BaseModel):
    note: str = ""


class SendEmailBody(BaseModel):
    to_email: str = ""


def _send_email_via_smtp(*, to_email: str, subject: str, body: str) -> None:
    host = (os.getenv("SMTP_HOST") or "").strip()
    port_raw = (os.getenv("SMTP_PORT") or "587").strip()
    user = (os.getenv("SMTP_USER") or "").strip()
    pwd = (os.getenv("SMTP_PASS") or "").strip()
    from_addr = (os.getenv("SMTP_FROM") or user).strip()
    use_tls = (os.getenv("SMTP_USE_TLS") or "true").strip().lower() not in {"0", "false", "no"}
    use_ssl = (os.getenv("SMTP_USE_SSL") or "false").strip().lower() in {"1", "true", "yes"}
    if not host:
        raise RuntimeError("SMTP_HOST is not configured")
    if not from_addr:
        raise RuntimeError("SMTP_FROM (or SMTP_USER) is required")
    if not to_email.strip():
        raise RuntimeError("Recipient email is empty")
    try:
        port = int(port_raw or "587")
    except ValueError:
        port = 587

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_email.strip()
    msg["Subject"] = subject or "Invoice validation output"
    msg.set_content(body or "")

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, timeout=45) as server:
            if user:
                server.login(user, pwd)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=45) as server:
            if use_tls:
                server.starttls()
            if user:
                server.login(user, pwd)
            server.send_message(msg)


@app.post("/exceptions/{job_id}/approve")
async def approve_manual(job_id: str, body: ApproveBody) -> dict[str, str]:
    job_manager.append_log(job_id, f"Manual approval recorded. Note: {body.note}")
    return {"status": "approved"}


@app.post("/exceptions/{job_id}/email")
async def send_email(job_id: str, body: Optional[SendEmailBody] = None) -> dict[str, str]:
    jr = job_manager.get(job_id)
    if not jr:
        raise HTTPException(404, "job not found")
    to_email = ((body.to_email if body else "") or os.getenv("SMTP_TO") or "").strip()
    if not to_email:
        raise HTTPException(400, "Recipient missing. Provide to_email in request body or set SMTP_TO env var.")

    draft = jr.email_draft or {}
    subject = str(draft.get("subject", "Invoice validation output")).strip() or "Invoice validation output"
    content = str(draft.get("body", "")).strip()
    if not content:
        exc = jr.exceptions or []
        lines = [f"- [{x.get('code')}] {x.get('message')} ({x.get('severity')})" for x in exc]
        content = "Exception summary:\n" + ("\n".join(lines) if lines else "No issues.")
    try:
        _send_email_via_smtp(to_email=to_email, subject=subject, body=content)
    except Exception as e:  # noqa: BLE001
        job_manager.append_log(job_id, f"Email send failed: {e}")
        raise HTTPException(500, f"Email send failed: {e}") from e

    job_manager.append_log(job_id, f"Email sent to {to_email}")
    return {"status": "sent", "to": to_email, "subject": subject}


@app.websocket("/ws/logs/{job_id}")
async def ws_logs(ws: WebSocket, job_id: str) -> None:
    # Must accept the socket before close/send; closing without accept breaks some clients/proxies.
    await ws.accept()
    if not job_manager.get(job_id):
        await ws.close(code=1008)
        return
    await job_manager.register_ws(job_id, ws)
    try:
        await ws.send_json({"type": "hello", "job_id": job_id})
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await job_manager.unregister_ws(job_id, ws)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
