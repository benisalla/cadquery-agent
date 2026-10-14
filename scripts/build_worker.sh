#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
runtime=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null && "$candidate" info >/dev/null 2>&1; then
    runtime="$candidate"
    break
  fi
done

if [[ -z "$runtime" ]]; then
  echo "Start Docker or install rootless Podman, then retry." >&2
  exit 1
fi

echo "Building the isolated CadQuery worker with $runtime..."
"$runtime" build --tag cadquery-agent-worker:local "$project_root/worker"
