#!/usr/bin/env python3
"""seed_issues.py — create one GitHub issue per `## T<n>` / `## G<n>` section of
docs/HACKATHON_ISSUES.md (docs/RELEASE_CHECKLIST.md step 6). Idempotent by title: a section
whose title already exists as an issue (open or closed) is skipped.

    python3 scripts/seed_issues.py --repo Tulum-DAO/orchestraos --dry-run
    python3 scripts/seed_issues.py --repo Tulum-DAO/orchestraos

Uses the `gh` CLI (honours GH_CONFIG_DIR). Labels come from the section's `labels: ...` line
and are created on the repo if missing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "HACKATHON_ISSUES.md"
HEAD = re.compile(r"^## ([TG]\d+) · (.+?)\s*$")
LABELS = re.compile(r"^`labels:\s*([^`]+)`\s*$")


def parse(text: str) -> list:
    out, cur = [], None
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            cur = {"key": m.group(1), "title": f"{m.group(1)} · {m.group(2)}", "labels": [], "body": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        lm = LABELS.match(line.strip())
        if lm and not cur["labels"]:
            cur["labels"] = [l.strip() for l in lm.group(1).split(",") if l.strip()]
            continue
        cur["body"].append(line)
    for s in out:
        s["body"] = "\n".join(s["body"]).strip() + f"\n\n_Seeded from docs/HACKATHON_ISSUES.md §{s['key']}._"
    return out


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--doc", default=str(DOC))
    ns = ap.parse_args(argv)
    sections = parse(Path(ns.doc).read_text())
    print(f"{len(sections)} sections in {ns.doc}")
    if ns.dry_run:
        for s in sections:
            print(f"  {s['title']}  [{', '.join(s['labels'])}]  ({len(s['body'])} chars)")
        return 0
    existing = {i["title"] for i in json.loads(gh("issue", "list", "--repo", ns.repo, "--state", "all",
                                                   "--limit", "500", "--json", "title"))}
    have = {l["name"] for l in json.loads(gh("label", "list", "--repo", ns.repo, "--limit", "200", "--json", "name"))}
    made = 0
    for s in sections:
        if s["title"] in existing:
            print(f"  skip (exists): {s['title']}")
            continue
        for lab in s["labels"]:
            if lab not in have:
                gh("label", "create", lab, "--repo", ns.repo, "--color", "0e8a16", check=False)
                have.add(lab)
        args = ["issue", "create", "--repo", ns.repo, "--title", s["title"], "--body", s["body"]]
        for lab in s["labels"]:
            args += ["--label", lab]
        url = gh(*args).strip()
        print(f"  created: {s['title']} -> {url}")
        made += 1
    print(f"created {made}, skipped {len(sections) - made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
