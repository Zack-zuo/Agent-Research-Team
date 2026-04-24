#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_root="$repo_root/plugins/research-agent-team"

required_paths=(
  ".codex-plugin/plugin.json"
  "README.md"
  "pyproject.toml"
  "scripts/rat_plugin_cli.py"
  "skills/research-agent-team/SKILL.md"
)

for relative_path in "${required_paths[@]}"; do
  if [[ ! -e "$plugin_root/$relative_path" ]]; then
    echo "Missing required plugin path: $plugin_root/$relative_path" >&2
    exit 1
  fi
done

for forbidden_path in docs tests .github .agents scripts README.md README.zh-CN.md CHANGELOG.md; do
  if [[ -e "$plugin_root/$forbidden_path" && "$forbidden_path" != "README.md" && "$forbidden_path" != "scripts" ]]; then
    echo "Unexpected monorepo-only path inside plugin root: $plugin_root/$forbidden_path" >&2
    exit 1
  fi
done

echo "Plugin release boundary validated at $plugin_root"
