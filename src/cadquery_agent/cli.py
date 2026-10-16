import argparse
import asyncio
from pathlib import Path

from cadquery_agent.agent import CadQueryAgent
from cadquery_agent.backend import OpenAICompatibleBackend
from cadquery_agent.bench import CASES, run_case, run_vision, run_voice
from cadquery_agent.files import save_source
from cadquery_agent.images import load_image
from cadquery_agent.rendering import DockerCadRenderer
from cadquery_agent.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate verified CadQuery models locally.")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="Generate a CadQuery Python script")
    generate.add_argument("--prompt", default="", help="Text description of the CAD part")
    generate.add_argument("--image", type=Path, help="PNG, JPEG, or WebP sketch or photo")
    generate.add_argument("--model", help="Code model (default: CAD_AGENT_MODEL)")
    generate.add_argument("--vision-model", help="Vision model (default: CAD_AGENT_VISION_MODEL)")
    generate.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/generated_part.py"),
        help="Destination for the verified Python source",
    )

    bench = commands.add_parser("bench", help="Score models on the same tasks")
    bench.add_argument("--model", action="append", default=[], help="Code model; repeatable")
    bench.add_argument("--vision-model", action="append", default=[], help="Vision model")
    bench.add_argument("--voice-model", action="append", default=[], help="Whisper model")
    bench.add_argument("--audio", type=Path, help="Speech clip for --voice-model")
    return parser


def build_agent() -> CadQueryAgent:
    settings = get_settings()
    return CadQueryAgent(
        backend=OpenAICompatibleBackend(settings),
        model=settings.model,
        vision_model=settings.vision_model,
        renderer=DockerCadRenderer(settings),
        max_attempts=settings.max_attempts,
    )


async def run_generate(args: argparse.Namespace) -> None:
    image = load_image(args.image, get_settings().max_image_bytes) if args.image else None
    result = await build_agent().generate(
        prompt=args.prompt, image=image, model=args.model, vision_model=args.vision_model
    )
    save_source(args.output, result.code)
    print(f"Wrote source to {args.output} using {result.model} after {result.attempts} attempt(s).")
    if result.render is not None:
        print(f"STL: {result.render.stl_path}\nSTEP: {result.render.step_path}")


async def run_bench(args: argparse.Namespace) -> None:
    agent = build_agent()
    for model in args.model:
        print(f"\ncode · {model}")
        scores = []
        for case in CASES:
            agent.model = model
            score = await run_case(agent, case)
            scores.append(score)
            mark = "PASS" if score.passed else "FAIL"
            print(
                f"  {mark} {score.seconds:4.0f}s {score.attempts} try  {case.prompt[:50]:50}  {score.note[:60]}"
            )
        passed = sum(score.passed for score in scores)
        print(f"  score {passed}/{len(scores)} in {sum(s.seconds for s in scores):.0f}s")
    for model in args.vision_model:
        passed, seconds, text = await run_vision(agent, model)
        print(f"\nvision · {model}: {'PASS' if passed else 'FAIL'} in {seconds:.0f}s\n{text}")
    if args.voice_model and args.audio is None:
        raise SystemExit("--voice-model needs --audio clip.wav")
    for model in args.voice_model:
        seconds, text = run_voice(args.audio.read_bytes(), model)
        print(f"\nvoice · {model}: {seconds:.1f}s  {text!r}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "generate":
        if not args.prompt.strip() and args.image is None:
            parser.error("generate requires --prompt, --image, or both")
        asyncio.run(run_generate(args))
    elif args.command == "bench":
        asyncio.run(run_bench(args))


if __name__ == "__main__":
    main()
