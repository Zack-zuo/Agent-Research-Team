# Event Contract

Events are append-only JSONL records written under `state/events/YYYY-MM-DD.jsonl`. They are the audit surface for command transitions, recovery actions, degraded behavior, schema migration, and hook delivery. Events are not a task queue and are not the only mechanism for any canonical state transition.

Each event envelope contains:

- `event_id`: generated event id.
- `event_type`: stable event name.
- `created_at`: UTC timestamp.
- `project_id`: project id, when available.
- `slot_id`, `task_id`, `activation_id`, `approval_id`: optional entity references.
- `payload`: event-specific object.

Important event families:

- `project.*`: project creation, mode changes, pause, resume.
- `topology.*`: slot creation, staffing mutations, retirement.
- `task.*`: task creation, admission, callback-driven transitions, recovery.
- `approval.*`: approval creation and decisions.
- `knowledge.*`, `graph.*`, `experiment.*`, `report.*`: workflow outputs and degraded paths.
- `schema.migration_started`, `schema.migrated`: audited migration lifecycle.
- `hook.delivered`, `hook.failed`: hook delivery outcomes.

Hook delivery receives the original event in a JSON envelope along with project id, name, schema version, timestamp, and root path. Hook-delivery events are emitted without recursively dispatching hooks. Consumers should treat events as audit records and should read canonical state files for current truth.

Event append failures are command failures because events are part of canonical audit state. Hook subscriber failures are different: they are logged and surfaced as warnings without rolling back the primary command result.
