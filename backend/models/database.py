"""SQLite persistence for jobs and audit trail."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "invoice_jobs.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                phase TEXT DEFAULT 'uploaded',
                current_agent TEXT,
                workflow_stopped INTEGER DEFAULT 0,
                failed_agent TEXT,
                master_json TEXT,
                extractions_json TEXT,
                exceptions_json TEXT,
                email_json TEXT,
                logs_json TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def upsert_job(
    job_id: str,
    *,
    phase: str = "uploaded",
    current_agent: Optional[str] = None,
    workflow_stopped: bool = False,
    failed_agent: Optional[str] = None,
    master: Optional[dict[str, Any]] = None,
    extractions: Optional[list[Any]] = None,
    exceptions: Optional[list[Any]] = None,
    email: Optional[dict[str, Any]] = None,
    logs: Optional[list[str]] = None,
) -> None:
    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        row = conn.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        payload = {
            "phase": phase,
            "current_agent": current_agent,
            "workflow_stopped": 1 if workflow_stopped else 0,
            "failed_agent": failed_agent,
            "master_json": json.dumps(master) if master is not None else None,
            "extractions_json": json.dumps(extractions) if extractions is not None else None,
            "exceptions_json": json.dumps(exceptions) if exceptions is not None else None,
            "email_json": json.dumps(email) if email is not None else None,
            "logs_json": json.dumps(logs) if logs is not None else None,
            "updated_at": now,
        }
        if row is None:
            conn.execute(
                """
                INSERT INTO jobs (job_id, phase, current_agent, workflow_stopped, failed_agent,
                    master_json, extractions_json, exceptions_json, email_json, logs_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    payload["phase"],
                    payload["current_agent"],
                    payload["workflow_stopped"],
                    payload["failed_agent"],
                    payload["master_json"],
                    payload["extractions_json"],
                    payload["exceptions_json"],
                    payload["email_json"],
                    payload["logs_json"],
                    now,
                    now,
                ),
            )
        else:
            sets = []
            vals: list[Any] = []
            for k, v in payload.items():
                if v is not None:
                    sets.append(f"{k} = ?")
                    vals.append(v)
            vals.append(job_id)
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", vals)
        conn.commit()
    finally:
        conn.close()


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("master_json", "extractions_json", "exceptions_json", "email_json", "logs_json"):
            if d.get(key):
                d[key.replace("_json", "")] = json.loads(d[key])
        return d
    finally:
        conn.close()
