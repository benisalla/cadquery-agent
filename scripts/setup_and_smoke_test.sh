#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
runtime_dir="$project_root/.ollama/runtime"
artifacts_dir="$project_root/artifacts"
ollama_binary="$runtime_dir/bin/ollama"

mkdir -p "$runtime_dir" "$project_root/.ollama/models" "$artifacts_dir"

if [[ ! -x "$ollama_binary" ]]; then
  curl --fail --show-error --location "https://ollama.com/download/ollama-linux-amd64.tar.zst" | zstd --decompress --stdout | tar -xf - -C "$runtime_dir"
fi

export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_MODELS="$project_root/.ollama/models"

server_pid=""
cleanup() {
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl --fail --silent "http://127.0.0.1:11434/api/version" >/dev/null; then
  "$ollama_binary" serve >"$artifacts_dir/ollama-server.log" 2>&1 &
  server_pid="$!"
fi

for _ in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:11434/api/version" >/dev/null; then
    break
  fi
  sleep 1
done
curl --fail --silent "http://127.0.0.1:11434/api/version" >/dev/null

"$ollama_binary" pull qwen2.5vl:3b
"$project_root/.venv/bin/cadquery-agent" generate --prompt "Create a 20 mm cube and assign the final CadQuery object to result." --output "$artifacts_dir/smoke_test_part.py"

echo "Local model setup and real generation smoke test completed."
