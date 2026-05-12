"""Tiny JSON file cache for repeated LLM calls."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[1]
CACHE_DIR = BASE_DIR / ".cache"


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _cache_path(namespace: str, payload: dict[str, Any]) -> Path:
    h = hashlib.sha256(_stable_json(payload).encode("utf-8", errors="ignore")).hexdigest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{namespace}_{h}.json"


def cache_get(namespace: str, payload: dict[str, Any]) -> Any | None:
    p = _cache_path(namespace, payload)
    if not p.exists():
        return None
    try:
        ttl = int((os.getenv("LLM_CACHE_TTL_SEC") or "0").strip() or "0")
    except ValueError:
        ttl = 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        saved_at = float(data.get("saved_at", 0.0) or 0.0)
        if ttl > 0 and saved_at > 0 and (time.time() - saved_at) > ttl:
            return None
        return data.get("value")
    except Exception:
        return None


def cache_set(namespace: str, payload: dict[str, Any], value: Any) -> None:
    p = _cache_path(namespace, payload)
    data = {"saved_at": time.time(), "value": value}
    try:
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        return

