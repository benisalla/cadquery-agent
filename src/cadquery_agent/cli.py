import argparse
import asyncio
from pathlib import Path

from cadquery_agent.agent import CadQueryAgent
from cadquery_agent.backend import OpenAICompatibleBackend
from cadquery_agent.files import save_source
from cadquery_agent.images import load_image
from cadquery_agent.settings import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate editable CadQuery source locally.")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Generate a CadQuery Python script")
    generate.add_argument("--prompt", default="", help="Text description of the CAD part")
    generate.add_argument("--image", type=Path, help="PNG, JPEG, or WebP reference image")
    generate.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/generated_part.py"),
        help="Destination for validated Python source",
    )
    return parser


async def run_generate(args: argparse.Namespace) -> None:
    settings = get_settings()
    image = load_image(args.image, settings.max_image_bytes) if args.image else None
    agent = CadQueryAgent(
        backend=OpenAICompatibleBackend(settings),
        model=settings.model,
        max_attempts=settings.max_attempts,
    )
    result = await agent.generate(prompt=args.prompt, image=image)
    save_source(args.output, result.code)
    print(
        f"Wrote validated source to {args.output} "
        f"using {result.model} after {result.attempts} attempt(s)."
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "generate":
        if not args.prompt.strip() and args.image is None:
            parser.error("generate requires --prompt, --image, or both")
        asyncio.run(run_generate(args))


if __name__ == "__main__":
    main()
