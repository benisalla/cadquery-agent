import json
import runpy
import sys
import traceback
from pathlib import Path

import cadquery as cq

SCRIPT = "/input/model.py"


def run_script() -> dict:
    try:
        return runpy.run_path(SCRIPT)
    except Exception as error:  # noqa: BLE001 - any script error is reported back
        # Report only the user's line, so the model gets short, actionable feedback.
        frames = [f for f in traceback.extract_tb(error.__traceback__) if f.filename == SCRIPT]
        where = f"line {frames[-1].lineno}: " if frames else ""
        sys.exit(f"{where}{type(error).__name__}: {error}")


def main() -> None:
    namespace = run_script()
    model = namespace.get("result", namespace.get("solid"))
    if model is None:
        raise RuntimeError("script did not assign result or solid")
    shape = model.findSolid() if isinstance(model, cq.Workplane) else model

    output = Path("/output")
    cq.exporters.export(shape, str(output / "model.stl"), tolerance=0.05)
    cq.exporters.export(shape, str(output / "model.step"))

    # Measurements let the harness check the geometry, not just that the code ran.
    box = shape.BoundingBox()
    stats = {
        "size": [round(box.xlen, 3), round(box.ylen, 3), round(box.zlen, 3)],
        "volume": round(shape.Volume(), 3),
        "solids": len(shape.Solids()),
        "valid": shape.isValid(),
    }
    (output / "stats.json").write_text(json.dumps(stats))


if __name__ == "__main__":
    main()
