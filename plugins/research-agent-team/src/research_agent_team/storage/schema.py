from __future__ import annotations

from typing import Any, Dict


SCHEMA_VERSION = "0.2.0"
MIGRATABLE_SCHEMA_VERSIONS = {"0.1.0"}


def read_schema_version(payload: Dict[str, Any]) -> str:
    value = payload.get("schema_version")
    if not isinstance(value, str) or not value.strip():
        return "0.1.0"
    return value.strip()


def is_migratable_schema_version(schema_version: str) -> bool:
    return schema_version in MIGRATABLE_SCHEMA_VERSIONS
