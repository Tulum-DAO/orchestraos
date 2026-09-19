"""The Brain seam — Arturo's conversational turn behind one interface (hackathon track T2).

    ApiBrain      today's Gemini OpenAI-compat path (BYO GEMINI_API_KEY)
    RuntimeBrain  shells the CLI you are already logged in to (claude -p / agy / codex exec)
                  with the SAME system prompt + tool schema; tools ride in the prompt as a JSON
                  envelope protocol because the CLIs have no function-calling API.
    NullBrain     no key AND no authed CLI: the service still boots, /health says so, every turn
                  answers with the fix instead of a traceback.

Selection: config `[arturo] brain = "auto" | "api" | "runtime"` (env ORCHESTRA_ARTURO_BRAIN).
auto = api if a key is present, else the first authed CLI from the runtime catalog
(config/providers.json filtered by [runtimes] enabled), else none.

Every implementation returns the OpenAI response SHAPE arturo-proxy already reads:
    resp.choices[0].message.content / .tool_calls[i].id/.type/.function.name/.function.arguments
    resp.choices[0].finish_reason
    stream=True -> iterator of chunks with .choices[0].delta.content
so the five call sites in arturo-proxy.py are one-line swaps (`brain.complete(...)`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable, Optional

log = logging.getLogger("arturo.brain")

BRAIN_MODES = ("auto", "api", "runtime")
DEFAULT_RUNTIME_TIMEOUT_S = 120.0
STREAM_CHUNK_CHARS = 48

NULL_REASON = ("No brain configured: set GEMINI_API_KEY (api brain) or log in to one agent CLI "
               "(claude / codex / agy) and restart Arturo. `orchestra doctor` shows which CLIs are "
               "installed and authed; see docs/ARTURO.md.")


# --- response shape ------------------------------------------------------------------------

def _tool_call(name: str, arguments, cid: Optional[str] = None):
    args = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
    return SimpleNamespace(id=cid or f"call_{uuid.uuid4().hex[:12]}", type="function",
                           function=SimpleNamespace(name=name, arguments=args))


def make_response(content: Optional[str], tool_calls=None, finish_reason: Optional[str] = None):
    tcs = list(tool_calls) if tool_calls else None
    msg = SimpleNamespace(role="assistant", content=content, tool_calls=tcs)
    fr = finish_reason or ("tool_calls" if tcs else "stop")
    return SimpleNamespace(choices=[SimpleNamespace(index=0, message=msg, finish_reason=fr)])


def make_chunk(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=SimpleNamespace(content=text),
                                                    finish_reason=None)])


def _chunked(text: str, n: int = STREAM_CHUNK_CHARS) -> Iterable[str]:
    for i in range(0, len(text), n):
        yield text[i:i + n]


# --- transcript rendering (messages -> one CLI prompt) --------------------------------------

def render_transcript(messages: list) -> tuple[str, str]:
    """Split OpenAI-style messages into (system_text, transcript_prompt). The transcript keeps
    user / assistant / tool-call / tool-result order and ends with an open `ASSISTANT:` turn."""
    system, lines = [], []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):   # multimodal parts -> text parts only
            content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = (content or "").strip()
        if role == "system":
            if content:
                system.append(content)
        elif role == "user":
            lines.append(f"USER: {content}")
        elif role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", None)
                name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "?")
                args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "{}")
                lines.append(f"ASSISTANT (tool call): {json.dumps({'name': name, 'arguments': _loads_or_raw(args)})}")
            if content:
                lines.append(f"ASSISTANT: {content}")
        elif role == "tool":
            lines.append(f"TOOL RESULT ({m.get('name') or m.get('tool_call_id') or 'tool'}): {content}")
    lines.append("ASSISTANT:")
    return "\n\n".join(system), "\n".join(lines)


def _loads_or_raw(s):
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s or "{}")
    except Exception:  # noqa: BLE001
        return s


def tool_protocol_block(tools: list, tool_choice=None) -> str:
    """The function-calling protocol for CLIs that have none: reply with ONLY a JSON envelope to
    call tools, plain text otherwise. Schema is the same OpenAI tool list the ApiBrain sends."""
    if not tools:
        return ""
    out = ["## Tools",
           "You can call tools. To call one or more, reply with ONLY this JSON object (no prose, no code fence):",
           '{"tool_calls":[{"name":"<tool name>","arguments":{...}}]}',
           "Each result comes back as a `TOOL RESULT (<name>):` line; then answer the user in plain text.",
           "Never claim an action happened unless its TOOL RESULT is in the transcript."]
    if tool_choice == "required":
        out.append("For THIS turn you must call at least one tool — a plain-text answer is not accepted.")
    out.append("")
    out.append("Available tools (name — description; parameters as JSON schema):")
    for t in tools:
        fn = t.get("function", t)
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        out.append(f"- {fn.get('name')} — {fn.get('description', '').strip()}")
        out.append(f"  parameters: {json.dumps(params, separators=(',', ':'))}")
    return "\n".join(out)


# --- parsing the CLI's answer ---------------------------------------------------------------

# What the operator sees when the CLI emitted tool-call markup we cannot turn into a call.
# Saying nothing would drop the turn; printing the markup is the defect this replaces.
NATIVE_MARKUP_FALLBACK = "Sorry — I garbled that one. Ask me again?"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _find_envelope(text: str) -> Optional[dict]:
    """First JSON object in `text` that carries a `tool_calls` list. Tries the whole string,
    then fenced blocks, then a brace-balanced scan (models add prose around the envelope)."""
    cands = [text.strip()] + [m.group(1).strip() for m in _FENCE_RE.finditer(text)]
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    cands.append(text[start:i + 1])
                    break
        start = text.find("{", start + 1)
    for c in cands:
        try:
            obj = json.loads(c)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
            return obj
    return None


# A CLI that is ALSO a tool-using agent sometimes answers in its NATIVE tool-call syntax
# instead of the JSON envelope the prompt asks for. That text carries no "tool_calls" key, so
# it used to fall through as the assistant's CONTENT — the operator saw raw markup on the
# Arturo home, and GET /api/arturo/threads stored it as the thread's snippet (found by effect
# on release sha 4ec0229 running the run-of-show's own beat-3.1 prompt, 2026-09-19).
_INVOKE_RE = re.compile(r'<invoke\s+name="([^"]*)"\s*>(.*?)</invoke>', re.S)
_PARAM_RE = re.compile(r'<parameter\s+name="([^"]*)"\s*>(.*?)</parameter>', re.S)
# A partial emission never closes its </invoke>; still markup, still must not be printed.
_INVOKE_OPEN_RE = re.compile(r'<invoke\s+name="[^"]*"\s*>')


def _coerce(raw: str):
    """Parameter bodies arrive as text. Give back the JSON value when it plainly is one, so a
    count reads as 30 and not "30"; otherwise the string, stripped."""
    v = raw.strip()
    try:
        return json.loads(v)
    except Exception:  # noqa: BLE001
        return v


def _find_native_calls(text: str) -> list:
    """Tool calls expressed as the CLI's native in-voke/parameter tag blocks (the tag name is
    written broken HERE on purpose: an unbroken one makes this file a court-guard tripwire for
    anyone quoting it; the regexes above carry the real thing).
    Returns [] when the text merely TALKS about the syntax — the opening tag must be present."""
    calls = []
    for name, body in _INVOKE_RE.findall(text):
        if not name.strip():
            continue
        args = {k: _coerce(v) for k, v in _PARAM_RE.findall(body)}
        calls.append(_tool_call(name.strip(), args))
    return calls


def _looks_like_native_markup(text: str) -> bool:
    return bool(_INVOKE_OPEN_RE.search(text))


def parse_cli_reply(text: str):
    text = (text or "").strip()
    env = _find_envelope(text) if "tool_calls" in text else None
    if env is None:
        # No JSON envelope. Before treating this as prose, check whether it is the CLI's own
        # tool-call syntax: honour the intent when it names a tool, and NEVER print the markup.
        if _looks_like_native_markup(text):
            native = _find_native_calls(text)
            if native:
                return make_response(None, native, "tool_calls")
            return make_response(NATIVE_MARKUP_FALLBACK, None, "stop")
        return make_response(text, None, "stop")
    calls = []
    for tc in env["tool_calls"]:
        if not isinstance(tc, dict) or not tc.get("name"):
            continue
        calls.append(_tool_call(str(tc["name"]), tc.get("arguments") or {}, tc.get("id")))
    if not calls:
        return make_response(text, None, "stop")
    return make_response(None, calls, "tool_calls")


# --- runtime command table ------------------------------------------------------------------

class UnsupportedRuntime(Exception):
    pass


@dataclass
class CommandSpec:
    argv: list
    stdin: Optional[str] = None
    output_file: Optional[Path] = None       # codex writes the last message here
    env_unset: list = field(default_factory=list)


def runtime_command(runtime: str, cli: str, system: str, prompt: str, model: str = "",
                    scratch: Optional[Path] = None) -> CommandSpec:
    """One non-interactive, tool-less, session-less invocation per runtime id (the ids are the
    runtime catalog's: claude / gemini / codex; `cli` is that provider's binary)."""
    if runtime == "claude":
        # --tools "" : the brain answers in text; Arturo runs its own tools. --strict-mcp-config +
        # --setting-sources "" : no MCP servers / hooks / plugins spin up (measured 5.1s -> 2.7s).
        argv = [cli, "-p", "--no-session-persistence", "--tools", "", "--strict-mcp-config",
                "--setting-sources", "", "--system-prompt", system]
        if model:
            argv += ["--model", model]
        return CommandSpec(argv=argv, stdin=prompt, env_unset=["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"])
    if runtime == "gemini":
        # agy --print swallows the next bare arg as the prompt, so the prompt is attached with `=`.
        argv = [cli, f"--print={system}\n\n{prompt}"]
        if model:
            argv += ["--model", model]
        return CommandSpec(argv=argv, stdin=None)
    if runtime == "codex":
        out = Path(tempfile.mkstemp(prefix="arturo-codex-", suffix=".txt", dir=str(scratch) if scratch else None)[1])
        argv = [cli, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never",
                "-o", str(out), "-"]
        if model:
            argv += ["-m", model]
        return CommandSpec(argv=argv, stdin=f"{system}\n\n{prompt}", output_file=out)
    raise UnsupportedRuntime(runtime)


def run_command(spec: CommandSpec, timeout: float) -> str:
    env = dict(os.environ)
    for k in spec.env_unset:
        env.pop(k, None)
    try:
        r = subprocess.run(spec.argv, input=spec.stdin, capture_output=True, text=True,
                           timeout=timeout, env=env, cwd=tempfile.gettempdir())
        if spec.output_file is not None:
            try:
                text = spec.output_file.read_text()
            except FileNotFoundError:
                text = ""
            if text.strip():
                return text
        if r.returncode != 0 and not (r.stdout or "").strip():
            raise RuntimeError(f"{spec.argv[0]} exited {r.returncode}: {(r.stderr or '').strip()[:300]}")
        return r.stdout or ""
    finally:
        if spec.output_file is not None:
            try:
                spec.output_file.unlink()
            except OSError:
                pass


# --- the brains -----------------------------------------------------------------------------

class Brain:
    kind = "abstract"
    model = ""

    def complete(self, messages, tools=None, tool_choice=None, max_tokens=1024, temperature=0.7,
                 stream=False, timeout=None, model=None):
        raise NotImplementedError

    def describe(self) -> dict:
        return {"kind": self.kind, "model": self.model}


class ApiBrain(Brain):
    """Today's path: Gemini's OpenAI-compatible endpoint through the openai client."""
    kind = "api"

    def __init__(self, api_key: str, model: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"):
        from openai import OpenAI   # imported here so a keyless install never needs it at boot
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, messages, tools=None, tool_choice=None, max_tokens=1024, temperature=0.7,
                 stream=False, timeout=None, model=None):
        kw = dict(model=self.model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = tool_choice or "auto"
        if stream:
            kw["stream"] = True
        if timeout is not None:
            kw["timeout"] = timeout
        return self._client.chat.completions.create(**kw)

    def describe(self):
        return {"kind": "api", "model": self.model, "provider": "gemini"}


class RuntimeBrain(Brain):
    """Shells the authed CLI. Tools ride in the system prompt as the JSON-envelope protocol."""
    kind = "runtime"

    def __init__(self, runtime: str, cli: str, model: str = "", runner: Callable = run_command,
                 timeout: float = DEFAULT_RUNTIME_TIMEOUT_S):
        self.runtime = runtime
        self.cli = cli
        self.model = model or f"{runtime}-cli-default"
        self._model_flag = model
        self._run = runner
        self._timeout = timeout

    def _text(self, messages, tools, tool_choice, timeout) -> str:
        system, prompt = render_transcript(messages)
        block = tool_protocol_block(tools, tool_choice)
        if block:
            system = f"{system}\n\n{block}" if system else block
        spec = runtime_command(self.runtime, self.cli, system, prompt, self._model_flag)
        return self._run(spec, timeout or self._timeout)

    def complete(self, messages, tools=None, tool_choice=None, max_tokens=1024, temperature=0.7,
                 stream=False, timeout=None, model=None):
        try:
            text = self._text(messages, tools, tool_choice, timeout)
        except Exception as e:  # noqa: BLE001 — a CLI hiccup is a sentence, not a 500
            log.error(f"runtime brain ({self.runtime}) failed: {e}")
            # The detail goes to the LOG above and NEVER into the reply: a CalledProcessError
            # stringifies to the whole argv, which carries --system-prompt followed by Arturo's
            # entire system prompt, and truncating to 200 chars only cuts it off mid-prompt
            # (byte-captured: fixtures/runtime_error_reply.txt). It reached the operator's
            # screen and persisted as the thread snippet. The operator gets prose.
            text = (f"My {self.runtime} brain did not answer that time — the {self.cli} CLI "
                    f"exited unexpectedly. Try that again, and run `orchestra doctor` if it "
                    f"keeps happening.")
            tools = None
        if stream:
            return (make_chunk(c) for c in _chunked(text.strip()))
        return parse_cli_reply(text) if tools else make_response(text.strip(), None, "stop")

    def describe(self):
        return {"kind": "runtime", "runtime": self.runtime, "cli": self.cli, "model": self.model}


class NullBrain(Brain):
    kind = "none"
    model = "none"

    def __init__(self, reason: str = NULL_REASON):
        self.reason = reason

    def complete(self, messages, tools=None, tool_choice=None, max_tokens=1024, temperature=0.7,
                 stream=False, timeout=None, model=None):
        text = f"I can't think yet. {self.reason}"
        if stream:
            return (make_chunk(c) for c in _chunked(text))
        return make_response(text, None, "stop")

    def describe(self):
        return {"kind": "none", "model": "none", "reason": self.reason}


# --- selection ------------------------------------------------------------------------------

def select_brain(mode: str, api_key: str, probes: list, api_model: str = "gemini-2.5-flash",
                 runtime_model: str = "", api_factory: Optional[Callable] = None) -> Brain:
    """`probes` = orchestra_cli.runtime_probe.probe_all() rows (id, cli, installed, authed)."""
    mode = (mode or "auto").strip().lower()
    if mode not in BRAIN_MODES:
        log.warning(f"arturo.brain={mode!r} unknown — treating as auto")
        mode = "auto"
    authed = [p for p in (probes or []) if p.get("installed") and p.get("authed") is True]

    def _api():
        try:
            return (api_factory or (lambda key, model: ApiBrain(key, model)))(api_key, api_model)
        except Exception as e:  # noqa: BLE001
            return NullBrain(f"api brain failed to initialise: {e}")

    def _runtime():
        p = authed[0]
        return RuntimeBrain(p["id"], p["cli"], runtime_model)

    if mode == "api":
        return _api() if api_key else NullBrain("arturo.brain=api but no GEMINI_API_KEY is set. " + NULL_REASON)
    if mode == "runtime":
        return _runtime() if authed else NullBrain("arturo.brain=runtime but no CLI is installed+logged in. " + NULL_REASON)
    if api_key:
        return _api()
    if authed:
        return _runtime()
    return NullBrain(NULL_REASON)


def probe_runtimes(repo_root: Path, enabled: Optional[list] = None) -> list:
    """Runtime catalog probe (the same one `orchestra doctor` runs). Never raises."""
    try:
        from orchestra_cli.runtime_probe import load_providers, probe_all
        providers = load_providers(Path(repo_root) / "config" / "providers.json")
        want = enabled or [p["id"] for p in providers]
        return probe_all(providers, want)
    except Exception as e:  # noqa: BLE001
        log.error(f"runtime probe failed: {e}")
        return []


def reselect_if_none(current, mode: str, api_key: str, repo_root, enabled: Optional[list],
                     api_model: str, runtime_model: str = "", probe_fn: Optional[Callable] = None,
                     min_interval_s: float = 5.0, _state: dict = {}):
    """Self-heal for a brain chosen at boot before any CLI was logged in (G14, 2026-09-17:
    on a clean install /health kept reporting brain=none after the user logged in, so the
    onboarding "Check again" never advanced). Re-probes at most every `min_interval_s`
    and returns a NEW brain only when one is now available; otherwise the current one.
    Never replaces a working brain."""
    import time as _t
    if current is None or getattr(current, "kind", None) != "none":
        return current
    now = _t.monotonic()
    if now - _state.get("last", -1e9) < min_interval_s:
        return current
    _state["last"] = now
    probes = (probe_fn or probe_runtimes)(repo_root, enabled)
    nb = select_brain(mode, api_key, probes=probes, api_model=api_model, runtime_model=runtime_model)
    if getattr(nb, "kind", "none") != "none":
        log.info(f"brain: re-selected after boot — {nb.describe()}")
        return nb
    return current
