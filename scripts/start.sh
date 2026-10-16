#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_root"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Python environment missing. Run: uv sync --extra dev" >&2
  exit 1
fi

port="${CAD_AGENT_PORT:-8000}"
if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
  echo "Port $port is already in use. Try: CAD_AGENT_PORT=$((port + 10)) ./scripts/start.sh" >&2
  exit 1
fi

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
if ! "$runtime" image inspect cadquery-agent-worker:local >/dev/null 2>&1; then
  ./scripts/build_worker.sh
fi

mkdir -p artifacts
ollama_pid=""
cleanup() {
  if [[ -n "$ollama_pid" ]]; then
    kill "$ollama_pid" 2>/dev/null || true
    wait "$ollama_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl --fail --silent http://127.0.0.1:11434/api/version >/dev/null; then
  ./scripts/local_ollama.sh serve >artifacts/ollama-server.log 2>&1 &
  ollama_pid="$!"
  for _ in $(seq 1 60); do
    curl --fail --silent http://127.0.0.1:11434/api/version >/dev/null && break
    sleep 1
  done
fi
curl --fail --silent http://127.0.0.1:11434/api/version >/dev/null

installed_models="$(./scripts/local_ollama.sh list)"
for model in qwen2.5-coder:3b qwen3.5:4b; do
  grep --quiet "^$model " <<<"$installed_models" || ./scripts/local_ollama.sh pull "$model"
done

echo "Starting CadQuery Studio at http://127.0.0.1:$port"
.venv/bin/uvicorn cadquery_agent.api:app --host 127.0.0.1 --port "$port"
