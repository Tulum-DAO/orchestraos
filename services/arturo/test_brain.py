# RED-first tests for services/arturo/brain.py — the Brain seam (hackathon track T2).
#
# Three implementations behind one interface:
#   ApiBrain      today's Gemini OpenAI-compat path (BYO key)
#   RuntimeBrain  shells the authed CLI from the runtime catalog (claude -p / agy / codex exec)
#   NullBrain     no key and no authed CLI: never crashes, answers with the fix
# Every call site in arturo-proxy reads the SAME response shape (choices[0].message.content /
# .tool_calls[i].function.name / .arguments; stream chunks .choices[0].delta.content), so the
# tests pin that shape for the runtime + null brains and pin the selection table.
import json
import subprocess
import pathlib

import pytest

from services.arturo import brain as B


# ---- selection table --------------------------------------------------------------------

def _probes(authed):
    return [{"id": rid, "cli": cli, "installed": True, "authed": ok, "auth_reason": None}
            for rid, cli, ok in authed]


def test_select_auto_prefers_api_when_key_present():
    b = B.select_brain("auto", api_key="k", probes=_probes([("claude", "claude", True)]),
                       api_factory=lambda key, model: B.NullBrain(f"stub:{key}:{model}"))
    # the factory ran (api chosen) even though a CLI is also authed
    assert b.reason == "stub:k:gemini-2.5-flash"


def test_select_auto_falls_to_first_authed_runtime_without_key():
    b = B.select_brain("auto", api_key="", probes=_probes([("claude", "claude", False),
                                                           ("codex", "codex", True),
                                                           ("gemini", "agy", True)]))
    assert b.kind == "runtime" and b.runtime == "codex" and b.cli == "codex"


def test_select_auto_null_when_nothing_authed():
    b = B.select_brain("auto", api_key="", probes=_probes([("claude", "claude", False)]))
    assert b.kind == "none"
    assert "log in" in b.reason.lower() or "key" in b.reason.lower()


def test_select_runtime_ignores_key():
    b = B.select_brain("runtime", api_key="k", probes=_probes([("claude", "claude", True)]))
    assert b.kind == "runtime" and b.runtime == "claude"


def test_select_api_without_key_is_null_not_crash():
    b = B.select_brain("api", api_key="", probes=_probes([("claude", "claude", True)]))
    assert b.kind == "none"


def test_select_unknown_mode_treated_as_auto():
    b = B.select_brain("bogus", api_key="", probes=_probes([("claude", "claude", True)]))
    assert b.kind == "runtime"


# ---- null brain never raises, response shape intact -------------------------------------

def test_null_brain_complete_shape():
    b = B.NullBrain("no key, no cli")
    r = b.complete([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])
    assert r.choices[0].message.content
    assert r.choices[0].message.tool_calls is None
    assert r.choices[0].finish_reason == "stop"


def test_null_brain_stream_shape():
    b = B.NullBrain("no key, no cli")
    chunks = list(b.complete([{"role": "user", "content": "hi"}], stream=True))
    assert "".join(c.choices[0].delta.content for c in chunks)


# ---- transcript rendering (messages -> one CLI prompt) -----------------------------------

def test_render_splits_system_and_transcript():
    msgs = [{"role": "system", "content": "SYS A"}, {"role": "system", "content": "SYS B"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "knowledge", "arguments": '{"query":"x"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "found it"},
            {"role": "assistant", "content": "It is x."},
            {"role": "user", "content": "thanks"}]
    system, prompt = B.render_transcript(msgs)
    assert "SYS A" in system and "SYS B" in system
    assert prompt.index("USER: hello") < prompt.index("knowledge") < prompt.index("TOOL RESULT") \
        < prompt.index("ASSISTANT: It is x.") < prompt.index("USER: thanks")
    assert prompt.rstrip().endswith("ASSISTANT:")


def test_tool_protocol_block_lists_every_tool_and_schema():
    tools = [{"type": "function", "function": {"name": "spawn_agent", "description": "Spawn one",
                                               "parameters": {"type": "object", "properties": {"session_name": {"type": "string"}},
                                                              "required": ["session_name"]}}}]
    block = B.tool_protocol_block(tools, tool_choice="auto")
    assert "spawn_agent" in block and "session_name" in block and '"tool_calls"' in block
    forced = B.tool_protocol_block(tools, tool_choice="required")
    assert "must" in forced.lower()


# ---- parsing the CLI's answer: JSON envelope -> tool calls, else text --------------------

def test_parse_plain_text_is_content():
    r = B.parse_cli_reply("Sure, spawning now.")
    assert r.choices[0].message.content == "Sure, spawning now."
    assert r.choices[0].message.tool_calls is None


def test_parse_tool_envelope_bare_json():
    out = json.dumps({"tool_calls": [{"name": "spawn_agent", "arguments": {"session_name": "docs-dev", "machine": "vps"}}]})
    r = B.parse_cli_reply(out)
    tc = r.choices[0].message.tool_calls
    assert len(tc) == 1 and tc[0].function.name == "spawn_agent"
    assert json.loads(tc[0].function.arguments) == {"session_name": "docs-dev", "machine": "vps"}
    assert tc[0].id and tc[0].type == "function"
    assert r.choices[0].finish_reason == "tool_calls"


def test_parse_tool_envelope_in_code_fence_with_prose():
    out = 'On it.\n```json\n{"tool_calls":[{"name":"list_agents","arguments":{}}]}\n```'
    r = B.parse_cli_reply(out)
    assert r.choices[0].message.tool_calls[0].function.name == "list_agents"


def test_parse_json_without_tool_calls_stays_text():
    r = B.parse_cli_reply('{"answer": 42}')
    assert r.choices[0].message.tool_calls is None
    assert "42" in r.choices[0].message.content


def test_parse_empty_is_empty_content_not_crash():
    r = B.parse_cli_reply("")
    assert r.choices[0].message.content == "" and r.choices[0].message.tool_calls is None


# ---- runtime command table -------------------------------------------------------------

def test_claude_command_puts_system_in_flag_and_prompt_on_stdin():
    spec = B.runtime_command("claude", "claude", "SYS", "PROMPT", model="")
    assert spec.argv[:2] == ["claude", "-p"]
    assert "--system-prompt" in spec.argv and spec.argv[spec.argv.index("--system-prompt") + 1] == "SYS"
    assert "--tools" in spec.argv          # the brain answers in text; the CLI must not run tools
    assert spec.stdin == "PROMPT"
    assert "CLAUDECODE" in spec.env_unset  # nested-session guard


def test_claude_command_model_flag_only_when_set():
    assert "--model" not in B.runtime_command("claude", "claude", "S", "P", model="").argv
    argv = B.runtime_command("claude", "claude", "S", "P", model="claude-haiku-4-5-20251001").argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5-20251001"


def test_gemini_command_attaches_prompt_to_print_flag():
    spec = B.runtime_command("gemini", "agy", "SYS", "PROMPT", model="")
    assert spec.argv[0] == "agy"
    joined = [a for a in spec.argv if a.startswith("--print=")]
    assert joined and "SYS" in joined[0] and "PROMPT" in joined[0]
    assert spec.stdin is None


def test_codex_command_reads_stdin_and_writes_last_message_file(tmp_path):
    spec = B.runtime_command("codex", "codex", "SYS", "PROMPT", model="", scratch=tmp_path)
    assert spec.argv[:2] == ["codex", "exec"]
    assert "--skip-git-repo-check" in spec.argv and "-o" in spec.argv
    assert spec.output_file and str(spec.output_file).startswith(str(tmp_path))
    assert spec.stdin and "SYS" in spec.stdin and "PROMPT" in spec.stdin


def test_unknown_runtime_raises():
    with pytest.raises(B.UnsupportedRuntime):
        B.runtime_command("nope", "nope", "S", "P")


# ---- RuntimeBrain end to end with a fake runner -----------------------------------------

def test_runtime_brain_complete_uses_runner_and_parses(monkeypatch):
    seen = {}

    def fake_run(spec, timeout):
        seen["spec"] = spec
        return '{"tool_calls":[{"name":"knowledge","arguments":{"query":"focus"}}]}'

    b = B.RuntimeBrain("claude", "claude", model="", runner=fake_run)
    r = b.complete([{"role": "system", "content": "S"}, {"role": "user", "content": "what should I focus on"}],
                   tools=[{"type": "function", "function": {"name": "knowledge", "description": "d",
                                                            "parameters": {"type": "object", "properties": {}}}}])
    assert r.choices[0].message.tool_calls[0].function.name == "knowledge"
    sys_flag = seen["spec"].argv[seen["spec"].argv.index("--system-prompt") + 1]
    assert "S" in sys_flag and "knowledge" in sys_flag      # tool schema rides in the system prompt
    assert b.kind == "runtime" and b.model


def test_runtime_brain_stream_yields_delta_chunks():
    b = B.RuntimeBrain("claude", "claude", model="", runner=lambda spec, timeout: "hello world")
    chunks = list(b.complete([{"role": "user", "content": "hi"}], stream=True))
    assert "".join(c.choices[0].delta.content for c in chunks) == "hello world"


def test_runtime_brain_runner_failure_degrades_to_text_not_raise():
    def boom(spec, timeout):
        raise RuntimeError("cli exploded")
    b = B.RuntimeBrain("claude", "claude", model="", runner=boom)
    r = b.complete([{"role": "user", "content": "hi"}])
    content = r.choices[0].message.content
    # CHANGED 2026-09-19: this used to assert `"cli exploded" in content` — it encoded the
    # LEAK as the contract. The exception detail belongs in the log; a CalledProcessError
    # stringifies to the whole argv incl. Arturo's system prompt. The degrade-not-raise
    # intent is what this test is really for, so that is what it now checks.
    assert content and content.strip(), "must still answer with something"
    assert "cli exploded" not in content, "raw exception text must not reach the operator"
    assert r.choices[0].message.tool_calls is None


# ---- health/describe --------------------------------------------------------------------

def test_describe_carries_kind_runtime_model():
    b = B.RuntimeBrain("codex", "codex", model="gpt-5", runner=lambda s, t: "")
    d = b.describe()
    assert d["kind"] == "runtime" and d["runtime"] == "codex" and d["model"] == "gpt-5"
    n = B.NullBrain("why").describe()
    assert n["kind"] == "none" and n["reason"] == "why"


def test_reselect_if_none_swaps_in_a_runtime_once_a_cli_is_authed(monkeypatch):
    """G14: brain chosen as none at boot must become live after the user logs in, without a
    restart; a working brain is never replaced; re-probes are rate-limited."""
    reselect_if_none, select_brain = B.reselect_if_none, B.select_brain
    none = select_brain("auto", "", probes=[], api_model="m")
    assert none.kind == "none"
    calls = []
    def probe(_root, _enabled):
        calls.append(1)
        return [{"id": "claude", "cli": "claude", "installed": True, "authed": True}]
    state = {}
    nb = reselect_if_none(none, "auto", "", "/x", None, "m", probe_fn=probe, _state=state)
    assert nb.kind == "runtime" and calls == [1]
    # a working brain is returned untouched and never re-probed
    assert reselect_if_none(nb, "auto", "", "/x", None, "m", probe_fn=probe, _state=state) is nb and calls == [1]
    # still none + within the interval -> no probe
    state2 = {"last": 10**12}
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 10**12 + 1)
    assert reselect_if_none(none, "auto", "", "/x", None, "m", probe_fn=probe, _state=state2) is none and calls == [1]


# --- native tool-call markup must never reach the operator as TEXT --------------------------
# Found by effect on the release sha 4ec0229 (2026-09-19, container art-rc) running the
# run-of-show's own beat-3.1 prompt: the claude CLI sometimes answers in its NATIVE tool-call
# syntax instead of the JSON envelope the brain prompt asks for. That text carries no
# "tool_calls" string, so parse_cli_reply fell through to make_response(text, ...) and the raw
# markup became the assistant's reply — shown on the Arturo home AND persisted as the thread's
# snippet in GET /api/arturo/threads.

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
# Read from a FILE, never inlined: a test file that CONTAINS this markup is itself a tripwire
# for the court-guard Stop hook, which cannot distinguish quoting the defect from emitting it.
# Provenance for both fixtures is in fixtures/README.md.
_NATIVE = (FIXTURES / "native_tool_markup_reply.txt").read_text()


def test_native_invoke_markup_becomes_a_real_tool_call():
    r = B.parse_cli_reply(_NATIVE)
    msg = r.choices[0].message
    assert r.choices[0].finish_reason == "tool_calls"
    assert msg.content is None, "raw markup must never be handed back as text"
    assert [c.function.name for c in msg.tool_calls] == ["get_agent_output"]
    assert json.loads(msg.tool_calls[0].function.arguments) == {"session_name": "hello", "lines": 30}


def test_native_markup_with_prose_around_it_still_parses():
    r = B.parse_cli_reply("Let me check on that.\n" + _NATIVE + "\nOne moment.")
    msg = r.choices[0].message
    assert msg.content is None
    assert [c.function.name for c in msg.tool_calls] == ["get_agent_output"]


def test_unparseable_markup_is_suppressed_not_printed():
    """A half-emitted invoke names no usable tool. The operator must not see the tag soup."""
    # Assembled from parts, not written whole: a source file that CONTAINS the literal tag is
    # itself a court-guard tripwire for anyone who reads or quotes it.
    half = "<" + 'invoke name=""' + ">\n<" + 'parameter name="x"' + ">1</parameter>"
    r = B.parse_cli_reply(half)
    msg = r.choices[0].message
    assert msg.tool_calls is None
    assert "<invoke" not in (msg.content or ""), "tag soup leaked to the operator"
    assert (msg.content or "").strip(), "suppressing the markup must still say something"


def test_prose_that_merely_mentions_invoke_is_untouched():
    """Guard the guard: talking ABOUT the syntax is not emitting it."""
    txt = "You can use the invoke syntax, or a parameter block, to call a tool."
    r = B.parse_cli_reply(txt)
    assert r.choices[0].message.content == txt
    assert r.choices[0].message.tool_calls is None


# --- a failing brain must not read its own argv out to the operator ------------------------
# Byte-captured in container art-cap (fixtures/runtime_error_reply.txt): a CalledProcessError
# stringifies to the entire command line, which carries --system-prompt followed by Arturo's
# whole system prompt. It reached the reply AND the thread snippet.

def test_runtime_failure_is_a_sentence_not_the_argv():
    captured = (FIXTURES / "runtime_error_reply.txt").read_text()
    assert "--system-prompt" in captured, "fixture no longer shows the leak it was captured for"

    class Boom(B.RuntimeBrain):
        def _text(self, *a, **k):
            raise subprocess.CalledProcessError(
                1, ["claude", "-p", "--system-prompt", "You are ARTURO — the operator's ..."])

    r = Boom(runtime="claude", cli="claude").complete([{"role": "user", "content": "hi"}])
    out = r.choices[0].message.content or ""
    assert "--system-prompt" not in out, "the argv leaked to the operator"
    assert "You are ARTURO" not in out, "the system prompt leaked to the operator"
    assert out.strip(), "a failing brain must still say something"
