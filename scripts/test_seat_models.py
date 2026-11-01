"""The 1M models a seat may switch to come from contract/seat-models.json (agent_model.seat_models), not a Python
literal: the file holds exactly what the literal held, a missing or bad file offers none (said once), and the
model picker's answer (load_catalog + describe, the GET /api/agents/:id/model body) is byte-identical to before."""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)
import agent_model as AM  # noqa: E402

#: What the module held as a literal before it read the file.
BEFORE = {"claude": [("claude-opus-5-5[1m]", "Opus 5.5 (1M)"), ("claude-opus-5[1m]", "Opus 5 (1M)")]}


def test_the_file_holds_exactly_what_the_literal_held():
    assert AM.seat_models() == BEFORE
    assert AM.SEAT_MODELS == BEFORE
    doc = json.loads(AM.SEAT_MODELS_FILE.read_text())
    assert {rt: [(m["id"], m["label"]) for m in rows] for rt, rows in doc["models"].items()} == AM.seat_models()


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"models": {"claude": [{"id": "x"}]}}', '{"no": 1}'])
def test_a_missing_or_bad_file_offers_no_1m_model_and_says_so_once(tmp_path, capsys, content):
    p = tmp_path / "seat-models.json"
    if content is not None:
        p.write_text(content)
    assert AM.seat_models(p) == {}
    assert AM.seat_models(p) == {}
    err = capsys.readouterr().err
    assert err.count("no 1M models offered to seats") == 1, err


def test_a_changed_file_is_read_again(tmp_path):
    import os
    p = tmp_path / "seat-models.json"
    p.write_text(json.dumps({"models": {"claude": [{"id": "claude-opus-5-5[1m]", "label": "Opus 5.5 (1M)"}]}}))
    assert AM.seat_models(p) == {"claude": [("claude-opus-5-5[1m]", "Opus 5.5 (1M)")]}
    p.write_text(json.dumps({"models": {"claude": []}}))
    os.utime(p, (1_900_000_000, 1_900_000_000))
    assert AM.seat_models(p) == {"claude": []}


SEATS = [("claude", "claude-opus-5-5[1m]"), ("claude", "claude-opus-5[1m]"), ("claude", "claude-sonnet-5"),
         ("claude", None), ("codex", "gpt-6"), ("gemini", "gemini-3-pro"), ("grok", "grok-5"), (None, None)]


@pytest.fixture
def catalog(tmp_path):
    cfg, state = tmp_path / "providers.json", tmp_path / "state"
    state.mkdir()
    cfg.write_text(json.dumps({"providers": [
        {"id": "claude", "model_catalog": {"static": [
            {"id": "claude-opus-5-5", "label": "Opus 5.5", "capabilities": {"context_tokens": 200000}},
            {"id": "claude-sonnet-5", "label": "Sonnet 5"}]}},
        {"id": "codex", "model_catalog": {"static": [{"id": "gpt-6", "label": "GPT-6"}]}},
        {"id": "gemini", "model_catalog": {"static": [{"id": "gemini-3-pro", "label": "Gemini 3 Pro"}]}}]}))
    (state / AM.LIVE_CATALOG).write_text(json.dumps({"providers": {"claude": ["claude-haiku-5"], "codex": ["gpt-6"]}}))
    return cfg, state


@pytest.mark.parametrize("runtime,current", SEATS)
def test_the_model_picker_answer_is_byte_identical_to_before(catalog, monkeypatch, runtime, current):
    cfg, state = catalog

    def body():
        rows = AM.load_catalog((runtime or "").lower(), cfg, state)
        return json.dumps(AM.describe(runtime, current, rows, AM.off_runtimes({})), sort_keys=False)
    now = body()
    monkeypatch.setattr(AM, "seat_models", lambda path=None: BEFORE)        # what the literal gave
    assert body() == now
