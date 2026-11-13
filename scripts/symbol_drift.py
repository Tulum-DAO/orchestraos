#!/usr/bin/env python3
"""Do two copies of the same code still agree, symbol by symbol? A drift check for forks.

When one codebase runs in two trees (this public repo and a private deployment that ports fixes by
hand, say), "we converged X" is a claim that rots silently: the next edit lands in one tree only.
This compares named functions/classes between tree A and tree B by their AST (docstrings dropped,
so comments, docstrings and formatting never count; logic does) and fails when any differs or is
missing, unless the spec allows that symbol with a written reason.

    python3 scripts/symbol_drift.py SPEC.json --a /path/to/tree-a --b /path/to/tree-b

SPEC.json:
    {"symbols": [{"file": "scripts/nudge_pane.py", "name": "_nudge_unlocked"},
                 {"file": "scripts/watch_gateway.py", "b_file": "scripts/gw.py", "name": "_grok_screen_ready"}],
     "allow":   {"scripts/nudge_pane.py::_nudge_unlocked": "tree B keeps its own pane reader (why)"}}

"name" may be dotted for a method ("Class.method"). "b_file" is optional (the file's path in tree B
when it differs). Paths are relative to their tree and may not leave it. A name defined more than
once in its scope (a redefinition, a property getter + setter) is compared as the whole sequence.
Only module-level and class-level defs are found: one inside `if`/`try` reports MISSING (loud).
Module/class docstrings are ignored like function ones. A `u''` prefix counts as a difference.

An allowed symbol is still compared and reported; it just does not fail the run. An allow covers a
DIFF only, never a MISSING symbol. An allow entry whose symbol now MATCHES is reported STALE, and one
that names no listed symbol is reported UNUSED, so allowances do not outlive the drift.

Exit 0: every symbol equal or allowed. Exit 1: drift (a DIFF or MISSING that is not allowed).
Exit 2: the spec is empty or malformed, a path leaves its tree, both trees are the same directory,
or a file cannot be read. A gate never passes on absent evidence.
"""
import argparse
import ast
import difflib
import json
import os
import sys


def _strip_docstrings(node):
    for n in ast.walk(node):
        body = getattr(n, "body", None)
        if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(getattr(body[0], "value", None), ast.Constant)
                and isinstance(body[0].value.value, str)):
            n.body = body[1:] or [ast.Pass()]
    return node


_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def find_symbol(source, name):
    """Every definition of `name` ("func" or "Class.method") in its scope, in source order, or [].
    All of them: Python runs the last of a redefinition, and a property's setter shares its name."""
    nodes = [ast.parse(source)]
    for part in name.split("."):
        nodes = [n for parent in nodes for n in parent.body if isinstance(n, _DEFS) and n.name == part]
        if not nodes:
            return []
    return nodes


def normalised(nodes):
    """A comparable form of already-stripped nodes: positions ignored."""
    return [ast.dump(n, annotate_fields=True, include_attributes=False) for n in nodes]


def _inside(tree, rel):
    """tree/rel, refusing an absolute path or one that leaves the tree (it would compare a file
    with itself and pass)."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        raise ValueError(f"path {rel!r} must be relative to its tree")
    root = os.path.realpath(tree)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, full]) != root:
        raise ValueError(f"path {rel!r} leaves its tree")
    return full


def check_spec(spec):
    """A non-empty list of {file, name} entries, or ValueError (exit 2): an empty spec checks nothing."""
    if not isinstance(spec, dict):
        raise ValueError("the spec must be a JSON object")
    syms = spec.get("symbols")
    if not isinstance(syms, list) or not syms:
        raise ValueError('the spec needs a non-empty "symbols" list')
    for s in syms:
        if not (isinstance(s, dict) and isinstance(s.get("file"), str) and isinstance(s.get("name"), str)
                and s["name"]):
            raise ValueError(f"symbol entry {s!r} needs string \"file\" and \"name\"")
    if not isinstance(spec.get("allow", {}), dict):
        raise ValueError('"allow" must be an object of "file::name": "reason"')


def compare(spec, tree_a, tree_b):
    """[(key, status, detail)]; status is EQUAL, DIFF, MISSING-A, MISSING-B."""
    out = []
    for sym in spec.get("symbols", []):
        fa = sym["file"]
        fb = sym.get("b_file") or fa
        key = f"{fa}::{sym['name']}"
        nodes = []
        for tree, f in ((tree_a, fa), (tree_b, fb)):
            with open(_inside(tree, f), encoding="utf-8") as fh:
                nodes.append(find_symbol(fh.read(), sym["name"]))
        na, nb = nodes
        # Strip ONCE (the nodes come from a fresh parse, so in place is safe) and reuse the result for
        # the comparison and the diff: stripping twice ate a second leading string, and a deepcopy
        # hit the recursion limit on a long expression chain (review R2).
        na = [_strip_docstrings(n) for n in na]
        nb = [_strip_docstrings(n) for n in nb]
        if not na or not nb:
            out.append((key, "MISSING-A" if not na else "MISSING-B", ""))
            continue
        if normalised(na) == normalised(nb):
            out.append((key, "EQUAL", ""))
            continue
        ua = [ln for n in na for ln in ast.unparse(n).splitlines()]
        ub = [ln for n in nb for ln in ast.unparse(n).splitlines()]
        diff = "\n".join(difflib.unified_diff(ua, ub, "a/" + fa, "b/" + fb, lineterm="", n=1))
        out.append((key, "DIFF", diff))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec")
    ap.add_argument("--a", required=True, help="tree A root")
    ap.add_argument("--b", required=True, help="tree B root")
    ap.add_argument("--diff", action="store_true", help="print the unified diff of each DIFF")
    ns = ap.parse_args(argv)
    try:
        if os.path.realpath(ns.a) == os.path.realpath(ns.b):
            raise ValueError("--a and --b are the same tree: every symbol would match itself")
        with open(ns.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
        check_spec(spec)
        rows = compare(spec, ns.a, ns.b)
    except (OSError, ValueError, SyntaxError, KeyError, TypeError, RecursionError) as e:
        print(f"symbol_drift: cannot read the spec or a file: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 2
    allow = spec.get("allow", {})
    drift = 0
    for key in sorted(set(allow) - {k for k, _, _ in rows}):
        print(f"UNUSED   allow entry {key} names no listed symbol (typo, or the symbol was dropped); remove it")
    for key, status, diff in rows:
        allowed = key in allow and status == "DIFF"      # an allow is for a known DIFF, never a vanished symbol
        if status == "EQUAL":
            note = "  (allow entry is STALE: the symbol matches now; remove it)" if key in allow else ""
            print(f"EQUAL    {key}{note}")
            continue
        if allowed:
            print(f"ALLOWED  {status:9} {key}: {allow[key]}")
        else:
            drift += 1
            print(f"DRIFT    {status:9} {key}")
        if ns.diff and diff:
            print(diff)
    print(f"\n{len(rows)} symbols; {drift} drifted (not allowed)")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
