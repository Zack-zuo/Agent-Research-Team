#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
plugin_root="$repo_root/plugins/research-agent-team"
dist_dir="$repo_root/dist"
archive_path="$dist_dir/research-agent-team-plugin.tar.gz"

bash "$repo_root/scripts/validate-release-boundary.sh"
mkdir -p "$dist_dir"
tar -czf "$archive_path" -C "$repo_root/plugins" "research-agent-team"

echo "Packaged plugin archive at $archive_path"
