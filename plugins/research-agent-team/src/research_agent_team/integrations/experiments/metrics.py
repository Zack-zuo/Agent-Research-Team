from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


KEY_VALUE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:=|:)\s*(.+?)\s*$")


def parse_metrics_file(path: Path) -> Dict[str, Any]:
    if path.suffix.lower() == ".json":
        return _parse_json_metrics(path)
    return _parse_key_value_metrics(path)


def _parse_json_metrics(path: Path) -> Dict[str, Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("JSON metrics must contain an object")
    return dict(parsed)


def _parse_key_value_metrics(path: Path) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = KEY_VALUE_RE.match(stripped)
        if not match:
            raise ValueError(f"metrics line {line_number} is not key=value or key: value")
        metrics[match.group(1)] = _coerce_scalar(match.group(2))
    return metrics


def _coerce_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
