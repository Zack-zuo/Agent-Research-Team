# ResearchAgentTeam Operator Guide

This guide covers the Stage 7 / M8 local-first plugin behavior. Operators should use command results and generated reports first, then inspect raw JSON only when diagnosing a specific issue.

## Returning to a project

Run `open_project` when returning to an existing project root. It performs schema preparation, supported migration, health preflight, stale activation recovery, adapter health normalization, and derived-view rebuilds. The result includes warnings, migration flags, adapter health, recovery counters, active and queued work counts, pending approvals, and recent artifacts.

## Routine status

Run `request_status` for the main operator snapshot. It writes `shared/reports/status-latest.md`, indexes that report as an artifact, dispatches matching hooks, and returns the same important counters in JSON.

Warnings are non-fatal. They usually indicate support-surface repair, degraded optional adapters, hook delivery failures, missing optional report sections, or recovery actions. Fatal integrity failures return structured errors and stop the command.

## Important paths

- `state/adapters/health.json`: normalized adapter health.
- `state/hooks/config.json`: best-effort local hook subscribers.
- `logs/hooks/YYYY-MM-DD.jsonl`: hook delivery diagnostics.
- `state/migrations/records/*.json`: completed migration records.
- `state/migrations/backups/<migration-id>/`: backups for migration-mutated files.
- `shared/reports/status-latest.md`: latest status report.
- `shared/reports/final-package-latest.md`: latest final package report.

## Recovery posture

Preflight repairs support surfaces but not missing canonical business state. If an error reports a missing task, slot, activation, experiment, or approval, restore the canonical file from backup before rerunning commands. After manual recovery, run `open_project` and `request_status`.
