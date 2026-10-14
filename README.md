# CadQuery Agent

A local harness that turns a text description, a reference image, or both into
editable CadQuery Python. It calls a model server on loopback only, so prompts
and images stay on your machine. The configured model must be open-weight.

## What it does

~~~mermaid
flowchart LR
  A["Text and/or image"] --> B["Local open-weight model"]
  B --> C["CadQuery Python"]
  C --> D["Static checks"]
  D --> E["Save editable script"]
~~~

- Uses an OpenAI-compatible /chat/completions endpoint on localhost.
- Supports text requests and image-plus-text requests using PNG, JPEG, or WebP.
- Saves returned scripts after checking syntax, imports, and unsafe patterns.
- Does not execute generated Python. Review scripts before running CadQuery.

## Recommended models for this machine

This machine has 16 GiB RAM and no NVIDIA GPU, so start with quantized 1.5B-3B
models served by Ollama:

- qwen2.5vl:3b: recommended default for text and images, about 3.2 GB.
- qwen2.5-coder:1.5b: fastest text-only option, about 1 GB.
- qwen2.5-coder:3b: stronger text-only option, about 1.9 GB.

Larger 7B+ models may run, but CPU generation will be slower and leave less
memory for the rest of the system.

## 1. Start a local model server

Install Ollama from its official installation instructions, then pull and run
the recommended vision model:

~~~bash
ollama pull qwen2.5vl:3b
ollama serve
~~~

This repository can also keep Ollama and its model data entirely inside the
project. Run the automated setup and real generation check once:

~~~bash
./scripts/setup_and_smoke_test.sh
~~~

For later sessions, start the project-local server in one terminal:

~~~bash
./scripts/local_ollama.sh serve
~~~

Ollama normally listens on 127.0.0.1:11434. The harness also works with a local
vLLM OpenAI-compatible server. Remote and hosted endpoint URLs are rejected.

## 2. Install the harness

~~~bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
cp .env.example .env
~~~

The example configuration uses qwen2.5vl:3b. To use a faster text-only model,
set CAD_AGENT_MODEL in .env to qwen2.5-coder:1.5b or qwen2.5-coder:3b.

## 3. Generate a model

Text prompt:

~~~bash
cadquery-agent generate --prompt "A 30 mm cube with a 10 mm through-hole centered on the top face"
~~~

Reference image plus prompt:

~~~bash
cadquery-agent generate --image part.png --prompt "Estimate the proportions; use millimeters"
~~~

Output defaults to artifacts/generated_part.py. Choose another path with
--output.

## Optional HTTP interface

~~~bash
uvicorn cadquery_agent.api:app --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000 for the visual interface or /docs for the API.
Use POST /generate for text or POST /generate-image for an image and optional
prompt. GET /health is a model-independent liveness check.

## Tests

~~~bash
pytest
~~~

Tests use a mocked local endpoint. They do not download a model or make network
requests.

## Current boundary

This harness generates code but does not execute it, render a preview, or export
STEP/STL. Image-based dimensions are estimates unless the image includes scale
or measurements. The next milestone is a reviewable CAD execution worker with
resource limits and STEP/STL export.
