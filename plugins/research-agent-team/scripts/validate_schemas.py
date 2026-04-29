#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_ROOT = PLUGIN_ROOT / "schemas"


def _fail(path: Path, message: str) -> None:
    raise SystemExit(f"Schema validation failed for {path.relative_to(PLUGIN_ROOT)}: {message}")


def _require_string(schema: dict[str, Any], path: Path, key: str) -> None:
    if not isinstance(schema.get(key), str) or not schema[key].strip():
        _fail(path, f"{key} must be a non-empty string")


def validate_schemas() -> int:
    schema_paths = sorted(SCHEMAS_ROOT.rglob("*.json"))
    if not schema_paths:
        raise SystemExit("Schema validation failed: no schema files found")

    for path in schema_paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _fail(path, str(exc))
        if not isinstance(schema, dict):
            _fail(path, "schema must be a JSON object")
        for key in ["$schema", "$id", "title", "type"]:
            _require_string(schema, path, key)
        if schema["type"] != "object":
            _fail(path, "Stage 0 schemas must use object roots")

    return len(schema_paths)


def main() -> int:
    count = validate_schemas()
    print(f"Schemas validated: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
