#!/usr/bin/env python3
"""Redact credentials from text before it is logged, written to a doc or sent anywhere.

Two passes over the text:
  1. SHAPES: provider token formats and credential-carrying syntax (see PATTERNS). Each rule
     names the part of its match that is secret; the rest (a key name, a header name, an
     auth scheme, a URL's user) stays so the redacted text still reads.
  2. KNOWN VALUES: exact strings the caller already knows are secret (from a file, or the
     values of credential-named environment variables). This catches a value no shape knows.

`spans(text)` returns the secret ranges (merged, sorted) so a caller can highlight or count them;
`redact(text)` replaces each range with the placeholder. The original value is never printed.

What must survive: git SHAs, UUIDs, content hashes (`sha256-...`), plain numbers such as
`max_tokens=4096`, and ordinary prose.

CLI (stdin -> stdout filter):
    python3 scripts/redact_secrets.py [--known-file PATH] [--known-env]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable

PLACEHOLDER = "<REDACTED>"
MIN_KNOWN_VALUE = 8          # shorter known values would also hit ordinary words

# A credential-ish word inside a key name. `token` must not be followed by `s` so that
# `max_tokens` / `input_tokens` are counters, not secrets.
# No bare `pwd` (the shell's working directory) and no session ids (they are UUIDs, not secrets).
_CRED_WORD = (r"(?:token(?!s)|secret|passw(?:or)?d|passphrase|api[_-]?key|apikey|access[_-]?key"
              r"|private[_-]?key|client[_-]?secret|credentials?|signing[_-]?key)")
_B = r"(?<![A-Za-z0-9_-])"   # left boundary for prefixed tokens
_E = r"(?![A-Za-z0-9_-])"    # right boundary


@dataclass(frozen=True)
class Rule:
    kind: str
    regex: re.Pattern
    group: int = 0           # the group that is secret (0 = whole match)
    min_len: int = 1
    skip_digits: bool = False  # a value made only of digits is a count, not a secret
    skip_refs: bool = False    # a value that NAMES a secret (a call, an env var, a template) is not one


PATTERNS: tuple[Rule, ...] = (
    # PEM private key blocks; an unterminated block is redacted to the end of the text.
    Rule("pem", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
                           re.DOTALL)),
    Rule("anthropic", re.compile(_B + r"sk-ant-[A-Za-z0-9_-]{20,}" + _E)),
    Rule("openai", re.compile(_B + r"sk-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}" + _E)),
    Rule("github", re.compile(_B + r"gh[pousr]_[A-Za-z0-9]{30,}" + _E)),
    Rule("github-pat", re.compile(_B + r"github_pat_[A-Za-z0-9_]{40,}" + _E)),
    Rule("slack", re.compile(_B + r"xox[abeoprs]-[A-Za-z0-9-]{10,}" + _E)),
    Rule("aws-key-id", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    Rule("google", re.compile(_B + r"AIza[0-9A-Za-z_-]{35}" + _E)),
    Rule("gitlab", re.compile(_B + r"glpat-[A-Za-z0-9_-]{20,}" + _E)),
    Rule("telegram", re.compile(r"(?<![0-9])[0-9]{6,}:[A-Za-z0-9_-]{30,}" + _E)),
    Rule("jwt", re.compile(_B + r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*" + _E)),
    # Authorization schemes: keep the scheme word, drop the credential.
    Rule("auth-scheme", re.compile(r"(?i)\b(?:Bearer|Basic|Token|Digest)\s+([A-Za-z0-9._~+/=-]{8,})"), 1),
    # Credential headers (any position: curl -H "X-Api-Key: ..."). The value runs to the end of
    # the line or a closing quote. A value that starts with an auth scheme is one token, left to
    # the rule above, so the rest of the line stays.
    Rule("header", re.compile(
        r"(?i)\b(?:proxy-)?(?:authorization|x-api-key|api-key|x-auth-token|x-access-token|cookie|set-cookie)"
        r"\s*:\s*(?!(?:Bearer|Basic|Token|Digest)\s)([^\s\"'][^\"'\r\n]*)"), 1),
    # user:password@ in a URL; the user stays.
    Rule("url-password", re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]*:([^@\s/]+)@"), 1),
    # Credential query parameters.
    Rule("query-param", re.compile(
        r"(?i)[?&](?:access_token|refresh_token|id_token|token|api_key|apikey|key|secret|client_secret"
        r"|password|pwd|sig|signature|code)=([^&#\s\"']+)"), 1, min_len=4),
    # data: URIs carrying base64 (screenshots, attachments): the payload goes, the media type stays.
    Rule("data-uri", re.compile(r"(?i)\bdata:[\w.+-]+/[\w.+-]+(?:;[\w.+-]+=[\w.+-]+)*;base64,([A-Za-z0-9+/=]+)"), 1,
         min_len=16),
    # KEY=value, KEY: value, "key": "value" where the key names a credential.
    Rule("credential-field", re.compile(
        r"(?i)[A-Za-z0-9_.-]*" + _CRED_WORD + r"[A-Za-z0-9_.-]*[\"']?\s*[=:]\s*[\"']?([^\s\"',;}&]+)"), 1,
         min_len=8, skip_digits=True, skip_refs=True),
)

# `secrets.get("X")`, `GEMINI_API_KEY`, `$TOKEN`, `${{ secrets.X }}`, `<your-key>`, `%(pw)s`
_REFERENCE = re.compile(r"[A-Z][A-Z0-9_]*|.*\(.*|[$<%].*|\{\{.*")


class Redactor:
    """Shape rules plus an optional set of known secret values."""

    def __init__(self, known_values: Iterable[str] = (), placeholder: str = PLACEHOLDER,
                 rules: tuple[Rule, ...] = PATTERNS):
        vals = {v.strip() for v in known_values if v and len(v.strip()) >= MIN_KNOWN_VALUE}
        self.known = sorted(vals, key=len, reverse=True)
        self.placeholder = placeholder
        self.rules = rules

    def raw_spans(self, text: str) -> list[tuple[int, int, str]]:
        out = []
        for rule in self.rules:
            for m in rule.regex.finditer(text):
                s, e = m.span(rule.group)
                if s < 0:
                    continue
                val = text[s:e]
                if len(val) < rule.min_len or (rule.skip_digits and val.isdigit()):
                    continue
                if rule.skip_refs and _REFERENCE.fullmatch(val):
                    continue
                out.append((s, e, rule.kind))
        for val in self.known:
            start = text.find(val)
            while start >= 0:
                out.append((start, start + len(val), "known-value"))
                start = text.find(val, start + 1)
        return out

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        """Secret ranges, sorted, overlaps merged (the earliest-starting rule names the range)."""
        merged: list[list] = []
        for s, e, kind in sorted(self.raw_spans(text)):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e, kind])
        return [tuple(m) for m in merged]

    def redact(self, text: str) -> str:
        parts, pos = [], 0
        for s, e, _ in self.spans(text):
            parts.append(text[pos:s])
            parts.append(self.placeholder)
            pos = e
        parts.append(text[pos:])
        return "".join(parts)


_CRED_NAME = re.compile(r"(?i)" + _CRED_WORD)


def known_values_from_env(env=None) -> list[str]:
    """Values of environment variables whose NAME looks like a credential. A value that is a
    filesystem path (GOOGLE_APPLICATION_CREDENTIALS=/path/key.json) names where a secret is,
    not the secret, so it is left out."""
    env = os.environ if env is None else env
    return [v for k, v in env.items() if _CRED_NAME.search(k) and v and not v.startswith(("/", "~"))]


def known_values_from_file(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def redact(text: str, known_values: Iterable[str] = ()) -> str:
    return Redactor(known_values).redact(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stdin -> stdout credential redaction filter")
    ap.add_argument("--known-file", action="append", default=[],
                    help="file with one known secret value per line (repeatable)")
    ap.add_argument("--known-env", action="store_true",
                    help="also redact the values of credential-named environment variables")
    args = ap.parse_args(argv)
    known: list[str] = []
    for p in args.known_file:
        known += known_values_from_file(p)
    if args.known_env:
        known += known_values_from_env()
    sys.stdout.write(Redactor(known).redact(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
