#!/usr/bin/env python3
"""Fail the build on a hardcoded secret fallback: `SECRET = env.X || "literal"`.

Why this is its own gate, next to `secret-scan` and `operator-identifier-scan`:

`detect-secrets` hunts things that LOOK like credentials -- high entropy, known token shapes.
A fallback secret is usually none of those. `orchestraOS-<thing>-<year>` reads as a label, sails
under every entropy threshold, and was committed to a public repo where it sat in two files
signing admin session cookies for a service whose env var was never set. The published string
WAS the key. Entropy was never the signal; the SHAPE was -- a secret-ish name, an env lookup,
and a literal standing behind it.

So this gate ignores the value entirely and matches the shape. Anything a reader would call a
default is fine (a path, a URL, a port, an empty string, another env var); only a literal
standing in for a SECRET is not.

Usage:
  scan_secret_fallbacks.py --git-ls
  scan_secret_fallbacks.py file.ts ...
Exit 1 on any hit. `secret-fallback-ok: <reason>` on the line exempts it, and exemptions are
counted in the summary so they cannot accumulate unseen.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# A name we treat as naming a secret. Deliberately broad on the stem...
SECRET_STEM = r"(?:SECRET|PASSWORD|PASSWD|TOKEN|APIKEY|API_KEY|PRIVATE_KEY|SIGNING_KEY|JWT|HMAC|SALT|CREDENTIAL)"
# ...and narrow on the suffix: a *_FILE / *_PATH / *_DIR / *_URL / *_URI / *_NAME / *_ID holds a
# LOCATION, not a secret, and a literal default for one is ordinary configuration.
LOCATION_SUFFIX = r"(?:_FILE|_PATH|_DIR|_URL|_URI|_NAME|_ID|_ENV|_VAR|_HEADER|_COOKIE|_PREFIX)\b"

# The prefix is OPTIONAL: requiring a character before the stem silently exempted every
# name that STARTS with one -- `API_KEY`, `TOKEN`, `JWT` -- which is the most common
# real-world spelling of this bug. Found by test_nullish_coalescing_is_the_same_bug.
_NAME = rf"(?:[A-Za-z_][A-Za-z0-9_]*)?{SECRET_STEM}[A-Za-z0-9_]*"

# JS/TS: `const X = process.env.Y || "lit"`  /  `?? 'lit'`
_JS = re.compile(
    rf"(?P<name>{_NAME})\s*(?::[^=]*)?=\s*"
    r"(?:process\.env(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]]+\])|import\.meta\.env\.[A-Za-z0-9_]+)"
    r"\s*(?:\|\||\?\?)\s*(?P<q>['\"`])(?P<lit>(?:(?!(?P=q)).){8,})(?P=q)",
    re.IGNORECASE,
)
# Python: `X = os.environ.get("Y", "lit")` / `os.getenv("Y", "lit")`
_PY = re.compile(
    rf"(?P<name>{_NAME})\s*(?::[^=]*)?=\s*"
    r"os\.(?:environ\.get|getenv)\(\s*['\"][^'\"]+['\"]\s*,\s*(?P<q>['\"])(?P<lit>(?:(?!(?P=q)).){8,})(?P=q)",
    re.IGNORECASE,
)
# Python: `X = os.environ["Y"] if ... else "lit"` is rare; `or "lit"` is the common one.
_PY_OR = re.compile(
    rf"(?P<name>{_NAME})\s*(?::[^=]*)?=\s*"
    r"os\.(?:environ(?:\.get)?\([^)]*\)|environ\[[^\]]+\]|getenv\([^)]*\))"
    r"\s+or\s+(?P<q>['\"])(?P<lit>(?:(?!(?P=q)).){8,})(?P=q)",
    re.IGNORECASE,
)

_PRAGMA = re.compile(r"secret-fallback-ok:\s*\S")
_SUFFIX = re.compile(LOCATION_SUFFIX, re.IGNORECASE)

SCANNED_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".sh", ".bash", ".go", ".rb"}


def scan_text(path: str, text: str) -> tuple[list[tuple[int, str, str]], int]:
    """Return (hits, pragma_count). A hit is (lineno, name, why) and NEVER carries the value."""
    hits: list[tuple[int, str, str]] = []
    pragmas = 0
    for i, line in enumerate(text.splitlines(), 1):
        if _PRAGMA.search(line):
            pragmas += 1
            continue
        for rx, why in ((_JS, "env-or-literal"), (_PY, "getenv-default-literal"), (_PY_OR, "env-or-literal")):
            m = rx.search(line)
            if not m:
                continue
            name = m.group("name")
            # A location-shaped name holds a path/url, not a secret.
            if _SUFFIX.search(name):
                continue
            hits.append((i, name, why))
            break
    return hits, pragmas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--git-ls", action="store_true",
                    help="enumerate tracked files internally; $(git ls-files) word-splits on "
                         "whitespace and lets a '-'-prefixed path be eaten as a flag")
    ap.add_argument("--min-files", type=int, default=0,
                    help="fail if fewer than N files were scanned, so a gate that silently "
                         "matched nothing cannot pass")
    args = ap.parse_args()

    files = list(args.files)
    if args.git_ls:
        out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True, check=True).stdout
        files += [f for f in out.split("\0") if f]

    scanned = 0
    pragmas = 0
    all_hits: list[tuple[str, int, str, str]] = []
    for f in files:
        if not any(f.endswith(e) for e in SCANNED_EXT):
            continue
        try:
            with open(f, encoding="utf-8", errors="strict") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        hits, pr = scan_text(f, text)
        pragmas += pr
        all_hits += [(f, ln, nm, why) for ln, nm, why in hits]

    print(f"secret-fallback-scan: scanned {scanned} files, {len(all_hits)} hits, "
          f"{pragmas} lines exempted by pragma")

    if scanned < args.min_files:
        print(f"REFUSING: scanned {scanned} files, expected at least {args.min_files}. "
              f"A gate that matched nothing is not a passing gate.", file=sys.stderr)
        return 2

    if all_hits:
        print("\nHardcoded secret fallbacks -- a literal behind a secret is a PUBLISHED key:\n",
              file=sys.stderr)
        for f, ln, nm, why in all_hits:
            print(f"  {f}:{ln}: [{why}] {nm} falls back to a string literal", file=sys.stderr)
        print("\nResolve from env, then a secret file, then a random per-process value "
              "(or fail closed). Never a literal. See api/src/lib/shared-secret.ts.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
