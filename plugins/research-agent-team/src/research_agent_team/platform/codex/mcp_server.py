from __future__ import annotations

import sys
from typing import Any

from research_agent_team.platform.codex import bridge

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised only in incomplete installs
    FastMCP = None  # type: ignore[assignment]


mcp = FastMCP("ResearchAgentTeam") if FastMCP is not None else None


def _tool(function: Any) -> Any:
    if mcp is None:
        return function
    return mcp.tool()(function)


@_tool
def interpret_request(
    text: str,
    root_path: str | None = None,
    requester_slot_id: str | None = None,
    confirmation_mode: str = "conservative",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map natural language to a ResearchAgentTeam command plan without executing it."""

    context_payload = dict(context or {})
    if root_path:
        context_payload["root_path"] = root_path
    if requester_slot_id:
        context_payload["requester_slot_id"] = requester_slot_id
    if confirmation_mode:
        context_payload["confirmation_mode"] = confirmation_mode
    return bridge.interpret_request(text, context_payload)


@_tool
def run_command(
    command_name: str,
    payload: dict[str, Any],
    launch_policy: str = "conservative",
) -> dict[str, Any]:
    """Run a ResearchAgentTeam command and include Codex launch decisions when possible."""

    envelope = bridge.run_command(command_name, payload)
    return bridge.with_launch_plan(
        envelope,
        bridge.root_path_from_payload(payload),
        source_command=command_name,
        policy=launch_policy,
    )


@_tool
def activation_callback(
    activation_command: str,
    root_path: str,
    activation_id: str,
    payload: dict[str, Any] | None = None,
    runtime_pid: int | None = None,
    launch_policy: str = "conservative",
) -> dict[str, Any]:
    """Run an activation callback and include Codex launch decisions for follow-up work."""

    envelope = bridge.run_activation_callback(
        activation_command,
        root_path,
        activation_id,
        payload,
        runtime_pid,
    )
    return bridge.with_launch_plan(envelope, root_path, source_command=activation_command, policy=launch_policy)


@_tool
def plan_launches(
    root_path: str,
    command_result: dict[str, Any],
    source_command: str | None = None,
    policy: str = "conservative",
) -> dict[str, Any]:
    """Classify launch_request values from a previous command or activation result."""

    return bridge.plan_launches_for_result(root_path, command_result, source_command=source_command, policy=policy)


@_tool
def render_launch_prompt(root_path: str, launch_request: dict[str, Any]) -> dict[str, Any]:
    """Render a sanitized Codex worker prompt from an approved launch request."""

    rendered = bridge.render_launch_prompt_for_request(root_path, launch_request)
    if not rendered.get("ok"):
        return rendered
    return rendered


def main() -> int:
    if mcp is None:
        print("The 'mcp' Python package is required to run the ResearchAgentTeam MCP server.", file=sys.stderr)
        return 1
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
