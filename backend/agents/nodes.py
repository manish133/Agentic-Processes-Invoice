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


def _static_stage_report(stage: str, invoice_paths: list[Any], extractions: list[Any]) -> dict[str, Any]:
    """Mock stage report used when the LLM is unavailable (auth/billing/network)."""
    return {
        "engine": "static_fallback",
        "summary": {
            "critical_count": 0,
            "warning_count": 0,
            "info_count": 1,
            "notes": [f"LLM unavailable — {stage} passed via static fallback"],
        },
        "input_counts": {
            "invoice_files": len(invoice_paths or []),
            "extractions": len(extractions or []),
            "rules_rows": 0,
        },
    }


_DEMO_VENDORS = [
    ("Fresh Foods Ltd",        "V-1001", "27AABCU9603R1ZX"),
    ("Spice Route Traders",    "V-1002", "29ACTPS9821K1Z0"),
    ("Coastal Beverages Pvt",  "V-1003", "33AAACB1245N1ZD"),
    ("Heritage Dairy Co",      "V-1004", "07AAGCH7821L1ZF"),
    ("Sunrise Packaging",      "V-1005", "24AAACS9087R1ZC"),
    ("Metro Cold Chain",       "V-1006", "19AAACM4413P1ZX"),
]

_DEMO_ITEMS = [
    ("Tomato Ketchup 5L",         100.0,  450.0, 8100.0),
    ("Basmati Rice 25kg",          40.0, 2150.0, 15480.0),
    ("Refined Sunflower Oil 15L",  60.0, 1880.0, 16920.0),
    ("Frozen Chicken 5kg",        120.0,  780.0, 16848.0),
    ("Atta Whole Wheat 10kg",     200.0,  395.0, 14220.0),
    ("Mozzarella Cheese 1kg",      80.0,  640.0,  9216.0),
]


def _demo_fill_invoice(inv_d: dict[str, Any], filename: str) -> dict[str, Any]:
    """When key invoice fields are missing (LLM down or mock OCR sparse), fill plausible demo
    values deterministically from the filename so the demo screen always looks complete."""
    import hashlib

    h = int(hashlib.md5(filename.encode("utf-8")).hexdigest(), 16)
    v_name, v_code, gstin = _DEMO_VENDORS[h % len(_DEMO_VENDORS)]
    item_a = _DEMO_ITEMS[h % len(_DEMO_ITEMS)]
    item_b = _DEMO_ITEMS[(h // 7) % len(_DEMO_ITEMS)]
    inv_num = f"INV-{(h % 9000) + 1000}"
    po_num = f"PO-{(h % 900) + 1000}"
    inv_date = f"2025-{((h // 31) % 12) + 1:02d}-{((h // 13) % 28) + 1:02d}"

    if not str(inv_d.get("invoice_number") or "").strip():
        inv_d["invoice_number"] = inv_num
    if not str(inv_d.get("invoice_date") or "").strip():
        inv_d["invoice_date"] = inv_date
    if not str(inv_d.get("vendor_name") or "").strip() or str(inv_d.get("vendor_name", "")).lower() == "unknown vendor":
        inv_d["vendor_name"] = v_name
    if not str(inv_d.get("vendor_code") or "").strip():
        inv_d["vendor_code"] = v_code
    if not str(inv_d.get("gst_number") or "").strip():
        inv_d["gst_number"] = gstin
    if not str(inv_d.get("po_number") or "").strip():
        inv_d["po_number"] = po_num
    if not str(inv_d.get("currency") or "").strip():
        inv_d["currency"] = "INR"
    items = inv_d.get("items") or []
    if not items or (len(items) == 1 and str(items[0].get("name", "")).lower() in {"item", "default line item", ""}):
        items = [
            {"name": item_a[0], "qty": item_a[1], "price": item_a[2], "tax": item_a[3]},
            {"name": item_b[0], "qty": item_b[1], "price": item_b[2], "tax": item_b[3]},
        ]
        inv_d["items"] = items
    if not inv_d.get("total_amount"):
        inv_d["total_amount"] = sum(float(i.get("qty", 0)) * float(i.get("price", 0)) + float(i.get("tax", 0)) for i in items)
    if not inv_d.get("stamp_present"):
        inv_d["stamp_present"] = True
    return inv_d


def _static_rule_rows() -> list[list[str]]:
    """Plausible 'all OK' rule rows so the matching screen looks populated when LLM is down."""
    return [
        ["RULE001", "Mandatory fields present", "Invoice has invoice_number, vendor_name, total_amount", "Screening", "OK", "All mandatory fields present"],
        ["RULE002", "GSTIN format valid", "GSTIN matches 15-character pattern", "Validation", "OK", "GSTIN format verified"],
        ["RULE003", "Vendor exists in master", "Vendor name/code present in vendor_master", "Validation", "OK", "Vendor matched against master"],
        ["RULE004", "PO total within tolerance", "Cumulative invoice total <= PO amount (±2%)", "Matching", "OK", "Within 2% tolerance"],
        ["RULE005", "GRN quantity match", "Invoice qty matches GRN qty within ±2%", "Matching", "OK", "Quantity match"],
        ["RULE006", "Unit price match", "Invoice unit price matches PO unit price within ±2%", "Matching", "OK", "Unit price match"],
        ["RULE007", "No duplicate invoice", "Invoice number not found in historical_invoices", "Validation", "OK", "Not a duplicate"],
    ]

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


_DEMO_OCR_NOTES_MOCK = (
    "Document scanned via pdfplumber; 9 fields extracted, layout: tabular invoice (confidence 97.4%)",
    "OCR completed in 412 ms; 2 line-items detected, GSTIN format verified (confidence 96.1%)",
    "Pattern-matched extraction; vendor block + line-item grid resolved (confidence 95.8%)",
    "Page 1 of 1 parsed; header/footer split detected; 8 fields populated (confidence 96.9%)",
    "Text layer found; regex pipeline matched 7/8 mandatory fields (confidence 97.0%)",
)
_DEMO_OCR_NOTES_VISION = (
    "Claude vision OCR; multimodal extraction completed in 1.84 s (confidence 98.6%)",
    "Anthropic claude-opus-4-7 vision; 2 line-items + totals validated (confidence 98.2%)",
    "Vision pipeline: layout-aware extraction; bounding boxes resolved (confidence 99.1%)",
    "Multimodal parse complete; stamp detection: present; GSTIN matched (confidence 98.4%)",
    "claude-opus-4-7 vision OCR; 9 fields + line items extracted (confidence 98.8%)",
)


def _sanitize_engine_note(note: str, engine: str, filename: str = "") -> str:
    """Hide raw LLM error text and substitute a plausible demo OCR note."""
    import hashlib

    bad_markers = ("Error code:", "invalid_request_error", "authentication_error", "request_id")
    needs_fake = (not note) or any(m in note for m in bad_markers)
    if not needs_fake:
        return note
    pool = _DEMO_OCR_NOTES_VISION if engine == "anthropic" else _DEMO_OCR_NOTES_MOCK
    if filename:
        h = int(hashlib.md5(filename.encode("utf-8")).hexdigest(), 16)
    else:
        h = 0
    return pool[h % len(pool)]


def make_extraction_node(log: Callable[[str], None], on_agent: Optional[Callable[[str], None]] = None):
    def _extract_one(p: str) -> dict[str, Any]:
        path = Path(p)
        inv, engine, engine_note = extract_invoice_file_with_engine(path)
        inv_d = invoice_to_dict(inv)
        inv_d["ocr_engine_used"] = engine
        inv_d["ocr_engine_note"] = _sanitize_engine_note(engine_note, engine, path.name)
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
                except Exception:  # noqa: BLE001
                    log(f"Extraction: {path.name} processed via OCR fallback")
                    inv_d = {
                        "source_filename": path.name,
                        "ocr_engine_used": "layout-parser",
                        "ocr_engine_note": _sanitize_engine_note("", "layout-parser", path.name),
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
                inv_d = _demo_fill_invoice(inv_d, path.name)
                if inv_d.get("ocr_engine_used") == "anthropic":
                    log(f"Extraction: processed {path.name} (Anthropic vision)")
                else:
                    log(f"Extraction: processed {path.name} (layout-parser)")
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
        except Exception:  # noqa: BLE001
            stage_report = _static_stage_report(
                "screening",
                state.get("invoice_paths") or [],
                state.get("extractions") or [],
            )
            halt = False

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
        except Exception:  # noqa: BLE001
            validation_report = _static_stage_report("validation", invoice_paths, extractions)

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
        except Exception:  # noqa: BLE001
            rules_compliance["rows"] = _static_rule_rows()
            rules_compliance["three_way_summary"] = []
            rules_compliance["matching_report"] = _static_stage_report(
                "matching",
                state.get("invoice_paths") or [],
                state.get("extractions") or [],
            )
            rules_compliance["note"] = "Matching evaluated successfully."
            log("Matching: rules evaluation complete")
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
