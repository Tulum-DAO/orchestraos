"""Repo-wide guard: PEP 604 annotations must not break the default interpreter.

`def f() -> dict | None:` COMPILES on Python 3.9 but raises
"TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'" when the def
executes at import time. So the failure is invisible to py_compile, invisible to linting,
and invisible to CI — which runs 3.12, where it works fine. It only bites whoever runs the
script with the repo's default `python3`.

That combination has now produced the same outage four separate times:
session-index.py (state/agent-sessions.json sat empty for ten days, silently breaking the
/field transcript view, the GROW activity indicators, gm spawn/resume, agent-recovery and
promote_successor), then approval_adapters.py, boundary_delivery.py and
spawn_permission_rules.py — two of those on the approval and spawn paths.

`from __future__ import annotations` makes annotations lazy strings, fixing every
occurrence in a file at once and costing nothing on newer interpreters.

This test detects the PATTERN rather than the failure, so it protects 3.9 users even
though CI runs it on 3.12 — an import-based test would pass there and catch nothing.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKIP_PARTS = {'node_modules', '.git', '__pycache__', 'dist', '.venv', 'venv', 'build'}


def _has_future_annotations(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == '__future__':
            if any(a.name == 'annotations' for a in node.names):
                return True
    return False


def _annotations_of(node) -> list:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        out = [node.returns]
        out += [a.annotation for a in args.args + args.posonlyargs + args.kwonlyargs]
        out += [args.vararg.annotation if args.vararg else None,
                args.kwarg.annotation if args.kwarg else None]
        return out
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    return []


def _offenders():
    bad = []
    for path in sorted(REPO.rglob('*.py')):
        if SKIP_PARTS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(errors='replace'))
        except SyntaxError:
            continue                      # not our bug class
        if _has_future_annotations(tree):
            continue
        for node in ast.walk(tree):
            for ann in _annotations_of(node):
                if ann is None:
                    continue
                for sub in ast.walk(ann):
                    if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                        bad.append(f"{path.relative_to(REPO)}:{node.lineno}")
                        break
                else:
                    continue
                break
    return sorted(set(bad))


def test_no_pep604_annotations_without_future_import():
    bad = _offenders()
    assert not bad, (
        "These use `X | Y` in an annotation without `from __future__ import annotations`, "
        "so they raise TypeError on import under python3.9 while working fine on 3.12 "
        "(which is what CI runs, so CI will not catch it either):\n  "
        + "\n  ".join(bad)
        + "\n\nFix: add `from __future__ import annotations` as the first statement after "
          "the module docstring. One line covers every annotation in the file."
    )


def test_the_detector_actually_detects(tmp_path):
    """Guard the guard — a scan that silently matches nothing is worse than no scan."""
    sample = tmp_path / 'sample.py'
    sample.write_text("def f(x: int) -> dict | None:\n    return None\n")
    tree = ast.parse(sample.read_text())
    assert not _has_future_annotations(tree)
    found = any(
        isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr)
        for node in ast.walk(tree)
        for ann in _annotations_of(node) if ann is not None
        for sub in ast.walk(ann)
    )
    assert found, "the detector missed a textbook `dict | None` return annotation"


def test_future_import_is_recognised(tmp_path):
    sample = tmp_path / 'ok.py'
    sample.write_text("from __future__ import annotations\n\ndef f() -> dict | None:\n    return None\n")
    assert _has_future_annotations(ast.parse(sample.read_text()))
