#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "$0")/.." && pwd)"
export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_MODELS="$project_root/.ollama/models"
# Ollama defaults to 4k tokens on CPU and silently drops older messages beyond it.
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-8192}"

ollama_binary="$project_root/.ollama/runtime/bin/ollama"
if [[ ! -x "$ollama_binary" ]]; then
  echo "Local Ollama is not installed. Run scripts/setup_and_smoke_test.sh first." >&2
  exit 1
fi

exec "$ollama_binary" "$@"
