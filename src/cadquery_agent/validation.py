import ast
import re
from dataclasses import dataclass

_BLOCKED_CALLS = {"compile", "eval", "exec", "input", "open", "__import__"}
_BLOCKED_METHODS = {"export", "show", "show_object"}
_UNSUPPORTED_CQ_CONSTRUCTORS = {"Box", "Cylinder", "Sphere"}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    is_valid: bool
    errors: tuple[str, ...]


def strip_thinking(text: str) -> str:
    """Reasoning models may prefix their answer with a <think> block."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def extract_python_code(text: str) -> str:
    text = strip_thinking(text)
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
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "cq"
                and node.func.attr in _UNSUPPORTED_CQ_CONSTRUCTORS
            ):
                errors.append(
                    f"unsupported constructor: cq.{node.func.attr}; use cq.Workplane methods"
                )
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            errors.append(f"dunder attribute is not allowed: {node.attr}")

    if not _assigned_names(tree).intersection({"result", "solid"}):
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


def lint_cadquery(source: str) -> tuple[str, ...]:
    """Catch CadQuery mistakes small models make often, before spending a render."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()
    problems = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args):
            continue
        argument = node.args[0]
        halved = (
            isinstance(argument, ast.BinOp)
            and isinstance(argument.op, ast.Div)
            and isinstance(argument.right, ast.Constant)
            and argument.right.value == 2
        )
        if node.func.attr == "hole" and halved:
            problems.append(
                f"line {node.lineno}: hole() takes the full diameter; do not divide by 2"
            )
        if (
            node.func.attr == "circle"
            and isinstance(argument, ast.Name)
            and "diameter" in argument.id.lower()
        ):
            problems.append(f"line {node.lineno}: circle() takes a radius; use {argument.id} / 2")
    return tuple(problems)


def ensure_result(source: str) -> str:
    """Small models often name the final object after the part; alias it instead of retrying."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    if not tree.body or _assigned_names(tree).intersection({"result", "solid"}):
        return source
    last = tree.body[-1]
    if isinstance(last, ast.Assign) and len(last.targets) == 1:
        target = last.targets[0]
        if isinstance(target, ast.Name):
            return f"{source.rstrip()}\n\nresult = {target.id}"
    return source


def _assigned_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else ()
        if isinstance(node, ast.AnnAssign):
            targets = (node.target,)
        names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def check_geometry(stats: dict) -> tuple[str, ...]:
    if not stats:
        return ()
    problems = []
    if not stats.get("valid", True):
        problems.append("the solid is invalid (broken or self-intersecting faces)")
    if stats.get("volume", 0) <= 0:
        problems.append("the solid has no volume")
    return tuple(problems)


def check_footprint(
    request: str, before: list[float] | None, after: list[float]
) -> tuple[str, ...]:
    """Flag a revision that changes the part's outline although the request gave no such size."""
    resize = r"\b(longer|shorter|wider|narrower|bigger|smaller|larger|scale|extend)"
    if not before or not after or re.search(resize, request, re.IGNORECASE):
        return ()
    numbers = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", request)}
    for old, new in zip(before[:2], after[:2], strict=True):
        change = abs(new - old)
        explained = any(abs(n - v) < 0.5 for n in numbers for v in (new, change / 2))
        if change > 0.5 and not explained:
            old_size = " x ".join(f"{v:g}" for v in before[:2])
            new_size = " x ".join(f"{round(v, 1):g}" for v in after[:2])
            problem = (
                f"the outline changed from {old_size} mm to {new_size} mm, but the request "
                "did not ask for that; build new features on the existing part "
                "(the part is centered on the origin)"
            )
            return (problem,)
    return ()
