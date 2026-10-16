from cadquery_agent.validation import (
    check_footprint,
    check_geometry,
    ensure_result,
    extract_python_code,
    lint_cadquery,
    validate_cadquery_source,
)


def test_accepts_basic_cadquery_source() -> None:
    source = """import cadquery as cq
result = cq.Workplane('XY').box(1, 2, 3)"""
    assert validate_cadquery_source(source).is_valid


def test_rejects_file_access_and_unapproved_imports() -> None:
    result = validate_cadquery_source(
        """import os
import cadquery as cq
result = open(os.devnull)"""
    )
    assert not result.is_valid
    assert "import is not allowed: os" in result.errors
    assert "call is not allowed: open" in result.errors


def test_extracts_markdown_fenced_code() -> None:
    marker = chr(96) * 3
    response = f"""Here is the code:
{marker}python
result = 1
{marker}"""
    assert extract_python_code(response) == "result = 1"


def test_rejects_wildcard_cadquery_imports() -> None:
    source = """from cadquery import *
result = Workplane('XY').box(1, 2, 3)"""
    result = validate_cadquery_source(source)
    assert not result.is_valid
    assert "from-imports and wildcard imports are not allowed" in result.errors
    assert "code must construct geometry with cq.Workplane(...)" in result.errors


def test_requires_canonical_cadquery_alias() -> None:
    source = """import cadquery
result = cadquery.Workplane('XY').box(1, 2, 3)"""
    result = validate_cadquery_source(source)
    assert not result.is_valid
    assert "import cadquery must use alias cq" in result.errors


def test_rejects_display_calls_that_do_not_work_in_the_worker() -> None:
    source = """import cadquery as cq
result = cq.Workplane('XY').box(20, 20, 20)
result.show()"""
    result = validate_cadquery_source(source)
    assert not result.is_valid
    assert "method call is not allowed: show" in result.errors


def test_rejects_nonexistent_top_level_primitive_constructors() -> None:
    source = """import cadquery as cq
base = cq.Workplane('XY').box(20, 20, 4)
boss = cq.Cylinder(diameter=10, height=5)
result = base.union(boss)"""
    result = validate_cadquery_source(source)
    assert not result.is_valid
    assert "unsupported constructor: cq.Cylinder; use cq.Workplane methods" in result.errors


def test_geometry_check_flags_invalid_or_empty_solids() -> None:
    assert check_geometry({}) == ()
    assert check_geometry({"size": [1, 1, 1], "volume": 1.0, "valid": True}) == ()
    problems = check_geometry({"size": [0, 0, 0], "volume": 0.0, "valid": False})
    assert len(problems) == 2


def test_ensure_result_aliases_the_last_assigned_object() -> None:
    source = "import cadquery as cq\nplate = cq.Workplane('XY').box(10, 10, 2)"
    fixed = ensure_result(source)
    assert fixed.endswith("result = plate")
    assert validate_cadquery_source(fixed).is_valid
    assert ensure_result(fixed) == fixed


def test_lint_catches_radius_and_diameter_mix_ups() -> None:
    source = """import cadquery as cq
hole_diameter = 6
boss_diameter = 24
plate = cq.Workplane('XY').box(10, 10, 2).faces('>Z').workplane().hole(hole_diameter / 2)
boss = cq.Workplane('XY').circle(boss_diameter).extrude(5)
result = plate"""
    problems = lint_cadquery(source)
    assert "line 4: hole() takes the full diameter; do not divide by 2" in problems
    assert "line 5: circle() takes a radius; use boss_diameter / 2" in problems
    assert lint_cadquery("import cadquery as cq\nresult = cq.Workplane('XY').circle(r)") == ()


def test_check_footprint_flags_unrequested_outline_change():
    request = "Add a 24 mm boss, 12 mm tall, on top."
    assert check_footprint(request, [100, 60, 8], [100, 60, 20]) == ()
    assert "100 x 60 mm to 112 x 72 mm" in check_footprint(request, [100, 60, 8], [112, 72, 20])[0]
    assert check_footprint("Make it 120 mm long.", [100, 60, 8], [120, 60, 8]) == ()
    assert check_footprint("Make it 20 mm longer.", [100, 60, 8], [120, 60, 8]) == ()
    assert check_footprint("Add a 10 mm tab on each side.", [100, 60, 8], [120, 60, 8]) == ()
    assert check_footprint(request, None, [112, 72, 20]) == ()
