#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_root="$repo_root/plugins/research-agent-team"

required_paths=(
  ".codex-plugin/plugin.json"
  "README.md"
  "LICENSE"
  "pyproject.toml"
  "scripts/rat_plugin_cli.py"
  "scripts/validate_manifest.py"
  "scripts/validate_schemas.py"
  "skills/research-agent-team/SKILL.md"
)

for relative_path in "${required_paths[@]}"; do
  if [[ ! -e "$plugin_root/$relative_path" ]]; then
    echo "Missing required plugin path: $plugin_root/$relative_path" >&2
    exit 1
  fi
  if [[ ! -s "$plugin_root/$relative_path" ]]; then
    echo "Required plugin path is empty: $plugin_root/$relative_path" >&2
    exit 1
  fi
done

for forbidden_path in docs tests .github .agents scripts README.md README.zh-CN.md CHANGELOG.md; do
  if [[ -e "$plugin_root/$forbidden_path" && "$forbidden_path" != "README.md" && "$forbidden_path" != "scripts" ]]; then
    echo "Unexpected monorepo-only path inside plugin root: $plugin_root/$forbidden_path" >&2
    exit 1
  fi
done

python -m py_compile \
  "$plugin_root/scripts/rat_plugin_cli.py" \
  "$plugin_root/scripts/render_launch_prompt.py" \
  "$plugin_root/scripts/validate_manifest.py" \
  "$plugin_root/scripts/validate_schemas.py"
python "$plugin_root/scripts/validate_manifest.py" >/dev/null
python "$plugin_root/scripts/validate_schemas.py" >/dev/null

echo "Plugin release boundary validated at $plugin_root"
