#!/usr/bin/env python3
"""check_imports.py — the gate every piece of code taken from n8n-io/nod passes.

OrchestraOS takes selected components from Nod (Apache-2.0, https://github.com/n8n-io/nod).
Nod moves fast and carries copyleft parts, so an import is allowed only when it is tracked,
licensed and kept at arm's length. This script enforces that over the whole repository:

  R1 ledger    integrations/nod/IMPORTS.jsonl is well formed: one JSON object per line with the
               fields in REQUIRED, a known verdict, a take-list id, a 40-hex upstream sha and a
               permissive license.
  R2 tree      every ledger `local` file exists and carries the provenance header naming the same
               upstream path and sha; every tracked file that carries a provenance header is in
               the ledger. A copied file cannot hide and a ledger line cannot point at nothing.
  R3 excluded  no ledger line comes from an excluded upstream path, and no ledgered file imports
               an excluded package (GPL speech, the unofficial WhatsApp client and its GPL crypto,
               GPL audio decoders, Pi's bundled subscription OAuth logins).
  R4 notice    once the ledger holds anything, NOTICE carries the Nod attribution.
  R5 boundary  a ledgered file stays a leaf: it may import the standard library, third-party
               packages, other ledgered files and the files its ledger line names in `local_deps`,
               and nothing else from this repository. That is what keeps each import replaceable.

Provenance header (any comment style, within the first 15 lines of the file):
    Upstream: n8n-io/nod <upstream path> @ <40-hex sha> (Apache-2.0), modified: yes|no

Exit 0 when every rule holds, 1 with one line per violation otherwise. `--root` points it at
another tree (the tests use this). Run it from CI through test_check_imports.py.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LEDGER = Path("integrations/nod/IMPORTS.jsonl")
REQUIRED = ("local", "upstream", "upstream_sha", "verdict", "modified", "license", "take_list_id")
VERDICTS = {"TAKE", "ADAPT"}
TAKE_LIST_IDS = {f"T{n}" for n in range(1, 19)}
LICENSES = {"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD"}
SHA = re.compile(r"^[0-9a-f]{40}$")
HEADER = re.compile(
    r"Upstream: n8n-io/nod (?P<path>\S+) @ (?P<sha>[0-9a-f]{40}) \(Apache-2\.0\), modified: (?P<mod>yes|no)"
)
HEADER_LINES = 15
SELF = {"integrations/nod/check_imports.py", "integrations/nod/test_check_imports.py"}

EXCLUDED_UPSTREAM = (
    "src/providers/local-speech/",
    "src/providers/chat-sdk/whatsapp.ts",
    "patches/libsignal",
    "patches/@whiskeysockets__baileys",
)
EXCLUDED_PACKAGES = (
    "@spelling-creator/spellophone",
    "@whiskeysockets/baileys",
    "libsignal",
    "@audio/decode-webm",
    "@earendil-works/pi-ai/oauth",
    "@earendil-works/pi-ai/lazy-oauth",
    "@earendil-works/pi-ai/bun-oauth",
)
NOTICE_MARKS = ("Nod", "n8n GmbH", "https://github.com/n8n-io/nod")

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx", ".sh"}
JS_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx"}
JS_IMPORT = re.compile(
    r"""(?:^|\s)(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]|"""
    r"""(?:^|\s)import\s+['"]([^'"]+)['"]|"""
    r"""(?:require|import)\(\s*['"]([^'"]+)['"]\s*\)""",
    re.M,
)
PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import|import\s+([\w.]+))", re.M)


def tracked_files(root: Path) -> list[str]:
    """Tracked files when root is a git checkout, else every file (test trees)."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True,
                             text=True, check=True).stdout
        files = [f for f in out.splitlines() if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and ".git" not in p.parts)


def read_ledger(root: Path, problems: list[str]) -> list[dict]:
    path = root / LEDGER
    if not path.exists():
        problems.append(f"R1 {LEDGER}: missing")
        return []
    rows = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"R1 {LEDGER}:{n}: not JSON ({e.msg})")
            continue
        if not isinstance(row, dict):
            problems.append(f"R1 {LEDGER}:{n}: not an object")
            continue
        missing = [k for k in REQUIRED if k not in row]
        if missing:
            problems.append(f"R1 {LEDGER}:{n}: missing {', '.join(missing)}")
            continue
        if row["verdict"] not in VERDICTS:
            problems.append(f"R1 {LEDGER}:{n}: verdict {row['verdict']!r} is not one of {sorted(VERDICTS)}")
        if row["take_list_id"] not in TAKE_LIST_IDS:
            problems.append(f"R1 {LEDGER}:{n}: take_list_id {row['take_list_id']!r} is not T1-T18")
        if not SHA.match(str(row["upstream_sha"])):
            problems.append(f"R1 {LEDGER}:{n}: upstream_sha is not a 40-hex commit")
        if row["license"] not in LICENSES:
            problems.append(f"R1 {LEDGER}:{n}: license {row['license']!r} is not in the permissive allowlist")
        if not isinstance(row["modified"], bool):
            problems.append(f"R1 {LEDGER}:{n}: modified must be true or false")
        deps = row.get("local_deps", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            problems.append(f"R1 {LEDGER}:{n}: local_deps must be a list of paths")
            row["local_deps"] = []
        row["_line"] = n
        rows.append(row)
    seen: dict[str, int] = {}
    for row in rows:
        if row["local"] in seen:
            problems.append(f"R1 {LEDGER}:{row['_line']}: {row['local']} already listed on line {seen[row['local']]}")
        seen.setdefault(row["local"], row["_line"])
    return rows


def header_of(path: Path) -> re.Match | None:
    try:
        head = path.read_text(errors="replace").splitlines()[:HEADER_LINES]
    except OSError:
        return None
    for line in head:
        m = HEADER.search(line)
        if m:
            return m
    return None


def check_tree(root: Path, rows: list[dict], files: list[str], problems: list[str]) -> None:
    ledgered = {r["local"]: r for r in rows}
    for local, row in ledgered.items():
        path = root / local
        if not path.is_file():
            problems.append(f"R2 {local}: in the ledger (line {row['_line']}) but the file does not exist")
            continue
        m = header_of(path)
        if not m:
            problems.append(f"R2 {local}: no provenance header in its first {HEADER_LINES} lines")
            continue
        if m["path"] != row["upstream"] or m["sha"] != row["upstream_sha"]:
            problems.append(f"R2 {local}: header says {m['path']} @ {m['sha'][:8]}, ledger says "
                            f"{row['upstream']} @ {str(row['upstream_sha'])[:8]}")
        if (m["mod"] == "yes") != bool(row["modified"]):
            problems.append(f"R2 {local}: header modified: {m['mod']} disagrees with the ledger")
    for f in files:
        if f in ledgered or Path(f).suffix not in CODE_SUFFIXES or f in SELF:
            continue
        if header_of(root / f):
            problems.append(f"R2 {f}: carries a Nod provenance header but is not in the ledger")


def imports_of(path: Path) -> list[str]:
    text = path.read_text(errors="replace")
    if path.suffix in JS_SUFFIXES:
        return [next(g for g in m.groups() if g) for m in JS_IMPORT.finditer(text)]
    if path.suffix == ".py":
        return [m.group(1) or m.group(2) for m in PY_IMPORT.finditer(text)]
    return []


def check_excluded(root: Path, rows: list[dict], problems: list[str]) -> None:
    for row in rows:
        up = str(row["upstream"])
        if any(up.startswith(x) for x in EXCLUDED_UPSTREAM):
            problems.append(f"R3 {row['local']}: upstream {up} is an excluded path (copyleft or provider policy)")
        path = root / row["local"]
        if not path.is_file():
            continue
        for spec in imports_of(path):
            if any(spec == p or spec.startswith(p + "/") for p in EXCLUDED_PACKAGES):
                problems.append(f"R3 {row['local']}: imports excluded package {spec}")


def check_notice(root: Path, rows: list[dict], problems: list[str]) -> None:
    if not rows:
        return
    notice = root / "NOTICE"
    text = notice.read_text() if notice.exists() else ""
    missing = [m for m in NOTICE_MARKS if m not in text]
    if missing:
        problems.append(f"R4 NOTICE: the ledger is not empty but NOTICE lacks the Nod attribution ({', '.join(missing)})")


def top_level_names(root: Path, files: list[str]) -> set[str]:
    names = set()
    for f in files:
        first = f.split("/", 1)[0]
        names.add(Path(first).stem if "/" not in f else first)
    return {n for n in names if n.isidentifier()}


def resolve_js(base: Path, spec: str, root: Path) -> str | None:
    target = (base.parent / spec).resolve()
    for cand in (target, *(target.with_suffix(s) for s in sorted(JS_SUFFIXES)),
                 *(target / f"index{s}" for s in sorted(JS_SUFFIXES))):
        if cand.is_file():
            try:
                return str(cand.relative_to(root.resolve()))
            except ValueError:
                return None
    return None


def module_file(root: Path, module: str) -> str | None:
    parts = module.split(".")
    for cand in (Path(*parts).with_suffix(".py"), Path(*parts) / "__init__.py"):
        if (root / cand).is_file():
            return str(cand)
    return None


def check_boundary(root: Path, rows: list[dict], files: list[str], problems: list[str]) -> None:
    ledgered = {r["local"] for r in rows}
    internal = top_level_names(root, files)
    for row in rows:
        local = row["local"]
        path = root / local
        if not path.is_file():
            continue
        allowed = ledgered | set(row.get("local_deps", []))
        for spec in imports_of(path):
            if path.suffix in JS_SUFFIXES:
                if not spec.startswith("."):
                    continue
                target = resolve_js(path, spec, root)
                if target is None or target not in allowed:
                    problems.append(f"R5 {local}: imports {spec}, which is neither ledgered nor in its local_deps")
            elif path.suffix == ".py":
                if spec.startswith("."):
                    problems.append(f"R5 {local}: relative import {spec}; use an absolute import listed in local_deps")
                    continue
                if spec.split(".")[0] not in internal:
                    continue
                target = module_file(root, spec)
                if target is None or target not in allowed:
                    problems.append(f"R5 {local}: imports repository module {spec}, which is not in its local_deps")


def check(root: Path) -> list[str]:
    problems: list[str] = []
    files = tracked_files(root)
    rows = read_ledger(root, problems)
    check_tree(root, rows, files, problems)
    check_excluded(root, rows, problems)
    check_notice(root, rows, problems)
    check_boundary(root, rows, files, problems)
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = ap.parse_args(argv)
    problems = check(args.root)
    for p in problems:
        print(p)
    if not problems:
        print("nod imports: ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
