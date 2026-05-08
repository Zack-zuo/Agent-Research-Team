# Activation Failures Runbook

Use this runbook when worker activations fail, stop sending heartbeats, are interrupted, or leave a slot unable to accept new work.

## First checks

1. Run `open_project` for the project root. Stage 7 preflight validates integrity, repairs support surfaces, runs stale activation recovery, and returns recovery counters.
2. Run `request_status`. The status report shows active, queued, blocked, and completed work plus budget hard stops and warnings.
3. Inspect the activation record under `state/activations/<activation-id>.json`.
4. Inspect the owner slot under `state/slots/<slot-id>.json` and the task under `state/tasks/<task-id>.json`.
5. If the activation was managed by the execution loop, inspect `agents/<slot-id>/activations/<activation-id>/worker.json` and `worker-events.jsonl`.

## Common outcomes

- `failed`: the worker reported failure. Review `failure_summary`, output artifacts, and task status.
- `interrupted`: recovery found stale work. If a checkpoint exists, the task may be requeued with resume context. Without a checkpoint, the task may be blocked.
- `cancelled`: the supervisor cancelled the activation. Confirm whether follow-up work is needed.
- `running` with old heartbeat: run `open_project` again to force stale recovery.
- worker `launch_requested` without a handle: the Codex supervisor did not attach a spawned subagent. Spawn the subagent from the stored prompt or cancel the activation.
- worker `spawned` or `running` with terminal activation state: run `research-agent-team-codex execution reconcile --root-path <project-root>` to update worker metadata.

## Budget hard stops

If `request_status` shows `budget_hard_limit`, inspect:

- the activation `consumed_budget`
- the task `budget_envelope`
- `state/budgets/current.json`
- pending approvals under `state/approvals/`

Do not clear hard-stop state by editing only reports or inbox views. Use approval flows or assign follow-up work with an explicit budget envelope.

## Recovery principles

Restore canonical state first. Missing task, slot, or activation records are fatal integrity failures because derived views cannot safely repair them. Use filesystem backups, migration backups, or version-control snapshots before editing JSON by hand.

After manual recovery, run `open_project` followed by `request_status`. If both succeed, use `resume_project` when queued work should continue.

For managed execution, use these commands before hand-editing worker metadata:

```bash
research-agent-team-codex execution inspect --root-path <project-root>
research-agent-team-codex execution reconcile --root-path <project-root>
research-agent-team-codex execution cancel --root-path <project-root> --activation-id <activation-id> --reason operator_recovery
```
