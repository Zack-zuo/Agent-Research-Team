# ResearchAgentTeam Hooks

Hooks are an observability and extension surface. Stage 0 ships an inert hook
descriptor so plugin packaging and validation can exercise the expected file
layout without making hooks part of any canonical state transition.

Later stages may add best-effort subscribers for events, reports, adapter
health, or release diagnostics. Hook failures must not become the only path for
task delegation, approvals, or durable project mutation.
