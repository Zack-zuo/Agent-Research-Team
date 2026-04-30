from __future__ import annotations

from typing import Any, Dict

from research_agent_team.storage import ProjectLayout, read_json


def load_staffing_policy(layout: ProjectLayout) -> Dict[str, Any]:
    return read_json(layout.state_dir / "policies" / "staffing.json")


def load_execution_policy(layout: ProjectLayout) -> Dict[str, Any]:
    return read_json(layout.state_dir / "policies" / "execution.json")
