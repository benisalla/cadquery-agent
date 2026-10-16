# CadQuery Studio

A small, readable **LLM harness** that turns text, voice, or a hand-drawn sketch into
verified, editable CadQuery models (Python, STL, STEP). Everything runs on your machine
with open-weight models; cloud models are opt-in and clearly labelled.

The point of the project is to show how a harness turns a small model into a useful tool:
the model only writes code, and the harness checks, runs, measures, and feeds errors back.

~~~mermaid
flowchart LR
  V["Voice"] --> W["Whisper (local)"] --> P["Prompt"]
  S["Sketch / photo"] --> VM["Vision model"] --> P
  P --> M["Code model"] --> C["Static checks"] --> R["Sandboxed CadQuery render"] --> G["Geometry check"]
  C -- problems --> M
  R -- error line --> M
  G -- invalid solid --> M
  G --> UI["3D view · STL · STEP · new revision"]
~~~

## The harness in one loop

`src/cadquery_agent/agent.py` is the whole idea:

1. **System prompt** with short rules and the CadQuery facts small models get wrong.
2. **Generate → check → render → measure.** Static checks block unsafe Python; the
   script runs in a no-network, read-only container; the worker reports size, volume, validity.
3. **Feedback as a conversation.** A failure becomes one short line
   (`line 15: NameError: name 'hole_diameter' is not defined`) sent back to the model.
   Only the latest failure is kept, so small context windows never overflow.
4. **Cheap fixes without the model.** If the script forgets `result = ...`, the harness
   adds it instead of spending another model call.

Per-model prompting lives in `src/cadquery_agent/models.py` (sampling, vision support,
turning off "thinking" for reasoning models).

## Project layout

| File | Role |
|---|---|
| `agent.py` | the harness loop: prompt, generate, check, repair |
| `models.py` | per-model prompt profiles |
| `backend.py` | OpenAI-compatible client (local Ollama/vLLM, optional Hugging Face cloud) |
| `validation.py` | static safety checks and small deterministic fixes |
| `rendering.py`, `worker/render.py` | sandboxed CadQuery run and measurements |
| `projects.py` | revisions, branching, manual code runs |
| `speech.py` | local Whisper speech-to-text |
| `bench.py` | model benchmark |
| `api.py` | HTTP API, including the live progress stream |
| `web/` | `index.html`, `styles.css`, `app.js`, and a local copy of three.js (works offline) |

## Quick start

~~~bash
uv sync --extra dev --extra voice   # Python deps, incl. local Whisper
./scripts/setup_and_smoke_test.sh   # once: project-local Ollama + first model
./scripts/start.sh                  # builds the worker if needed, starts everything
~~~

Open http://127.0.0.1:8000. If the port is busy: `CAD_AGENT_PORT=8010 ./scripts/start.sh`.
The CAD worker needs Docker or rootless Podman.

## Using the studio

- **Type** a part and press Enter, or **Voice** to speak it (Stop & use / Cancel).
- **Image** attaches a photo of a drawing; **Draw** opens a sketch pad (pen, eraser, undo).
  Write the sizes on the drawing.
- Build **one feature per step**. Each verified step is a revision; pick an older one to branch.
- Edit the Python directly and press **Run code** (Ctrl/⌘ + Enter).
- **Arrange** places copies of revisions in 3D: move, rotate, drop to floor, remove, then
  **Join into model**. Joining writes plain CadQuery code, so the result stays editable.
- **History** and **Code** panels open and close; on small screens they become drawers.
- The **Harness log** shows every attempt and why it failed.

## Models

Click the model name in the top bar to switch. Every model below ran on this machine
(16 GB RAM, 8 CPU cores, no GPU). Add any Ollama model with
`./scripts/local_ollama.sh pull <name>`; it appears in the picker automatically.

Measured with `cadquery-agent bench` (5 CAD tasks checked by size and volume, 3 attempts each):

| Job | Model | Size | Result | Time | Notes |
|---|---|---|---|---|---|
| Code | **qwen2.5-coder:3b** (default) | 1.9 GB | 4/5 | 89 s | best balance |
| Code | qwen3.5:4b | 3.3 GB | 4/5 | 221 s | thinking turned off by its profile |
| Code | qwen2.5-coder:1.5b | 1.0 GB | 3/5 | 33 s | fastest, misreads more |
| Code | qwen3:4b-instruct-2507-q4_K_M | 2.5 GB | 3/5 (twice) | 92–166 s | answers directly (no thinking); always misses the cylinder hole and L-bracket wall |
| Drawing | **qwen3.5:4b** (default) | 3.3 GB | read 80 × 50 × 5 mm + Ø20 hole | 23 s | also writes code |
| Drawing | qwen2.5vl:3b | 3.2 GB | read 80 × 50 × 5 mm + Ø20 hole | 95 s | |
| Voice | **base.en** (default) | 145 MB | exact transcript | 1 s | Whisper via faster-whisper |
| Voice | tiny.en | 75 MB | small word slips | 4 s* | |
| Voice | small.en | 480 MB | exact transcript | 13 s | |

\* includes the one-time model load. Results vary between runs; benchmark on your own machine.
Researched but not tested here (disk space): `qwen3-vl:4b-instruct` (3.3 GB) and `gemma3:4b`
(3.4 GB). Both are 4-bit builds that fit a 16 GB CPU-only machine. Pull one and it appears in the picker.

### Cloud models (optional, not local)

Set `HF_TOKEN` in `.env` to also list larger open models served by Hugging Face
(Qwen2.5-Coder-32B, Qwen3-Coder-30B, Qwen3.5-35B, Qwen3-VL-30B, Gemma-3-27B).
They are marked with a cloud icon and an amber border in the UI. **They do not run on your machine:** prompts and drawings are
sent to Hugging Face, and the free tier has a monthly quota, so they stop working when it
runs out. Local models have no such limit.

### Compare models yourself

~~~bash
cadquery-agent bench --model qwen2.5-coder:3b --model qwen3.5:4b       # 5 CAD tasks
cadquery-agent bench --vision-model qwen3.5:4b                          # reads a drawn plate
cadquery-agent bench --voice-model base.en --audio clip.wav             # speech to text
~~~

Code tasks pass only when the rendered part has the expected **size and volume**, so a
misplaced hole or a "hole" built as a boss counts as a failure.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CAD_AGENT_MODEL` | `qwen2.5-coder:3b` | default code model |
| `CAD_AGENT_VISION_MODEL` | `qwen3.5:4b` | default model for drawings |
| `CAD_AGENT_WHISPER_MODEL` | `base.en` | default voice model |
| `CAD_AGENT_MAX_ATTEMPTS` | `3` | generate/repair rounds per step |
| `HF_TOKEN` | empty | enables Hugging Face cloud models |
| `OLLAMA_CONTEXT_LENGTH` | `8192` | set by `scripts/local_ollama.sh`; Ollama's CPU default (4k) silently drops history |

The local model server must be on localhost; other URLs are rejected.

## Command line and API

~~~bash
cadquery-agent generate --prompt "A 30 mm cube with a 10 mm through hole" --model qwen2.5-coder:3b
cadquery-agent generate --image sketch.png
~~~

API docs at `/docs`. Main routes: `POST /projects`, `POST /projects/{id}/steps`
(form: `instruction`, `image`, `model`, `vision_model`, `base_revision`),
`POST /projects/{id}/source` (JSON code), `GET /models`, `POST /transcribe`.

## Tests

~~~bash
.venv/bin/pytest
~~~

Tests use fake model servers and renderers; they download nothing.

## Limits

Small local models still misread intent: always check dimensions before fabrication.
Sketch dimensions are estimates unless written on the drawing.
