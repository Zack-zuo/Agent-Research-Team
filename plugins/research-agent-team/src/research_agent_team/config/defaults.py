from __future__ import annotations

from typing import Any, Dict


def default_policy_files() -> Dict[str, Dict[str, Any]]:
    return {
        "staffing.json": {
            "max_active_seniors": 3,
            "max_active_juniors_per_senior": 3,
            "require_staffing_approval": False,
        },
        "execution.json": {
            "queue_advancement": "command_driven",
            "executable_roles": ["senior_phd", "junior_phd"],
            "one_active_activation_per_slot": True,
        },
        "budget.json": {
            "budget_dimensions": ["token_budget", "wall_clock_seconds", "compute_units", "experiment_runs"],
            "default_task_budget": {"token_budget": 200000, "wall_clock_seconds": 14400},
            "requires_approval_for_override": True,
        },
        "approvals.json": {
            "staffing": "immediate",
            "budget_overrides": "supervisor_or_user",
            "final_package": "user",
        },
    }


def default_budget_state() -> Dict[str, Any]:
    return {
        "project_limits": {"token_budget": 2000000, "wall_clock_seconds": 172800, "compute_units": 1000},
        "role_limits": {
            "senior_phd": {"token_budget": 500000, "wall_clock_seconds": 86400},
            "junior_phd": {"token_budget": 250000, "wall_clock_seconds": 43200, "experiment_runs": 20},
        },
        "task_overrides": {},
        "consumed_to_date": {},
        "thresholds": {"soft_fraction": 0.8, "hard_fraction": 1.0},
        "last_recomputed_at": None,
    }


def default_adapter_config() -> Dict[str, Any]:
    return {
        "graph": {"adapter": "local_file", "mode": "local", "enabled": True},
        "experiments": {"adapter": "local_file", "mode": "local", "enabled": True},
        "hooks": {"mode": "best_effort"},
    }


def default_adapter_health() -> Dict[str, Any]:
    return {
        "graph": {"status": "healthy", "message": "Local-file graph adapter configured."},
        "experiments": {"status": "healthy", "message": "Local-file experiment adapter configured."},
        "hooks": {"status": "healthy", "message": "No hook subscribers configured."},
    }


def default_hook_config() -> Dict[str, Any]:
    return {"subscribers": []}


def default_knowledge_state(scope_type: str, scope_id: str) -> Dict[str, Any]:
    return {
        "scope_type": scope_type,
        "scope_id": scope_id,
        "source_hashes": {},
        "compiled_artifact_ids_by_output_path": {},
        "backlinks": {},
        "last_attempted_at": None,
        "last_synced_at": None,
        "last_error": None,
        "graph_dirty": False,
    }
