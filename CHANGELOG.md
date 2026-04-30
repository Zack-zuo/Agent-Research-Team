# Changelog

## M4 - Stage 3

- implemented task admission for `assign_task`, including requester and owner validation, queue placement, effective budget envelopes, and immediate admission for idle eligible slots
- added activation runtime state, launch request materialization, launch prompt rendering, worker callbacks, checkpoints, completion/failure/interruption/cancellation handling, and stale activation recovery
- preserved command-driven queue advancement and one active activation per slot

## 0.1.0

- established the canonical source-monorepo layout outside the plugin subtree
- backfilled repo-level documentation, CI metadata, release scripts, and tests
- kept the installable plugin boundary at `plugins/research-agent-team/`
