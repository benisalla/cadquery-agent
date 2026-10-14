from cadquery_agent.validation import extract_python_code, validate_cadquery_source


def test_accepts_basic_cadquery_source() -> None:
    source = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)"
    assert validate_cadquery_source(source).is_valid


def test_rejects_file_access_and_unapproved_imports() -> None:
    result = validate_cadquery_source("import os\nresult = open(os.devnull)")
    assert not result.is_valid
    assert "import is not allowed: os" in result.errors
    assert "call is not allowed: open" in result.errors


def test_extracts_markdown_fenced_code() -> None:
    marker = chr(96) * 3
    response = f"Here is the code:\n{marker}python\nresult = 1\n{marker}"
    assert extract_python_code(response) == "result = 1"
