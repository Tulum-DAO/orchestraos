"""Level 10: ask_jev state assembly, the read-only command gate, and the spend ledger."""
import json

import pytest

from _jevtest import answers_decide, choice_a, noul_a
from jev import ask_jev as aj
from jev.client import JevTransportError

EFFECTS = ["read_only", "reversible", "irreversible"]
Q = {"q": {"type": "noul", "instructions": "Is `output` a real failure?"}}


def gate_answers(effect, conf=0.9, destructive=0.1):
    return {"effect": choice_a(effect, conf, EFFECTS), "destructive_intent": noul_a(destructive)}


def fake_run(stdout="FAILED test_x", stderr="", code=1):
    ran = []

    def run(cmd, cwd):
        ran.append(cmd)
        return {"command": cmd, "exit_code": code, "stdout": stdout, "stderr": stderr}
    run.ran = ran
    return run


def test_parse_state():
    assert aj.parse_state('{"a": 1}') == {"a": 1}
    assert aj.parse_state("[1]") == [1]
    assert aj.parse_state("{not json}") == "{not json}"
    assert aj.parse_state({"x": 1}) == {"x": 1}


def test_ask_jev_refuses_empty_state():
    for empty in ("", "  ", {}, "{}"):
        with pytest.raises(aj.AskStateError, match="empty"):
            aj.ask_jev(empty, Q, answers_decide({}))
    assert aj.ask_jev("hello", json.dumps(Q), answers_decide({"q": noul_a(0.3)}))["answers"]["q"]["noul"] == 0.3


def test_assemble_order_and_summary(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    run = fake_run()
    asm = aj.assemble_state('{"plan": "fix"}', ["a.py", "missing.py"], "pytest -q", str(tmp_path), run,
                            gate=lambda c, d: {"allow": True, "reason": "ok"})
    st = asm["state"]
    assert list(st) == ["plan", "files", "output"]
    assert st["files"] == {"a.py": "print(1)\n"} and st["output"]["exit_code"] == 1
    sm = asm["summary"]
    assert sm["own_fields"] == ["plan"] and sm["files"] == ["a.py"] and sm["output"].startswith("pytest -q, exit 1")
    assert {"path": "missing.py", "reason": "not found"} in sm["skipped"]


def test_plain_text_and_list_state_are_wrapped():
    gate = lambda c, d: {"allow": True, "reason": ""}  # noqa: E731
    assert aj.assemble_state("a note", gate=gate)["state"] == {"text": "a note"}
    assert aj.assemble_state("[1, 2]", gate=gate)["state"] == {"items": [1, 2]}


def test_nothing_to_judge_and_own_state_cap():
    with pytest.raises(aj.AskStateError, match="nothing to judge"):
        aj.assemble_state()
    with pytest.raises(aj.AskStateError, match="Do not paste file contents"):
        aj.assemble_state("x" * (aj.MAX_OWN_STATE_CHARS + 10))


def test_more_than_20_files_points_to_ask_jev_files(tmp_path):
    for i in range(21):
        (tmp_path / f"f{i}.txt").write_text("x")
    with pytest.raises(aj.AskStateError, match="ask_jev_files"):
        aj.assemble_state(paths=["."], cwd=str(tmp_path))


def test_overflow_names_parts_and_a_split(tmp_path, monkeypatch):
    monkeypatch.setattr(aj, "STATE_TOKEN_BUDGET", 100)
    (tmp_path / "a.txt").write_text("a" * 300)
    (tmp_path / "b.txt").write_text("b" * 300)
    with pytest.raises(aj.AskStateError) as ei:
        aj.assemble_state(paths=["a.txt", "b.txt"], cwd=str(tmp_path))
    msg = str(ei.value)
    assert "the limit per call is 100 tokens" in msg and "Split into 2 calls" in msg and "call 1: paths [" in msg


def test_oversize_part_is_named():
    parts = [{"name": "huge.log", "tokens": 500, "kind": "file"}, {"name": "your state", "tokens": 10, "kind": "own"}]
    msg = aj.overflow_message(parts, 100)
    assert "Too large for any single call: huge.log" in msg and "Split into" not in msg


def test_suggest_split_first_fit():
    parts = [{"name": n, "tokens": t, "kind": "file"} for n, t in (("a", 60), ("b", 50), ("c", 40))]
    groups = aj.suggest_split(parts, 100)
    assert [[p["name"] for p in g] for g in groups] == [["a", "c"], ["b"]]


# ---------------------------------------------------------------- the command gate

def test_command_gate_requires_read_only():
    assert aj.command_gate("pytest", ".", answers_decide(gate_answers("read_only")))["allow"]
    r = aj.command_gate("npm install", ".", answers_decide(gate_answers("reversible")))
    assert not r["allow"] and "not read-only" in r["reason"]
    assert aj.command_gate("pytest -q", ".", answers_decide(gate_answers("read_only", conf=0.59)))["allow"]
    assert not aj.command_gate("cat x", ".", answers_decide(gate_answers("read_only", conf=0.49)))["allow"]
    r = aj.command_gate("rm -rf /", ".", answers_decide(gate_answers("irreversible", 0.95, 0.95)))
    assert not r["allow"] and r["reason"].startswith("blocked")


def test_command_gate_fails_closed():
    def down(s, q):
        raise JevTransportError("x")
    r = aj.command_gate("ls", ".", down)
    assert not r["allow"] and "not run" in r["reason"]


def test_ask_jev_tool_never_runs_a_refused_command(tmp_path):
    run = fake_run()

    def decide(state, questions):
        if "effect" in questions:
            return {"answers": gate_answers("irreversible", 0.95, 0.95)}
        return {"answers": {"q": noul_a(0.4)}, "usage": {"input_tokens": 3, "output_tokens": 1}, "model": "m"}

    out = aj.ask_jev_tool(state="context", command="rm -rf build", questions_json=Q, cwd=str(tmp_path), decide=decide, run=run)
    assert run.ran == [] and out["state_summary"]["command_refused"].startswith("blocked")
    assert out["answers"]["q"]["noul"] == 0.4
    with pytest.raises(aj.AskStateError, match="refused"):
        aj.ask_jev_tool(command="rm -rf build", questions_json=Q, cwd=str(tmp_path), decide=decide, run=run)


def test_ask_jev_tool_runs_read_only_command_and_returns_only_answers(tmp_path):
    run = fake_run(stdout="SECRET_OUTPUT_LINE")
    calls = []

    def decide(state, questions):
        calls.append(state)
        if "effect" in questions:
            return {"answers": gate_answers("read_only")}
        return {"answers": {"q": noul_a(0.9)}, "usage": {"input_tokens": 3, "output_tokens": 1}, "model": "m"}

    out = aj.ask_jev_tool(command="pytest -q", questions_json=json.dumps(Q), cwd=str(tmp_path), decide=decide, run=run)
    assert run.ran == ["pytest -q"] and calls[-1]["output"]["stdout"] == "SECRET_OUTPUT_LINE"
    assert "SECRET_OUTPUT_LINE" not in json.dumps(out)


def test_ask_jev_tool_validates_questions_before_running(tmp_path):
    run = fake_run()
    with pytest.raises(ValueError):
        aj.ask_jev_tool(command="ls", questions_json="{bad", cwd=str(tmp_path), decide=None, run=run)
    assert run.ran == []


def test_run_command_real(tmp_path):
    out = aj.run_command("echo hi; echo err >&2; exit 3", str(tmp_path))
    assert out == {"command": "echo hi; echo err >&2; exit 3", "exit_code": 3, "stdout": "hi\n", "stderr": "err\n"}


def test_description_teaches_the_three_types():
    for word in ("noul", "choice", "score", "questions_json", "ask_jev_files", "read-only"):
        assert word in aj.ASK_JEV_DESCRIPTION


# ---------------------------------------------------------------- spend ledger

def test_ledger():
    led = aj.empty_ledger()
    assert aj.summarize(led, 1.0) == "No Jev calls this session."
    led = aj.record(led, {"input_tokens": 1_000_000, "output_tokens": 5}, 2)
    assert led["usd"] == pytest.approx(0.042) and led["questions"] == 2
    led = aj.record(led, {"input_tokens": 10, "output_tokens": 1, "cost": 0.008}, 1)
    assert led["usd"] == pytest.approx(0.05) and led["calls"] == 2
    assert aj.summarize(led, 5.0) == "2 Jev calls, 3 questions, $0.050000, the agent's own spend was 100x that"
