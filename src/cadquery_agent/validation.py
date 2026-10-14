import ast
from dataclasses import dataclass

_ALLOWED_IMPORT_ROOTS = {"cadquery", "numpy"}
_BLOCKED_CALLS = {"compile", "eval", "exec", "input", "open", "__import__"}


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
                if alias.name.split(".", 1)[0] not in _ALLOWED_IMPORT_ROOTS:
                    errors.append(f"import is not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".", 1)[0] not in _ALLOWED_IMPORT_ROOTS:
                errors.append(f"import is not allowed: {module or '<relative>'}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BLOCKED_CALLS:
                errors.append(f"call is not allowed: {node.func.id}")
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
    return ValidationResult(is_valid=not errors, errors=tuple(dict.fromkeys(errors)))
