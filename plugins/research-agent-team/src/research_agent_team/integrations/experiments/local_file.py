from __future__ import annotations

from typing import Any, Dict, List


class LocalFileExperimentAdapter:
    adapter_type = "local_file"

    def prepare(self, *, task_id: str, bundle_id: str, experiment_request: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "prepared": True,
            "task_id": task_id,
            "bundle_id": bundle_id,
            "experiment_request_id": experiment_request["experiment_request_id"],
        }

    def run(self, *, experiment_request: Dict[str, Any], budget_envelope: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "title": experiment_request["title"],
            "objective": experiment_request["objective"],
            "hypothesis": experiment_request.get("hypothesis", ""),
            "method": experiment_request["method"],
            "run_parameters": dict(experiment_request.get("run_parameters") or {}),
            "budget_envelope": dict(budget_envelope or {}),
        }

    def compare(self, *, primary_run_id: str, compared_run_ids: List[str]) -> Dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "primary_run_id": primary_run_id,
            "compared_run_ids": list(compared_run_ids),
            "comparison_count": len(compared_run_ids),
        }

    def publish(self, *, experiment_run_id: str, output_dir: str) -> Dict[str, Any]:
        return {
            "adapter_type": self.adapter_type,
            "experiment_run_id": experiment_run_id,
            "output_dir": output_dir,
            "published": True,
        }
