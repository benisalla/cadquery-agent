import runpy
from pathlib import Path

import cadquery as cq


def main() -> None:
    namespace = runpy.run_path("/input/model.py")
    model = namespace.get("result", namespace.get("solid"))
    if model is None:
        raise RuntimeError("script did not assign result or solid")

    output = Path("/output")
    cq.exporters.export(model, str(output / "model.stl"), tolerance=0.05)
    cq.exporters.export(model, str(output / "model.step"))


if __name__ == "__main__":
    main()
