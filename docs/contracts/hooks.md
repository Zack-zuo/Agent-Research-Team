# Hook Contract

Hooks are local best-effort subscribers configured per generated project at `state/hooks/config.json`. They are intended for observability, local notifications, and extension scripts. Hooks must never be the only route for task delegation, approval replay, budget enforcement, reporting, or any canonical state transition.

The hook config shape is:

```json
{
  "subscribers": [
    {
      "subscriber_id": "capture-report-generated",
      "enabled": true,
      "event_types": ["report.generated", "*"],
      "command_argv": ["python", "subscriber.py"],
      "working_directory": "/absolute/project/root",
      "timeout_seconds": 10
    }
  ]
}
```

Matching uses exact event type or `*`. Enabled matching subscribers are launched as local subprocesses. The event envelope is written to stdin as JSON. The configured command must exit with status 0 for delivery to be considered successful.

Delivery outcomes are written to `logs/hooks/YYYY-MM-DD.jsonl` with `timestamp`, `subscriber_id`, `event_type`, `status`, `returncode`, and `stderr`. Successful delivery emits `hook.delivered`; failed delivery emits `hook.failed`. Hook events are audit records and do not dispatch hooks recursively.

Failure semantics:

- Nonzero exit, launch errors, invalid command argv, and timeouts are captured as failed deliveries.
- The primary command result remains successful when core business logic succeeded.
- The command result includes warnings naming failed subscribers.
- Operators should fix or disable noisy subscribers in `state/hooks/config.json`.

Hooks are part of Stage 7 hardening, not a public remote automation interface. They run with the local permissions of the command process.
