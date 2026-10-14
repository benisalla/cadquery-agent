import ast
from dataclasses import dataclass

_BLOCKED_CALLS = {"compile", "eval", "exec", "input", "open", "__import__"}
_BLOCKED_METHODS = {"export", "show", "show_object"}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    is_valid: bool
    errors: tuple[str, ...]


def extract_python_code(text: str) -> str:
    marker = chr(96) * 3
    if marker not in text:
        return text.strip()
    parts = text.split(marker)
    if len(parts) < 3:
        return text.strip()
    body = parts[1].strip()
    if body.lower().startswith("python"):
        body = body[6:].lstrip()
    return body.strip()


def validate_cadquery_source(source: str) -> ValidationResult:
    errors: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return ValidationResult(
            is_valid=False,
            errors=(f"syntax error on line {error.lineno}: {error.msg}",),
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                expected_alias = {"cadquery": "cq", "numpy": "np"}.get(alias.name)
                if expected_alias is None:
                    errors.append(f"import is not allowed: {alias.name}")
                elif alias.asname != expected_alias:
                    errors.append(f"import {alias.name} must use alias {expected_alias}")
        elif isinstance(node, ast.ImportFrom):
            errors.append("from-imports and wildcard imports are not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BLOCKED_CALLS:
                errors.append(f"call is not allowed: {node.func.id}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _BLOCKED_METHODS:
                errors.append(f"method call is not allowed: {node.func.attr}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"dunder attribute is not allowed: {node.attr}")

    assigned_names: set[str] = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else ()
        if isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        for target in targets:
            if isinstance(target, ast.Name):
                assigned_names.add(target.id)

    if not assigned_names.intersection({"result", "solid"}):
        errors.append("code must assign the final CadQuery object to result or solid")

    has_workplane = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Workplane"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "cq"
        for node in ast.walk(tree)
    )
    if not has_workplane:
        errors.append("code must construct geometry with cq.Workplane(...)")
    return ValidationResult(is_valid=not errors, errors=tuple(dict.fromkeys(errors)))
