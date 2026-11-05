"""Tests for the gate classifier (scripts/gate_classify.py).

calls.jsonl holds synthetic calls in the shapes real agents make (placeholders expanded at load time by
gate_fixture_harvest.expand); cases.jsonl holds the hand-written edge cases. Every row is labelled by the
rule tables in the attention part 2 plan, section 3.
"""
import json
import os
import pathlib
import posixpath
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_classify as GC  # noqa: E402
import gate_fixture_harvest as GH  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures" / "gate"
OWN = ["/home/agent/proj", "/home/agent/.orchestra/state", "/home/agent/.orchestra/.workspace"]


def _load(name):
    return [GH.expand(json.loads(line)) for line in (FIX / name).read_text().splitlines() if line.strip()]


def _fake_realpath(mapping):
    def real(p):
        p = posixpath.normpath(p)
        for src, dst in (mapping or {}).items():
            if GC.inside(p, src):
                return posixpath.normpath(dst + p[len(src):])
        return p
    return real


def _ctx(c):
    return GC.CallCtx(tool=c["tool"], tool_input=c["input"], cwd=c.get("cwd", "/home/agent/proj"),
                      own_dirs=tuple(c.get("own_dirs", OWN)), scratch_dirs=("/tmp",), home="/home/agent",
                      local_bins=frozenset(c.get("local_bins", [])), package_scripts=c.get("package_scripts"),
                      realpath=_fake_realpath(c.get("realpath")))


CASES = _load("calls.jsonl") + _load("cases.jsonl")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.get("id", "?"))
def test_fixture(case, monkeypatch):
    monkeypatch.setattr(GC, "_bin_exists", lambda cwd, name: name in case.get("local_bins", []))
    hits = GC.classify(_ctx(case))
    assert sorted([h.category, h.action] for h in hits) == sorted(case["expect"])
    if case["expect"]:
        assert all(h.certain for h in hits if h.action != "unrecognised")
        assert any(not h.certain for h in hits) == (case.get("certain") is False)
    for h in hits:
        assert h.category in GC.CATEGORIES and h.action in GC.ACTIONS[h.category]
        assert len(h.detail) <= GC.DETAIL_MAX
    if "expect_folder" in case:
        assert [h.folder for h in hits] == [case["expect_folder"]]


def test_fixture_counts():
    calls = _load("calls.jsonl")
    assert 250 <= len(calls) <= 320
    assert len({c["id"] for c in CASES}) == len(CASES)


def test_details_are_redacted():
    h = GC.classify(GC.CallCtx("Bash", {"command": "curl -X POST https://x.io -H 'Authorization: Bearer sk-abcdefghijklmnop'"},
                               "/p", ("/p",), ("/tmp",), "/h"))
    assert "sk-abc" not in h[0].detail and "<redacted>" in h[0].detail


def test_redact_patterns():
    assert GC.redact("token=abc123 ok") == "<redacted> ok"
    assert "<redacted>" in GC.redact("x " + "a1" * 20)
    assert GC.redact("ghp_" + "A" * 30) == "<redacted>"
    assert len(GC.redact("y" * 500)) == GC.DETAIL_MAX


def test_fingerprint_ignores_description_and_key_order():
    a = GC.fingerprint("Bash", {"command": "ls", "description": "x"})
    b = GC.fingerprint("Bash", {"description": "y", "command": "ls"})
    assert a == b and a != GC.fingerprint("Bash", {"command": "ls -a"})
    assert a != GC.fingerprint("Read", {"command": "ls"})


def test_unparseable_shell_is_uncertain_not_a_pass():
    h = GC.classify(GC.CallCtx("Bash", {"command": "echo 'unterminated"}, "/p", ("/p",), ("/tmp",), "/h"))
    assert [(x.category, x.action, x.certain) for x in h] == [("destructive", "unrecognised", False)]


def test_inside_is_component_wise():
    assert GC.inside("/home/agent/proj/a", "/home/agent/proj")
    assert GC.inside("/home/agent/proj", "/home/agent/proj")
    assert not GC.inside("/home/agent/proj2/a", "/home/agent/proj")
    assert GC.inside("/home/agent/proj/../proj/x", "/home/agent/proj/")
    assert not GC.inside("/home/agent/proj/../other", "/home/agent/proj")
    assert not GC.inside("relative", "/home/agent")
    assert not GC.inside("", "/x")


def test_is_secret_path():
    for p in ("/a/.env", "/a/.env.local", "/a/k.pem", "/h/.ssh/config", "/h/.aws/credentials", "/h/.npmrc",
              "/h/.claude/settings.json", "/d/state/attention/GATE_OFF", "/r/hooks/gate-hook.py", "/a/api_token.txt"):
        assert GC.is_secret_path(p), p
    for p in ("/a/environment.ts", "/a/src/app.py", "/h/.claude/projects/x.jsonl"):
        assert not GC.is_secret_path(p), p


def test_hit_round_trip():
    h = GC.Hit("outside_world", "push", "git push origin", True, None, "origin")
    assert GC.Hit.from_dict(h.to_dict()) == h
    assert json.loads(json.dumps(h.to_dict()))["recipient"] == "origin"


def test_every_action_has_a_fixture():
    seen = {tuple(e) for c in CASES for e in c["expect"]}
    want = {(cat, act) for cat, acts in GC.ACTIONS.items() for act in acts}
    assert want - seen == set()


def test_classify_never_raises():
    weird = ["", "   ", "$(", "((", "a <<", "`", "|||", "cat <<EOF\nno end", "\x00", "{ ; }", "ssh", "curl",
             "find . -exec", "xargs", "npm run", "sudo", "env", "timeout", "python3 -c", "git", "gh"]
    for w in weird:
        hits = GC.classify(GC.CallCtx("Bash", {"command": w}, "/p", ("/p",), ("/tmp",), "/h"))
        assert all(isinstance(h, GC.Hit) for h in hits)
    assert GC.classify(GC.CallCtx("Bash", {}, "/p", ("/p",), ("/tmp",), "/h"))[0].action == "unrecognised"
    assert GC.classify(GC.CallCtx("Write", {}, "/p", ("/p",), ("/tmp",), "/h"))[0].action == "unrecognised"


def test_committed_fixtures_pass_the_privacy_check():
    for name in ("calls.jsonl", "cases.jsonl"):
        assert GH.check_text((FIX / name).read_text(), deny=()) == []


def test_hot_path_is_stdlib_only():
    import ast
    tree = ast.parse(pathlib.Path(GC.__file__).read_text())
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)} | \
           {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= {"dataclasses", "hashlib", "json", "os", "re", "shlex", "posixpath", "__future__", "typing"}


def test_speed():
    import time
    cases = _load("calls.jsonl")
    for c in cases[:20]:
        GC.classify(_ctx(c))           # warm the regex caches
    t = time.perf_counter()
    for c in cases:
        GC.classify(_ctx(c))
    assert (time.perf_counter() - t) / max(1, len(cases)) < 0.002        # < 2 ms per call


def test_a_folder_grant_does_not_cover_a_write_through_a_symlink_inside_it():
    """review r1: a hit's folder is its real path, and a grant's folder is stored real (supervision_store)."""
    import gate_policy as GP
    ctx = GC.CallCtx(tool="Write", tool_input={"file_path": "/home/agent/proj/sub/link/passwd", "content": "x"},
                     cwd="/home/agent/other", own_dirs=("/home/agent/other",), scratch_dirs=("/tmp",),
                     home="/home/agent", realpath=_fake_realpath({"/home/agent/proj/sub/link": "/etc"}))
    hits = GC.classify(ctx)
    assert [(h.category, h.action, h.folder) for h in hits] == [("outside_folder", "edit_outside", "/etc")]
    grant = {"category": "outside_folder", "narrow_folder": "/home/agent/proj/sub"}
    assert not GP.grant_matches(grant, hits[0])
    plain = GC.classify(GC.CallCtx(tool="Write", tool_input={"file_path": "/home/agent/proj/sub/a.txt", "content": "x"},
                                   cwd="/home/agent/other", own_dirs=("/home/agent/other",), scratch_dirs=("/tmp",),
                                   home="/home/agent", realpath=_fake_realpath({})))
    assert GP.grant_matches(grant, plain[0])


def test_several_writes_through_different_links_do_not_collapse_to_the_lexical_parent():
    """review r2: each target is resolved before the common folder (writes and deletes)."""
    import gate_policy as GP
    real = _fake_realpath({"/home/agent/proj/sub/l1": "/etc", "/home/agent/proj/sub/l2": "/var"})
    grant = {"category": "outside_folder", "narrow_folder": "/home/agent/proj/sub"}
    dgrant = {"category": "destructive", "narrow_folder": "/home/agent/proj/sub"}
    for cmd in ("touch /home/agent/proj/sub/l1/a /home/agent/proj/sub/l2/b",
                "rm -rf /home/agent/proj/sub/l1/a /home/agent/proj/sub/l2/b"):
        hits = GC.classify(GC.CallCtx(tool="Bash", tool_input={"command": cmd}, cwd="/home/agent/other",
                                      own_dirs=("/home/agent/other",), scratch_dirs=("/tmp",), home="/home/agent",
                                      realpath=real))
        assert hits, cmd
        for h in hits:
            assert h.folder and not GC.inside(h.folder, "/home/agent/proj/sub"), (cmd, h)
            assert not GP.grant_matches(grant, h) and not GP.grant_matches(dgrant, h), (cmd, h)


def test_deleting_a_link_is_recorded_at_the_link_and_link_slash_at_its_target():
    """review r3: rm -rf link removes the link itself (its real parent + name); link/ follows it."""
    real = _fake_realpath({"/home/agent/proj/link": "/etc"})

    def folder(cmd):
        hits = GC.classify(GC.CallCtx(tool="Bash", tool_input={"command": cmd}, cwd="/home/agent/other",
                                      own_dirs=("/home/agent/other",), scratch_dirs=("/tmp",), home="/home/agent",
                                      realpath=real))
        return [h.folder for h in hits if h.action == "delete_files"]
    assert folder("rm -rf /home/agent/proj/link") == ["/home/agent/proj/link"]
    assert folder("rm -rf /home/agent/proj/link/") == ["/etc"]


def test_an_unknown_delete_target_leaves_the_folder_unknown():
    """review r4: xargs rm with a known and an unknown (stdin) target is not narrowed to the known one."""
    hits = GC.classify(GC.CallCtx(tool="Bash", tool_input={"command": "printf x | xargs rm /home/agent/proj/foo"},
                                  cwd="/home/agent/proj", own_dirs=("/home/agent/proj",), scratch_dirs=("/tmp",),
                                  home="/home/agent", realpath=_fake_realpath({})))
    dels = [h for h in hits if h.action == "delete_files"]
    assert dels and all(h.folder is None for h in dels)


def test_an_unknown_http_client_option_is_unrecognised_and_never_covered_by_a_folder_grant():
    """review r8 / PM: an option the tables don't know makes the call unrecognised; a known output beside it keeps
    its own folder, but the unrecognised hit (no folder) is never covered by a folder-narrowed grant."""
    import gate_policy as GP
    hits = GC.classify(GC.CallCtx(tool="Bash", tool_input={"command": "curl --frobnicate -o /var/tmp/known http://127.0.0.1:8890/x"},
                                  cwd="/home/agent/proj", own_dirs=("/home/agent/proj",), scratch_dirs=("/tmp",),
                                  home="/home/agent", realpath=_fake_realpath({})))
    unrec = [h for h in hits if h.action == "unrecognised"]
    assert unrec and unrec[0].folder is None and not unrec[0].certain
    assert not GP.grant_matches({"category": "destructive", "narrow_folder": "/var/tmp"}, unrec[0])
