from __future__ import annotations

import argparse
import json
import select
import sys
from pathlib import Path
from typing import Any

from research_agent_team.contracts.commands import ACTIVATION_COMMAND_NAMES, COMMAND_NAMES
from research_agent_team.platform.codex import bridge


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


def run_command(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    result = bridge.run_command(args.command_name, payload)
    _dump_json(result)
    return 0 if result.get("ok") else 1


def run_activation(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    result = bridge.run_activation_callback(
        args.activation_command,
        args.root_path,
        args.activation_id,
        payload,
        args.runtime_pid,
    )
    _dump_json(result)
    return 0 if result.get("ok") else 1


def run_render_launch_prompt(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    result = bridge.render_launch_prompt_for_request(args.root_path, payload)
    if result.get("ok"):
        print(result["result"]["prompt"])
        return 0
    error_payload = result.get("error", {})
    if isinstance(error_payload, dict) and error_payload.get("message"):
        print(error_payload["message"], file=sys.stderr)
    else:
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
    return 1


def _load_context(args: argparse.Namespace) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if getattr(args, "context_json", None):
        context.update(_load_json_object(args.context_json))
    if getattr(args, "context_file", None):
        context.update(_load_json_object(Path(args.context_file).read_text(encoding="utf-8")))
    if getattr(args, "root_path", None):
        context["root_path"] = args.root_path
    if getattr(args, "requester_slot_id", None):
        context["requester_slot_id"] = args.requester_slot_id
    if getattr(args, "confirmation_mode", None):
        context["confirmation_mode"] = args.confirmation_mode
    return context


def run_interpret(args: argparse.Namespace) -> int:
    result = bridge.interpret_request(args.text, _load_context(args))
    _dump_json(result)
    return 0 if result.get("ok") else 1


def run_plan_launches(args: argparse.Namespace) -> int:
    payload = _load_payload(args)
    result = bridge.plan_launches_for_result(args.root_path, payload, source_command=args.source_command, policy=args.policy)
    _dump_json(result)
    return 0 if result.get("ok") else 1


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

    interpret_parser = subparsers.add_parser("interpret", help="Map natural language to a ResearchAgentTeam command plan.")
    interpret_parser.add_argument("--text", required=True, help="Natural-language request to interpret.")
    interpret_parser.add_argument("--root-path")
    interpret_parser.add_argument("--requester-slot-id")
    interpret_parser.add_argument("--confirmation-mode", choices=("conservative", "aggressive"), default="conservative")
    interpret_parser.add_argument("--context-json")
    interpret_parser.add_argument("--context-file")
    interpret_parser.set_defaults(func=run_interpret)

    plan_launches_parser = subparsers.add_parser("plan-launches", help="Plan Codex handling for launch_request results.")
    plan_launches_parser.add_argument("--root-path", required=True)
    plan_launches_parser.add_argument("--source-command", required=True, choices=COMMAND_NAMES + ACTIVATION_COMMAND_NAMES)
    plan_launches_parser.add_argument("--policy", choices=("conservative", "confirm_all"), default="conservative")
    plan_launches_parser.add_argument("--payload-json")
    plan_launches_parser.add_argument("--payload-file")
    plan_launches_parser.set_defaults(func=run_plan_launches)

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
