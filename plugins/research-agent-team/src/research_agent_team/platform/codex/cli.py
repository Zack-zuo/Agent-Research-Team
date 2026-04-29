from __future__ import annotations

import argparse
import json
import select
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Tuple, Type

from research_agent_team.contracts.commands import ACTIVATION_COMMAND_NAMES, COMMAND_NAMES


def _stdin_has_data() -> bool:
    if sys.stdin.isatty():
        return False
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    return bool(readable)


def _load_json_object(text: str) -> dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("JSON payload must be an object")
    return parsed


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "payload_json", None):
        return _load_json_object(args.payload_json)
    if getattr(args, "payload_file", None):
        return _load_json_object(Path(args.payload_file).read_text(encoding="utf-8"))
    if _stdin_has_data():
        stdin_text = sys.stdin.read().strip()
        if stdin_text:
            return _load_json_object(stdin_text)
    return {}


def _dump_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _not_implemented(kind: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": "not_implemented",
            "kind": kind,
            "command": name,
            "message": f"{kind} '{name}' is defined for the Stage 0 plugin surface but is not implemented yet.",
        },
        "payload_keys": sorted(payload.keys()),
    }


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": result}


def _error(error: Any) -> dict[str, Any]:
    return {"ok": False, "error": error.to_dict()}


def _load_command_handler(
    command_name: str,
) -> Tuple[Optional[Callable[[dict[str, Any]], dict[str, Any]]], Optional[Type[Exception]]]:
    if command_name in {"create_project", "open_project", "switch_operating_mode", "pause_project", "resume_project"}:
        from research_agent_team.application.errors import CommandError
        from research_agent_team.application.project_service import (
            create_project,
            open_project,
            pause_project,
            resume_project,
            switch_operating_mode,
        )

        return {
            "create_project": create_project,
            "open_project": open_project,
            "switch_operating_mode": switch_operating_mode,
            "pause_project": pause_project,
            "resume_project": resume_project,
        }[command_name], CommandError

    if command_name in {"show_team_topology", "add_senior", "add_junior", "retire_senior", "retire_junior"}:
        from research_agent_team.application.errors import CommandError
        from research_agent_team.application.topology_service import (
            add_junior,
            add_senior,
            retire_junior,
            retire_senior,
            show_team_topology,
        )

        return {
            "show_team_topology": show_team_topology,
            "add_senior": add_senior,
            "add_junior": add_junior,
            "retire_senior": retire_senior,
            "retire_junior": retire_junior,
        }[command_name], CommandError

    return None, None


def run_command(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    handler, command_error = _load_command_handler(args.command_name)
    if handler is None:
        _dump_json(_not_implemented("command", args.command_name, payload))
        return 1
    assert command_error is not None
    try:
        _dump_json(_ok(handler(payload)))
        return 0
    except command_error as exc:
        _dump_json(_error(exc))
        return 1


def run_activation(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    _dump_json(_not_implemented("activation", args.activation_command, payload))
    return 1


def run_render_launch_prompt(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    command_payload = dict(payload)
    command_payload["root_path"] = args.root_path
    _dump_json(_not_implemented("render_launch_prompt", "render-launch-prompt", command_payload))
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-agent-team-codex")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    command_parser = subparsers.add_parser("command", help="Run a ResearchAgentTeam command.")
    command_parser.add_argument("command_name", choices=COMMAND_NAMES)
    command_parser.add_argument("--payload-json")
    command_parser.add_argument("--payload-file")
    command_parser.set_defaults(func=run_command)

    activation_parser = subparsers.add_parser("activation", help="Run an activation callback.")
    activation_parser.add_argument("activation_command", choices=ACTIVATION_COMMAND_NAMES)
    activation_parser.add_argument("--root-path", required=True)
    activation_parser.add_argument("--activation-id", required=True)
    activation_parser.add_argument("--runtime-pid", type=int)
    activation_parser.add_argument("--payload-json")
    activation_parser.add_argument("--payload-file")
    activation_parser.set_defaults(func=run_activation)

    prompt_parser = subparsers.add_parser("render-launch-prompt", help="Render a worker launch prompt.")
    prompt_parser.add_argument("--root-path", required=True)
    prompt_parser.add_argument("--payload-json")
    prompt_parser.add_argument("--payload-file")
    prompt_parser.set_defaults(func=run_render_launch_prompt)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
