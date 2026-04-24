#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_root="$repo_root/plugins/research-agent-team"
manifest_path="$plugin_root/.codex-plugin/plugin.json"
cli_path="$plugin_root/scripts/rat_plugin_cli.py"

bash "$repo_root/scripts/validate-release-boundary.sh"

manifest_size="$(wc -c < "$manifest_path")"
cli_size="$(wc -c < "$cli_path")"

if (( manifest_size < 20 || cli_size < 20 )); then
  echo "Plugin subtree is still scaffold-only; limiting smoke test to boundary validation."
  exit 0
fi

python "$cli_path" --help >/dev/null
echo "Plugin CLI smoke test completed"
