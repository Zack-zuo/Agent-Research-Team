# Degraded Adapters and Hooks Runbook

Stage 7 keeps optional integrations observable without making them hard dependencies for unrelated orchestration.

## Adapter health

Inspect `state/adapters/config.json` and `state/adapters/health.json` together. Preflight normalizes health entries to `status`, `message`, and `updated_at`. Degraded graph adapters make `rebuild_graph` return a degraded result without mutating unrelated task, activation, or approval state. Experiment adapter failures stop experiment admission before fake outputs are recorded.

## Hook failures

Hooks are configured in `state/hooks/config.json` and logged under `logs/hooks/YYYY-MM-DD.jsonl`. A failed subscriber creates a command warning and emits `hook.failed`, but the primary command remains successful when core state was written.

To debug:

1. Open the relevant hook log entry.
2. Check subscriber id, return code, stderr, and event type.
3. Run the subscriber command manually in its configured working directory.
4. Disable noisy subscribers by setting `enabled` to false.

Hooks are local subprocess observers, not a reliable remote workflow system.
