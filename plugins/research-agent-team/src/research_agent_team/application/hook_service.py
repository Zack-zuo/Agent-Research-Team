from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional

from research_agent_team.domain import Event, HookSubscriber
from research_agent_team.shared import new_id, now_utc, utc_date
from research_agent_team.storage import ProjectLayout, append_jsonl, read_json


def _subscriber_matches(event_type: str, event_types: List[str]) -> bool:
    return "*" in event_types or event_type in event_types


def _hook_envelope(layout: ProjectLayout, event: Event) -> Dict[str, Any]:
    project = read_json(layout.project_state)
    return {
        "event": event.to_dict(),
        "project": {
            "project_id": project.get("project_id"),
            "name": project.get("name"),
        },
        "timestamp": event.created_at,
        "schema_version": project.get("schema_version"),
        "root_path": str(layout.root),
    }


def _append_hook_event(layout: ProjectLayout, event_type: str, payload: Dict[str, Any]) -> None:
    project = read_json(layout.project_state)
    timestamp = now_utc()
    event = Event(
        event_id=new_id("event"),
        event_type=event_type,
        created_at=timestamp,
        project_id=project.get("project_id"),
        slot_id="supervisor",
        payload=payload,
    )
    append_jsonl(layout.events_dir / f"{utc_date(timestamp)}.jsonl", event.to_dict())


def _append_hook_log(layout: ProjectLayout, payload: Dict[str, Any]) -> None:
    timestamp = payload.get("timestamp")
    if not isinstance(timestamp, str):
        timestamp = now_utc()
        payload["timestamp"] = timestamp
    append_jsonl(layout.hook_logs_dir / f"{utc_date(timestamp)}.jsonl", payload)


def _record_failed_hook_delivery(
    layout: ProjectLayout,
    *,
    subscriber_id: str,
    source_event_type: str,
    returncode: Optional[int],
    stderr: str,
) -> str:
    _append_hook_log(
        layout,
        {
            "timestamp": now_utc(),
            "subscriber_id": subscriber_id,
            "event_type": source_event_type,
            "status": "failed",
            "returncode": returncode,
            "stderr": stderr,
        },
    )
    _append_hook_event(
        layout,
        "hook.failed",
        {
            "subscriber_id": subscriber_id,
            "source_event_type": source_event_type,
            "returncode": returncode,
        },
    )
    return f"Hook subscriber {subscriber_id} failed for event {source_event_type}"


def _subscriber_id(payload: Any, index: int) -> str:
    if isinstance(payload, dict):
        raw_subscriber_id = payload.get("subscriber_id")
        if isinstance(raw_subscriber_id, str) and raw_subscriber_id.strip():
            return raw_subscriber_id.strip()
    return f"subscriber-{index}"


def _payload_would_receive_event(payload: Any, event_type: str) -> bool:
    if not isinstance(payload, dict):
        return True
    if payload.get("enabled") is False:
        return False
    event_types = payload.get("event_types", [])
    if not isinstance(event_types, list) or any(not isinstance(item, str) for item in event_types):
        return True
    return _subscriber_matches(event_type, [item.strip() for item in event_types if item.strip()])


def _load_subscriber_payloads(layout: ProjectLayout, event_type: str) -> tuple[List[Any], List[str]]:
    try:
        config = read_json(layout.hook_config)
    except (OSError, ValueError) as exc:
        warning = _record_failed_hook_delivery(
            layout,
            subscriber_id="hook-config",
            source_event_type=event_type,
            returncode=None,
            stderr=f"invalid hook config: {exc}",
        )
        return [], [warning]
    subscribers = config.get("subscribers", [])
    if not isinstance(subscribers, list):
        warning = _record_failed_hook_delivery(
            layout,
            subscriber_id="hook-config",
            source_event_type=event_type,
            returncode=None,
            stderr="invalid hook config: subscribers must be a list",
        )
        return [], [warning]
    return subscribers, []


def dispatch_event_hooks(layout: ProjectLayout, event: Event) -> List[str]:
    if not layout.hook_config.exists() or event.event_type.startswith("hook."):
        return []
    subscriber_payloads, warnings = _load_subscriber_payloads(layout, event.event_type)
    if not subscriber_payloads:
        return warnings

    subscribers: List[HookSubscriber] = []
    for index, subscriber_payload in enumerate(subscriber_payloads, start=1):
        if not _payload_would_receive_event(subscriber_payload, event.event_type):
            continue
        try:
            if not isinstance(subscriber_payload, dict):
                raise ValueError("hook subscriber must be an object")
            subscribers.append(HookSubscriber.from_dict(subscriber_payload))
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            warnings.append(
                _record_failed_hook_delivery(
                    layout,
                    subscriber_id=_subscriber_id(subscriber_payload, index),
                    source_event_type=event.event_type,
                    returncode=None,
                    stderr=f"invalid hook subscriber config: {exc}",
                )
            )
    if not subscribers:
        return warnings

    envelope = json.dumps(_hook_envelope(layout, event), sort_keys=True)
    for subscriber in subscribers:
        try:
            completed = subprocess.run(
                subscriber.command_argv,
                input=envelope,
                text=True,
                capture_output=True,
                cwd=subscriber.working_directory,
                timeout=subscriber.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            warnings.append(
                _record_failed_hook_delivery(
                    layout,
                    subscriber_id=subscriber.subscriber_id,
                    source_event_type=event.event_type,
                    returncode=None,
                    stderr="timeout",
                )
            )
            continue
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            warnings.append(
                _record_failed_hook_delivery(
                    layout,
                    subscriber_id=subscriber.subscriber_id,
                    source_event_type=event.event_type,
                    returncode=None,
                    stderr=str(exc),
                )
            )
            continue

        if completed.returncode == 0:
            _append_hook_log(
                layout,
                {
                    "timestamp": now_utc(),
                    "subscriber_id": subscriber.subscriber_id,
                    "event_type": event.event_type,
                    "status": "delivered",
                    "returncode": completed.returncode,
                    "stderr": completed.stderr,
                },
            )
            _append_hook_event(
                layout,
                "hook.delivered",
                {"subscriber_id": subscriber.subscriber_id, "source_event_type": event.event_type},
            )
        else:
            warnings.append(
                _record_failed_hook_delivery(
                    layout,
                    subscriber_id=subscriber.subscriber_id,
                    source_event_type=event.event_type,
                    returncode=completed.returncode,
                    stderr=completed.stderr,
                )
            )
    return warnings
