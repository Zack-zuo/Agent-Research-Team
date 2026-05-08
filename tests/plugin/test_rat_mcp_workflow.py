import json
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from research_agent_team.platform.codex import mcp_server


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"
SCRIPT = PLUGIN_ROOT / "scripts" / "rat_plugin_mcp.py"


class RatMcpWorkflowTests(unittest.TestCase):
    def create_project(self, root: Path) -> None:
        result = mcp_server.run_command(
            "create_project",
            {
                "name": "MCP Workflow Demo",
                "root_path": str(root),
                "initial_charter_text": "MCP workflow test project.",
            },
        )
        self.assertTrue(result["ok"], result)

    def test_run_command_includes_launch_plan_when_root_path_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)

            assigned = mcp_server.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Review shared notes and write a summary.",
                    "success_criteria": ["Write summary"],
                    "input_path_roots": ["shared/raw"],
                    "expected_output_types": ["markdown"],
                },
            )

            self.assertTrue(assigned["ok"], assigned)
            self.assertEqual(assigned["launch_plan"]["auto_launch_count"], 1)
            self.assertEqual(assigned["launch_plan"]["decisions"][0]["action"], "auto_launch")

    def test_activation_callback_includes_launch_plan_for_terminal_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = mcp_server.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Review shared notes and write a summary.",
                    "success_criteria": ["Write summary"],
                },
            )
            activation_id = assigned["result"]["launch_request"]["activation_id"]

            marked = mcp_server.activation_callback("mark-running", str(root), activation_id)

            self.assertTrue(marked["ok"], marked)
            self.assertEqual(marked["launch_plan"]["launch_request_count"], 0)
            self.assertIn("No launch_request", marked["launch_plan"]["messages"][0])

    def test_render_launch_prompt_returns_prompt_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = mcp_server.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Review shared notes and write a summary.",
                    "success_criteria": ["Write summary"],
                },
            )

            rendered = mcp_server.render_launch_prompt(str(root), assigned["result"]["launch_request"])

            self.assertTrue(rendered["ok"], rendered)
            self.assertIn("Before doing work, mark the activation running", rendered["result"]["prompt"])

    def test_mcp_stdio_lists_workflow_tools_and_handles_call(self) -> None:
        mcp_config = json.loads((PLUGIN_ROOT / "mcp" / ".mcp.json").read_text(encoding="utf-8"))
        server = mcp_config["mcpServers"]["research-agent-team"]
        process = subprocess.Popen(
            [server["command"], *server["args"]],
            cwd=PLUGIN_ROOT / server["cwd"],
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            initialize = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "rat-test", "version": "0.1.0"},
                },
            }
            self._send(process, initialize)
            initialized = self._read_message(process)
            self.assertEqual(initialized["id"], 1)
            self.assertIn("serverInfo", initialized["result"])

            self._send(process, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
            self._send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
            tools = self._read_message(process)
            tool_names = {tool["name"] for tool in tools["result"]["tools"]}
            self.assertIn("interpret_request", tool_names)
            self.assertIn("run_command", tool_names)
            self.assertIn("activation_callback", tool_names)
            self.assertIn("plan_launches", tool_names)
            self.assertIn("render_launch_prompt", tool_names)
            self.assertIn("execution_start_pending", tool_names)
            self.assertIn("execution_attach_subagent", tool_names)
            self.assertIn("execution_reconcile", tool_names)

            self._send(
                process,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "interpret_request",
                        "arguments": {"text": "show current progress"},
                    },
                },
            )
            called = self._read_message(process)
            self.assertEqual(called["id"], 3)
            self.assertNotIn("error", called)
        finally:
            process.kill()
            process.communicate(timeout=5)

    def test_execution_start_pending_mcp_uses_fake_subagent_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "rat-project"
            self.create_project(root)
            assigned = mcp_server.run_command(
                "assign_task",
                {
                    "root_path": str(root),
                    "requester_slot_id": "supervisor",
                    "owner_slot_id": "senior-01",
                    "title": "Review notes",
                    "description": "Review shared notes and write a summary.",
                    "success_criteria": ["Write summary"],
                },
            )
            activation_id = assigned["result"]["launch_request"]["activation_id"]

            started = mcp_server.execution_start_pending(
                str(root),
                adapter_name="fake-subagent",
                fake_launch_status="spawned",
            )

            self.assertTrue(started["ok"], started)
            self.assertEqual(started["result"]["started_count"], 1)
            self.assertEqual(started["result"]["subagent_launch_requests"][0]["activation_id"], activation_id)

    def _send(self, process: subprocess.Popen[str], message: dict[str, Any]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def _read_message(self, process: subprocess.Popen[str]) -> dict[str, Any]:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 10)
        if not readable:
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.fail(f"Timed out waiting for MCP response. stderr={stderr}")
        line = process.stdout.readline()
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            stderr = process.stderr.read() if process.stderr is not None else ""
            self.fail(f"Invalid JSON-RPC line: {line!r}; stderr={stderr}; error={exc}")
        self.assertIsInstance(parsed, dict)
        return parsed


if __name__ == "__main__":
    unittest.main()
