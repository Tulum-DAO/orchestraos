"""Levels 8-9: judgments about files; the files never reach the caller."""
import json
import os
import threading
import time

import pytest

from _jevtest import answers_decide, choice_a, noul_a
from jev import files
from jev.client import JevClient, JevTransportError


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src" / "auth").mkdir(parents=True)
    (r / "node_modules" / "dep").mkdir(parents=True)
    (r / "src" / "auth" / "session.py").write_text("def validate_token(t):\n    return check(t)\n")
    (r / "src" / "billing.py").write_text("def invoice(): pass\n")
    (r / "src" / "empty.py").write_text("")
    (r / "src" / "logo.png").write_bytes(b"\x89PNG....")
    (r / "src" / "blob.bin").write_bytes(b"abc\0def")
    (r / "node_modules" / "dep" / "index.js").write_text("x")
    (r / "README.md").write_text("# readme\n")
    (tmp_path / "secret.txt").write_text("outside")
    os.symlink(tmp_path / "secret.txt", r / "src" / "link.txt")
    return str(r)


def test_read_file_state(repo):
    assert files.read_file_state("README.md", repo) == {"path": "README.md", "content": "# readme\n"}
    for path, msg in (("nope.py", "not found"), ("src", "not a file"), ("src/blob.bin", "binary"),
                      ("../secret.txt", "outside"), ("src/link.txt", "outside")):
        with pytest.raises(files.FileStateError, match=msg):
            files.read_file_state(path, repo)


def test_read_file_state_size_limit(repo, monkeypatch):
    monkeypatch.setattr(files, "MAX_FILE_CHARS", 5)
    with pytest.raises(files.FileStateError, match="too large"):
        files.read_file_state("README.md", repo)


def test_ask_file_bool_sends_the_file_returns_only_the_answer(repo):
    calls = []
    r = files.ask_file_bool("src/auth/session.py", "Does `content` validate tokens?", repo, yes="checks a token",
                            decide=answers_decide({"answer": noul_a(0.91)}, calls))
    assert r["answer"] is True and r["noul"] == 0.91
    assert "validate_token" in calls[0][0]["content"] and "validate_token" not in json.dumps(r)
    assert calls[0][1]["answer"]["criteria"] == {"true": "checks a token"}


def test_ask_file_choice_adds_an_exit(repo):
    calls = []
    files.ask_file_choice("src/billing.py", "Which layer?", {"api": "routes", "domain": "logic"}, repo,
                          answers_decide({"answer": choice_a("domain", 0.7)}, calls))
    assert "other" in calls[0][1]["answer"]["criteria"]
    calls.clear()
    files.ask_file_choice("src/billing.py", "Which?", {"a": None, "none": None}, repo,
                          answers_decide({"answer": choice_a("a", 0.7)}, calls))
    assert "other" not in calls[0][1]["answer"]["criteria"]


def test_ask_file_score_nearest_level(repo):
    a = {"type": "score", "score": 1.5, "confidence": 0.5, "legend": {"0": "safe", "1": "some", "2": "risky"},
         "probabilities": {"0": 0.1, "1": 0.3, "2": 0.6}}
    r = files.ask_file_score("src/billing.py", "Risk?", ["safe", "some", "risky"], repo, answers_decide({"answer": a}))
    assert r["nearest"] == "risky" and r["top"] == 2  # JS Math.round(1.5) == 2


def test_ask_jev_file_dispatch_and_errors(repo):
    d = answers_decide({"answer": noul_a(0.2)})
    assert files.ask_jev_file("README.md", "q?", repo, decide=d)["answer"] is False
    with pytest.raises(ValueError, match="options"):
        files.ask_jev_file("README.md", "q?", repo, kind="choice")
    with pytest.raises(ValueError, match="levels"):
        files.ask_jev_file("README.md", "q?", repo, kind="score")
    with pytest.raises(ValueError, match="unknown kind"):
        files.ask_jev_file("README.md", "q?", repo, kind="maybe")


def test_expand_and_prune(repo):
    flat = files.expand_patterns(["src"], repo)
    assert "src/billing.py" in flat and "src/auth/session.py" not in flat
    deep = files.expand_patterns(["src/"], repo, recursive=True)
    assert "src/auth/session.py" in deep
    globbed = files.expand_patterns(["**/*.py", "missing.py"], repo)
    assert "src/auth/session.py" in globbed and "missing.py" in globbed
    pruned = files.prune_files(files.expand_patterns(["src", "node_modules/dep/index.js", "missing.py", "../secret.txt"], repo), repo)
    reasons = {s["path"]: s["reason"] for s in pruned["skipped"]}
    # Binary CONTENT survives prune (extension check only, as in the reference); read_file_state rejects it.
    assert pruned["files"] == ["src/billing.py", "src/blob.bin"]
    assert reasons["src/empty.py"] == "empty" and reasons["src/logo.png"].startswith("binary")
    assert reasons["node_modules/dep/index.js"] == "skipped directory"
    assert reasons["missing.py"] == "not found" and reasons["../secret.txt"] == "outside the repo"
    assert reasons["src/link.txt"] == "outside the repo"


def test_prune_cap(repo):
    p = files.prune_files(["src/billing.py", "README.md", "src/auth/session.py"], repo, cap=2)
    assert len(p["files"]) == 2 and "file cap" in p["skipped"][0]["reason"]


def test_parse_questions():
    assert files.parse_questions('{"q": {"type": "noul", "instructions": "x"}}')["q"]["type"] == "noul"
    with pytest.raises(ValueError, match="not valid JSON"):
        files.parse_questions("{nope")
    with pytest.raises(ValueError, match="object keyed"):
        files.parse_questions("[1]")
    with pytest.raises(ValueError, match="unknown type"):
        files.parse_questions({"q": {"type": "x", "instructions": "y"}})


def test_ask_files_parallel_with_per_file_failures(repo):
    seen, lock, active, peak = [], threading.Lock(), [0], [0]

    def decide(state, questions):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
            seen.append(state["path"])
        if state["path"] == "README.md":
            raise JevTransportError("down")
        return {"answers": {"q": noul_a(0.8)}, "usage": {"input_tokens": 1, "output_tokens": 1}}

    q = json.dumps({"q": {"type": "noul", "instructions": "Is `content` about auth?"}})
    out = files.ask_files(["**/*.py", "README.md"], q, repo, decide=decide, concurrency=4)
    assert [r["path"] for r in out["results"]] == ["src/auth/session.py", "src/billing.py"]
    assert out["calls"] == 2 and peak[0] >= 2
    reasons = {s["path"]: s["reason"] for s in out["skipped"]}
    assert reasons["README.md"].startswith("call failed") and reasons["src/empty.py"] == "empty"
    assert all("content" not in json.dumps(r["answers"]) for r in out["results"])


def test_ask_files_bad_questions_fail_before_any_read(repo):
    def never(s, q):
        raise AssertionError("no call")
    with pytest.raises(ValueError):
        files.ask_files(["**/*.py"], "{bad", repo, decide=never)


def test_ask_files_bug_in_decide_is_not_swallowed(repo):
    def buggy(s, q):
        raise KeyError("oops")
    with pytest.raises(KeyError):
        files.ask_files(["src/billing.py"], {"q": {"type": "noul", "instructions": "x"}}, repo, decide=buggy)


def test_ask_files_with_mock_end_to_end(repo):
    c = JevClient(provider="mock")
    out = files.ask_files(["src/"], {"layer": {"type": "choice", "instructions": "Which layer is `content`?",
                                               "criteria": {"auth": "tokens, sessions", "billing": "invoices", "other": None}}},
                          repo, recursive=True, decide=c.system_one)
    picks = {r["path"]: r["answers"]["layer"]["choice"] for r in out["results"]}
    assert picks == {"src/auth/session.py": "auth", "src/billing.py": "billing"}


def test_pick_first_file():
    cands = [{"path": "a.py", "note": "auth"}, {"path": "b.py"}]
    q = files.pick_question("Which first?", cands)
    assert set(q["pick"]["criteria"]) == {"a.py", "b.py", "none"}
    assert files.pick_first_file("q", cands, answers_decide({"pick": choice_a("a.py", 0.6)}))["path"] == "a.py"
    assert files.pick_first_file("q", cands, answers_decide({"pick": choice_a("a.py", 0.2)}))["path"] is None
    assert files.pick_first_file("q", cands, answers_decide({"pick": choice_a("none", 0.9)}))["path"] is None
    assert files.pick_first_file("q", [], None) == {"path": None, "confidence": 0, "probabilities": {}}
