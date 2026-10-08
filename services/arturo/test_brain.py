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
from pathlib import Path

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
    # CHANGED 2026-09-29 (DEC-1790669162399904 §1.2): the model rides ATTACHED (--model=<id>),
    # never as a separate argv element, so a value can never be parsed as a flag of its own.
    argv = B.runtime_command("claude", "claude", "S", "P", model="").argv
    assert not any(a == "--model" or a.startswith("--model=") for a in argv)
    argv = B.runtime_command("claude", "claude", "S", "P", model="claude-haiku-4-5-20251001").argv
    assert "--model=claude-haiku-4-5-20251001" in argv and "--model" not in argv


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


# ---- codex parity: --output-schema forces the JSON envelope shape (2026-09-19) ----------
# codex exec never honoured the prose JSON-envelope protocol (0/3, fabricated actions instead
# of admitting it made no tool call). `codex exec --output-schema <FILE>` constrains its final
# response to a schema; paired with the SAME tool_protocol_block prompt this reliably produces
# the envelope. Claude and gemini are untouched — this is additive, codex-only.

def test_codex_command_adds_output_schema_when_tools_present(tmp_path):
    spec = B.runtime_command("codex", "codex", "SYS", "PROMPT", model="", scratch=tmp_path,
                             use_schema=True)
    assert "--output-schema" in spec.argv
    schema_path = Path(spec.argv[spec.argv.index("--output-schema") + 1])
    assert schema_path.is_file()
    schema = json.loads(schema_path.read_text())
    # strict mode: every object needs additionalProperties:false, and arguments ride as a
    # JSON-encoded STRING (arguments_json) because a free-form object is illegal in strict mode.
    assert schema["additionalProperties"] is False
    call_schema = schema["properties"]["tool_calls"]["items"]
    assert call_schema["additionalProperties"] is False
    assert set(call_schema["properties"]) == {"name", "arguments_json"}


def test_codex_command_omits_output_schema_for_plain_turn(tmp_path):
    spec = B.runtime_command("codex", "codex", "SYS", "PROMPT", model="", scratch=tmp_path,
                             use_schema=False)
    assert "--output-schema" not in spec.argv


def test_claude_and_gemini_commands_unaffected_by_use_schema_flag():
    # additive / codex-only: the flag does not even exist as an argv concept elsewhere
    c = B.runtime_command("claude", "claude", "S", "P", model="", use_schema=True)
    assert "--output-schema" not in c.argv
    g = B.runtime_command("gemini", "agy", "S", "P", model="", use_schema=True)
    assert "--output-schema" not in g.argv


def test_parse_codex_envelope_decodes_arguments_json_string():
    out = json.dumps({"tool_calls": [{"name": "spawn_agent",
                                      "arguments_json": json.dumps({"session": "probe-seat", "task": "Say hi."})}],
                      "text": ""})
    r = B.parse_cli_reply(out)
    tc = r.choices[0].message.tool_calls
    assert len(tc) == 1 and tc[0].function.name == "spawn_agent"
    assert json.loads(tc[0].function.arguments) == {"session": "probe-seat", "task": "Say hi."}
    assert r.choices[0].finish_reason == "tool_calls"


def test_parse_codex_envelope_empty_tool_calls_falls_back_to_text_field():
    # a plain-answer turn under the schema: tool_calls:[] with the real answer in "text" —
    # the raw JSON string must never leak into message.content as if it were prose.
    out = json.dumps({"tool_calls": [], "text": "4"})
    r = B.parse_cli_reply(out)
    assert r.choices[0].message.content == "4"
    assert r.choices[0].message.tool_calls is None
    assert r.choices[0].finish_reason == "stop"


def test_runtime_brain_codex_passes_output_schema_when_tools_present(monkeypatch):
    seen = {}

    def fake_run(spec, timeout):
        seen["spec"] = spec
        return json.dumps({"tool_calls": [{"name": "spawn_agent",
                                           "arguments_json": json.dumps({"session": "probe-seat"})}],
                           "text": ""})

    b = B.RuntimeBrain("codex", "codex", model="", runner=fake_run)
    r = b.complete([{"role": "system", "content": "S"}, {"role": "user", "content": "commission probe-seat"}],
                   tools=[{"type": "function", "function": {"name": "spawn_agent", "description": "d",
                                                            "parameters": {"type": "object", "properties": {}}}}],
                   tool_choice="required")
    assert "--output-schema" in seen["spec"].argv
    assert r.choices[0].message.tool_calls[0].function.name == "spawn_agent"


def test_codex_command_uses_minitems_schema_when_tool_choice_required(tmp_path):
    # measured 2026-09-19: prompt-only "you must call a tool" wording still lets codex answer
    # in prose 2/3 times. A schema with tool_calls.minItems=1 is a STRUCTURAL guarantee the API
    # enforces, not a request the model can ignore — 5/5 clean with it, vs 1/3 without.
    spec = B.runtime_command("codex", "codex", "SYS", "PROMPT", model="", scratch=tmp_path,
                             use_schema=True, require_tool_call=True)
    schema_path = Path(spec.argv[spec.argv.index("--output-schema") + 1])
    schema = json.loads(schema_path.read_text())
    assert schema["properties"]["tool_calls"]["minItems"] == 1


def test_codex_command_general_schema_allows_empty_tool_calls_when_not_required(tmp_path):
    spec = B.runtime_command("codex", "codex", "SYS", "PROMPT", model="", scratch=tmp_path,
                             use_schema=True, require_tool_call=False)
    schema_path = Path(spec.argv[spec.argv.index("--output-schema") + 1])
    schema = json.loads(schema_path.read_text())
    assert "minItems" not in schema["properties"]["tool_calls"]


def test_runtime_brain_codex_selects_required_schema_for_tool_choice_required(monkeypatch):
    seen = {}

    def fake_run(spec, timeout):
        seen["spec"] = spec
        return json.dumps({"tool_calls": [{"name": "spawn_agent", "arguments_json": "{}"}], "text": ""})

    b = B.RuntimeBrain("codex", "codex", model="", runner=fake_run)
    b.complete([{"role": "user", "content": "commission it"}],
              tools=[{"type": "function", "function": {"name": "spawn_agent", "parameters": {"type": "object", "properties": {}}}}],
              tool_choice="required")
    schema_path = Path(seen["spec"].argv[seen["spec"].argv.index("--output-schema") + 1])
    assert json.loads(schema_path.read_text())["properties"]["tool_calls"]["minItems"] == 1


def test_runtime_brain_codex_plain_turn_stays_prose_not_empty_envelope(monkeypatch):
    """The regression this guards: a schema that FORCES the envelope shape on a turn with no
    tools would turn "hello" into raw `{"tool_calls":[],"text":"..."}` prose. No tools ->
    no schema flag -> the CLI's own text is what the user sees."""
    seen = {}

    def fake_run(spec, timeout):
        seen["spec"] = spec
        return "Hi there!"

    b = B.RuntimeBrain("codex", "codex", model="", runner=fake_run)
    r = b.complete([{"role": "user", "content": "hello"}])
    assert "--output-schema" not in seen["spec"].argv
    assert r.choices[0].message.content == "Hi there!"
    assert r.choices[0].message.tool_calls is None


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


# ---- codex: its own shell off, and told the listed tools are real (2026-09-30) -------------
# Measured on staging with Arturo's exact 30k-char prompt ("check which agents are running"):
# codex ran its OWN sandboxed shell (tmux ls — blocked by bwrap in the container) on every run,
# and in 2 of 3 it emitted a correct Arturo tool call first, kept going, and ended on "I can't
# access the live agent registry" — the last message is the one we read, so the call was lost.
# With its shell off but no framing: 0/3 ("no agent-status tool is connected here"). With the
# note: 5/5 tool calls, 6-11s instead of 15-20s; chat turns stayed text (3/3); follow-ups with a
# TOOL RESULT answered from it (3/3).

def test_codex_runs_with_its_own_shell_switched_off(tmp_path):
    # `-c features.X=false`, not `--disable X`: --disable with a name the installed codex does
    # not know is a hard error ("Unknown feature flag") and would fail every turn; -c ignores it.
    for use_schema in (True, False):
        argv = B.runtime_command("codex", "codex", "S", "P", scratch=tmp_path, use_schema=use_schema).argv
        pairs = {argv[i + 1] for i, a in enumerate(argv) if a == "-c"}
        assert {"features.shell_tool=false", "features.unified_exec=false"} <= pairs
        assert "--disable" not in argv


def test_codex_is_told_the_listed_tools_are_real_when_tools_ride_the_turn(tmp_path):
    system = "SYS\n\n## Tools\nYou can call tools.\n\nAvailable tools (name — description):\n- list_agents — x"
    spec = B.runtime_command("codex", "codex", system, "USER: hi\nASSISTANT:", scratch=tmp_path, use_schema=True)
    assert spec.stdin.count(B.CODEX_TOOLS_NOTE) == 2
    assert spec.stdin.index(B.CODEX_TOOLS_NOTE) < spec.stdin.index("Available tools (name")
    assert spec.stdin.rstrip().endswith(B.CODEX_TOOLS_NOTE)


def test_a_codex_turn_without_tools_gets_no_note(tmp_path):
    spec = B.runtime_command("codex", "codex", "SYS", "USER: hi\nASSISTANT:", scratch=tmp_path, use_schema=False)
    assert B.CODEX_TOOLS_NOTE not in spec.stdin


def test_claude_and_gemini_get_no_codex_flags_or_note():
    c = B.runtime_command("claude", "claude", "S", "P", use_schema=True)
    g = B.runtime_command("gemini", "agy", "S", "P", use_schema=True)
    for spec in (c, g):
        assert "features.shell_tool=false" not in spec.argv
        assert B.CODEX_TOOLS_NOTE not in (spec.stdin or "") + " ".join(spec.argv)


# ---- a degenerate codex tail never reaches the operator (2026-09-30) ---------------------
# Staging, operator's thread web_y3ryvgvu: "How are you doing?" came back as a valid schema
# envelope whose text was the answer, then a stray "}" (the model closing JSON it was already
# inside), then glitch tokens and its own reasoning: "…How's your day starting?}\U0005f7c2 恒一
# Erotiske?Winvalid? 天天中彩票买.}无码不卡高清免费 … Actually schema should valid JSON only…".
# Not reproducible (0/12 replays, with and without the tools note) — a rare sampling glitch — but
# it was shown AND saved to the thread, and the next turn answered the wrong question.

GLITCH = ("Doing well—quiet, focused, and ready to help. How’s your day starting?}\U0005f7c2 恒一 "
          "Erotiske?Winvalid?  天天中彩票买.}无码不卡高清免费 香港六合彩?ганахь? 大发快三计划  code required? "
          "Actually schema should valid JSON only. Need no weird. Let's craft.%timeout?")


def _codex_text(text):
    env = json.dumps({"tool_calls": [], "text": text}, ensure_ascii=False)
    return B.parse_cli_reply(env).choices[0].message.content


def test_a_glitched_codex_tail_is_cut_at_the_stray_brace():
    assert _codex_text(GLITCH) == "Doing well—quiet, focused, and ready to help. How’s your day starting?"


def test_braces_in_an_ordinary_reply_are_kept():
    t = "Put `{name}` in the template and it fills in per agent."
    assert _codex_text(t) == t


def test_a_reply_in_another_language_is_kept():
    for t in ("元気です。今日は何をしましょうか？", "Всё хорошо, чем займёмся?", "¿Qué tal? Todo bien por aquí."):
        assert _codex_text(t) == t


def test_unassigned_codepoints_never_reach_the_operator():
    assert _codex_text("All good\U0005f7c2 here.") == "All good here."


# ---- a broken JSON envelope never reaches the operator ----------------------------------
# Operator report, 2026-10-08 (fresh install, CLI brain): Arturo printed its own tool-call
# envelope as chat text, and the two calls in it never ran. The envelope was one "}" short:
# the second call's object was never closed before "]}". json.loads refused it, so
# _find_envelope found nothing and the whole string fell through as the assistant's prose.

_SHORT_ONE_BRACE = ('{"tool_calls":[{"name":"list_agents","arguments":{}},'
                    '{"name":"remember_note","arguments":{"note":"the gm seat shows a red dot",'
                    '"category":"project"}]}')


def test_envelope_missing_an_object_close_still_runs_its_calls():
    with pytest.raises(ValueError):
        json.loads(_SHORT_ONE_BRACE)            # the shape really is invalid JSON
    r = B.parse_cli_reply(_SHORT_ONE_BRACE)
    msg = r.choices[0].message
    assert msg.content is None, "the envelope must never be handed back as text"
    assert [c.function.name for c in msg.tool_calls] == ["list_agents", "remember_note"]
    assert json.loads(msg.tool_calls[1].function.arguments) == {
        "note": "the gm seat shows a red dot", "category": "project"}


def test_envelope_cut_off_mid_string_is_suppressed_not_printed():
    """Truncated inside an argument: the call's intent is not recoverable, so nothing runs —
    and the operator still never sees the JSON."""
    cut = '{"tool_calls":[{"name":"remember_note","arguments":{"note":"half a no'
    msg = B.parse_cli_reply(cut).choices[0].message
    assert msg.tool_calls is None
    assert "tool_calls" not in (msg.content or "")
    assert (msg.content or "").strip(), "suppressing the envelope must still say something"


def test_prose_before_a_broken_envelope_is_kept():
    msg = B.parse_cli_reply('Acknowledged.\n{"tool_calls":[{"name":"list_agents","arguments":{"x":"cu').choices[0].message
    assert msg.tool_calls is None
    assert msg.content == "Acknowledged."


def test_a_wrong_closer_is_not_guessed_into_a_call():
    """Only a missing object close is repaired. A '}' where a list should close could mean
    anything, so it is not reshaped into a call that might carry the wrong arguments."""
    odd = '{"tool_calls":[{"name":"list_agents","arguments":{}}}'
    msg = B.parse_cli_reply(odd).choices[0].message
    assert msg.tool_calls is None
    assert "tool_calls" not in (msg.content or "")


def test_braces_inside_strings_do_not_confuse_the_repair():
    s = ('{"tool_calls":[{"name":"remember_note","arguments":{"note":"a ] and a } and a \\" quote",'
         '"category":"project"}]}')
    msg = B.parse_cli_reply(s).choices[0].message
    assert [c.function.name for c in msg.tool_calls] == ["remember_note"]
    assert json.loads(msg.tool_calls[0].function.arguments)["note"] == 'a ] and a } and a " quote'


def test_runtime_brain_toolless_turn_never_prints_an_envelope():
    """A turn offered no tools can run none, but a model can still answer in the protocol it
    saw on an earlier turn. Its envelope must not become the reply."""
    b = B.RuntimeBrain("claude", "claude", model="",
                       runner=lambda spec, timeout: 'On it.\n{"tool_calls":[{"name":"list_agents","arguments":{}}]}')
    msg = b.complete([{"role": "user", "content": "hi"}]).choices[0].message
    assert msg.tool_calls is None
    assert msg.content == "On it."


def test_runtime_brain_stream_never_streams_an_envelope():
    b = B.RuntimeBrain("claude", "claude", model="",
                       runner=lambda spec, timeout: 'Acknowledged.\n{"tool_calls":[{"name":"list_agents","arguments":{}}]}')
    chunks = list(b.complete([{"role": "user", "content": "hi"}], stream=True))
    assert "".join(c.choices[0].delta.content for c in chunks) == "Acknowledged."


@pytest.mark.parametrize("cut", [
    '{"tool_calls":[{"name":"get_agent_output","arguments":{"session":"gm","lines":20',
    '{"tool_calls":[{"name":"list_agents","arguments":{}},{"name":"kill_agent"',
    '{"tool_calls":[{"name":"send_telegram","arguments":{"text":"Deploy finished"',
    '{"tool_calls":[{"name":"list_agents","arguments":{}}',
])
def test_a_cut_off_envelope_never_runs_anything(cut):
    """A reply that stops early (a CLI at its output limit) must not be closed and run: the
    last call's arguments may be unfinished. Review finding on the first version of the repair."""
    msg = B.parse_cli_reply(cut).choices[0].message
    assert msg.tool_calls is None
    assert "tool_calls" not in (msg.content or "")


def test_a_fenced_broken_envelope_leaves_no_fence_behind():
    msg = B.parse_cli_reply('Sure.\n```json\n{"tool_calls":[{"name":"list_agents","arguments":{"x":"cu').choices[0].message
    assert msg.content == "Sure."
