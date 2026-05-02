#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"


def _fail(message: str) -> None:
    raise SystemExit(f"Manifest validation failed: {message}")


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{name} must be an object")
    return value


def _validate_relative_path(value: str, field_name: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        _fail(f"{field_name} must stay inside the plugin root")
    resolved = (PLUGIN_ROOT / Path(*path.parts)).resolve()
    plugin_root = PLUGIN_ROOT.resolve()
    if resolved != plugin_root and plugin_root not in resolved.parents:
        _fail(f"{field_name} escapes the plugin root")
    if not resolved.exists():
        _fail(f"{field_name} does not exist: {value}")


def validate_manifest() -> None:
    try:
        manifest = _require_object(json.loads(MANIFEST_PATH.read_text(encoding="utf-8")), "manifest")
    except json.JSONDecodeError as exc:
        _fail(str(exc))

    for field_name in ["name", "version", "description", "license", "skills", "mcpServers", "interface"]:
        if field_name not in manifest:
            _fail(f"missing required field: {field_name}")

    if manifest["name"] != "research-agent-team":
        _fail("name must be research-agent-team")

    _validate_relative_path(str(manifest["skills"]), "skills")
    _validate_relative_path(str(manifest["mcpServers"]), "mcpServers")
    _validate_mcp_config(str(manifest["mcpServers"]))

    interface = _require_object(manifest["interface"], "interface")
    for field_name in ["displayName", "shortDescription", "longDescription", "developerName", "category"]:
        if not isinstance(interface.get(field_name), str) or not interface[field_name].strip():
            _fail(f"interface.{field_name} must be a non-empty string")

    if not isinstance(interface.get("capabilities"), list) or not interface["capabilities"]:
        _fail("interface.capabilities must be a non-empty list")
    if not isinstance(interface.get("defaultPrompt"), list) or not interface["defaultPrompt"]:
        _fail("interface.defaultPrompt must be a non-empty list")


def _validate_mcp_config(relative_path: str) -> None:
    try:
        config = _require_object(json.loads((PLUGIN_ROOT / relative_path).read_text(encoding="utf-8")), "mcpServers config")
    except json.JSONDecodeError as exc:
        _fail(f"invalid mcpServers JSON: {exc}")

    server_map = config.get("mcpServers") or config.get("mcp_servers")
    if not isinstance(server_map, dict) or not server_map:
        _fail("mcpServers config must declare at least one server")
    server = _require_object(server_map.get("research-agent-team"), "mcpServers.research-agent-team")
    if server.get("command") != "uv":
        _fail("mcpServers.research-agent-team.command must be uv")
    if server.get("args") != ["run", "--project", ".", "python", "./scripts/rat_plugin_mcp.py"]:
        _fail("mcpServers.research-agent-team.args must run the MCP server through the plugin project environment")
    if server.get("cwd") != ".":
        _fail("mcpServers.research-agent-team.cwd must be .")


def main() -> int:
    validate_manifest()
    print(f"Manifest validated at {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
