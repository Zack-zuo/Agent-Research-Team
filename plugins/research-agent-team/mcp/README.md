# ResearchAgentTeam MCP

The plugin registers a local STDIO MCP server through `.codex-plugin/plugin.json`
and `mcp/.mcp.json`. Codex starts the server with:

```json
{
  "mcpServers": {
    "research-agent-team": {
      "command": "uv",
      "args": ["run", "--project", ".", "python", "./scripts/rat_plugin_mcp.py"],
      "cwd": "."
    }
  }
}
```

The MCP server is a thin protocol adapter over the same application services as
`research-agent-team-codex`. It does not duplicate state-transition rules.

Exposed tools:

- `interpret_request`: map natural language to a structured command plan.
- `run_command`: run a ResearchAgentTeam command and include a conservative
  launch plan when the payload has `root_path`.
- `activation_callback`: run worker callbacks and include follow-up launch
  planning.
- `plan_launches`: classify launch requests from command or callback results.
- `render_launch_prompt`: render an approved launch request into a worker
  prompt.

The MCP tools never launch Codex workers directly. They return launch decisions
and rendered prompts so the Codex host workflow can enforce confirmation policy
before spawning subagents.
