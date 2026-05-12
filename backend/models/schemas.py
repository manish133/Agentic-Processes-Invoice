"""Pydantic schemas for invoices, masters, and API responses."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class InvoiceItem(BaseModel):
    name: str = ""
    qty: float = 0.0
    price: float = 0.0
    tax: float = 0.0


class ExtractedInvoice(BaseModel):
    """Structured output from Extraction Agent (mock OCR)."""

    invoice_number: str = ""
    invoice_date: Optional[str] = None  # ISO date string
    vendor_name: str = ""
    vendor_code: str = ""
    gst_number: str = ""
    po_number: str = ""
    currency: str = "INR"
    items: list[InvoiceItem] = Field(default_factory=list)
    total_amount: float = 0.0
    stamp_present: bool = False
    source_filename: str = ""


class MasterData(BaseModel):
    """Loaded from Excel workbook."""

    po_master: list[dict[str, Any]] = Field(default_factory=list)
    vendor_master: list[dict[str, Any]] = Field(default_factory=list)
    grn_master: list[dict[str, Any]] = Field(default_factory=list)
    historical_invoices: list[dict[str, Any]] = Field(default_factory=list)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ExceptionRecord(BaseModel):
    code: str
    message: str
    severity: Severity = Severity.WARNING
    field: Optional[str] = None
    document: Optional[str] = None


class EmailDraft(BaseModel):
    subject: str = ""
    body: str = ""
    to_placeholder: str = "approver@qsr.example.com"


class AgentName(str, Enum):
    SUPERVISOR = "supervisor"
    EXTRACTION = "extraction"
    SCREENING = "screening"
    VALIDATION = "validation"
    MATCHING = "matching"
    EXCEPTION = "exception"
    EMAIL = "email"


class AgentStateSnapshot(BaseModel):
    name: str
    status: str  # idle | running | done | failed


class JobStatus(BaseModel):
    job_id: str
    phase: str = "uploaded"
    current_agent: Optional[str] = None
    agents: list[AgentStateSnapshot] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    workflow_stopped: bool = False
    failed_agent: Optional[str] = None
    # Per-agent structured output for UI (extraction, screening, validation, matching, …)
    stage_outputs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
