from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from research_agent_team.application.errors import CommandError
from research_agent_team.integrations.experiments.metrics import parse_metrics_file
from research_agent_team.shared import now_utc
from research_agent_team.storage import ProjectLayout, write_json_atomic, write_text_atomic


MAX_DIFF_BYTES = 200_000


class LocalCommandExperimentAdapter:
    adapter_type = "local_command"

    def __init__(self, *, settings: Optional[Dict[str, Any]] = None) -> None:
        self.settings = dict(settings or {})

    def validate_request(self, *, layout: ProjectLayout, run_parameters: Dict[str, Any]) -> None:
        self._command(run_parameters)
        self._timeout_seconds(run_parameters)
        if run_parameters.get("working_directory") is not None:
            self._working_directory(layout, run_parameters, PurePosixPath("experiments") / "runs" / "placeholder")
        self._relative_paths(run_parameters, "metrics_files")
        self._relative_paths(run_parameters, "output_paths")
        if run_parameters.get("allow_command_execution") is not True:
            raise CommandError(
                "experiment_command_not_allowed",
                "Local command experiments require run_parameters.allow_command_execution=true.",
                field="run_parameters.allow_command_execution",
            )

    def prepare(self, *, task_id: str, bundle_id: str, experiment_request: Dict[str, Any]) -> Dict[str, Any]:
        run_parameters = dict(experiment_request.get("run_parameters") or {})
        return {
            "adapter_type": self.adapter_type,
            "prepared": True,
            "task_id": task_id,
            "bundle_id": bundle_id,
            "experiment_request_id": experiment_request["experiment_request_id"],
            "command": list(run_parameters.get("command") or []),
            "working_directory": run_parameters.get("working_directory"),
            "timeout_seconds": run_parameters.get("timeout_seconds"),
        }

    def run(
        self,
        *,
        experiment_request: Dict[str, Any],
        budget_envelope: Dict[str, Any],
        layout: ProjectLayout,
        run: Dict[str, Any],
        activation: Dict[str, Any],
        task: Dict[str, Any],
    ) -> Dict[str, Any]:
        del budget_envelope, task
        run_parameters = dict(experiment_request.get("run_parameters") or {})
        command = self._command(run_parameters)
        timeout_seconds = self._timeout_seconds(run_parameters)
        run_root = PurePosixPath(run["run_root"])
        run_dir = layout.root / run_root
        logs_dir = run_dir / "logs"
        diagnostics_dir = run_dir / "diagnostics"
        outputs_dir = run_dir / "outputs"
        generated_dir = outputs_dir / "generated"
        git_dir = run_dir / "git"
        metrics_dir = run_dir / "metrics"
        for directory in (logs_dir, diagnostics_dir, generated_dir, git_dir, metrics_dir):
            directory.mkdir(parents=True, exist_ok=True)

        working_directory = self._working_directory(layout, run_parameters, run_root)
        before_git = self._git_state(layout.root)
        start_monotonic = time.monotonic()
        started_at = now_utc()
        diagnostics: List[Dict[str, Any]] = []
        env = os.environ.copy()
        env.update(
            {
                "RAT_PROJECT_ROOT": str(layout.root),
                "RAT_EXPERIMENT_REQUEST_ID": str(experiment_request["experiment_request_id"]),
                "RAT_EXPERIMENT_RUN_ID": str(run["experiment_run_id"]),
                "RAT_EXPERIMENT_RUN_ROOT": str(run_dir),
                "RAT_EXPERIMENT_OUTPUT_DIR": str(generated_dir),
                "RAT_EXPERIMENT_METRICS_DIR": str(metrics_dir),
                "RAT_ACTIVATION_ID": str(activation.get("activation_id") or ""),
            }
        )

        exit_code: Optional[int]
        stdout = ""
        stderr = ""
        status = "succeeded"
        try:
            completed = subprocess.run(
                command,
                cwd=working_directory,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            exit_code = completed.returncode
            status = "succeeded" if completed.returncode == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            stdout = _timeout_text(exc.stdout)
            stderr = _timeout_text(exc.stderr)
            exit_code = None
            status = "timed_out"
            diagnostics.append(
                {
                    "code": "command_timeout",
                    "message": f"Command timed out after {timeout_seconds} seconds.",
                    "timeout_seconds": timeout_seconds,
                }
            )
        except OSError as exc:
            exit_code = None
            status = "failed"
            stderr = str(exc)
            diagnostics.append(
                {
                    "code": "command_launch_failed",
                    "message": f"Could not launch command: {exc}",
                    "command_executable": command[0],
                }
            )
        ended_at = now_utc()
        duration_seconds = round(time.monotonic() - start_monotonic, 6)
        after_git = self._git_state(layout.root)
        diff_summary = self._git_diff(layout.root, "--stat")
        full_diff = self._git_diff(layout.root, "--binary")

        stdout_path = logs_dir / "stdout.log"
        stderr_path = logs_dir / "stderr.log"
        write_text_atomic(stdout_path, stdout)
        write_text_atomic(stderr_path, stderr)

        environment = self._environment(layout, working_directory, command)
        write_json_atomic(diagnostics_dir / "environment.json", environment)
        write_json_atomic(git_dir / "before.json", before_git)
        write_json_atomic(git_dir / "after.json", after_git)
        write_text_atomic(git_dir / "diff-stat.txt", diff_summary)
        if full_diff:
            if len(full_diff.encode("utf-8")) <= MAX_DIFF_BYTES:
                write_text_atomic(git_dir / "diff.patch", full_diff)
            else:
                diagnostics.append(
                    {
                        "code": "git_diff_too_large",
                        "message": f"Full git diff exceeded {MAX_DIFF_BYTES} bytes and was not written.",
                    }
                )

        metrics, metric_artifacts = self._parse_metrics(layout, run_parameters, working_directory, metrics_dir, diagnostics)
        output_artifacts = self._generated_outputs(layout, generated_dir)
        output_artifacts.extend(self._declared_outputs(layout, run_parameters, working_directory))

        execution = {
            "adapter_type": self.adapter_type,
            "experiment_run_id": run["experiment_run_id"],
            "experiment_request_id": experiment_request["experiment_request_id"],
            "activation_id": activation.get("activation_id"),
            "command": command,
            "cwd": _project_relative(layout, working_directory),
            "status": status,
            "exit_code": exit_code,
            "timeout_seconds": timeout_seconds,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration_seconds,
        }
        write_json_atomic(logs_dir / "execution.json", execution)

        artifact_descriptors = [
            {"path": _project_relative(layout, stdout_path), "type": "experiment_stdout_log"},
            {"path": _project_relative(layout, stderr_path), "type": "experiment_stderr_log"},
            {"path": _project_relative(layout, logs_dir / "execution.json"), "type": "experiment_execution_metadata"},
            {"path": _project_relative(layout, diagnostics_dir / "environment.json"), "type": "experiment_environment"},
            {"path": _project_relative(layout, git_dir / "before.json"), "type": "experiment_git_state"},
            {"path": _project_relative(layout, git_dir / "after.json"), "type": "experiment_git_state"},
            {"path": _project_relative(layout, git_dir / "diff-stat.txt"), "type": "experiment_git_diff_summary"},
            *metric_artifacts,
            *output_artifacts,
        ]
        if (git_dir / "diff.patch").exists():
            artifact_descriptors.append({"path": _project_relative(layout, git_dir / "diff.patch"), "type": "experiment_git_diff"})

        return {
            "adapter_type": self.adapter_type,
            "title": experiment_request["title"],
            "objective": experiment_request["objective"],
            "hypothesis": experiment_request.get("hypothesis", ""),
            "method": experiment_request["method"],
            "run_parameters": run_parameters,
            "command": execution,
            "environment": environment,
            "git": {
                "before": before_git,
                "after": after_git,
                "diff_summary": diff_summary,
                "diff_path": _project_relative(layout, git_dir / "diff.patch") if (git_dir / "diff.patch").exists() else None,
            },
            "metrics": metrics,
            "diagnostics": diagnostics,
            "artifact_descriptors": _dedupe_artifacts(artifact_descriptors),
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

    def _command(self, run_parameters: Dict[str, Any]) -> List[str]:
        raw_command = run_parameters.get("command")
        if (
            not isinstance(raw_command, list)
            or not raw_command
            or any(not isinstance(part, str) or not part for part in raw_command)
        ):
            raise CommandError("invalid_payload", "run_parameters.command must be a non-empty list of strings", field="run_parameters.command")
        return list(raw_command)

    def _timeout_seconds(self, run_parameters: Dict[str, Any]) -> int:
        raw_timeout = run_parameters.get("timeout_seconds", self.settings.get("default_timeout_seconds", 300))
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)) or raw_timeout <= 0:
            raise CommandError("invalid_payload", "run_parameters.timeout_seconds must be a positive number", field="run_parameters.timeout_seconds")
        timeout = int(raw_timeout)
        max_timeout = self.settings.get("max_timeout_seconds")
        if isinstance(max_timeout, (int, float)) and not isinstance(max_timeout, bool) and timeout > int(max_timeout):
            raise CommandError(
                "invalid_payload",
                "run_parameters.timeout_seconds exceeds configured maximum",
                field="run_parameters.timeout_seconds",
                max_timeout_seconds=int(max_timeout),
            )
        return timeout

    def _working_directory(self, layout: ProjectLayout, run_parameters: Dict[str, Any], run_root: PurePosixPath) -> Path:
        raw_working_directory = run_parameters.get("working_directory")
        relative = PurePosixPath(raw_working_directory) if isinstance(raw_working_directory, str) and raw_working_directory.strip() else run_root
        if relative.is_absolute() or ".." in relative.parts:
            raise CommandError("invalid_payload", "run_parameters.working_directory must stay inside the project", field="run_parameters.working_directory")
        if relative.parts[:1] == ("state",):
            raise CommandError("invalid_payload", "run_parameters.working_directory cannot be under state/", field="run_parameters.working_directory")
        path = layout.project_relative_path(relative.as_posix())
        if not path.exists() or not path.is_dir():
            raise CommandError(
                "invalid_payload",
                "run_parameters.working_directory must reference an existing project directory",
                field="run_parameters.working_directory",
                path=relative.as_posix(),
            )
        return path

    def _relative_paths(self, run_parameters: Dict[str, Any], key: str) -> List[PurePosixPath]:
        value = run_parameters.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise CommandError("invalid_payload", f"run_parameters.{key} must be a list of non-empty strings", field=f"run_parameters.{key}")
        paths = []
        for item in value:
            relative = PurePosixPath(item)
            if relative.is_absolute() or "." in relative.parts or ".." in relative.parts:
                raise CommandError("invalid_payload", f"run_parameters.{key} entries must be relative paths", field=f"run_parameters.{key}")
            paths.append(relative)
        return paths

    def _parse_metrics(
        self,
        layout: ProjectLayout,
        run_parameters: Dict[str, Any],
        working_directory: Path,
        metrics_dir: Path,
        diagnostics: List[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], List[Dict[str, str]]]:
        metrics: Dict[str, Any] = {}
        artifacts: List[Dict[str, str]] = []
        for relative in self._relative_paths(run_parameters, "metrics_files"):
            metrics_path = (working_directory / Path(*relative.parts)).resolve()
            if layout.root not in metrics_path.parents and metrics_path != layout.root:
                diagnostics.append({"code": "metrics_path_outside_project", "message": f"Metrics path escapes project root: {relative}"})
                continue
            if not metrics_path.exists() or not metrics_path.is_file():
                diagnostics.append({"code": "metrics_file_missing", "message": f"Metrics file was not found: {relative}"})
                continue
            archived_path = metrics_dir / "__".join(relative.parts)
            shutil.copyfile(metrics_path, archived_path)
            artifacts.append({"path": _project_relative(layout, archived_path), "type": "experiment_metrics"})
            try:
                metrics.update(parse_metrics_file(metrics_path))
            except Exception as exc:
                diagnostics.append(
                    {
                        "code": "metrics_parse_failed",
                        "message": f"Could not parse metrics file {relative}: {exc}",
                        "path": _project_relative(layout, metrics_path),
                    }
                )
        return metrics, artifacts

    def _declared_outputs(self, layout: ProjectLayout, run_parameters: Dict[str, Any], working_directory: Path) -> List[Dict[str, str]]:
        artifacts = []
        for relative in self._relative_paths(run_parameters, "output_paths"):
            output_path = (working_directory / Path(*relative.parts)).resolve()
            if output_path.is_file() and (layout.root in output_path.parents or output_path == layout.root):
                artifacts.append({"path": _project_relative(layout, output_path), "type": "experiment_generated_output"})
        return artifacts

    def _generated_outputs(self, layout: ProjectLayout, generated_dir: Path) -> List[Dict[str, str]]:
        artifacts = []
        for path in sorted(generated_dir.rglob("*")):
            if path.is_file():
                artifacts.append({"path": _project_relative(layout, path), "type": "experiment_generated_output"})
        return artifacts

    def _environment(self, layout: ProjectLayout, working_directory: Path, command: List[str]) -> Dict[str, Any]:
        return {
            "python": {
                "version": platform.python_version(),
                "executable": sys.executable,
                "implementation": platform.python_implementation(),
            },
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "platform": platform.platform(),
            },
            "cwd": _project_relative(layout, working_directory),
            "project_root": str(layout.root),
            "command_executable": command[0],
            "tool_versions": {
                "git": self._tool_version("git", "--version"),
                "uv": self._tool_version("uv", "--version"),
            },
        }

    def _tool_version(self, *command: str) -> Optional[str]:
        try:
            completed = subprocess.run(list(command), text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return (completed.stdout or completed.stderr).strip() or None

    def _git_state(self, root: Path) -> Dict[str, Any]:
        is_repo = (self._git_output(root, "rev-parse", "--is-inside-work-tree") or "").strip() == "true"
        if not is_repo:
            return {"is_git_repository": False}
        branch = (self._git_output(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
        commit = (self._git_output(root, "rev-parse", "HEAD") or "").strip()
        status = self._git_output(root, "status", "--porcelain") or ""
        return {
            "is_git_repository": True,
            "branch": branch,
            "commit": commit,
            "is_dirty": bool(status.strip()),
            "status_porcelain": [line for line in status.splitlines() if line],
        }

    def _git_output(self, root: Path, *args: str) -> Optional[str]:
        try:
            completed = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout

    def _git_diff(self, root: Path, *args: str) -> str:
        head_diff = self._git_output(root, "diff", "HEAD", *args)
        if head_diff is not None:
            return head_diff
        unstaged = self._git_output(root, "diff", *args) or ""
        staged = self._git_output(root, "diff", "--cached", *args) or ""
        if unstaged and staged:
            return unstaged.rstrip() + "\n" + staged
        return unstaged or staged


def _project_relative(layout: ProjectLayout, path: Path) -> str:
    return path.resolve().relative_to(layout.root).as_posix()


def _timeout_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _dedupe_artifacts(artifacts: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped = []
    for artifact in artifacts:
        key = (artifact["path"], artifact["type"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(artifact)
    return deduped
