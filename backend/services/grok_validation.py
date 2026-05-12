"""GROK-powered screening/validation/matching against extracted invoices + masters + rules."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from models.schemas import ExceptionRecord, Severity
from services.cache_store import cache_get, cache_set
from services.grok_ocr import invoke_llm_json, is_grok_enabled

RULE_COLUMNS = ["#", "Rule Description", "Validation Logic", "Agent Type", "Result", "Notes"]
RULEBOOK_VERSION = 0


def _safe_json_snippet(obj: Any, max_chars: int) -> str:
    raw = json.dumps(obj, ensure_ascii=False, default=str)
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars] + " ...[truncated]"


def _stage_payload_limit() -> int:
    try:
        v = int((os.environ.get("GROK_STAGE_PAYLOAD_CHARS") or "70000").strip())
    except Exception:
        v = 70000
    return max(15000, min(v, 200000))


def _to_severity(v: str) -> Severity:
    s = str(v or "").strip().lower()
    if s == "critical":
        return Severity.CRITICAL
    if s == "info":
        return Severity.INFO
    return Severity.WARNING


def _normalize_exception_rows(rows: Any) -> list[ExceptionRecord]:
    out: list[ExceptionRecord] = []
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        out.append(
            ExceptionRecord(
                code=str(r.get("code", "LLM_VALIDATION")).strip() or "LLM_VALIDATION",
                message=str(r.get("message", "Validation note from LLM")).strip() or "Validation note from LLM",
                severity=_to_severity(str(r.get("severity", "warning"))),
                field=str(r.get("field", "")).strip() or None,
                document=str(r.get("document", "")).strip() or None,
            )
        )
    return out


def _result_token(v: Any) -> str:
    s = str(v or "").strip().upper()
    if s in {"OK", "NOT OK"}:
        return s
    return ""


def _norm_txt(s: Any) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _rule007_vendor_exists(extractions: list[dict[str, Any]], master: dict[str, Any]) -> tuple[bool, str]:
    """
    Deterministic cross-check for RULE007 to prevent LLM false negatives.
    """
    vm = master.get("vendor_master") if isinstance(master, dict) else []
    if not isinstance(vm, list) or not vm:
        return False, "vendor_master is empty in context"
    code_set = {str(r.get("vendor_code", "")).strip().upper() for r in vm if isinstance(r, dict)}
    name_set = {_norm_txt(r.get("vendor_name", "")) for r in vm if isinstance(r, dict)}
    name_set = {x for x in name_set if x}

    missing: list[str] = []
    for inv in extractions or []:
        if not isinstance(inv, dict):
            continue
        vcode = str(inv.get("vendor_code", "")).strip().upper()
        vname = _norm_txt(inv.get("vendor_name", ""))
        code_ok = bool(vcode and vcode in code_set)
        name_ok = bool(vname and (vname in name_set or any(vname in n or n in vname for n in name_set)))
        if not (code_ok or name_ok):
            missing.append(f"{vcode or '<no-code>'}:{str(inv.get('vendor_name', '')).strip() or '<no-name>'}")
    if missing:
        return False, f"master mismatch for {', '.join(missing[:3])}"
    return True, "vendor found in normalized vendor_master by code/name"


def _ensure_complete_rule_verdicts(
    *,
    stage: str,
    rule_results: Any,
    checklist: list[dict[str, str]],
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    invoice_paths: list[str],
) -> list[dict[str, Any]]:
    """
    Dynamic mode: keep GROK-provided rows only (one pass, no static checklist enforcement).
    """
    rows_in = [r for r in (rule_results or []) if isinstance(r, dict)]
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows_in, start=1):
        rid = str(r.get("rule_id", "")).strip() or f"RULE{i:03d}"
        result = _result_token(r.get("result")) or "NOT OK"
        out.append(
            {
                "rule_id": rid,
                "rule_description": str(r.get("rule_description", "")).strip() or f"Rule {i}",
                "validation_logic": str(r.get("validation_logic", "")).strip() or "Defined by GROK",
                "agent_type": str(r.get("agent_type", "Validation")).strip().title(),
                "result": result,
                "result_notes": str(r.get("result_notes", "")).strip() or "No details provided.",
                "document": str(r.get("document", "")).strip() or None,
            }
        )
    return out


def normalize_rule_results(llm_rule_results: Any) -> list[list[str]]:
    """
    Normalize GROK-generated rules to a stable UI format.
    Keeps LLM-authored rules while enforcing consistent columns/order.
    """
    rows = _ensure_complete_rule_verdicts(
        stage="matching",
        rule_results=llm_rule_results,
        checklist=[],
        extractions=[],
        master={},
        invoice_paths=[],
    )
    out: list[list[str]] = []
    for r in rows:
        rid = str(r.get("rule_id", "")).strip()
        desc = str(r.get("rule_description", "")).strip()
        logic = str(r.get("validation_logic", "")).strip()
        agent_type = str(r.get("agent_type", "Validation")).strip().title()
        if agent_type.lower() not in {"screening", "validation", "matching"}:
            agent_type = "Validation"
        result = _result_token(r.get("result")) or "NOT OK"
        notes = str(r.get("result_notes", "")).strip() or "No details provided."
        doc = str(r.get("document", "")).strip()
        if doc:
            notes = f"[{doc}] {notes}"
        out.append([rid, desc, logic, agent_type, result, notes])
    return out


def _normalize_three_way(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(dict(r))
    return out


def _sample_columns(rows: Any, limit: int = 12) -> list[str]:
    if not isinstance(rows, list) or not rows:
        return []
    first = rows[0]
    if not isinstance(first, dict):
        return []
    return [str(k) for k in list(first.keys())[:limit]]


def _build_source_snapshot(
    *,
    master: dict[str, Any],
    rules_template_rows: list[dict[str, Any]],
    static_rulebook: list[dict[str, str]],
    invoice_paths: list[str],
    extractions: list[dict[str, Any]],
) -> dict[str, Any]:
    vm = master.get("vendor_master") if isinstance(master, dict) else []
    pm = master.get("po_master") if isinstance(master, dict) else []
    gm = master.get("grn_master") if isinstance(master, dict) else []
    hm = master.get("historical_invoices") if isinstance(master, dict) else []
    return {
        "invoice_files_count": len(invoice_paths),
        "invoice_filenames": [str(Path(p).name) for p in invoice_paths[:20]],
        "extractions_count": len(extractions),
        "masters": {
            "vendor_master_rows": len(vm) if isinstance(vm, list) else 0,
            "po_master_rows": len(pm) if isinstance(pm, list) else 0,
            "grn_master_rows": len(gm) if isinstance(gm, list) else 0,
            "historical_invoice_rows": len(hm) if isinstance(hm, list) else 0,
            "vendor_master_columns_sample": _sample_columns(vm),
            "po_master_columns_sample": _sample_columns(pm),
            "grn_master_columns_sample": _sample_columns(gm),
            "historical_columns_sample": _sample_columns(hm),
        },
        "rules": {
            "uploaded_rules_template_rows": len(rules_template_rows or []),
            "uploaded_rules_columns_sample": _sample_columns(rules_template_rows),
            "static_rulebook_rows": len(static_rulebook),
            "static_rulebook_ids_sample": [r.get("rule_id", "") for r in static_rulebook[:20]],
        },
    }


def evaluate_stage_with_grok(
    *,
    stage: str,
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    rules_template_rows: list[dict[str, Any]],
    invoice_paths: list[str],
) -> tuple[list[ExceptionRecord], dict[str, Any], dict[str, Any]]:
    """
    Ask GROK to perform one stage decision using all available context.
    Returns:
      (exceptions, stage_report, rules_compliance_payload_for_ui_or_empty)
    """
    if not is_grok_enabled():
        raise RuntimeError("GROK stage decision requested but no API key configured.")

    static_checklist = []
    source_snapshot = _build_source_snapshot(
        master=master,
        rules_template_rows=rules_template_rows or [],
        static_rulebook=static_checklist,
        invoice_paths=invoice_paths,
        extractions=extractions,
    )
    payload = {
        "stage": stage,
        "invoice_files": invoice_paths,
        "extracted_invoices": extractions,
        "master_data": master,
        "static_validation_checklist": static_checklist,
        "source_snapshot": source_snapshot,
    }

    system_prompt = (
        "You are a strict AP invoice decision engine. "
        "You evaluate one stage at a time: screening, validation, or matching. "
        "Use extracted invoices and masters as context. "
        "For screening: mandatory fields and basic readiness checks. "
        "For validation: vendor/PO/tax/date/currency/business consistency checks. "
        "For matching: PO/GRN/3-way and rules matrix results. "
        "Return ONLY JSON with this schema: "
        "{\"exceptions\": [{\"code\": str, \"message\": str, \"severity\": \"critical|warning|info\", "
        "\"field\": str|null, \"document\": str|null}], "
        "\"summary\": {\"critical_count\": int, \"warning_count\": int, \"info_count\": int, \"notes\": [str]}, "
        "\"rule_results\": ["
        "{\"rule_id\": str, \"rule_description\": str, \"validation_logic\": str, \"agent_type\": \"screening|validation|matching\", "
        "\"result\": \"OK|NOT OK\", \"result_notes\": str, \"document\": str|null}"
        "], "
        "\"three_way_summary\": [dict]}"
        ". Use critical when invoice should block approval."
    )

    user_prompt = (
        f"Evaluate stage '{stage}' using provided extraction + masters + rules context.\n\n"
        f"{_safe_json_snippet(payload, _stage_payload_limit())}"
    )

    cache_payload = {
        "v": 7,
        "mode": "single_stage",
        "stage": stage,
        "extractions": extractions,
        "master": master,
        "rules": rules_template_rows,
        "files": invoice_paths,
    }
    cached = cache_get("grok_stage_eval", cache_payload)
    if isinstance(cached, dict):
        data = cached
    else:
        data = invoke_llm_json(system_prompt=system_prompt, user_prompt=user_prompt)
        cache_set("grok_stage_eval", cache_payload, data)
    data_rule_results = data.get("rule_results") if isinstance(data, dict) else []
    if stage == "matching":
        data["rule_results"] = _ensure_complete_rule_verdicts(
            stage=stage,
            rule_results=data_rule_results,
            checklist=[],
            extractions=extractions,
            master=master,
            invoice_paths=invoice_paths,
        )

    exceptions = _normalize_exception_rows(data.get("exceptions"))
    summary = data.get("summary") if isinstance(data, dict) else {}
    if not isinstance(summary, dict):
        summary = {}
    report = {
        "engine": "grok",
        "summary": summary,
        "input_counts": {
            "invoice_files": len(invoice_paths),
            "extractions": len(extractions),
            "rules_rows": len(data.get("rule_results") or []) if isinstance(data, dict) else 0,
        },
    }
    rules_payload = {
        "columns": RULE_COLUMNS,
        "rows": normalize_rule_results(data.get("rule_results")),
        "three_way_summary": _normalize_three_way(data.get("three_way_summary")),
    }
    return exceptions, report, rules_payload


def evaluate_all_stages_with_grok(
    *,
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    rules_template_rows: list[dict[str, Any]],
    invoice_paths: list[str],
) -> dict[str, Any]:
    """
    Single GROK call for screening+validation+matching to reduce latency.
    """
    if not is_grok_enabled():
        raise RuntimeError("GROK stage decision requested but no API key configured.")

    static_checklist = []
    source_snapshot = _build_source_snapshot(
        master=master,
        rules_template_rows=rules_template_rows or [],
        static_rulebook=static_checklist,
        invoice_paths=invoice_paths,
        extractions=extractions,
    )
    payload = {
        "invoice_files": invoice_paths,
        "extracted_invoices": extractions,
        "master_data": master,
        "rules_template_rows": rules_template_rows,
        "source_snapshot": source_snapshot,
    }
    system_prompt = (
        "You are a strict AP invoice decision engine. "
        "Evaluate three stages in one pass: screening, validation, matching. "
        "Define and evaluate the validation checks yourself in one pass from the provided context. "
        "In matching.rule_results, provide complete rule rows with explicit agent_type. "
        "Return ONLY JSON with schema: "
        "{"
        "\"screening\": {\"exceptions\": [{\"code\": str, \"message\": str, \"severity\": \"critical|warning|info\", "
        "\"field\": str|null, \"document\": str|null}], "
        "\"summary\": {\"critical_count\": int, \"warning_count\": int, \"info_count\": int, \"notes\": [str]}}, "
        "\"validation\": {\"exceptions\": [{\"code\": str, \"message\": str, \"severity\": \"critical|warning|info\", "
        "\"field\": str|null, \"document\": str|null}], "
        "\"summary\": {\"critical_count\": int, \"warning_count\": int, \"info_count\": int, \"notes\": [str]}}, "
        "\"matching\": {\"exceptions\": [{\"code\": str, \"message\": str, \"severity\": \"critical|warning|info\", "
        "\"field\": str|null, \"document\": str|null}], "
        "\"summary\": {\"critical_count\": int, \"warning_count\": int, \"info_count\": int, \"notes\": [str]}, "
        "\"rule_results\": [{\"rule_id\": str, \"rule_description\": str, \"validation_logic\": str, "
        "\"agent_type\": \"screening|validation|matching\", "
        "\"result\": \"OK|NOT OK\", \"result_notes\": str, \"document\": str|null}], "
        "\"three_way_summary\": [dict]}"
        "}"
    )
    user_prompt = (
        "Evaluate screening, validation, and matching in one run using provided extraction + masters + rules context.\n\n"
        f"{_safe_json_snippet(payload, _stage_payload_limit())}"
    )
    cache_payload = {
        "v": 7,
        "mode": "all_stages",
        "extractions": extractions,
        "master": master,
        "rules": rules_template_rows,
        "files": invoice_paths,
    }
    cached = cache_get("grok_stage_eval", cache_payload)
    if isinstance(cached, dict):
        data = cached
    else:
        data = invoke_llm_json(system_prompt=system_prompt, user_prompt=user_prompt)
        cache_set("grok_stage_eval", cache_payload, data)
    if not isinstance(data, dict):
        raise RuntimeError("Invalid GROK multi-stage response.")
    m = data.get("matching")
    if isinstance(m, dict):
        m["rule_results"] = _ensure_complete_rule_verdicts(
            stage="matching",
            rule_results=m.get("rule_results"),
            checklist=[],
            extractions=extractions,
            master=master,
            invoice_paths=invoice_paths,
        )
    return data


def validate_with_grok(
    *,
    extractions: list[dict[str, Any]],
    master: dict[str, Any],
    rules_template_rows: list[dict[str, Any]],
    invoice_paths: list[str],
) -> tuple[list[ExceptionRecord], dict[str, Any]]:
    """Backward-compatible wrapper for validation stage."""
    excs, report, _ = evaluate_stage_with_grok(
        stage="validation",
        extractions=extractions,
        master=master,
        rules_template_rows=rules_template_rows,
        invoice_paths=invoice_paths,
    )
    return excs, report


def exception_panel_with_grok(
    *,
    exceptions: list[dict[str, Any]],
    stage_outputs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Build email draft + exceptions panel via GROK.
    """
    if not is_grok_enabled():
        raise RuntimeError("GROK exception synthesis requested but no API key configured.")

    payload = {
        "exceptions": exceptions,
        "stage_outputs": stage_outputs,
    }
    system_prompt = (
        "You are an AP operations exception summarizer. "
        "Return ONLY JSON with schema: "
        "{\"email_draft\": {\"subject\": str, \"body\": str, \"critical\": bool}, "
        "\"exceptions_panel\": {\"rows\": [{\"code\": str, \"severity\": str, \"message\": str, "
        "\"agent\": str|null, \"field\": str|null, \"document\": str|null}], "
        "\"total_issues\": int, \"critical\": bool, \"failed_stage\": str|null, \"notes\": [str]}}."
    )
    user_prompt = (
        "Create final exception panel and escalation email from the pipeline outcomes.\n\n"
        f"{_safe_json_snippet(payload, 45000)}"
    )
    cache_payload = {
        "v": 1,
        "exceptions": exceptions,
        "stage_outputs": stage_outputs,
    }
    cached = cache_get("grok_exception_panel", cache_payload)
    if isinstance(cached, dict):
        data = cached
    else:
        data = invoke_llm_json(system_prompt=system_prompt, user_prompt=user_prompt)
        cache_set("grok_exception_panel", cache_payload, data)
    if not isinstance(data, dict):
        raise RuntimeError("Invalid GROK exception response.")
    ed = data.get("email_draft")
    ep = data.get("exceptions_panel")
    if not isinstance(ed, dict) or not isinstance(ep, dict):
        raise RuntimeError("Missing email_draft/exceptions_panel from GROK response.")
    ed_out = {
        "subject": str(ed.get("subject", "Invoice processing exceptions")).strip() or "Invoice processing exceptions",
        "body": str(ed.get("body", "")).strip(),
        "critical": bool(ed.get("critical", False)),
    }
    rows_raw = ep.get("rows")
    rows: list[dict[str, Any]] = []
    if isinstance(rows_raw, list):
        for r in rows_raw:
            if isinstance(r, dict):
                rows.append(
                    {
                        "code": str(r.get("code", "")).strip(),
                        "severity": str(r.get("severity", "")).strip(),
                        "message": str(r.get("message", "")).strip(),
                        "agent": str(r.get("agent", "")).strip() or None,
                        "field": str(r.get("field", "")).strip() or None,
                        "document": str(r.get("document", "")).strip() or None,
                    }
                )
    ep_out = {
        "rows": rows if rows else exceptions,
        "total_issues": int(ep.get("total_issues", len(rows) if rows else len(exceptions))),
        "critical": bool(ep.get("critical", False)),
        "failed_stage": str(ep.get("failed_stage", "")).strip() or None,
        "notes": ep.get("notes") if isinstance(ep.get("notes"), list) else [],
    }
    return ed_out, ep_out

