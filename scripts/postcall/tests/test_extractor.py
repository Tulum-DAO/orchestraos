"""The real cmt-v2 extractor (design §2): argv shape, failure vs. empty, output parsing, shape
validation. Every test injects a fake `runner`; the real `claude` CLI is never invoked."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from postcall import extractor, sources


def _call(transcript="**SPEAKER_00** _[00:01]_\nHi, I'm Shaw. I will email you the deck tomorrow.\n"):
    return sources.Call(call_id="pocket:t1", source="pocket", title="t", created_at="2026-11-25T10:00:00Z",
                        duration_s=600, category="call", diarized=True, ingested=True,
                        transcript=transcript, content_hash="h")


def _item(**over):
    item = {"owner": "shaw", "kind": "commitment", "counterpart": "Ana", "project": "",
            "action_type": "email", "operation": "send", "deliverable": "deck",
            "commitment_text": "email Ana the deck", "timeframe": "tomorrow", "explicit_date": "",
            "quote": "I will email you the deck tomorrow.", "speaker": "SPEAKER_00",
            "agent_takeable": True, "agent_work": "draft the email", "recurring": False}
    item.update(over)
    return item


def _payload(items):
    return {"shaw_label": "SPEAKER_00", "shaw_label_evidence": "Hi, I'm Shaw.", "items": items}


class FakeRunner:
    """Records each call and returns a canned CompletedProcess-like result (or raises)."""

    def __init__(self, stdout="", returncode=0, stderr="", raises=None):
        self.stdout, self.returncode, self.stderr, self.raises = stdout, returncode, stderr, raises
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


def _ok_runner(items=None):
    return FakeRunner(stdout=json.dumps(_payload([_item()] if items is None else items)))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("POSTCALL_CLAUDE_BIN", raising=False)
    monkeypatch.delenv("POSTCALL_EXTRACT_MODEL", raising=False)


# --- argv / invocation -------------------------------------------------------------------------

def test_argv_is_niced_pinned_headless_and_carries_prompt_and_transcript():
    runner = _ok_runner()
    call = _call()
    extractor.run(call, runner=runner)
    argv, _ = runner.calls[0]
    assert argv[:6] == ["nice", "-n", "19", "taskset", "-c", "6,7"]
    assert argv[6] == "claude" and argv[7] == "-p"
    prompt = argv[-1]
    assert call.transcript in prompt
    assert extractor.PROMPT_VERSION == "cmt-v2"
    assert extractor.PROMPT_VERSION in prompt
    assert "owner" in prompt and "participant" in prompt and "verbatim" in prompt.lower()


def test_claude_bin_from_param_or_env(monkeypatch):
    runner = _ok_runner()
    extractor.run(_call(), runner=runner, claude_bin="/opt/claude")
    assert runner.calls[0][0][6] == "/opt/claude"
    monkeypatch.setenv("POSTCALL_CLAUDE_BIN", "/env/claude")
    extractor.run(_call(), runner=runner)
    assert runner.calls[1][0][6] == "/env/claude"


def test_model_flag_absent_by_default():
    runner = _ok_runner()
    extractor.run(_call(), runner=runner)
    assert "--model" not in runner.calls[0][0]
    assert len(runner.calls[0][0]) == 9  # prefix(6) + bin + -p + prompt


def test_model_flag_from_param_or_env(monkeypatch):
    runner = _ok_runner()
    extractor.run(_call(), runner=runner, model="sonnet")
    argv = runner.calls[0][0]
    assert argv[8:10] == ["--model", "sonnet"]
    monkeypatch.setenv("POSTCALL_EXTRACT_MODEL", "opus")
    extractor.run(_call(), runner=runner)
    assert runner.calls[1][0][8:10] == ["--model", "opus"]


def test_stdin_devnull_capture_and_timeout_passed():
    runner = _ok_runner()
    extractor.run(_call(), runner=runner, timeout=42)
    _, kw = runner.calls[0]
    assert kw["stdin"] is subprocess.DEVNULL
    assert kw["timeout"] == 42
    assert kw["capture_output"] is True and kw["text"] is True


# --- parsing -------------------------------------------------------------------------------------

def test_valid_json_is_ok_with_items():
    ex = extractor.run(_call(), runner=_ok_runner())
    assert ex.ok is True and ex.error is None
    assert ex.raw["shaw_label"] == "SPEAKER_00"
    assert ex.raw["items"] == [_item()]


def test_code_fenced_json_is_parsed():
    body = json.dumps(_payload([_item()]))
    ex = extractor.run(_call(), runner=FakeRunner(stdout=f"```json\n{body}\n```\n"))
    assert ex.ok is True and len(ex.raw["items"]) == 1


def test_fenced_block_wins_over_stray_braces_in_prose():
    body = json.dumps(_payload([_item()]))
    out = f"I skipped {{the pleasantries}} as asked.\n```json\n{body}\n```\n"
    ex = extractor.run(_call(), runner=FakeRunner(stdout=out))
    assert ex.ok is True and len(ex.raw["items"]) == 1


def test_leading_and_trailing_prose_around_json_is_parsed():
    body = json.dumps(_payload([_item(quote="a {brace} inside a string")]))
    ex = extractor.run(_call(), runner=FakeRunner(stdout=f"Here you go:\n{body}\nHope that helps {{}}"))
    assert ex.ok is True
    assert ex.raw["items"][0]["quote"] == "a {brace} inside a string"


def test_empty_items_is_ok_not_failure():
    ex = extractor.run(_call(), runner=_ok_runner(items=[]))
    assert ex.ok is True and ex.error is None
    assert ex.raw["items"] == []


def test_bad_json_is_failure():
    ex = extractor.run(_call(), runner=FakeRunner(stdout='{"items": [ oops'))
    assert ex.ok is False and ex.raw is None and ex.error == "bad json"


def test_no_json_object_at_all_is_failure():
    ex = extractor.run(_call(), runner=FakeRunner(stdout="I could not find anything."))
    assert ex.ok is False and ex.error == "bad json"


def test_wrong_shape_is_failure():
    ex = extractor.run(_call(), runner=FakeRunner(stdout='{"commitments": []}'))
    assert ex.ok is False and ex.raw is None


# --- process failures ------------------------------------------------------------------------------

def test_nonzero_returncode_is_failure():
    ex = extractor.run(_call(), runner=FakeRunner(stdout=json.dumps(_payload([])), returncode=3,
                                                  stderr="boom"))
    assert ex.ok is False and ex.raw is None
    assert "3" in ex.error


def test_timeout_is_failure():
    runner = FakeRunner(raises=subprocess.TimeoutExpired(cmd="claude", timeout=180))
    ex = extractor.run(_call(), runner=runner)
    assert ex.ok is False and ex.raw is None and ex.error == "timeout"


def test_oserror_is_failure():
    ex = extractor.run(_call(), runner=FakeRunner(raises=FileNotFoundError("no claude")))
    assert ex.ok is False and ex.raw is None and ex.error.startswith("oserror: ")


# --- item validation -------------------------------------------------------------------------------

def test_items_with_bad_owner_or_missing_quote_are_dropped():
    good_shaw, good_them = _item(), _item(owner="participant", speaker="SPEAKER_01")
    items = [good_shaw, _item(owner="third_party"), _item(owner=""), {k: v for k, v in _item().items()
                                                                       if k != "quote"},
             _item(quote=""), "not a dict", good_them]
    ex = extractor.run(_call(), runner=_ok_runner(items=items))
    assert ex.ok is True
    assert ex.raw["items"] == [good_shaw, good_them]  # survivors kept unchanged, in order


# --- truncation / existing -------------------------------------------------------------------------

def test_long_transcript_is_truncated_head_kept_and_flagged():
    cap = extractor.MAX_TRANSCRIPT_CHARS
    assert cap == 60_000
    transcript = "H" * cap + "TAIL_MARKER"
    runner = _ok_runner()
    ex = extractor.run(_call(transcript), runner=runner)
    prompt = runner.calls[0][0][-1]
    assert "H" * cap in prompt and "TAIL_MARKER" not in prompt
    assert ex.ok is True and ex.raw["_truncated"] is True


def test_short_transcript_not_flagged():
    ex = extractor.run(_call(), runner=_ok_runner())
    assert "_truncated" not in ex.raw


def test_existing_occurrences_appear_in_prompt():
    existing = [{"occ": "occ_abc123", "quote": "I'll send the contract Friday", "owner": "shaw",
                 "action": "send contract"}]
    runner = _ok_runner()
    extractor.run(_call(), runner=runner, existing=existing)
    prompt = runner.calls[0][0][-1]
    assert "occ_abc123" in prompt and "I'll send the contract Friday" in prompt
    assert "same_as_occ" in prompt


def test_no_existing_section_without_existing():
    runner = _ok_runner()
    extractor.run(_call(), runner=runner)
    assert "occ_" not in runner.calls[0][0][-1]


def test_default_extractor_delegates_to_run(monkeypatch):
    seen = []
    monkeypatch.setattr(extractor, "run", lambda call, **kw: seen.append(call) or "sentinel")
    call = _call()
    assert extractor.default_extractor(call) == "sentinel" and seen == [call]
