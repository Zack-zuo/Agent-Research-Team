from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Protocol


WORKER_ACTIVE_STATUSES = {"launch_requested", "spawned", "running", "cancel_requested"}
WORKER_TERMINAL_STATUSES = {"completed", "failed", "interrupted", "cancelled", "stale"}
WORKER_STATUSES = WORKER_ACTIVE_STATUSES | WORKER_TERMINAL_STATUSES


@dataclass(frozen=True)
class WorkerLaunchSpec:
    root_path: str
    activation_id: str
    slot_id: str
    task_id: str
    prompt: str
    prompt_path: str
    timeout_seconds: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def spawn_request(self, adapter: str, handle: Optional[str] = None, diagnostics: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return {
            "activation_id": self.activation_id,
            "slot_id": self.slot_id,
            "task_id": self.task_id,
            "project_root": self.root_path,
            "prompt_path": self.prompt_path,
            "prompt": self.prompt,
            "timeout_seconds": self.timeout_seconds,
            "adapter": adapter,
            "handle": handle,
            "metadata": dict(self.metadata),
            "diagnostics": dict(diagnostics or {}),
        }


@dataclass(frozen=True)
class WorkerLaunchResult:
    status: str
    handle: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in WORKER_STATUSES:
            raise ValueError(f"unsupported worker launch status: {self.status}")


@dataclass(frozen=True)
class WorkerObservation:
    status: str
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in WORKER_STATUSES | {"unknown"}:
            raise ValueError(f"unsupported worker observation status: {self.status}")


class WorkerLaunchAdapter(Protocol):
    name: str

    def launch(self, spec: WorkerLaunchSpec) -> WorkerLaunchResult:
        ...

    def observe(self, worker_state: Mapping[str, Any]) -> WorkerObservation:
        ...

    def cancel(self, worker_state: Mapping[str, Any], reason: str) -> WorkerObservation:
        ...


class CodexSubagentLaunchAdapter:
    """Host-facing adapter: return structured requests for Codex to spawn."""

    name = "codex_subagent"

    def launch(self, spec: WorkerLaunchSpec) -> WorkerLaunchResult:
        return WorkerLaunchResult(
            status="launch_requested",
            diagnostics={"message": "Spawn this request as a Codex subagent and attach the host handle."},
        )

    def observe(self, worker_state: Mapping[str, Any]) -> WorkerObservation:
        return WorkerObservation(status="unknown", diagnostics={"message": "Codex host observation is not available inside the plugin."})

    def cancel(self, worker_state: Mapping[str, Any], reason: str) -> WorkerObservation:
        handle = worker_state.get("handle")
        return WorkerObservation(
            status="cancel_requested",
            diagnostics={
                "reason": reason,
                "handle": handle,
                "message": "Cancel the Codex subagent in the host, then reconcile activation callbacks.",
            },
        )


class FakeSubagentLaunchAdapter:
    """Deterministic adapter for execution-loop tests."""

    name = "fake_subagent"

    def __init__(
        self,
        launch_status: str = "running",
        observe_status: str = "running",
        cancel_status: str = "cancelled",
        failure_summary: str = "Fake subagent failed.",
    ) -> None:
        self.launch_status = launch_status
        self.observe_status = observe_status
        self.cancel_status = cancel_status
        self.failure_summary = failure_summary

    def launch(self, spec: WorkerLaunchSpec) -> WorkerLaunchResult:
        diagnostics: Dict[str, Any] = {"message": f"Fake subagent launch returned {self.launch_status}."}
        if self.launch_status == "failed":
            diagnostics["failure_summary"] = self.failure_summary
        return WorkerLaunchResult(status=self.launch_status, handle=f"fake-{spec.activation_id}", diagnostics=diagnostics)

    def observe(self, worker_state: Mapping[str, Any]) -> WorkerObservation:
        diagnostics: Dict[str, Any] = {"message": f"Fake subagent observation returned {self.observe_status}."}
        if self.observe_status == "failed":
            diagnostics["failure_summary"] = self.failure_summary
        return WorkerObservation(status=self.observe_status, diagnostics=diagnostics)

    def cancel(self, worker_state: Mapping[str, Any], reason: str) -> WorkerObservation:
        diagnostics: Dict[str, Any] = {"reason": reason, "message": f"Fake subagent cancel returned {self.cancel_status}."}
        return WorkerObservation(status=self.cancel_status, diagnostics=diagnostics)


def adapter_from_name(
    adapter_name: str,
    *,
    fake_launch_status: str = "running",
    fake_observe_status: str = "running",
    fake_cancel_status: str = "cancelled",
    fake_failure_summary: str = "Fake subagent failed.",
) -> WorkerLaunchAdapter:
    normalized = adapter_name.replace("-", "_")
    if normalized == "codex_subagent":
        return CodexSubagentLaunchAdapter()
    if normalized == "fake_subagent":
        return FakeSubagentLaunchAdapter(
            launch_status=fake_launch_status,
            observe_status=fake_observe_status,
            cancel_status=fake_cancel_status,
            failure_summary=fake_failure_summary,
        )
    raise ValueError(f"unsupported worker adapter: {adapter_name}")
