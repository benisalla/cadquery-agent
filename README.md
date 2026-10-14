# CadQuery Agent

A local harness that turns a text description, a reference image, or both into
editable CadQuery Python, STL, and STEP. It calls a model server on loopback
only, so prompts and images stay on your machine. The configured model must be
open-weight.

## What it does

~~~mermaid
flowchart LR
  A["Text and/or image"] --> B["Local open-weight model"]
  B --> C["CadQuery Python"]
  C --> D["Static checks"]
  D --> E["Isolated CadQuery worker"]
  E --> F["3D preview + STL/STEP"]
~~~

- Uses an OpenAI-compatible /chat/completions endpoint on localhost.
- Supports text requests and image-plus-text requests using PNG, JPEG, or WebP.
- Checks returned scripts before execution.
- Runs generated code only in a no-network, read-only, resource-limited Docker
  or rootless Podman container.
- Shows an interactive Three.js preview and provides STL and STEP downloads.

## Recommended models for this machine

This machine has 16 GiB RAM and no NVIDIA GPU, so start with quantized 1.5B-3B
models served by Ollama:

- qwen2.5-coder:1.5b: recommended reliable text model, about 1 GB.
- qwen2.5vl:3b: optional text-and-image model, about 3.2 GB and more RAM.
- qwen2.5-coder:3b: stronger text-only option, about 1.9 GB.

Larger 7B+ models may run, but CPU generation will be slower and leave less
memory for the rest of the system.

## 1. Start a local model server

Install Ollama from its official installation instructions, then pull and run
the recommended vision model:

~~~bash
ollama pull qwen2.5-coder:1.5b
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

The example configuration uses qwen2.5-coder:1.5b for reliable text input on
this machine. Set CAD_AGENT_MODEL to qwen2.5vl:3b only when image input is
needed and enough memory is free.

## 3. Build the safe CAD worker

Install either Docker or rootless Podman, then run:

~~~bash
./scripts/build_worker.sh
~~~

The worker uses CadQuery/OpenCascade to create the real geometry. Generated
code has no network, a read-only root filesystem, dropped Linux capabilities,
and CPU, memory, process, and time limits.

## 4. Open the text-to-3D interface

The easiest start command builds the worker when needed, starts the project-local
Ollama server, and opens the API on loopback:

~~~bash
./scripts/start.sh
~~~

Open http://127.0.0.1:8000, describe a part, and select **Generate 3D model**.
Rotate and zoom the result, inspect its editable Python, or download STEP/STL.

## Command-line code generation

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

## HTTP API

~~~bash
uvicorn cadquery_agent.api:app --host 127.0.0.1 --port 8000
~~~

Open http://127.0.0.1:8000/docs for the API. POST /models/generate creates a
text-driven model; POST /models/generate-image accepts an image and optional
prompt. The older /generate endpoints remain available for code-only use.
GET /health is a model-independent liveness check.

## Tests

~~~bash
pytest
~~~

Tests use a mocked local endpoint. They do not download a model or make network
requests.

## Current boundary

Generated geometry must still be reviewed before fabrication. Small local models
can misunderstand complex mechanical intent, and image-based dimensions are
estimates unless the image includes scale or measurements. The editable Python
and STEP output make correction possible without regenerating everything.
