# Command Quick Reference

The CLI, schemas, documentation, and future MCP tools share this public command
surface.

Implemented project lifecycle:

- `create_project`
- `open_project`
- `switch_operating_mode`
- `pause_project`
- `resume_project`

Implemented topology:

- `show_team_topology`
- `add_senior`
- `add_junior`
- `retire_senior`
- `retire_junior`

Implemented task admission and activation runtime:

- `assign_task`
- `render-launch-prompt`
- activation `mark-running`
- activation `heartbeat`
- activation `checkpoint`
- activation `complete`
- activation `fail`
- activation `interrupt`
- activation `cancel`

Reserved for later roadmap stages:

- `approve_checkpoint`
- `reject_checkpoint`
- `request_status`
- `generate_report`
- `sync_knowledge_base`
- `rebuild_graph`
- `run_experiment`
- `review_experiment`
