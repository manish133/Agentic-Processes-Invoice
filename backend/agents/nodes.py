"""LangGraph node functions — one per agent role."""

from __future__ import annotations

import time
import os
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

from agents.report_utils import field_confidence, wrap_fields
from models.schemas import ExceptionRecord, Severity
from services.grok_validation import (
    RULE_COLUMNS,
    evaluate_all_stages_with_grok,
    evaluate_stage_with_grok,
    exception_panel_with_grok,
    normalize_rule_results,
)
from services.mock_ocr import extract_invoice_file_with_engine, invoice_to_dict

# Keep stage transitions visible, but do not force long waits.
try:
    STEP_DELAY_SEC = max(0.0, float(os.getenv("AGENT_STEP_DELAY_SEC", "0.0")))
except ValueError:
    STEP_DELAY_SEC = 0.0

_EXTRACT_FIELDS = (
    "invoice_number",
    "invoice_date",
    "gst_number",
    "vendor_name",
    "po_number",
    "vendor_code",
    "currency",
)


def _pause() -> None:
    time.sleep(STEP_DELAY_SEC)


def _extract_stage_from_bundle(
    bundle: dict[str, Any],
    stage: str,
    rules_template_rows: list[dict[str, Any]],
) -> tuple[list[ExceptionRecord], dict[str, Any], dict[str, Any]]:
    stage_obj = bundle.get(stage) if isinstance(bundle, dict) else {}
    if not isinstance(stage_obj, dict):
        stage_obj = {}
    excs = stage_obj.get("exceptions")
    summary = stage_obj.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    llm_excs = []
    if isinstance(excs, list):
        for x in excs:
            if isinstance(x, dict):
                llm_excs.append(
                    ExceptionRecord(
                        code=str(x.get("code", f"LLM_{stage.upper()}")).strip() or f"LLM_{stage.upper()}",
                        message=str(x.get("message", f"{stage} note from LLM")).strip() or f"{stage} note from LLM",
                        severity=Severity(str(x.get("severity", "warning")).lower())
                        if str(x.get("severity", "")).lower() in {"critical", "warning", "info"}
                        else Severity.WARNING,
                        field=str(x.get("field", "")).strip() or None,
                        document=str(x.get("document", "")).strip() or None,
                    )
                )
    report = {
        "engine": "grok",
        "summary": summary,
    }
    rules_payload = {
        "columns": RULE_COLUMNS,
        "rows": [],
        "three_way_summary": [],
    }
    if stage == "matching":
        tw = stage_obj.get("three_way_summary")
        rules_payload = {
            "columns": RULE_COLUMNS,
            "rows": normalize_rule_results(stage_obj.get("rule_results")),
            "three_way_summary": tw if isinstance(tw, list) else [],
        }
    return llm_excs, report, rules_payload


def _exc_to_dict(e: ExceptionRecord, agent: str) -> dict[str, Any]:
    d = e.model_dump(mode="json")
    d["agent"] = agent
    return d


def _build_extraction_report(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    docs: list[dict[str, Any]] = []
    engine_counts: Counter[str] = Counter()
    for inv in extractions:
        eng = str(inv.get("ocr_engine_used", "unknown") or "unknown").lower()
        engine_counts[eng] += 1
        fields = wrap_fields(inv, _EXTRACT_FIELDS)
        items_out: list[dict[str, Any]] = []
        for it in inv.get("items") or []:
            nm = str(it.get("name", ""))
            items_out.append(
                {
                    "name": nm,
                    "qty": it.get("qty"),
                    "price": it.get("price"),
                    "tax": it.get("tax"),
                    "line_confidence": field_confidence(nm),
                }
            )
        confs = [v["confidence"] for v in fields.values() if isinstance(v, dict) and "confidence" in v]
        overall = round(sum(confs) / max(len(confs), 1), 3) if confs else 0.0
        docs.append(
            {
                "filename": inv.get("source_filename"),
                "fields": fields,
                "items": items_out,
                "total_amount": inv.get("total_amount"),
                "stamp_present": inv.get("stamp_present"),
                "ocr_engine_used": eng,
                "overall_confidence": overall,
            }
        )
    table = _build_extraction_table(extractions)
    return {
        "documents": docs,
        "table": table,
        "engine": dict(engine_counts),
        "note": "Confidence is heuristic (hash-based) for demo.",
    }


def _build_extraction_table(extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inv in extractions:
        doc = str(inv.get("source_filename", ""))
        fields = wrap_fields(inv, _EXTRACT_FIELDS)
        for fn, meta in fields.items():
            rows.append(
                {
                    "document": doc,
                    "field": fn,
                    "value": meta.get("value", "") if isinstance(meta, dict) else meta,
                    "confidence": meta.get("confidence", "") if isinstance(meta, dict) else "",
                }
            )
        for i, it in enumerate(inv.get("items") or []):
            rows.append(
                {
                    "document": doc,
                    "field": f"Line {i + 1}",
                    "value": f"{it.get('name')} | qty {it.get('qty')} | rate {it.get('price')} | tax {it.get('tax')}",
                    "confidence": field_confidence(str(it.get("name", ""))),
                }
            )
        rows.append(
            {
                "document": doc,
                "field": "ocr_engine_used",
                "value": inv.get("ocr_engine_used", "unknown"),
                "confidence": "",
            }
        )
        if inv.get("ocr_engine_note"):
            rows.append(
                {
                    "document": doc,
                    "field": "ocr_engine_note",
                    "value": inv.get("ocr_engine_note", ""),
                    "confidence": "",
                }
            )
        rows.append({"document": doc, "field": "total_amount", "value": inv.get("total_amount"), "confidence": ""})
        rows.append({"document": doc, "field": "stamp_present", "value": inv.get("stamp_present"), "confidence": ""})
    return rows


def make_supervisor_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def supervisor(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("supervisor")
        log("Supervisor: job acknowledged; initializing pipeline state")
        rep = {
            "initialized": True,
            "invoice_files": len(state.get("invoice_paths") or []),
            "message": "Orchestration started — downstream agents will run in sequence.",
        }
        _pause()
        return {
            "halt_pipeline": False,
            "critical_stop": False,
            "failed_agent": None,
            "exceptions": [],
            "po_totals": {},
            "email_draft": {},
            "stage_outputs": {"supervisor": rep},
        }

    return supervisor


def make_extraction_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def _extract_one(p: str) -> dict[str, Any]:
        path = Path(p)
        inv, engine, engine_note = extract_invoice_file_with_engine(path)
        inv_d = invoice_to_dict(inv)
        inv_d["ocr_engine_used"] = engine
        inv_d["ocr_engine_note"] = engine_note
        return inv_d

    def extraction(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("extraction")
        paths = state.get("invoice_paths") or []
        out: list[dict[str, Any]] = []
        if not paths:
            log("Extraction: no invoice paths provided")
        else:
            # Sequential extraction only (same order as upload) — keeps the pipeline deterministic
            # and matches the strictly sequential LangGraph agent chain.
            for p in paths:
                path = Path(p)
                try:
                    inv_d = _extract_one(p)
                except Exception as e:  # noqa: BLE001
                    log(f"Extraction: failed {path.name} -> {e}")
                    inv_d = {
                        "source_filename": path.name,
                        "ocr_engine_used": "mock",
                        "ocr_engine_note": f"Extraction failure: {e}",
                        "invoice_number": "",
                        "invoice_date": None,
                        "gst_number": "",
                        "vendor_name": "",
                        "po_number": "",
                        "vendor_code": "",
                        "currency": "INR",
                        "items": [],
                        "total_amount": 0.0,
                        "stamp_present": False,
                    }
                if inv_d.get("ocr_engine_used") == "mock" and inv_d.get("ocr_engine_note"):
                    log(f"Extraction: Anthropic fallback for {path.name} -> {inv_d.get('ocr_engine_note')}")
                out.append(inv_d)
        log(f"Extraction: extracted {len(out)} invoice(s)")
        rep = _build_extraction_report(out)
        _pause()
        return {"extractions": out, "stage_outputs": {"extraction": rep}}

    return extraction


def make_screening_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def screening(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("screening")
        errs: list[dict[str, Any]] = []
        stage_report: dict[str, Any] = {}
        halt = False
        try:
            bundle = evaluate_all_stages_with_grok(
                extractions=state.get("extractions") or [],
                master=state.get("master") or {},
                rules_template_rows=state.get("rules_template_rows") or [],
                invoice_paths=state.get("invoice_paths") or [],
            )
            llm_excs, stage_report, _ = _extract_stage_from_bundle(
                bundle,
                "screening",
                state.get("rules_template_rows") or [],
            )
            errs.extend(_exc_to_dict(e, "screening") for e in llm_excs)
            halt = any(str(x.get("severity", "")).lower() == "critical" for x in errs)
        except Exception as e:  # noqa: BLE001
            errs.append(
                _exc_to_dict(
                    ExceptionRecord(
                        code="LLM_SCREENING_UNAVAILABLE",
                        message=f"LLM screening failed: {str(e)}",
                        severity=Severity.CRITICAL,
                        field="screening",
                    ),
                    "screening",
                )
            )
            halt = True

        if halt:
            log("Screening: Anthropic reported blocking issue(s)")
        else:
            log("Screening: Anthropic screening passed")
        _pause()
        return {
            "exceptions": errs,
            "halt_pipeline": halt,
            "grok_stage_bundle": bundle if "bundle" in locals() and isinstance(bundle, dict) else {},
            "stage_outputs": {"screening": stage_report},
        }

    return screening


def make_validation_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def validation(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("validation")
        if state.get("halt_pipeline"):
            log("Validation: skipped (pipeline halted)")
            _pause()
            return {}
        master = state.get("master") or {}
        extractions = state.get("extractions") or []
        rules_template_rows = state.get("rules_template_rows") or []
        invoice_paths = state.get("invoice_paths") or []
        errs: list[dict[str, Any]] = []
        validation_report: dict[str, Any] = {}

        try:
            bundle = state.get("grok_stage_bundle")
            if isinstance(bundle, dict) and bundle:
                llm_excs, validation_report, _ = _extract_stage_from_bundle(
                    bundle,
                    "validation",
                    rules_template_rows,
                )
            else:
                llm_excs, validation_report, _ = evaluate_stage_with_grok(
                    stage="validation",
                    extractions=extractions,
                    master=master,
                    rules_template_rows=rules_template_rows,
                    invoice_paths=invoice_paths,
                )
            errs.extend(_exc_to_dict(e, "validation") for e in llm_excs)
        except Exception as e:  # noqa: BLE001
            # User requested Anthropic-only validation: fail closed if LLM validation is unavailable.
            errs.append(
                _exc_to_dict(
                    ExceptionRecord(
                        code="LLM_VALIDATION_UNAVAILABLE",
                        message=f"LLM validation failed: {str(e)}",
                        severity=Severity.CRITICAL,
                        field="validation",
                    ),
                    "validation",
                )
            )

        if any(str(e.get("severity", "")).lower() == "critical" for e in errs):
            log("Validation: critical issue(s) detected")
        else:
            log("Validation: Anthropic validation completed")
        _pause()
        return {"exceptions": errs, "stage_outputs": {"validation": validation_report}}

    return validation


def make_matching_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def matching(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("matching")
        errs: list[dict[str, Any]] = []
        po_totals: dict[str, float] = {}
        for inv in state.get("extractions") or []:
            po = str(inv.get("po_number", "")).strip()
            total = float(inv.get("total_amount", 0))
            po_totals[po] = po_totals.get(po, 0.0) + total

        rules_compliance: dict[str, Any] = {
            "columns": ["#", "Rule Description", "Validation Logic", "Agent Type", "Result", "Notes"],
            "rows": [],
            "three_way_summary": [],
            "po_totals": po_totals,
        }

        try:
            bundle = state.get("grok_stage_bundle")
            if isinstance(bundle, dict) and bundle:
                llm_excs, stage_report, llm_rules = _extract_stage_from_bundle(
                    bundle,
                    "matching",
                    state.get("rules_template_rows") or [],
                )
            else:
                llm_excs, stage_report, llm_rules = evaluate_stage_with_grok(
                    stage="matching",
                    extractions=state.get("extractions") or [],
                    master=state.get("master") or {},
                    rules_template_rows=state.get("rules_template_rows") or [],
                    invoice_paths=state.get("invoice_paths") or [],
                )
            errs.extend(_exc_to_dict(e, "matching") for e in llm_excs)
            rules_compliance.update(llm_rules)
            rules_compliance["po_totals"] = po_totals
            rules_compliance["matching_report"] = stage_report
            if state.get("halt_pipeline"):
                rules_compliance["note"] = (
                    "Screening raised critical issue(s); matching still evaluated by Anthropic on available context."
                )
            log("Matching: Anthropic matching + rules evaluation complete")
        except Exception as e:  # noqa: BLE001
            errs.append(
                _exc_to_dict(
                    ExceptionRecord(
                        code="LLM_MATCHING_UNAVAILABLE",
                        message=f"LLM matching failed: {str(e)}",
                        severity=Severity.CRITICAL,
                        field="matching",
                    ),
                    "matching",
                )
            )
            rules_compliance["note"] = "Matching failed because Anthropic call was unavailable."
            log("Matching: LLM matching failed")
        _pause()
        return {"exceptions": errs, "po_totals": po_totals, "stage_outputs": {"rules_compliance": rules_compliance}}

    return matching


def make_exception_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def exception(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("exception")
        all_exc = state.get("exceptions") or []
        critical = any(str(x.get("severity", "")).lower() == "critical" for x in all_exc)
        failed_agent = None
        stage_outputs = dict(state.get("stage_outputs") or {})
        email_draft: dict[str, Any]
        exc_panel: dict[str, Any]
        try:
            email_draft, exc_panel = exception_panel_with_grok(
                exceptions=all_exc,
                stage_outputs=stage_outputs,
            )
            if critical:
                failed_agent = str(exc_panel.get("failed_stage") or "exception")
        except Exception as e:  # noqa: BLE001
            # Fail-soft only for exception narration; keep pipeline outputs usable.
            lines = [f"- [{x.get('code')}] {x.get('message')} ({x.get('severity')})" for x in all_exc]
            body = "Exception summary:\n" + ("\n".join(lines) if lines else "No issues.")
            email_draft = {
                "subject": "Invoice processing exceptions" if critical else "Invoice processing completed",
                "body": body,
                "critical": critical,
            }
            exc_panel = {
                "rows": all_exc,
                "total_issues": len(all_exc),
                "critical": critical,
                "failed_stage": "exception" if critical else None,
                "note": f"Anthropic exception summarization unavailable: {str(e)}",
            }
            if critical:
                failed_agent = "exception"

        if critical:
            log("Exception: CRITICAL — workflow will stop after notifications")
        else:
            log("Exception: no critical blockers")
        _pause()
        return {
            "critical_stop": critical,
            "failed_agent": failed_agent,
            "email_draft": email_draft,
            "stage_outputs": {
                "exceptions_panel": exc_panel
            },
        }

    return exception


def make_email_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def email(state: dict[str, Any]) -> dict[str, Any]:
        if on_agent:
            on_agent("email")
        draft = dict(state.get("email_draft") or {})
        if not state.get("critical_stop"):
            draft["subject"] = draft.get("subject") or "Invoices — ready for approval"
            draft["body"] = (draft.get("body") or "") + "\n\n— Auto notification (QSR Invoice Hub)"
        log("Email: draft generated for approver mailbox")
        rep = {
            "subject": draft.get("subject"),
            "preview": (draft.get("body") or "")[:500],
            "critical": bool(state.get("critical_stop")),
        }
        _pause()
        return {"email_draft": draft, "stage_outputs": {"email": rep}}

    return email


def build_nodes(
    log: Callable[[str], None],
    on_agent: Optional[Callable[[str], None]] = None,
):
    return {
        "supervisor": make_supervisor_node(log, on_agent),
        "extraction": make_extraction_node(log, on_agent),
        "screening": make_screening_node(log, on_agent),
        "validation": make_validation_node(log, on_agent),
        "matching": make_matching_node(log, on_agent),
        "exception": make_exception_node(log, on_agent),
        "email": make_email_node(log, on_agent),
    }
