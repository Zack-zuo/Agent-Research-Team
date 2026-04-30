# Changelog

## M5 - Stage 4

- implemented governance services for artifact indexing, visibility enforcement, permission manifests, approval replay, status reporting, report generation, and derived slot inbox/outbox views
- exposed `approve_checkpoint`, `reject_checkpoint`, `request_status`, and `generate_report` through the Codex-facing CLI
- made budget hard stops, pending approvals, recent artifacts, and generated Markdown reports visible through command results and report artifacts

## M4 - Stage 3

- implemented task admission for `assign_task`, including requester and owner validation, queue placement, effective budget envelopes, and immediate admission for idle eligible slots
- added activation runtime state, launch request materialization, launch prompt rendering, worker callbacks, checkpoints, completion/failure/interruption/cancellation handling, and stale activation recovery
- preserved command-driven queue advancement and one active activation per slot

## 0.1.0

- established the canonical source-monorepo layout outside the plugin subtree
- backfilled repo-level documentation, CI metadata, release scripts, and tests
- kept the installable plugin boundary at `plugins/research-agent-team/`
