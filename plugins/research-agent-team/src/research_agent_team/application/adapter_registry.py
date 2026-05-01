from __future__ import annotations

from typing import Any, Dict

from research_agent_team.shared import now_utc


ADAPTER_NAMES = ("graph", "experiments", "hooks")
_DEGRADED_STATUSES = {"failed", "unavailable", "degraded"}


def _normalize_status(raw_status: Any) -> str:
    status = str(raw_status or "unknown")
    if status in _DEGRADED_STATUSES:
        return "degraded"
    if status == "healthy":
        return "healthy"
    return "unknown"


def _normalize_entry(name: str, raw: Any) -> Dict[str, Any]:
    timestamp = now_utc()
    if isinstance(raw, dict):
        if "status" in raw:
            raw_status = str(raw.get("status") or "unknown")
            status = _normalize_status(raw_status)
            message = str(raw.get("message") or f"{name} adapter status is {status}.")
            updated_at = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else timestamp
            return {"status": status, "message": message, "updated_at": updated_at}
        legacy_state = str(raw.get("state") or "unknown")
        last_error = raw.get("last_error")
        status = _normalize_status(legacy_state)
        message = str(last_error or f"Legacy {name} adapter state: {legacy_state}.")
        return {"status": status, "message": message, "updated_at": timestamp}
    if isinstance(raw, str):
        status = _normalize_status(raw)
        return {"status": status, "message": f"{name} adapter status is {raw}.", "updated_at": timestamp}
    return {"status": "unknown", "message": f"{name} adapter health was not recorded.", "updated_at": timestamp}


def normalize_adapter_health(raw_health: Dict[str, Any] | None) -> Dict[str, Any]:
    raw_health = raw_health or {}
    normalized: Dict[str, Any] = {}
    for name in ADAPTER_NAMES:
        normalized[name] = _normalize_entry(name, raw_health.get(name))
    for name, value in raw_health.items():
        if name not in normalized:
            normalized[name] = _normalize_entry(name, value)
    return normalized


def adapter_health_warnings(adapter_health: Dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for name, payload in adapter_health.items():
        if not isinstance(payload, dict):
            continue
        status = str(payload.get("status") or "unknown")
        if status not in {"degraded", "failed", "unavailable"}:
            continue
        message = payload.get("message")
        warning = f"{name} adapter is {status}"
        if message:
            warning = f"{warning}: {message}"
        warnings.append(warning)
    return warnings
