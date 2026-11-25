"""Tests for integrations/nod/check_imports.py — one per rule, each on a small fixture tree, plus the
real repository, so CI fails the moment an untracked, copyleft or entangled Nod import lands."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_imports", HERE / "check_imports.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

SHA = "08da5a7f10648db7814ca9b7a1b3a458293ffba5"  # pragma: allowlist secret (public Nod commit sha)
NOTICE_OK = "OrchestraOS\n\nNod\nCopyright 2026 n8n GmbH\nhttps://github.com/n8n-io/nod\n"


def header(upstream, sha=SHA, modified="yes", comment="#"):
    return f"{comment} Upstream: n8n-io/nod {upstream} @ {sha} (Apache-2.0), modified: {modified}\n"


def row(local, upstream, **kw):
    base = {"local": local, "upstream": upstream, "upstream_sha": SHA, "verdict": "ADAPT",
            "modified": True, "license": "Apache-2.0", "take_list_id": "T3"}
    base.update(kw)
    return base


def tree(tmp_path, files, rows, notice=NOTICE_OK):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    led = tmp_path / "integrations/nod/IMPORTS.jsonl"
    led.parent.mkdir(parents=True, exist_ok=True)
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "NOTICE").write_text(notice)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    (tmp_path / "scripts/fleet.py").write_text("X = 1\n")
    return tmp_path


def problems(root):
    return ci.check(root)


def rules(found):
    return sorted({p.split()[0] for p in found})


def test_clean_import_passes(tmp_path):
    root = tree(tmp_path,
                {"scripts/redact_nod.py": header("src/server/redaction.ts") + "import re\nimport json\n"},
                [row("scripts/redact_nod.py", "src/server/redaction.ts")])
    assert problems(root) == []


def test_empty_ledger_needs_no_notice(tmp_path):
    root = tree(tmp_path, {}, [], notice="OrchestraOS\n")
    assert problems(root) == []


@pytest.mark.parametrize("field,value", [
    ("verdict", "SKIP"), ("take_list_id", "T19"), ("upstream_sha", "08da5a7"),
    ("license", "GPL-3.0-or-later"), ("modified", "yes"),
])
def test_r1_bad_field_is_refused(tmp_path, field, value):
    root = tree(tmp_path,
                {"scripts/a.py": header("src/server/redaction.ts") + "import re\n"},
                [row("scripts/a.py", "src/server/redaction.ts", **{field: value})])
    assert "R1" in rules(problems(root))


def test_r1_missing_field_and_bad_json(tmp_path):
    root = tree(tmp_path, {}, [])
    (root / "integrations/nod/IMPORTS.jsonl").write_text('{"local": "x.py"}\nnot json\n')
    found = problems(root)
    assert any("missing" in p for p in found) and any("not JSON" in p for p in found)


def test_r2_ledger_line_without_file(tmp_path):
    root = tree(tmp_path, {}, [row("scripts/gone.py", "src/server/redaction.ts")])
    assert any(p.startswith("R2 scripts/gone.py") and "does not exist" in p for p in problems(root))


def test_r2_copied_file_without_ledger_line(tmp_path):
    root = tree(tmp_path, {"dashboard/src/Frame.tsx": header("src/web/components/attachment-frame.tsx",
                                                               comment="//") + "export const A = 1;\n"}, [])
    assert any("not in the ledger" in p for p in problems(root))


def test_r2_header_must_match_ledger(tmp_path):
    other = "1" * 40
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts", sha=other, modified="no")},
                [row("scripts/a.py", "src/server/redaction.ts")])
    found = problems(root)
    assert any("header says" in p for p in found) and any("modified" in p for p in found)


def test_r2_ledgered_file_without_header(tmp_path):
    root = tree(tmp_path, {"scripts/a.py": "import re\n"}, [row("scripts/a.py", "src/server/redaction.ts")])
    assert any("no provenance header" in p for p in problems(root))


def test_r3_excluded_upstream_path(tmp_path):
    up = "src/providers/local-speech/phonemizer-worker.ts"
    root = tree(tmp_path, {"services/ph.ts": header(up, comment="//")}, [row("services/ph.ts", up)])
    assert any(p.startswith("R3") and "excluded path" in p for p in problems(root))


@pytest.mark.parametrize("line", [
    "import { makeWASocket } from '@whiskeysockets/baileys';",
    "import { login } from '@earendil-works/pi-ai/oauth';",
    "const s = require('libsignal');",
    "import decode from '@audio/decode-webm';",
])
def test_r3_excluded_package_import(tmp_path, line):
    up = "src/providers/pi/models.ts"
    root = tree(tmp_path, {"services/m.ts": header(up, comment="//") + line + "\n"}, [row("services/m.ts", up)])
    assert any(p.startswith("R3") and "excluded package" in p for p in problems(root))


def test_r4_notice_required_once_ledger_is_used(tmp_path):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + "import re\n"},
                [row("scripts/a.py", "src/server/redaction.ts")], notice="OrchestraOS\n")
    assert "R4" in rules(problems(root))


def test_r5_python_import_of_repo_module_is_refused(tmp_path):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + "from scripts import fleet\nimport scripts.fleet\n"},
                [row("scripts/a.py", "src/server/redaction.ts")])
    assert "R5" in rules(problems(root))


def test_r5_declared_local_dep_is_allowed(tmp_path):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + "import scripts.fleet\n"},
                [row("scripts/a.py", "src/server/redaction.ts", local_deps=["scripts/fleet.py"])])
    assert problems(root) == []


def test_r5_js_relative_import_must_be_ledgered_or_declared(tmp_path):
    up = "src/web/components/attachment-frame.tsx"
    files = {"dashboard/src/Frame.tsx": header(up, comment="//") + "import { cn } from './lib/utils';\nimport React from 'react';\n",
             "dashboard/src/lib/utils.ts": "export const cn = () => '';\n"}
    root = tree(tmp_path, files, [row("dashboard/src/Frame.tsx", up, take_list_id="T2")])
    assert "R5" in rules(problems(root))
    root = tree(tmp_path, files, [row("dashboard/src/Frame.tsx", up, take_list_id="T2",
                                      local_deps=["dashboard/src/lib/utils.ts"])])
    assert problems(root) == []


def test_real_repository_passes():
    root = HERE.parents[1]
    assert ci.check(root) == []


def test_cli_exit_codes(tmp_path, capsys):
    assert ci.main(["--root", str(tree(tmp_path, {}, []))]) == 0
    (tmp_path / "integrations/nod/IMPORTS.jsonl").write_text("nope\n")
    assert ci.main(["--root", str(tmp_path)]) == 1
    assert "R1" in capsys.readouterr().out


# Review of PR #612 (codex-gm): each reproduced bypass now has a red test.

@pytest.mark.parametrize("upstream", [
    "src/providers/x/../local-speech/phonemizer.ts",
    "./src/providers/local-speech/phonemizer-worker.ts",
    "src//providers/local-speech/phonemizer-worker.ts",
    "/src/providers/local-speech/phonemizer-worker.ts",
])
def test_review_r3_traversal_in_upstream_is_refused(tmp_path, upstream):
    root = tree(tmp_path, {"services/ph.ts": header(upstream, comment="//")}, [row("services/ph.ts", upstream)])
    assert any(p.startswith("R1") and "plain relative path" in p for p in problems(root))


def test_review_local_path_traversal_is_refused(tmp_path):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + "import re\n"},
                [row("scripts/../scripts/a.py", "src/server/redaction.ts")])
    assert any("plain relative path" in p for p in problems(root))


@pytest.mark.parametrize("lic", ["MIT", "BSD-3-Clause", "ISC"])
def test_review_license_must_match_the_apache_header(tmp_path, lic):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + "import re\n"},
                [row("scripts/a.py", "src/server/redaction.ts", license=lic)])
    assert any(p.startswith("R1") and "license" in p for p in problems(root))


@pytest.mark.parametrize("code", [
    "const local = './lib/utils';\nconst m = await import(local);\n",
    "const m = require(name);\n",
    "const m = await import(`./lib/${x}`);\n",
])
def test_review_r5_computed_js_import_is_refused(tmp_path, code):
    up = "src/web/components/attachment-frame.tsx"
    files = {"dashboard/src/Frame.tsx": header(up, comment="//") + code,
             "dashboard/src/lib/utils.ts": "export const cn = () => '';\n"}
    root = tree(tmp_path, files, [row("dashboard/src/Frame.tsx", up, take_list_id="T2")])
    assert any(p.startswith("R5") and "computed import" in p for p in problems(root))


@pytest.mark.parametrize("code", [
    "import importlib\nm = importlib.import_module('scripts.fleet')\n",
    "m = __import__('scripts.fleet')\n",
])
def test_review_r5_computed_python_import_is_refused(tmp_path, code):
    root = tree(tmp_path, {"scripts/a.py": header("src/server/redaction.ts") + code},
                [row("scripts/a.py", "src/server/redaction.ts")])
    assert any(p.startswith("R5") and "computed import" in p for p in problems(root))


def test_review_literal_dynamic_import_of_a_declared_dep_still_passes(tmp_path):
    up = "src/web/components/attachment-frame.tsx"
    files = {"dashboard/src/Frame.tsx": header(up, comment="//") + "const m = await import('./lib/utils');\n",
             "dashboard/src/lib/utils.ts": "export const cn = () => '';\n"}
    root = tree(tmp_path, files, [row("dashboard/src/Frame.tsx", up, take_list_id="T2",
                                      local_deps=["dashboard/src/lib/utils.ts"])])
    assert problems(root) == []
