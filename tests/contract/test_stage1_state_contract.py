import json
import tempfile
import unittest
from pathlib import Path

import yaml

from research_agent_team.config.defaults import default_adapter_config, default_budget_state, default_policy_files
from research_agent_team.domain import AgentSlot, OperatingMode, ProjectStatus, SlotRole, SlotStatus
from research_agent_team.storage import ProjectLayout, append_jsonl, read_json, write_json_atomic, write_yaml_atomic


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "research-agent-team"


class Stage1StateContractTests(unittest.TestCase):
    def test_domain_models_validate_local_invariants_without_filesystem_dependencies(self) -> None:
        senior = AgentSlot(
            slot_id="senior-01",
            role=SlotRole.SENIOR_PHD,
            parent_slot_id="supervisor",
            status=SlotStatus.ACTIVE,
            workspace_root="agents/senior-01/workspace",
            kb_root="agents/senior-01/kb",
            inbox_root="agents/senior-01/inbox",
            outbox_root="agents/senior-01/outbox",
        )

        self.assertEqual(senior.role, SlotRole.SENIOR_PHD)
        self.assertEqual(senior.to_dict()["status"], "active")

        with self.assertRaises(ValueError):
            AgentSlot(
                slot_id="junior-01",
                role=SlotRole.JUNIOR_PHD,
                parent_slot_id=None,
                status=SlotStatus.ACTIVE,
                workspace_root="agents/junior-01/workspace",
                kb_root="agents/junior-01/kb",
                inbox_root="agents/junior-01/inbox",
                outbox_root="agents/junior-01/outbox",
            )

        self.assertEqual(OperatingMode.AUTONOMOUS.value, "autonomous")
        self.assertEqual(ProjectStatus.PAUSED.value, "paused")

    def test_project_layout_exposes_canonical_paths_and_rejects_root_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            layout = ProjectLayout(Path(tmpdir) / "project")

            self.assertEqual(layout.project_manifest, layout.root / "project.yaml")
            self.assertEqual(layout.project_state, layout.root / "state" / "project.json")
            self.assertEqual(layout.topology_state, layout.root / "state" / "topology" / "current.json")
            self.assertEqual(layout.slot_state_path("senior-01"), layout.root / "state" / "slots" / "senior-01.json")
            self.assertEqual(layout.slot_workspace("senior-01"), layout.root / "agents" / "senior-01" / "workspace")

            self.assertEqual(
                layout.project_relative_path("shared/wiki/index.md"),
                layout.root / "shared" / "wiki" / "index.md",
            )

            for unsafe in ["../escape.md", "/tmp/escape.md", "shared/../../escape.md"]:
                with self.assertRaises(ValueError, msg=unsafe):
                    layout.project_relative_path(unsafe)

    def test_atomic_json_yaml_and_jsonl_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            json_path = root / "state" / "project.json"
            yaml_path = root / "project.yaml"
            log_path = root / "state" / "events" / "2026-04-29.jsonl"

            write_json_atomic(json_path, {"name": "demo", "schema_version": "0.2.0"})
            write_yaml_atomic(yaml_path, {"name": "demo", "root_path": str(root)})
            append_jsonl(log_path, {"event_type": "project.created"})
            append_jsonl(log_path, {"event_type": "topology.slot_created"})

            self.assertEqual(read_json(json_path)["schema_version"], "0.2.0")
            self.assertEqual(yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["name"], "demo")
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["event_type"] for event in events], ["project.created", "topology.slot_created"])

    def test_defaults_and_schemas_share_required_stage1_shapes(self) -> None:
        policies = default_policy_files()

        self.assertIn("staffing.json", policies)
        self.assertEqual(policies["staffing.json"]["max_active_seniors"], 3)
        self.assertEqual(policies["staffing.json"]["max_active_juniors_per_senior"], 3)
        self.assertIn("project_limits", default_budget_state())
        self.assertEqual(default_adapter_config()["graph"]["adapter"], "graphify")

        schema_expectations = {
            "state/project.schema.json": {"project_id", "name", "schema_version", "status", "operating_mode"},
            "state/slot.schema.json": {"slot_id", "role", "status", "parent_slot_id"},
            "state/topology.schema.json": {"project_id", "generation", "supervisor_slot_id", "active_slot_ids"},
            "events/event-envelope.schema.json": {"event_id", "event_type", "created_at", "project_id"},
        }
        for relative_path, required_keys in schema_expectations.items():
            schema = json.loads((PLUGIN_ROOT / "schemas" / relative_path).read_text(encoding="utf-8"))
            self.assertTrue(required_keys.issubset(set(schema["required"])), relative_path)


if __name__ == "__main__":
    unittest.main()
